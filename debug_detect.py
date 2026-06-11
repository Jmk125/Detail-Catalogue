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
    _cv2_detection_profiles,
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


def _record_rejection(
    examples: dict[str, list[dict]],
    reason: str,
    box: list[int],
    area_ratio: float,
    aspect: float,
) -> None:
    examples.setdefault(reason, []).append(
        {
            "box": box,
            "area_ratio": area_ratio,
            "aspect": aspect,
        }
    )


def _print_candidate_diagnostics(
    label: str,
    stats: dict[str, int],
    rejected_examples: dict[str, list[dict]],
    scale: float,
) -> None:
    rejected_parts = [
        f"{key}={stats.get(key, 0)}"
        for key in ("too_small", "too_large", "bad_aspect", "title_block")
        if stats.get(key, 0)
    ]
    suffix = f"; rejected {'; '.join(rejected_parts)}" if rejected_parts else ""
    print(
        f"{label} raw contours: {stats.get('raw_contours', 0)}; "
        f"kept: {stats.get('kept', 0)}{suffix}"
    )

    for reason in ("too_small", "too_large", "bad_aspect", "title_block"):
        examples = sorted(
            rejected_examples.get(reason, []),
            key=lambda item: item["area_ratio"],
            reverse=True,
        )[:3]
        for item in examples:
            box = item["box"]
            original_b = _scale_box(box, scale)
            print(
                f"  rejected {reason:<11} box {box}  original={original_b}  "
                f"area%={item['area_ratio'] * 100:.3f}  aspect={item['aspect']:.2f}"
            )


def _cv2_candidate_diagnostics(
    cv2,
    thresh,
    width,
    height,
    *,
    line_kernel=45,
    text_kernel=(55, 12),
    dilate_kernel=24,
    iterations=2,
    min_area_ratio=0.0008,
    max_area_ratio=0.58,
    max_aspect=12,
    min_aspect=0.05,
):
    sheet_area = width * height
    kernel_x = cv2.getStructuringElement(cv2.MORPH_RECT, (line_kernel, 3))
    kernel_y = cv2.getStructuringElement(cv2.MORPH_RECT, (3, line_kernel))
    horizontal = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_x, iterations=1)
    vertical = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_y, iterations=1)
    combined = cv2.bitwise_or(horizontal, vertical)

    textish_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, text_kernel)
    textish = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, textish_kernel, iterations=1)
    combined = cv2.bitwise_or(combined, textish)

    dilater = cv2.getStructuringElement(cv2.MORPH_RECT, (dilate_kernel, dilate_kernel))
    combined = cv2.dilate(combined, dilater, iterations=iterations)
    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    stats = {
        "raw_contours": len(contours),
        "kept": 0,
        "too_small": 0,
        "too_large": 0,
        "bad_aspect": 0,
        "title_block": 0,
    }
    rejected_examples: dict[str, list[dict]] = {}
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        area_ratio = area / max(1, sheet_area)
        aspect = w / max(h, 1)
        box = [x, y, w, h]
        if area < sheet_area * min_area_ratio:
            stats["too_small"] += 1
            _record_rejection(rejected_examples, "too_small", box, area_ratio, aspect)
            continue
        if area > sheet_area * max_area_ratio:
            stats["too_large"] += 1
            _record_rejection(rejected_examples, "too_large", box, area_ratio, aspect)
            continue
        if aspect > max_aspect or aspect < min_aspect:
            stats["bad_aspect"] += 1
            _record_rejection(rejected_examples, "bad_aspect", box, area_ratio, aspect)
            continue
        if x > width * 0.55 and y > height * 0.72 and area > sheet_area * 0.025:
            stats["title_block"] += 1
            _record_rejection(rejected_examples, "title_block", box, area_ratio, aspect)
            continue
        stats["kept"] += 1
        boxes.append(box)
    return boxes, stats, rejected_examples


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

    preview_candidates, preview_stats, preview_rejected = _cv2_candidate_diagnostics(
        cv2,
        thresh,
        width,
        height,
        line_kernel=17,
        text_kernel=(24, 6),
        dilate_kernel=5,
        iterations=1,
        min_area_ratio=0.00012,
        max_area_ratio=0.14,
        max_aspect=18,
    )
    _print_candidate_diagnostics("Preview", preview_stats, preview_rejected, scale)
    preview_boxes = _merge_boxes(preview_candidates, dx=3, dy=3)
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
        candidates, stats, rejected = _cv2_candidate_diagnostics(
            cv2, thresh, width, height, **candidate_profile
        )
        merged = _merge_boxes(candidates, dx=merge_dx, dy=merge_dy)
        _print_candidate_diagnostics(name.title(), stats, rejected, scale)
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
