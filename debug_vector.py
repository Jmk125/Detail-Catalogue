"""Diagnostic: inspect what the vector/text layer of a PDF page contains and what
the vector detector extracts from it.

Usage:
    python debug_vector.py /path/to/drawing.pdf --page 1 --zoom 2.0 --overlay

This is the quickest way to see, on a real sheet, whether the page is a true
vector PDF (lots of drawings/words) or a flattened scan (almost none), how many
explicit detail rectangles exist, and how many candidate boxes the detector
produces. Use it to sanity-check before trusting the vector pass on a new design
team's drawings.
"""
import argparse
import json
from pathlib import Path

import fitz

from app import vector_detect as v


def main(pdf_path: str, *, page_number: int, zoom: float, overlay: bool) -> None:
    doc = fitz.open(pdf_path)
    try:
        index = page_number - 1
        if index < 0 or index >= len(doc):
            print(f"Page {page_number} out of range (1..{len(doc)})")
            return
        page = doc[index]
        pr = page.rect
        px_w, px_h = int(round(pr.width * zoom)), int(round(pr.height * zoom))
        scale_x, scale_y = px_w / pr.width, px_h / pr.height

        drawings = page.get_drawings()
        words = page.get_text("words")
        print(f"Page {page_number}: {pr.width:.0f} x {pr.height:.0f} pts -> {px_w} x {px_h} px @ zoom {zoom}")
        print(f"Vector drawings: {len(drawings)}   text words: {len(words)}")
        if len(drawings) < 4 and len(words) < 8:
            print("=> Looks like a scanned/flattened page (no usable vector layer); raster fallback would be used.")

        rects, ink, vwords = v._page_geometry(page, scale_x, scale_y, px_w, px_h)
        rect_candidates = v._candidates_from_rectangles(rects, px_w, px_h)
        print(f"Explicit rectangles: {len(rects)}  -> detail-sized rectangle candidates: {len(rect_candidates)}")
        print(f"Ink boxes: {len(ink)}   word boxes: {len(vwords)}")

        sample_words = [page.get_text("words")[i][4] for i in range(min(12, len(words)))]
        if sample_words:
            print("Sample text:", ", ".join(sample_words))

        results = v.detect_boxes_from_pdf(pdf_path, index, zoom=zoom, page_pixel_size=(px_w, px_h))
        if results is None:
            print("Vector detector returned None (no usable vector content).")
            return
        print(f"\nVector candidate boxes: {len(results)}")
        print(json.dumps(results, indent=2))

        if overlay:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            import cv2
            import numpy as np

            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n >= 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            for r in results:
                x, y, w, h = int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
                cv2.rectangle(img, (x, y), (x + w, y + h), (255, 0, 0), 6)
                cv2.putText(img, r["id"], (x, max(30, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 3, cv2.LINE_AA)
            out = Path(pdf_path).with_name(f"{Path(pdf_path).stem}.p{page_number}.vector_boxes.png")
            cv2.imwrite(str(out), img)
            print(f"\nOverlay written: {out}")
    finally:
        doc.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect a PDF page's vector layer and vector detection output.")
    parser.add_argument("pdf_path", help="PDF file to inspect")
    parser.add_argument("--page", type=int, default=1, help="1-based page number (default: 1)")
    parser.add_argument("--zoom", type=float, default=2.0, help="Render zoom used by the app (default: 2.0)")
    parser.add_argument("--overlay", action="store_true", help="Write a PNG overlay of the detected boxes")
    args = parser.parse_args()
    main(args.pdf_path, page_number=args.page, zoom=args.zoom, overlay=args.overlay)
