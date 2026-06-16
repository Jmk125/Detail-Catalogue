import tempfile
import unittest
from pathlib import Path

try:
    import fitz
except Exception:  # pragma: no cover - exercised only when PyMuPDF is absent
    fitz = None

from app.detector import detect_candidate_detail_boxes
from app.vector_detect import detect_boxes_from_pdf

ZOOM = 2.0
PAGE_W = 1200
PAGE_H = 900
COLS = 4
ROWS = 3


def _build_boxed_detail_pdf(path: Path) -> tuple[int, int]:
    """A vector sheet: a grid of bordered details, each with interior lines and a
    title, plus a full-sheet border and a title block in the corner."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # Sheet border (should be ignored as too large).
    page.draw_rect(fitz.Rect(20, 20, PAGE_W - 20, PAGE_H - 20), color=(0, 0, 0), width=1)
    # Title block in the bottom-right corner (should be ignored).
    page.draw_rect(fitz.Rect(PAGE_W - 260, PAGE_H - 160, PAGE_W - 30, PAGE_H - 30), color=(0, 0, 0), width=1)

    gap_x, gap_y = 40, 50
    w = (PAGE_W - gap_x * (COLS + 1)) / COLS
    h = (PAGE_H - 200 - gap_y * (ROWS + 1)) / ROWS
    for r in range(ROWS):
        for c in range(COLS):
            x = gap_x + c * (w + gap_x)
            y = gap_y + r * (h + gap_y)
            page.draw_rect(fitz.Rect(x, y, x + w, y + h), color=(0, 0, 0), width=1)
            for off in range(20, int(h) - 20, 22):
                page.draw_line(fitz.Point(x + 10, y + off), fitz.Point(x + w - 10, y + off), color=(0, 0, 0), width=0.5)
            page.insert_text(fitz.Point(x + 8, y + h + 14), f"{r * COLS + c + 1}  DETAIL TITLE", fontsize=9)
            page.insert_text(fitz.Point(x + 8, y + h + 26), 'SCALE: 1-1/2" = 1\'-0"', fontsize=7)

    doc.save(str(path))
    doc.close()
    return int(PAGE_W * ZOOM), int(PAGE_H * ZOOM)


def _build_unboxed_detail_pdf(path: Path) -> tuple[int, int]:
    """A vector sheet whose details have NO borders -- only interior linework and
    a title/scale label per detail. Exercises the clustering path."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    gap_x, gap_y = 80, 90
    w = (PAGE_W - gap_x * 3) / 2
    h = (PAGE_H - gap_y * 3) / 2
    for r in range(2):
        for c in range(2):
            x = gap_x + c * (w + gap_x)
            y = gap_y + r * (h + gap_y)
            for off in range(15, int(h) - 30, 18):
                page.draw_line(fitz.Point(x, y + off), fitz.Point(x + w, y + off), color=(0, 0, 0), width=0.6)
            page.insert_text(fitz.Point(x, y + h - 6), f"{r * 2 + c + 1}  WALL SECTION", fontsize=10)
            page.insert_text(fitz.Point(x, y + h + 6), "N.T.S.", fontsize=8)
    doc.save(str(path))
    doc.close()
    return int(PAGE_W * ZOOM), int(PAGE_H * ZOOM)


def _build_scanned_pdf(path: Path) -> None:
    """A flattened/image-only page: one raster image, no vector paths or text."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 150))
    pix.clear_with(255)
    page.insert_image(fitz.Rect(0, 0, PAGE_W, PAGE_H), pixmap=pix)
    doc.save(str(path))
    doc.close()


@unittest.skipIf(fitz is None, "PyMuPDF is not installed")
class VectorDetectTests(unittest.TestCase):
    def test_reads_boxed_details_from_rectangles(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "boxed.pdf"
            px_w, px_h = _build_boxed_detail_pdf(pdf)
            boxes = detect_boxes_from_pdf(pdf, 0, zoom=ZOOM, page_pixel_size=(px_w, px_h))
        self.assertIsNotNone(boxes)
        # 12 details in the grid; allow a small tolerance either way.
        self.assertGreaterEqual(len(boxes), 10)
        self.assertLessEqual(len(boxes), 14)
        self.assertTrue(all(b["source"] == "vector" for b in boxes))

    def test_reads_unboxed_details_from_clusters(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "unboxed.pdf"
            px_w, px_h = _build_unboxed_detail_pdf(pdf)
            boxes = detect_boxes_from_pdf(pdf, 0, zoom=ZOOM, page_pixel_size=(px_w, px_h))
        self.assertIsNotNone(boxes)
        self.assertGreaterEqual(len(boxes), 4)

    def test_scanned_page_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "scanned.pdf"
            _build_scanned_pdf(pdf)
            boxes = detect_boxes_from_pdf(pdf, 0, zoom=ZOOM, page_pixel_size=(int(PAGE_W * ZOOM), int(PAGE_H * ZOOM)))
        self.assertIsNone(boxes)

    def test_detector_prefers_vector_when_pdf_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "boxed.pdf"
            px_w, px_h = _build_boxed_detail_pdf(pdf)
            doc = fitz.open(str(pdf))
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), alpha=False)
            png = Path(tmp) / "page.png"
            pix.save(str(png))
            doc.close()

            boxes = detect_candidate_detail_boxes(
                png,
                pdf_path=pdf,
                source_page_index=0,
                zoom=ZOOM,
                page_size=(px_w, px_h),
            )
        self.assertGreaterEqual(len(boxes), 10)
        self.assertTrue(all(b["source"] == "vector" for b in boxes))


if __name__ == "__main__":
    unittest.main()
