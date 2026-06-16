import tempfile
import unittest
from pathlib import Path

try:
    import fitz
except Exception:  # pragma: no cover - exercised only when PyMuPDF is absent
    fitz = None

from app import vector_detect as v
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


def _build_unnumbered_titled_pdf(path: Path) -> tuple[int, int]:
    """Separated, unboxed details with a title but NO detail number (a notes/detail
    sheet like C100). The grid strategy cannot fire; clustering or text anchors
    must still recover boxes."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    cols, rows = 3, 2
    gx, gy = 120, 130
    w = (PAGE_W - gx * (cols + 1)) / cols
    h = (PAGE_H - gy * (rows + 1)) / rows
    for r in range(rows):
        for c in range(cols):
            x = gx + c * (w + gx)
            y = gy + r * (h + gy)
            for off in range(15, int(h) - 40, 17):
                page.draw_line(fitz.Point(x, y + off), fitz.Point(x + w, y + off), width=0.6)
            page.insert_text(fitz.Point(x, y + h - 6), "TYPICAL WALL SECTION", fontsize=15)
            page.insert_text(fitz.Point(x, y + h + 6), "N.T.S.", fontsize=8)
    doc.save(str(path))
    doc.close()
    return int(PAGE_W * ZOOM), int(PAGE_H * ZOOM)


def _build_numbered_grid_pdf(path: Path) -> tuple[int, int]:
    """A grid of unboxed details, each with a graphic and a bottom-left circled
    number + title (the common real-sheet layout, e.g. A530). Exercises the
    detail-number grid strategy."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    cols, rows = 4, 3  # 12 numbered details
    gx, gy = 40, 70
    w = (PAGE_W - gx * (cols + 1)) / cols
    h = (PAGE_H - 160 - gy * (rows + 1)) / rows
    # Linework spanning the whole sheet (dimension/grid lines) connects the
    # details so proximity clustering collapses -- the grid strategy must win.
    for yy in range(80, PAGE_H - 80, 60):
        page.draw_line(fitz.Point(40, yy), fitz.Point(PAGE_W - 40, yy), width=0.4)
    for xx in range(80, PAGE_W - 80, 70):
        page.draw_line(fitz.Point(xx, 40), fitz.Point(xx, PAGE_H - 40), width=0.4)
    n = 0
    for r in range(rows):
        for c in range(cols):
            n += 1
            x = gx + c * (w + gx)
            y = gy + r * (h + gy)
            # detail graphic
            for off in range(15, int(h) - 60, 16):
                page.draw_line(fitz.Point(x + 12, y + off), fitz.Point(x + w - 12, y + off), width=0.5)
            # bottom-left number (heading font) + title (heading) + small scale
            page.insert_text(fitz.Point(x + 6, y + h - 14), f"{n}", fontsize=16)
            page.insert_text(fitz.Point(x + 34, y + h - 14), "WINDOW HEAD AT MASONRY", fontsize=16)
            page.insert_text(fitz.Point(x + 34, y + h - 2), 'SCALE: 1-1/2"=1\'-0"', fontsize=7)
            for k in range(5):  # body/dimension text dominates the size histogram
                page.insert_text(fitz.Point(x + 20 + (k % 3) * 55, y + 18 + (k // 3) * 18), '3"', fontsize=7)
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

    def test_unnumbered_titled_sheet_still_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "notes.pdf"
            px_w, px_h = _build_unnumbered_titled_pdf(pdf)
            boxes = detect_boxes_from_pdf(pdf, 0, zoom=ZOOM, page_pixel_size=(px_w, px_h))
        # No detail numbers, so the grid strategy cannot fire; another strategy
        # must still recover the 6 separated details.
        self.assertIsNotNone(boxes)
        self.assertGreaterEqual(len(boxes), 4)

    def test_numbered_grid_uses_grid_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "grid.pdf"
            px_w, px_h = _build_numbered_grid_pdf(pdf)
            doc = fitz.open(str(pdf))
            report = v.page_detection_report(doc[0], zoom=ZOOM, page_pixel_size=(px_w, px_h))
            doc.close()
        self.assertEqual(report["selected"], "grid_anchors")
        # 12 numbered details; the grid should recover most of them.
        self.assertGreaterEqual(len(report["results"]), 10)
        self.assertLessEqual(len(report["results"]), 12)

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
