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
    _remove_composite_boxes,
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

    profiles = (
        ("coarse", {
            "line_kernel": 45,
            "text_kernel": (55, 12),
            "dilate_kernel": 24,
            "iterations": 2,
            "min_area_ratio": 0.0008,
            "max_area_ratio": 0.58,
            "merge_dx": 14,
            "merge_dy": 14,
        }),
        ("fine", {
            "line_kernel": 25,
            "text_kernel": (35, 8),
            "dilate_kernel": 10,
            "iterations": 1,
            "min_area_ratio": 0.00055,
            "max_area_ratio": 0.40,
            "merge_dx": 5,
            "merge_dy": 5,
        }),
        ("loose", {
            "line_kernel": 17,
            "text_kernel": (24, 6),
            "dilate_kernel": 7,
            "iterations": 1,
            "min_area_ratio": 0.00025,
            "max_area_ratio": 0.22,
            "max_aspect": 16,
            "merge_dx": max(4, int(width * 0.010)),
            "merge_dy": max(4, int(height * 0.014)),
        }),
    )

    boxes = []
    for name, profile in profiles:
        candidate_profile = dict(profile)
        merge_dx = candidate_profile.pop("merge_dx")
        merge_dy = candidate_profile.pop("merge_dy")
        candidates = _cv2_candidates(cv2, thresh, width, height, **candidate_profile)
        merged = _merge_boxes(candidates, dx=merge_dx, dy=merge_dy)
        print(f"{name.title()} candidates: {len(candidates)}")
        print(f"After merge - {name}: {len(merged)}")
        for b in merged:
            area_pct = (b[2] * b[3]) / sheet_area * 100
            print(f"  {name:<6} box {b}  area%={area_pct:.1f}")
        boxes.extend(merged)
    boxes = _merge_labels_under_details(boxes, width, height)
    print(f"After label-merge: {len(boxes)}")

    boxes = _dedupe_boxes(boxes)
    print(f"After dedupe: {len(boxes)}")

    boxes = _remove_composite_boxes(boxes)
    print(f"After composite suppression: {len(boxes)}")
    for b in boxes:
        area_pct = (b[2] * b[3]) / sheet_area * 100
        print(f"  box {b}  area%={area_pct:.2f}")

    results = _format_results(boxes, width, height, 80)
    print(f"Final results after area filter (0.15%-62%): {len(results)}")


if __name__ == "__main__":
    main(sys.argv[1])
