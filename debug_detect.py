"""Diagnostic: show how many candidate boxes survive each detection stage for a page image.

Usage (on the Pi, inside the venv):
    python debug_detect.py /home/pi/detail-catalogue/data/projects/<project_id>/pages/page_0001.webp
"""
import sys
from pathlib import Path

import cv2

from app.detector import (
    _cv2_candidates,
    _cv2_detection_profiles,
    _cv2_preview_candidates,
    _packed_layout_metrics,
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

    preview_candidates = _cv2_preview_candidates(cv2, thresh, width, height)
    preview_boxes = _merge_boxes(preview_candidates, dx=3, dy=3)
    layout_metrics = _packed_layout_metrics(preview_boxes, width, height, raw_count=len(preview_candidates))
    packed_layout = layout_metrics["packed_layout"]
    mode = "packed" if packed_layout else "loose"
    profiles = tuple(
        (f"{mode}_{idx}", profile)
        for idx, profile in enumerate(_cv2_detection_profiles(width, height, packed_layout), start=1)
    )
    print(f"Preview raw kept: {len(preview_candidates)}")
    print(f"Preview candidates after merge: {len(preview_boxes)}")
    print(
        "Packed metrics: "
        f"occupied_cells={layout_metrics['occupied_cells']}, "
        f"median_nearest={layout_metrics['median_nearest']:.3f}, "
        f"median_area={layout_metrics['median_area'] * 100:.2f}%, "
        f"broad_grid={layout_metrics['broad_grid']}, "
        f"very_many_close={layout_metrics['very_many_close_clusters']}, "
        f"many_raw={layout_metrics['many_raw_clusters']}"
    )
    print(f"Packed layout: {packed_layout}")

    boxes = []
    for name, profile in profiles:
        candidate_profile = dict(profile)
        merge_dx = candidate_profile.pop("merge_dx")
        merge_dy = candidate_profile.pop("merge_dy")
        candidates = _cv2_candidates(cv2, thresh, width, height, **candidate_profile)
        merged = _merge_boxes(candidates, dx=merge_dx, dy=merge_dy)
        print(f"{name.title()} profile candidates: {len(candidates)}")
        print(f"After merge - {name}: {len(merged)}")
        for b in merged:
            area_pct = (b[2] * b[3]) / sheet_area * 100
            print(f"  {name:<6} box {b}  area%={area_pct:.1f}")
        boxes.extend(merged)
    boxes = _merge_labels_under_details(boxes, width, height, packed_layout=packed_layout)
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
