"""Diagnostic: inspect what the vector/text layer of a PDF page contains and what
each vector detection strategy extracts from it.

Usage:
    python debug_vector.py /path/to/drawing.pdf --page 1 --zoom 2.0 --overlay

This is the quickest way to see, on a real sheet, whether the page is a true
vector PDF, how many explicit detail rectangles exist, the font-size profile of
its text (which drives title/number anchoring), and the box count + quality score
of every strategy so you can tell which one is carrying the sheet.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

import fitz

from app import vector_detect as v


def _font_histogram(page, scale_x, scale_y, width, height):
    spans = v._text_spans(page, scale_x, scale_y, width, height)
    hist = Counter(round(s, 1) for _t, _b, s in spans if s > 0)
    return spans, hist


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

        report = v.page_detection_report(page, zoom=zoom, page_pixel_size=(px_w, px_h))
        diag = report["diagnostics"]

        print(f"Page {page_number}: {pr.width:.0f} x {pr.height:.0f} pts -> {px_w} x {px_h} px @ zoom {zoom}")
        print(f"Usable vector layer: {report['usable']}")
        print(
            f"Ink boxes: {diag['drawings']}   words: {diag['words']}   "
            f"rectangles: {diag['rects_total']} (detail-sized: {diag['rect_candidates']})   "
            f"text spans: {diag['spans']}"
        )
        print(
            f"Heading anchors: body_size={diag.get('body_size', 0):.1f}px "
            f"threshold={diag.get('threshold', 0):.1f}px "
            f"heading_spans={diag.get('heading_spans', 0)} "
            f"anchor_groups={diag.get('anchor_groups', 0)}"
        )

        spans, hist = _font_histogram(page, scale_x, scale_y, px_w, px_h)
        print("Font-size histogram (px -> count):")
        for size, count in sorted(hist.items()):
            print(f"  {size:6.1f}  x{count}")
        big = sorted([(s, t) for t, _b, s in spans], reverse=True)[:12]
        if big:
            print("Largest text:", ", ".join(f"{t!r}@{s:.0f}px" for s, t in big))

        print("\nStrategies (box count / quality score):")
        for s in report["strategies"]:
            mark = "  <= selected" if s["name"] == report["selected"] else ""
            print(f"  {s['name']:<26} raw={s['raw']:<4} final={s['final']:<4} score={s['score']:<6}{mark}")

        results = report["results"]
        print(f"\nSelected strategy: {report['selected']}  -> {len(results)} boxes")
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
                cv2.putText(img, r["id"], (x, max(30, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 0, 0), 4, cv2.LINE_AA)
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
