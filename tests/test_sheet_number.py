import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

from app.sheet_number import debug_sheet_number_read, parse_sheet_number_text, read_sheet_number_from_pdf_text, read_sheet_number_with_template_ocr


class SheetNumberReaderTests(unittest.TestCase):
    def test_parse_prefers_labeled_sheet_number(self):
        self.assertEqual(parse_sheet_number_text("PROJECT NO 24017   SHEET NUMBER A501"), "A-501")

    def test_parse_keeps_dotted_structural_number(self):
        self.assertEqual(parse_sheet_number_text("SHEET NO S2.01"), "S2.01")

    def test_parse_keeps_full_numeric_hyphenated_sheet_number(self):
        self.assertEqual(parse_sheet_number_text("07-005"), "07-005")
        self.assertEqual(parse_sheet_number_text("SHEET NUMBER 07 - 005"), "07-005")

    def test_parse_reassembles_fragmented_structural_sheet_number(self):
        self.assertEqual(parse_sheet_number_text("0 3 - 1 1 0"), "03-110")
        self.assertEqual(parse_sheet_number_text("03110"), "03-110")

    def test_read_sheet_number_from_pdf_text_uses_red_box_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sheet.pdf"
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            page.insert_text((470, 700), "SHEET NUMBER", fontsize=8)
            page.insert_text((500, 724), "07-005", fontsize=20)
            doc.save(pdf_path)
            doc.close()

            # The rendered page is 2x the PDF dimensions. This box mirrors the
            # draggable red sheet-number box sent by the browser in image pixels.
            result = read_sheet_number_from_pdf_text(
                pdf_path,
                0,
                {"x": 920, "y": 1360, "w": 280, "h": 120},
                image_width=1224,
                image_height=1584,
                pdf_width=612,
                pdf_height=792,
            )

        self.assertEqual(result, "07-005")

    def test_read_sheet_number_from_pdf_text_reassembles_separate_glyphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "structural_sheet.pdf"
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            x = 500
            for char in "03-110":
                page.insert_text((x, 724), char, fontsize=20)
                x += 16
            doc.save(pdf_path)
            doc.close()

            result = read_sheet_number_from_pdf_text(
                pdf_path,
                0,
                {"x": 920, "y": 1360, "w": 280, "h": 120},
                image_width=1224,
                image_height=1584,
                pdf_width=612,
                pdf_height=792,
            )

        self.assertEqual(result, "03-110")

    def test_template_ocr_reads_high_contrast_raster_sheet_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            crop_path = Path(tmp) / "raster_sheet_number.png"
            image = Image.new("RGB", (500, 160), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 100)
            draw.text((20, 25), "03-110", font=font, fill="black")
            image.save(crop_path)

            self.assertEqual(read_sheet_number_with_template_ocr(crop_path), "03-110")

    def test_debug_sheet_number_read_reports_pdf_text_and_final_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "debug_sheet.pdf"
            crop_path = tmp_path / "crop.png"
            Image.new("RGB", (280, 120), "white").save(crop_path)
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            page.insert_text((500, 724), "03-110", fontsize=20)
            doc.save(pdf_path)
            doc.close()

            debug = debug_sheet_number_read(
                pdf_path,
                0,
                {"x": 920, "y": 1360, "w": 280, "h": 120},
                crop_path,
                image_width=1224,
                image_height=1584,
                pdf_width=612,
                pdf_height=792,
            )

        self.assertEqual(debug["final_sheet_number"], "03-110")
        self.assertEqual(debug["pdf_text"]["parsed"], "03-110")
        self.assertIn("clip_pdf_points", debug)


if __name__ == "__main__":
    unittest.main()
