"""Bounded offline CPU OCR; imported only for pages needing recognition.

Models travel inside the pinned RapidOCR wheel. Never accept model paths, URLs,
runtime options or output paths from an upload. All document bytes stay in RAM.
The caller must run this module in the resource-limited extraction subprocess.
"""
import ctypes
import hashlib
from importlib import metadata
import io
import json
import math
import os
from pathlib import Path
import sys
import warnings

from recruit_core import RecruitError

MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 20000
OCR_MAX_SIDE = 1600
MAX_PDF_OBJECTS = 2000
MAX_FORM_DEPTH = 8
MANIFEST = Path(__file__).resolve().parent.parent / "recruit" / "ocr-models.json"


def verify_models():
    """Check installed local models without importing OCR or accessing network."""
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        dist = metadata.distribution(manifest["distribution"])
        if dist.version != manifest["version"]:
            raise ValueError("version mismatch")
        paths = {}
        for role, entry in manifest["models"].items():
            path = Path(dist.locate_file("rapidocr/models/" + entry["file"]))
            if path.stat().st_size != entry["bytes"]:
                raise ValueError("size mismatch")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != entry["sha256"]:
                raise ValueError("hash mismatch")
            paths[role] = path
        return paths
    except (OSError, ValueError, KeyError, metadata.PackageNotFoundError):
        raise RecruitError("本地 OCR 模型缺失或校验失败，请管理员运行安装脚本修复；也可另存为文字 PDF 或 DOCX。本次不计用量。") from None


def _windows_dll_directory():
    if sys.platform != "win32":
        return None
    # msvc-runtime installs the redistributable DLLs beside venv Python. Verify
    # and use that directory explicitly, so a developer's system VC installation
    # cannot conceal a missing DLL in the distributable USB folder.
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["windows_runtime"]
        if metadata.version(manifest["distribution"]) != manifest["version"]:
            raise ValueError("version mismatch")
        directory = Path(sys.executable).resolve().parent
        for name, entry in manifest["files"].items():
            path = directory / name
            if path.stat().st_size != entry["bytes"]:
                raise ValueError("size mismatch")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                    raise ValueError("hash mismatch")
        return os.add_dll_directory(str(directory))
    except (OSError, ValueError, KeyError, metadata.PackageNotFoundError):
        raise RecruitError("便携包缺少或损坏了本地 OCR 运行库，请管理员运行安装脚本修复。本次不计用量。") from None


def _check_dimensions(width, height):
    if (width <= 0 or height <= 0 or width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE
            or width * height > MAX_IMAGE_PIXELS):
        raise RecruitError("图片过大：每张最多 2000 万像素、单边最多 20000 像素；请缩小图片后上传。本次不计用量。")


def decode_image(raw):
    """Validate header before any pixel decode; return detached, bounded RGB PIL."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise RecruitError("缺少图片解析组件，请管理员运行安装脚本修复。本次不计用量。") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP", "BMP"}:
                    raise RecruitError("图片格式不支持，请上传 JPG、PNG、WEBP 或 BMP。")
                _check_dimensions(*original.size)
                if getattr(original, "n_frames", 1) != 1:
                    raise RecruitError("不支持多帧或动图简历，请导出为单张图片或 PDF。")
                # JPEG can downsample in the decoder, before allocating RGB.
                original.draft("RGB", (OCR_MAX_SIDE, OCR_MAX_SIDE))
                corrected = ImageOps.exif_transpose(original)
                try:
                    corrected.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE), Image.Resampling.LANCZOS)
                    # Composite transparent PNG/WEBP on paper white, not black.
                    if corrected.mode in ("RGBA", "LA") or "transparency" in corrected.info:
                        with corrected.convert("RGBA") as rgba:
                            result = Image.new("RGB", rgba.size, "white")
                            result.paste(rgba, mask=rgba.getchannel("A"))
                            return result
                    return corrected.convert("RGB")
                finally:
                    corrected.close()
    except RecruitError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise RecruitError("图片像素过大，已阻止解码；请缩小到 2000 万像素以内后上传。本次不计用量。") from None
    except Exception:
        raise RecruitError("图片无法读取或已经损坏，请重新导出 JPG、PNG、WEBP 或 BMP。本次不计用量。") from None


class PDFRenderer:
    """Open once per file, render and release each bounded page serially."""
    def __init__(self, raw):
        try:
            import pypdfium2 as pdfium
        except ImportError:
            raise RecruitError("缺少扫描 PDF 渲染组件，请管理员运行安装脚本修复。本次不计用量。") from None
        self.pdfium = pdfium
        self.document = pdfium.PdfDocument(raw)
        if len(self.document) > 20:
            self.document.close()
            raise RecruitError("PDF 最多 20 页，请拆分后上传。")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.document.close()

    def _check_objects(self, page):
        # Query intrinsic sizes without decoding pixels. Walk Form XObjects too;
        # get_objects(max_depth=...) would silently ignore objects beyond depth.
        raw = self.pdfium.raw
        seen_count = 0
        total_pixels = 0

        def walk(parent, depth=0, form=False):
            nonlocal seen_count, total_pixels
            count = raw.FPDFFormObj_CountObjects(parent) if form else raw.FPDFPage_CountObjects(parent)
            if count < 0 or count + seen_count > MAX_PDF_OBJECTS:
                raise RecruitError("PDF 页面对象过多，请简化或重新导出 PDF。")
            seen_count += count
            for index in range(count):
                obj = (raw.FPDFFormObj_GetObject(parent, index) if form
                       else raw.FPDFPage_GetObject(parent, index))
                kind = raw.FPDFPageObj_GetType(obj)
                if kind == raw.FPDF_PAGEOBJ_FORM:
                    if depth >= MAX_FORM_DEPTH:
                        raise RecruitError("PDF 页面嵌套过深，请重新导出 PDF。")
                    walk(obj, depth + 1, True)
                elif kind == raw.FPDF_PAGEOBJ_IMAGE:
                    width, height = ctypes.c_uint(), ctypes.c_uint()
                    if not raw.FPDFImageObj_GetImagePixelSize(obj, width, height):
                        raise RecruitError("PDF 内图片尺寸无效，请重新导出 PDF。")
                    _check_dimensions(width.value, height.value)
                    total_pixels += width.value * height.value
                    if total_pixels > MAX_IMAGE_PIXELS:
                        raise RecruitError("PDF 单页图片总像素超过 2000 万，请降低扫描分辨率后上传。本次不计用量。")

        walk(page)

    def render(self, index):
        page = self.document[index]
        try:
            width, height = page.get_size()
            if not all(math.isfinite(v) and 0 < v <= 14400 for v in (width, height)):
                raise RecruitError("PDF 页面尺寸异常，请重新导出常规纸张大小的 PDF。")
            self._check_objects(page)
            # At most 1600 px on either axis, never above 150 dpi. Annotation
            # appearances are excluded so they cannot hide uninspected bitmaps.
            scale = min(150 / 72, (OCR_MAX_SIDE - 1) / max(width, height))
            bitmap = page.render(scale=scale, draw_annots=False)
            try:
                with bitmap.to_pil() as image:
                    return image.convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()


def _no_download(*_args, **_kwargs):
    raise RecruitError("本地 OCR 所需文件不完整，请管理员运行安装脚本修复；识别过程不会联网下载。本次不计用量。")


class OCRSession:
    def __init__(self):
        paths = verify_models()
        self._dll_directory = _windows_dll_directory()
        try:
            import cv2
            from rapidocr import RapidOCR
            from rapidocr.utils.download_file import DownloadFile
        except ImportError:
            raise RecruitError("本地 OCR 组件未安装完整，请管理员运行安装脚本修复。本次不计用量。") from None
        cv2.setNumThreads(1)
        cv2.ocl.setUseOpenCL(False)
        # Fail closed if an upstream fallback ever asks for a model/font/dict.
        # The source package is unmodified; the adapter runs only in our worker.
        DownloadFile.run = staticmethod(_no_download)
        params = {
            "Global.log_level": "critical", "Global.max_side_len": OCR_MAX_SIDE,
            # RapidOCR eagerly creates a visualizer even for text-only results.
            # Empty explicit font path avoids its automatic font download. We
            # never call the visualizer, so no font file is required.
            "Global.font_path": "", "Global.text_score": 0.5,
            "EngineConfig.onnxruntime.intra_op_num_threads": 1,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
            "EngineConfig.onnxruntime.use_cuda": False,
            "EngineConfig.onnxruntime.use_dml": False,
            "EngineConfig.onnxruntime.use_cann": False,
            "Det.limit_side_len": 960, "Det.limit_type": "max",
            "Det.max_candidates": 500, "Rec.rec_batch_num": 1, "Cls.cls_batch_num": 1,
        }
        params.update({role + ".model_path": str(path) for role, path in paths.items()})
        self.engine = RapidOCR(params=params)

    def recognize_pil(self, image):
        import cv2
        import numpy as np
        _check_dimensions(*image.size)
        # RapidOCR upsizes a short axis to its minimum after enforcing max_side;
        # a 1x1600 image could otherwise become 32x48000 and exhaust memory.
        if min(image.size) < 32:
            raise RecruitError("图片过窄或分辨率过低，请上传完整清晰的简历图片（短边至少 32 像素）。本次不计用量。")
        if max(image.size) > OCR_MAX_SIDE:
            raise RecruitError("OCR 输入分辨率超限，请缩小图片。")
        # Flatten gradual phone-camera shadows at bounded resolution. OCR's DB
        # detector returns quadrilaterals; its crop rectification handles mild
        # skew/perspective and the classifier handles upside-down text lines.
        gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
        background = cv2.GaussianBlur(gray, (0, 0), sigmaX=25)
        corrected = cv2.divide(gray, np.maximum(background, 1), scale=230)
        del gray, background
        pixels = cv2.cvtColor(corrected, cv2.COLOR_GRAY2BGR)
        del corrected
        result = self.engine(pixels)
        try:
            return "\n".join(result.txts or ())
        finally:
            # RapidOCROutput retains both input pixels and cropped line images.
            del result, pixels
