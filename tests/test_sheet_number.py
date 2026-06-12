import tempfile
import unittest
from pathlib import Path

import fitz

from app.sheet_number import parse_sheet_number_text, read_sheet_number_from_pdf_text


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


if __name__ == "__main__":
    unittest.main()
