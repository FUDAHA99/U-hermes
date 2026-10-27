"""Offline OCR regressions. Run with the complete bundle's Python and models.

Includes actual ONNX/CPU inference in the same restricted subprocess used by
HR uploads. No key, model provider or network connection is used.
"""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from recruit_core import ConfigStore, RecruitError, TASKS, extract_upload, validate_config
from recruit_limits import EXTRACT_SECONDS, MEMORY_BYTES
from recruit_ocr import OCRSession, PDFRenderer, decode_image, verify_models, _windows_dll_directory
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

SPEC = importlib.util.spec_from_file_location("recruit_ocr_server", SCRIPTS / "recruit-server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
ASSETS = SCRIPTS.parent / "recruit"
BODY = "虚构候选人，申请新媒体运营岗位。负责内容策划、数据分析、社群运营和短视频剪辑，能够使用表格复盘阅读量及转化率。" * 3


def upload(name, raw):
    return {"name": name, "content": base64.b64encode(raw).decode("ascii")}


def pdf_pages(texts):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(595, 842)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(("BT /F1 12 Tf 40 700 Td (" + text + ") Tj ET").encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def png_header(width, height):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0")) + chunk(b"IEND", b""))


class OCRRoutingTests(unittest.TestCase):
    def test_text_pdf_never_loads_ocr_or_renderer(self):
        path = next((ASSETS / "demo-resumes").glob("01_*.pdf"))
        with patch.object(OCRSession, "__init__", side_effect=AssertionError("OCR not needed")), \
                patch.object(PDFRenderer, "__init__", side_effect=AssertionError("render not needed")):
            result = extract_upload(upload(path.name, path.read_bytes()))
        self.assertEqual(result["extraction"], {"method": "text", "text_pages": 1, "ocr_pages": 0})

    def test_scanned_and_mixed_pdf_route_per_page(self):
        for texts, expected in [([""], (0, 1)), (["Experience skills results " * 8, "", "projects " * 20], (2, 1))]:
            with self.subTest(pages=len(texts)), patch.object(OCRSession, "__init__", return_value=None), \
                    patch.object(OCRSession, "recognize_pil", return_value=BODY) as recognize, \
                    patch.object(PDFRenderer, "render", return_value=Image.new("RGB", (100, 100))) as render:
                result = extract_upload(upload("mixed.pdf", pdf_pages(texts)))
                self.assertEqual((result["extraction"]["text_pages"], result["extraction"]["ocr_pages"]), expected)
                render.assert_called_once_with(0 if len(texts) == 1 else 1)
                recognize.assert_called_once()
                self.assertIn(BODY, result["text"])

    def test_sparse_text_layer_uses_full_page_ocr_without_duplicate_header(self):
        with patch.object(OCRSession, "__init__", return_value=None), \
                patch.object(OCRSession, "recognize_pil", return_value=BODY):
            result = extract_upload(upload("header.pdf", pdf_pages(["Page 1"])))
        self.assertEqual(result["text"], BODY)
        self.assertEqual(result["extraction"]["ocr_pages"], 1)

    def test_twenty_scan_pages_share_one_engine_and_release_each_bitmap(self):
        images = []
        def render(index):
            if images:
                with self.assertRaises(ValueError):
                    images[-1].getpixel((0, 0))
            image = Image.new("RGB", (100, 100))
            images.append(image)
            return image
        with patch.object(OCRSession, "__init__", return_value=None) as initialize, \
                patch.object(OCRSession, "recognize_pil", return_value=BODY), \
                patch.object(PDFRenderer, "render", side_effect=render):
            result = extract_upload(upload("twenty.pdf", pdf_pages([""] * 20)))
        initialize.assert_called_once()
        self.assertEqual(result["extraction"]["ocr_pages"], 20)
        with self.assertRaises(ValueError):
            images[-1].getpixel((0, 0))

    def test_pdf_embedded_oversized_image_is_rejected_before_rendering(self):
        writer = PdfWriter()
        page = writer.add_blank_page(595, 842)
        image = DecodedStreamObject()
        image.set_data(b"\0")
        image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                      NameObject("/Width"): NumberObject(50000), NameObject("/Height"): NumberObject(50000),
                      NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(image)})})
        content = DecodedStreamObject()
        content.set_data(b"q 500 0 0 500 0 0 cm /Im0 Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
        output = io.BytesIO()
        writer.write(output)
        import pypdfium2
        with patch.object(pypdfium2.PdfPage, "render", side_effect=AssertionError("must not decode")):
            with self.assertRaisesRegex(RecruitError, "图片过大"):
                extract_upload(upload("bomb.pdf", output.getvalue()))

    def test_missing_or_tampered_model_fails_closed(self):
        paths = verify_models()
        self.assertEqual(set(paths), {"Det", "Cls", "Rec"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class MissingDistribution:
                version = "3.4.2"
                def locate_file(self, path):
                    return root / Path(path).name
            with patch("recruit_ocr.metadata.distribution", return_value=MissingDistribution()):
                with self.assertRaisesRegex(RecruitError, "模型缺失或校验失败"):
                    verify_models()
                for path in paths.values():
                    (root / path.name).write_bytes(b"x" * path.stat().st_size)
                with self.assertRaisesRegex(RecruitError, "模型缺失或校验失败"):
                    verify_models()

    def test_windows_runtime_uses_current_interpreter_directory_and_checks_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "moved USB" / "Scripts"
            scripts.mkdir(parents=True)
            dll = scripts / "msvcp140.dll"
            dll.write_bytes(b"fake-VC-DLL-for-path-test")
            manifest = root / "models.json"
            manifest.write_text(json.dumps({"windows_runtime": {"distribution": "msvc-runtime",
                "version": "fixture", "files": {dll.name: {"bytes": dll.stat().st_size,
                "sha256": hashlib.sha256(dll.read_bytes()).hexdigest()}}}}))
            with patch("recruit_ocr.sys.platform", "win32"), \
                    patch("recruit_ocr.sys.executable", str(scripts / "python.exe")), \
                    patch("recruit_ocr.MANIFEST", manifest), \
                    patch("recruit_ocr.metadata.version", return_value="fixture"), \
                    patch("recruit_ocr.os.add_dll_directory", create=True) as add_directory:
                self.assertIs(_windows_dll_directory(), add_directory.return_value)
                add_directory.assert_called_once_with(str(scripts.resolve()))
                dll.write_bytes(b"x" * dll.stat().st_size)
                with self.assertRaisesRegex(RecruitError, "运行库"):
                    _windows_dll_directory()
                self.assertEqual(add_directory.call_count, 1)

    def test_windows_runtime_missing_files_fails_before_native_import(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("recruit_ocr.sys.platform", "win32"), \
                    patch("recruit_ocr.sys.executable", str(Path(directory) / "python.exe")), \
                    patch("recruit_ocr.metadata.version", return_value="14.44.35112"):
                with self.assertRaisesRegex(RecruitError, "运行库"):
                    _windows_dll_directory()

    def test_all_image_formats_are_decoded_locally(self):
        for suffix, fmt in [("jpg", "JPEG"), ("jpeg", "JPEG"), ("png", "PNG"), ("webp", "WEBP"), ("bmp", "BMP")]:
            output = io.BytesIO()
            with Image.new("RGB", (200, 300), "white") as image:
                image.save(output, format=fmt)
            with self.subTest(format=fmt), patch.object(OCRSession, "__init__", return_value=None), \
                    patch.object(OCRSession, "recognize_pil", return_value=BODY):
                result = extract_upload(upload("resume." + suffix, output.getvalue()))
            self.assertEqual(result["extraction"]["ocr_pages"], 1)

    def test_large_and_bomb_images_rejected_before_decode(self):
        for dimensions in [(5000, 5000), (100000, 100000)]:
            with self.subTest(dimensions=dimensions), patch.object(Image.Image, "load", side_effect=AssertionError("must not decode")):
                with self.assertRaises(RecruitError):
                    decode_image(png_header(*dimensions))

    def test_exif_orientation_and_resize(self):
        output = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        with Image.new("RGB", (1800, 900), "white") as image:
            image.save(output, format="JPEG", exif=exif)
        with decode_image(output.getvalue()) as decoded:
            self.assertGreater(decoded.height, decoded.width)
            self.assertLessEqual(max(decoded.size), 1600)

    def test_extremely_thin_image_rejected_before_rapidocr_can_upscale(self):
        with patch.object(OCRSession, "__init__", return_value=None):
            session = OCRSession()
            with Image.new("RGB", (1, 1600), "white") as image:
                with self.assertRaises(RecruitError):
                    session.recognize_pil(image)

    def test_ocr_disabled_still_accepts_text_but_rejects_scan_and_image(self):
        with patch.object(OCRSession, "__init__", side_effect=AssertionError("disabled")):
            for name, raw in [("scan.pdf", pdf_pages([""])), ("photo.jpg", b"fake")]:
                with self.subTest(name=name), self.assertRaisesRegex(RecruitError, "OCR 已关闭"):
                    extract_upload(upload(name, raw), ocr_enabled=False)
            result = extract_upload(upload("text.pdf", pdf_pages(["skills " * 30])), ocr_enabled=False)
        self.assertEqual(result["extraction"]["ocr_pages"], 0)

    def test_too_little_ocr_text_and_failure_are_actionable(self):
        for answer in ("", "?! .", "只有姓名"):
            with self.subTest(answer=answer), patch.object(OCRSession, "__init__", return_value=None), \
                    patch.object(OCRSession, "recognize_pil", return_value=answer):
                with self.assertRaisesRegex(RecruitError, "第 1 页.*文字太少"):
                    extract_upload(upload("empty.pdf", pdf_pages([""])))
        with patch.object(OCRSession, "__init__", side_effect=RuntimeError("private stack")):
            with self.assertRaisesRegex(RecruitError, "未计用量") as error:
                extract_upload(upload("broken.pdf", pdf_pages([""])))
        self.assertNotIn("private stack", str(error.exception))

    def test_ocr_character_limit_and_pdf_page_limit(self):
        with patch.object(OCRSession, "__init__", return_value=None), \
                patch.object(OCRSession, "recognize_pil", return_value="字" * 20001):
            with self.assertRaisesRegex(RecruitError, "20000"):
                extract_upload(upload("long.pdf", pdf_pages([""])))
        with patch.object(OCRSession, "__init__", side_effect=AssertionError("before OCR")):
            with self.assertRaisesRegex(RecruitError, "20 页"):
                extract_upload(upload("long.pdf", pdf_pages([""] * 21)))

    def test_old_doc_has_conversion_instructions(self):
        with self.assertRaisesRegex(RecruitError, "另存为 DOCX 或 PDF"):
            extract_upload(upload("resume.doc", b"legacy"))

    def test_old_config_defaults_to_ocr_without_overwriting_customer_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ASSETS, root / "recruit")
            store = ConfigStore(root)
            store.initialize()
            config = store.load()
            self.assertTrue(config.pop("ocr_enabled"))
            file = store.data / "config.json"
            file.write_text(json.dumps(config), encoding="utf-8")
            before = file.read_bytes()
            store.initialize()
            self.assertTrue(store.load()["ocr_enabled"])
            self.assertEqual(file.read_bytes(), before)
            config["ocr_enabled"] = False
            self.assertFalse(validate_config(config)["ocr_enabled"])
            config["ocr_enabled"] = "false"
            with self.assertRaises(RecruitError):
                validate_config(config)


class OCRWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recruit-ocr-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        shutil.copytree(ASSETS, root / "recruit")
        self.app = server.RecruitApp(root)

    def assert_clean_and_free(self):
        self.assertFalse(self.app.busy.locked())
        self.assertEqual({task: self.app.usage.snapshot()[task] for task in TASKS}, dict.fromkeys(TASKS, 0))
        runs = self.app.store.data / "runs"
        self.assertFalse(runs.exists() and list(runs.iterdir()))

    def test_real_offline_scan_mixed_and_phone_photo(self):
        for prefix, pages in [("06", (0, 1)), ("07", (1, 1)), ("08", (0, 1))]:
            path, = (ASSETS / "demo-resumes").glob(prefix + "_*.*")
            with self.subTest(name=path.name):
                result = self.app.extract(upload(path.name, path.read_bytes()))
                self.assertIn("虚构", result["text"])
                self.assertIn("运营", result["text"])
                self.assertGreater(len(result["text"]), 150)
                self.assertEqual((result["extraction"]["text_pages"], result["extraction"]["ocr_pages"]), pages)
                self.assert_clean_and_free()

    def test_blank_image_fails_in_real_worker_without_usage(self):
        image = io.BytesIO()
        with Image.new("RGB", (600, 800), "white") as blank:
            blank.save(image, format="PNG")
        with self.assertRaisesRegex(RecruitError, "文字太少"):
            self.app.extract(upload("blank.png", image.getvalue()))
        self.assert_clean_and_free()

    def test_disabled_config_reaches_worker_without_usage(self):
        config = self.app.store.load()
        config["ocr_enabled"] = False
        (self.app.store.data / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(RecruitError, "OCR 已关闭"):
            self.app.extract(upload("scan.pdf", pdf_pages([""])))
        self.assert_clean_and_free()

    def test_real_worker_rejects_decompression_bomb_without_usage(self):
        with self.assertRaises(RecruitError):
            self.app.extract(upload("bomb.png", png_header(100000, 100000)))
        self.assert_clean_and_free()

    def test_timeout_kills_child_cleans_and_does_not_charge(self):
        real_popen = subprocess.Popen
        children = []
        def slow_child(*args, **kwargs):
            child = real_popen([sys.executable, "-c", "import time;time.sleep(20)"], **kwargs)
            children.append(child)
            return child
        with patch.object(server.subprocess, "Popen", side_effect=slow_child), patch.object(server, "EXTRACT_SECONDS", 0.1):
            with self.assertRaisesRegex(RecruitError, "未计用量"):
                self.app.extract(upload("scan.pdf", pdf_pages([""])))
        self.assertIsNotNone(children[0].poll())
        self.assert_clean_and_free()

    def test_resource_limits_and_network_child_process_block(self):
        code = '''import sys, json, socket, subprocess
sys.path.insert(0, sys.argv[1])
from recruit_limits import restrict_documents, MEMORY_BYTES, EXTRACT_SECONDS
restrict_documents()
blocked = 0
for action in (lambda: socket.create_connection(("127.0.0.1", 1)), lambda: subprocess.run([sys.executable, "-c", "pass"])):
    try: action()
    except PermissionError: blocked += 1
assert blocked == 2
if sys.platform != "win32":
    import resource
    assert resource.getrlimit(resource.RLIMIT_AS) == (MEMORY_BYTES, MEMORY_BYTES)
    assert resource.getrlimit(resource.RLIMIT_CPU) == (EXTRACT_SECONDS, EXTRACT_SECONDS)
print("limited")
'''
        result = subprocess.run([sys.executable, "-I", "-c", code, str(SCRIPTS)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("limited", result.stdout)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    unittest.main()
