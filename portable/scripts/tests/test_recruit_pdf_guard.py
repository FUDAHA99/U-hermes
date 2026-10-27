"""Image declarations hidden in PDF rendering resources are checked pre-decode."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from recruit_core import RecruitError
from recruit_pdf_guard import guard_pdf_images
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           NameObject, NumberObject)


def dictionary(**kwargs):
    return DictionaryObject({NameObject("/" + key): value for key, value in kwargs.items()})


def image(width, height):
    stream = DecodedStreamObject()
    stream.set_data(b"\0")
    stream.update(dictionary(Type=NameObject("/XObject"), Subtype=NameObject("/Image"),
                             Width=NumberObject(width), Height=NumberObject(height),
                             ColorSpace=NameObject("/DeviceGray"), BitsPerComponent=NumberObject(8)))
    return stream


def drawing(resources=None, data=b"q Q"):
    stream = DecodedStreamObject()
    stream.set_data(data)
    stream.update(dictionary(Type=NameObject("/XObject"), Subtype=NameObject("/Form")))
    if resources is not None:
        stream[NameObject("/Resources")] = resources
    return stream


def page_with(resources=None, data=b"q Q"):
    writer = PdfWriter()
    page = writer.add_blank_page(595, 842)
    if resources is not None:
        page[NameObject("/Resources")] = resources
    content = DecodedStreamObject()
    content.set_data(data)
    page[NameObject("/Contents")] = content
    return page


class PDFGuardTests(unittest.TestCase):
    def test_legal_image_and_mask_never_decode_pixels(self):
        base, mask = image(100, 200), image(100, 200)
        base[NameObject("/SMask")] = mask
        page = page_with(dictionary(XObject=dictionary(Photo=base)))
        with patch.object(base, "get_data", side_effect=AssertionError("image must stay encoded")), \
                patch.object(mask, "get_data", side_effect=AssertionError("mask must stay encoded")):
            guard_pdf_images(page)

    def test_oversized_soft_and_explicit_masks_are_rejected(self):
        for field in ("/SMask", "/Mask"):
            with self.subTest(mask=field):
                base = image(1, 1)
                base[NameObject(field)] = image(30000, 30000)
                with self.assertRaisesRegex(RecruitError, "图片过大"):
                    guard_pdf_images(page_with(dictionary(XObject=dictionary(Photo=base))))

    def test_mask_and_image_pixels_are_added(self):
        base = image(4000, 3000)
        base[NameObject("/SMask")] = image(4000, 3000)
        with self.assertRaisesRegex(RecruitError, "总像素"):
            guard_pdf_images(page_with(dictionary(XObject=dictionary(Photo=base))))

    def test_form_and_tiling_pattern_resources_are_checked(self):
        for kind in ("form", "pattern"):
            with self.subTest(kind=kind):
                nested = drawing(dictionary(XObject=dictionary(Bomb=image(5000, 5000))))
                if kind == "pattern":
                    del nested[NameObject("/Subtype")]
                    nested[NameObject("/PatternType")] = NumberObject(1)
                    resources = dictionary(Pattern=dictionary(P0=nested))
                else:
                    resources = dictionary(XObject=dictionary(F0=nested))
                with self.assertRaisesRegex(RecruitError, "图片过大"):
                    guard_pdf_images(page_with(resources))

    def test_graphics_state_soft_mask_group_is_checked(self):
        group = drawing(dictionary(XObject=dictionary(Bomb=image(5000, 5000))))
        resources = dictionary(ExtGState=dictionary(GS=dictionary(SMask=dictionary(G=group))))
        with self.assertRaisesRegex(RecruitError, "图片过大"):
            guard_pdf_images(page_with(resources))

    def test_type_three_font_glyph_inline_images_are_checked(self):
        glyph = drawing(data=b"BI /W 5000 /H 5000 /BPC 8 /CS /G /F /AHx ID 00> EI")
        font = dictionary(Subtype=NameObject("/Type3"), CharProcs=dictionary(A=glyph))
        with self.assertRaisesRegex(RecruitError, "图片过大"):
            guard_pdf_images(page_with(dictionary(Font=dictionary(F0=font))))

    def test_inline_image_dimensions_are_checked(self):
        guard_pdf_images(page_with(data=b"BI /W 1 /H 1 /BPC 8 /CS /G ID \0 EI"))
        with self.assertRaisesRegex(RecruitError, "图片过大"):
            guard_pdf_images(page_with(data=b"BI /W 5000 /H 5000 /BPC 8 /CS /G /F /AHx ID 00> EI"))

    def test_cycles_and_repeated_image_resources_are_bounded(self):
        form = drawing()
        shared = image(4000, 3000)
        resources = dictionary(XObject=dictionary(F0=form, A=shared, B=shared))
        form[NameObject("/Resources")] = resources
        guard_pdf_images(page_with(resources))
        with patch("recruit_pdf_guard.MAX_RESOURCE_VISITS", 3):
            with self.assertRaisesRegex(RecruitError, "资源过多"):
                guard_pdf_images(page_with(resources))

    def test_parent_and_annotations_are_not_walked(self):
        page = page_with()
        page[NameObject("/Parent")] = dictionary(Resources=dictionary(XObject=dictionary(Bomb=image(30000, 30000))))
        page[NameObject("/Annots")] = ArrayObject([dictionary(AP=image(30000, 30000))])
        guard_pdf_images(page)

    def test_deep_resource_graph_is_rejected(self):
        nested = drawing(dictionary(XObject=dictionary(Photo=image(100, 100))))
        for _ in range(6):
            nested = drawing(dictionary(XObject=dictionary(F0=nested)))
        with self.assertRaisesRegex(RecruitError, "嵌套过深"):
            guard_pdf_images(page_with(dictionary(XObject=dictionary(F0=nested))))

    def test_real_reader_indirect_resources_and_existing_demos(self):
        writer = PdfWriter()
        base = image(1, 1)
        base[NameObject("/SMask")] = writer._add_object(image(30000, 30000))
        page = writer.add_blank_page(595, 842)
        page[NameObject("/Resources")] = dictionary(XObject=dictionary(Photo=writer._add_object(base)))
        raw = io.BytesIO()
        writer.write(raw)
        with self.assertRaisesRegex(RecruitError, "图片过大"):
            guard_pdf_images(PdfReader(io.BytesIO(raw.getvalue())).pages[0])
        for path in (SCRIPTS.parent / "recruit/demo-resumes").glob("*.pdf"):
            with self.subTest(demo=path.name):
                for page in PdfReader(path).pages:
                    guard_pdf_images(page)


if __name__ == "__main__":
    unittest.main()
