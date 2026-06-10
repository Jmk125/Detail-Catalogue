"""Diagnostic: show how many candidate boxes survive each detection stage for a page image.

Usage (on the Pi, inside the venv):
    python debug_detect.py /home/pi/detail-catalogue/data/projects/<project_id>/pages/page_0001.webp
"""
import sys
from pathlib import Path

import cv2

from app.detector import (
    _cv2_candidates,
    _merge_boxes,
    _merge_labels_under_details,
    _dedupe_boxes,
    _format_results,
)


def main(image_path: str) -> None:
    img = cv2.imread(image_path)
    if img is None:
        print(f"Could not read image: {image_path}")
        return

    height, width = img.shape[:2]
    sheet_area = width * height
    print(f"Image size: {width} x {height} (area={sheet_area})")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 51, 15
    )

    coarse = _cv2_candidates(cv2, thresh, width, height, line_kernel=45, text_kernel=(55, 12), dilate_kernel=24, iterations=2)
    fine = _cv2_candidates(cv2, thresh, width, height, line_kernel=25, text_kernel=(35, 8), dilate_kernel=10, iterations=1)
    print(f"Coarse candidates: {len(coarse)}")
    print(f"Fine candidates:   {len(fine)}")

    merged_coarse = _merge_boxes(coarse, dx=14, dy=14)
    merged_fine = _merge_boxes(fine, dx=5, dy=5)
    print(f"After merge - coarse: {len(merged_coarse)}, fine: {len(merged_fine)}")
    for b in merged_coarse:
        area_pct = (b[2] * b[3]) / sheet_area * 100
        print(f"  coarse box {b}  area%={area_pct:.1f}")
    for b in merged_fine:
        area_pct = (b[2] * b[3]) / sheet_area * 100
        print(f"  fine box   {b}  area%={area_pct:.1f}")

    boxes = merged_coarse + merged_fine
    boxes = _merge_labels_under_details(boxes, width, height)
    print(f"After label-merge: {len(boxes)}")

    boxes = _dedupe_boxes(boxes)
    print(f"After dedupe: {len(boxes)}")
    for b in boxes:
        area_pct = (b[2] * b[3]) / sheet_area * 100
        print(f"  box {b}  area%={area_pct:.2f}")

    results = _format_results(boxes, width, height, 80)
    print(f"Final results after area filter (0.15%-62%): {len(results)}")


if __name__ == "__main__":
    main(sys.argv[1])
