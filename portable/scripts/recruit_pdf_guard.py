"""Check PDF image declarations before PDFium decodes a page for local OCR.

Walk only rendering resources reachable from this page, never /Parent, /Root or
the reader's whole object table. Includes masks, patterns, forms, Type 3 glyphs
and inline images. The caller retains the bounded pypdf configuration and the
document worker's memory/time limits; this is not an OS sandbox.
"""
from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, StreamObject

from recruit_core import RecruitError

MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 20000
MAX_RESOURCE_VISITS = 2000
MAX_RESOURCE_DEPTH = 16
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_CONTENT_BYTES = 8 * 1024 * 1024


def guard_pdf_images(page):
    """Fail closed on oversized images or excessive rendering-resource graphs."""
    seen = set()
    visits = 0
    pixels = 0
    content_bytes = 0
    pdf = getattr(getattr(page, "indirect_reference", None), "pdf", None)

    def resolve(value):
        return value.get_object() if hasattr(value, "get_object") else value

    def image_dimensions(settings, inline=False):
        nonlocal pixels
        width = resolve(settings.get("/Width", settings.get("/W") if inline else None))
        height = resolve(settings.get("/Height", settings.get("/H") if inline else None))
        if (not isinstance(width, int) or isinstance(width, bool)
                or not isinstance(height, int) or isinstance(height, bool)
                or width <= 0 or height <= 0):
            raise RecruitError("PDF 图片尺寸无效，请重新导出 PDF；本次未计用量。")
        if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
            raise RecruitError("PDF 内图片过大：最多 2000 万像素、单边最多 20000 像素，请降低扫描分辨率；本次未计用量。")
        pixels += width * height
        if pixels > MAX_IMAGE_PIXELS:
            raise RecruitError("PDF 单页图片及蒙版总像素超过 2000 万，请降低扫描分辨率；本次未计用量。")

    def content(stream):
        nonlocal content_bytes, visits
        if stream is None:
            return
        parsed = stream if isinstance(stream, ContentStream) else ContentStream(stream, pdf)
        size = len(parsed.get_data())
        content_bytes += size
        if size > MAX_STREAM_BYTES or content_bytes > MAX_CONTENT_BYTES:
            raise RecruitError("PDF 页面绘制内容过大，请简化或重新导出 PDF；本次未计用量。")
        for operands, operator in parsed.operations:
            if operator == b"INLINE IMAGE":
                visits += 1
                if visits > MAX_RESOURCE_VISITS:
                    raise RecruitError("PDF 页面图片或资源过多，请简化 PDF；本次未计用量。")
                image_dimensions(operands["settings"], inline=True)

    def walk(value, role="object", depth=0):
        nonlocal visits
        if value is None:
            return
        visits += 1
        if visits > MAX_RESOURCE_VISITS or depth > MAX_RESOURCE_DEPTH:
            raise RecruitError("PDF 页面资源过多或嵌套过深，请简化 PDF；本次未计用量。")
        value = resolve(value)
        if not isinstance(value, (DictionaryObject, ArrayObject)):
            return
        key = (id(value), role)
        if key in seen:
            return
        seen.add(key)
        if isinstance(value, ArrayObject):
            for entry in value:
                walk(entry, depth=depth + 1)
            return
        if role == "map":
            for entry in value.values():
                walk(entry, depth=depth + 1)
            return
        if role == "resources":
            for name in ("/XObject", "/Pattern", "/ExtGState", "/Font"):
                walk(value.get(name), "map", depth + 1)
            return
        if resolve(value.get("/Subtype")) == "/Image":
            # Never call get_data() on image streams, including soft masks.
            image_dimensions(value)
        elif isinstance(value, StreamObject):
            content(value)
        walk(value.get("/Resources"), "resources", depth + 1)
        walk(value.get("/CharProcs"), "map", depth + 1)
        for name in ("/Mask", "/SMask", "/G", "/Image", "/Alternates"):
            walk(value.get(name), depth=depth + 1)

    try:
        content(page.get_contents())
        walk(page.get("/Resources"), "resources")
    except RecruitError:
        raise
    except Exception:
        raise RecruitError("PDF 图片资源无法安全读取，请重新导出 PDF；本次未计用量。") from None
