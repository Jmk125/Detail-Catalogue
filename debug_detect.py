"""Diagnostic: show how many candidate boxes survive each detection stage for a page image.

Usage (on the Pi, inside the venv):
    python debug_detect.py /home/pi/detail-catalogue/data/projects/<project_id>/pages/page_0001.webp

By default this mirrors production detection by resizing large sheets down to a
2200px max dimension before detecting, then scaling boxes back to the original
image size. Use --no-scale to inspect the original full-resolution behavior.
"""
import argparse
import json
from pathlib import Path

import cv2

from app.detector import (
    _cv2_candidates,
    _cv2_detection_profiles,
    _cv2_preview_boxes,
    _estimate_packed_layout,
    _merge_boxes,
    _merge_labels_under_details,
    _dedupe_boxes,
    _format_results,
    _remove_composite_boxes,
)


def _scale_box(box: list[int], scale: float) -> list[int]:
    if scale == 1.0:
        return list(map(int, box))
    inv = 1.0 / scale
    return [int(round(v * inv)) for v in box]


def _draw_overlay(image_path: str, results: list[dict], output_path: Path) -> None:
    img = cv2.imread(image_path)
    if img is None:
        print(f"Could not write overlay because image could not be read: {image_path}")
        return

    for result in results:
        x = int(round(result["x"]))
        y = int(round(result["y"]))
        w = int(round(result["w"]))
        h = int(round(result["h"]))
        cv2.rectangle(img, (x, y), (x + w, y + h), (255, 0, 0), 8)
        cv2.putText(
            img,
            result.get("id", "box"),
            (x, max(35, y - 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (255, 0, 0),
            3,
            cv2.LINE_AA,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), img)
    print(f"Overlay written: {output_path}")


def main(image_path: str, *, target: int = 2200, overlay_path: str | None | bool = False) -> None:
    img = cv2.imread(image_path)
    if img is None:
        print(f"Could not read image: {image_path}")
        return

    orig_height, orig_width = img.shape[:2]
    scale = min(1.0, target / max(orig_width, orig_height)) if target else 1.0
    if scale < 1.0:
        img = cv2.resize(
            img,
            (int(round(orig_width * scale)), int(round(orig_height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    height, width = img.shape[:2]
    sheet_area = width * height
    print(f"Original image size: {orig_width} x {orig_height} (area={orig_width * orig_height})")
    print(f"Detection image size: {width} x {height} (area={sheet_area})")
    print(f"Production scale: {scale:.6f}" if scale < 1.0 else "Production scale: none")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 51, 15
    )

    preview_boxes = _cv2_preview_boxes(cv2, thresh, width, height)
    packed_layout = _estimate_packed_layout(preview_boxes, width, height)
    mode = "packed" if packed_layout else "loose"
    profiles = tuple(
        (f"{mode}_{idx}", profile)
        for idx, profile in enumerate(_cv2_detection_profiles(width, height, packed_layout), start=1)
    )
    print(f"Preview candidates: {len(preview_boxes)}")
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
            original_b = _scale_box(b, scale)
            print(f"  {name:<6} box {b}  original={original_b}  area%={area_pct:.1f}")
        boxes.extend(merged)
    boxes = _merge_labels_under_details(boxes, width, height, packed_layout=packed_layout)
    print(f"After label-merge: {len(boxes)}")

    boxes = _dedupe_boxes(boxes)
    print(f"After dedupe: {len(boxes)}")

    boxes = _remove_composite_boxes(boxes)
    print(f"After composite suppression: {len(boxes)}")
    for b in boxes:
        area_pct = (b[2] * b[3]) / sheet_area * 100
        original_b = _scale_box(b, scale)
        print(f"  box {b}  original={original_b}  area%={area_pct:.2f}")

    original_boxes = [_scale_box(b, scale) for b in boxes]
    results = _format_results(original_boxes, orig_width, orig_height, 80)
    print(f"Final app-style results after area filter (0.15%-62%): {len(results)}")
    print(json.dumps(results, indent=2))

    if overlay_path is not False:
        if overlay_path is None:
            source = Path(image_path)
            overlay_path = str(source.with_name(f"{source.stem}.debug_boxes{source.suffix}"))
        _draw_overlay(image_path, results, Path(str(overlay_path)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Debug detail box detection stages for a rendered page image.")
    parser.add_argument("image_path", help="Rendered page image to inspect")
    parser.add_argument(
        "--no-scale",
        action="store_true",
        help="Run detection at the image's original size instead of production's 2200px max dimension.",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=2200,
        help="Production detection max dimension to emulate. Default: 2200.",
    )
    parser.add_argument(
        "--overlay",
        nargs="?",
        const=None,
        default=False,
        help="Write a debug overlay image. Optionally pass an output path; default is <image>.debug_boxes.<ext>.",
    )
    args = parser.parse_args()
    main(args.image_path, target=0 if args.no_scale else args.target, overlay_path=args.overlay)
