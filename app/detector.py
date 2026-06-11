from pathlib import Path
import importlib

import numpy as np
from PIL import Image, ImageFilter


def _cv2():
    try:
        return importlib.import_module("cv2")
    except Exception:
        return None


def _union(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0 = min(ax, bx)
    y0 = min(ay, by)
    x1 = max(ax + aw, bx + bw)
    y1 = max(ay + ah, by + bh)
    return [x0, y0, x1 - x0, y1 - y0]


def _close_or_overlap(a, b, dx=18, dy=18):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + dx < bx or bx + bw + dx < ax or
        ay + ah + dy < by or by + bh + dy < ay
    )


def _merge_boxes(boxes, dx=18, dy=18):
    boxes = [list(map(int, b)) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        used = [False] * len(boxes)
        for i, b in enumerate(boxes):
            if used[i]:
                continue
            cur = b
            used[i] = True
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                if _close_or_overlap(cur, boxes[j], dx, dy):
                    cur = _union(cur, boxes[j])
                    used[j] = True
                    changed = True
            out.append(cur)
        boxes = out
    return boxes


def _intersection_area(a, b):
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    x0 = max(ax, bx)
    y0 = max(ay, by)
    x1 = min(ax + aw, bx + bw)
    y1 = min(ay + ah, by + bh)
    return max(0, x1 - x0) * max(0, y1 - y0)


def _dedupe_boxes(boxes, overlap_threshold=0.86):
    """Remove near-identical multi-scale boxes without dropping useful larger crops.

    Earlier versions treated any box fully contained by a smaller one as a
    duplicate. That is fine when two passes found the same detail with slightly
    different padding, but it is harmful for sparse/unboxed sheets where a fine
    pass may find a small callout inside a larger valid detail crop. Require the
    boxes to be similar enough in size (or to have strong IoU) before considering
    them duplicates.
    """
    kept = []
    for box in sorted([list(map(int, b[:4])) for b in boxes], key=lambda b: (b[2] * b[3], b[1], b[0])):
        area = max(1, box[2] * box[3])
        duplicate = False
        for existing in kept:
            existing_area = max(1, existing[2] * existing[3])
            intersection = _intersection_area(box, existing)
            overlap = intersection / min(area, existing_area)
            iou = intersection / max(1, area + existing_area - intersection)
            size_similarity = min(area, existing_area) / max(area, existing_area)
            if overlap >= overlap_threshold and (size_similarity >= 0.45 or iou >= 0.55):
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return sorted(kept, key=lambda b: (b[1], b[0]))


def _remove_composite_boxes(boxes):
    """Drop large boxes that merely wrap several smaller detected details.

    Sparse passes intentionally add small candidates for loose details and labels.
    Occasionally a coarser pass also returns a column/row-sized box around several
    of those candidates. Keep the smaller reviewable detail boxes instead of the
    composite wrapper.
    """
    normalized = [list(map(int, b[:4])) for b in boxes]
    keep = []
    for box in normalized:
        area = max(1, box[2] * box[3])
        child_centers_x = []
        child_centers_y = []
        for other in normalized:
            if other is box:
                continue
            other_area = max(1, other[2] * other[3])
            if other_area >= area * 0.60:
                continue
            if _intersection_area(box, other) / other_area < 0.88:
                continue
            child_centers_x.append(other[0] + other[2] / 2)
            child_centers_y.append(other[1] + other[3] / 2)

        wraps_multiple_rows = (
            len(child_centers_y) >= 3
            and max(child_centers_y) - min(child_centers_y) > box[3] * 0.35
        )
        wraps_multiple_columns = (
            len(child_centers_x) >= 3
            and max(child_centers_x) - min(child_centers_x) > box[2] * 0.35
        )
        if wraps_multiple_rows or wraps_multiple_columns:
            continue
        keep.append(box)
    return keep


def _median(values):
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _estimate_packed_layout(boxes, width, height):
    """Return True when a sheet has many closely-spaced detail clusters.

    The detector has two competing goals: loose sheets need generous grouping and
    label reach, while packed sheets need small kernels/gaps so adjacent details do
    not collapse into large groups. This lightweight pre-pass looks only at small
    preview clusters and chooses the safer packed settings when the page has many
    candidates spread across a grid with short nearest-neighbor distances.
    """
    sheet_area = max(1, width * height)
    preview = []
    for x, y, w, h in [list(map(int, b[:4])) for b in boxes]:
        area = w * h
        if area < sheet_area * 0.00012 or area > sheet_area * 0.14:
            continue
        aspect = w / max(1, h)
        if aspect > 18 or aspect < 0.04:
            continue
        # Do not let seals/title blocks dominate the layout choice.
        if x > width * 0.78 and area > sheet_area * 0.012:
            continue
        preview.append([x, y, w, h])

    if len(preview) < 10:
        return False

    centers = [(x + w / 2, y + h / 2) for x, y, w, h in preview]
    nearest = []
    for i, (cx, cy) in enumerate(centers):
        best = None
        for j, (ox, oy) in enumerate(centers):
            if i == j:
                continue
            dist = ((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5
            best = dist if best is None else min(best, dist)
        if best is not None:
            nearest.append(best / max(width, height))

    occupied = set()
    for cx, cy in centers:
        occupied.add((min(5, int(cx / max(1, width) * 6)), min(3, int(cy / max(1, height) * 4))))

    median_nearest = _median(nearest)
    area_ratios = [(w * h) / sheet_area for _, _, w, h in preview]
    median_area = _median(area_ratios)
    broad_grid = len(occupied) >= 14
    very_many_close_clusters = len(preview) >= 24 and len(occupied) >= 10 and median_nearest <= 0.12
    small_enough_to_split = median_area <= 0.04
    return small_enough_to_split and (broad_grid or very_many_close_clusters)


def _cv2_preview_boxes(cv2, thresh, width, height):
    candidates = _cv2_candidates(
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
    return _merge_boxes(candidates, dx=3, dy=3)


def _cv2_detection_profiles(width, height, packed_layout):
    if packed_layout:
        return (
            {
                "line_kernel": 25,
                "text_kernel": (32, 8),
                "dilate_kernel": 8,
                "iterations": 1,
                "min_area_ratio": 0.00045,
                "max_area_ratio": 0.20,
                "merge_dx": 4,
                "merge_dy": 4,
            },
            {
                "line_kernel": 17,
                "text_kernel": (24, 6),
                "dilate_kernel": 5,
                "iterations": 1,
                "min_area_ratio": 0.00025,
                "max_area_ratio": 0.16,
                "max_aspect": 16,
                "merge_dx": 3,
                "merge_dy": 3,
            },
            {
                "line_kernel": 11,
                "text_kernel": (18, 5),
                "dilate_kernel": 3,
                "iterations": 1,
                "min_area_ratio": 0.00018,
                "max_area_ratio": 0.10,
                "max_aspect": 18,
                "merge_dx": 2,
                "merge_dy": 2,
            },
        )

    return (
        {
            "line_kernel": 45,
            "text_kernel": (55, 12),
            "dilate_kernel": 24,
            "iterations": 2,
            "min_area_ratio": 0.0008,
            "max_area_ratio": 0.58,
            "merge_dx": 14,
            "merge_dy": 14,
        },
        {
            "line_kernel": 25,
            "text_kernel": (35, 8),
            "dilate_kernel": 10,
            "iterations": 1,
            "min_area_ratio": 0.00055,
            "max_area_ratio": 0.40,
            "merge_dx": 5,
            "merge_dy": 5,
        },
        {
            "line_kernel": 17,
            "text_kernel": (24, 6),
            "dilate_kernel": 7,
            "iterations": 1,
            "min_area_ratio": 0.00025,
            "max_area_ratio": 0.22,
            "max_aspect": 16,
            "merge_dx": max(4, int(width * 0.010)),
            "merge_dy": max(4, int(height * 0.014)),
        },
    )


def _pillow_preview_boxes(mask_img, width, height, sheet_area):
    preview_img = mask_img.filter(ImageFilter.MaxFilter(3))
    mask = np.asarray(preview_img) > 0
    preview = []
    for x, y, w, h, pixels in _connected_components(mask):
        area = w * h
        if area < sheet_area * 0.00012 or area > sheet_area * 0.14:
            continue
        aspect = w / max(h, 1)
        if aspect > 18 or aspect < 0.04:
            continue
        if pixels < sheet_area * 0.00010:
            continue
        preview.append([x, y, w, h])
    return _merge_boxes(preview, dx=3, dy=3)


def _pillow_detection_profiles(width, height, packed_layout):
    if packed_layout:
        return (
            {"factor": 0.45, "merge_gap": 4, "min_area_ratio": 0.00045, "max_area_ratio": 0.20, "max_aspect": 12},
            {"factor": 0.32, "merge_gap": 3, "min_area_ratio": 0.00025, "max_area_ratio": 0.16, "max_aspect": 16},
            {"factor": 0.24, "merge_gap": 2, "min_area_ratio": 0.00018, "max_area_ratio": 0.10, "max_aspect": 18},
        )
    return (
        {"factor": 1.0, "merge_gap": 14, "min_area_ratio": 0.0008, "max_area_ratio": 0.58, "max_aspect": 12},
        {"factor": 0.45, "merge_gap": 5, "min_area_ratio": 0.00055, "max_area_ratio": 0.40, "max_aspect": 12},
        {"factor": 0.32, "merge_gap": max(4, int(min(width, height) * 0.010)), "min_area_ratio": 0.00025, "max_area_ratio": 0.22, "max_aspect": 16},
    )

def _horizontal_overlap_ratio(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    return overlap / max(1, min(aw, bw))


def _center_distance_x(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return abs((ax + aw / 2) - (bx + bw / 2))


def _merge_labels_under_details(boxes, sheet_w, sheet_h, *, packed_layout=False):
    """
    Merge likely detail title/detail-number labels below main detail boxes.

    This is intentionally generous because the review UI can delete/resize later.
    """
    boxes = [list(map(int, b)) for b in boxes]
    boxes.sort(key=lambda b: (b[1], b[0]))

    changed = True
    while changed:
        changed = False
        used = [False] * len(boxes)
        new_boxes = []

        for i, main in enumerate(boxes):
            if used[i]:
                continue

            cur = main
            used[i] = True

            for j, cand in enumerate(boxes):
                if used[j] or i == j:
                    continue

                cx, cy, cw, ch = cur
                bx, by, bw, bh = cand

                gap = by - (cy + ch)
                if gap < -sheet_h * 0.01:
                    continue

                overlap = _horizontal_overlap_ratio(cur, cand)
                center_close = _center_distance_x(cur, cand) < max(cw, bw) * 0.40
                # Some design teams place detail tags/titles noticeably below the
                # graphic. Allow a longer reach only when the lower candidate is
                # horizontally centered like a label, which avoids broadly merging
                # stacked details.
                label_reach = 0.055 if packed_layout else (0.10 if center_close else 0.065)
                near_below = gap <= sheet_h * label_reach
                label_like_height = bh <= min(max(ch * 0.55, sheet_h * 0.075), sheet_h * 0.10)
                not_huge = (bw * bh) < (cw * ch) * 0.45
                reasonable_width = bw <= cw * 1.65 or center_close

                if near_below and label_like_height and not_huge and reasonable_width and (overlap >= 0.18 or center_close):
                    cur = _union(cur, cand)
                    used[j] = True
                    changed = True

            new_boxes.append(cur)

        boxes = new_boxes

    return boxes


def _format_results(boxes: list[list[int]], width: int, height: int, max_boxes: int) -> list[dict]:
    sheet_area = width * height
    padded = []
    for x, y, w, h in boxes:
        pad = 18
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(width, x + w + pad)
        y1 = min(height, y + h + pad)
        pw = x1 - x0
        ph = y1 - y0
        if pw * ph < sheet_area * 0.0015 or pw * ph > sheet_area * 0.62:
            continue
        padded.append([x0, y0, pw, ph])

    padded.sort(key=lambda b: (b[1], b[0]))
    return [
        {
            "id": f"det_{idx:03d}",
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "confidence": 0.50,
            "source": "detector",
        }
        for idx, (x, y, w, h) in enumerate(padded[:max_boxes], start=1)
    ]


def _cv2_candidate_mask(
    cv2,
    thresh,
    *,
    line_kernel=45,
    text_kernel=(55, 12),
    dilate_kernel=24,
    iterations=2,
):
    kernel_x = cv2.getStructuringElement(cv2.MORPH_RECT, (line_kernel, 3))
    kernel_y = cv2.getStructuringElement(cv2.MORPH_RECT, (3, line_kernel))
    horizontal = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_x, iterations=1)
    vertical = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_y, iterations=1)
    combined = cv2.bitwise_or(horizontal, vertical)

    textish_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, text_kernel)
    textish = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, textish_kernel, iterations=1)
    combined = cv2.bitwise_or(combined, textish)

    dilater = cv2.getStructuringElement(cv2.MORPH_RECT, (dilate_kernel, dilate_kernel))
    return cv2.dilate(combined, dilater, iterations=iterations)


def _record_cv2_rejection(rejected_examples, reason, box, area_ratio, aspect):
    rejected_examples.setdefault(reason, []).append(
        {
            "box": box,
            "area_ratio": area_ratio,
            "aspect": aspect,
        }
    )


def _filter_cv2_contours(
    cv2,
    contours,
    width,
    height,
    *,
    min_area_ratio=0.0008,
    max_area_ratio=0.58,
    max_aspect=12,
    min_aspect=0.05,
    stats_prefix="",
):
    sheet_area = width * height
    boxes = []
    stats = {
        f"{stats_prefix}raw_contours": len(contours),
        f"{stats_prefix}kept": 0,
        f"{stats_prefix}too_small": 0,
        f"{stats_prefix}too_large": 0,
        f"{stats_prefix}bad_aspect": 0,
        f"{stats_prefix}title_block": 0,
    }
    rejected_examples = {}
    largest_too_large_ratio = 0
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        area_ratio = area / max(1, sheet_area)
        aspect = w / max(h, 1)
        box = [x, y, w, h]
        if area < sheet_area * min_area_ratio:
            stats[f"{stats_prefix}too_small"] += 1
            _record_cv2_rejection(rejected_examples, f"{stats_prefix}too_small", box, area_ratio, aspect)
            continue
        if area > sheet_area * max_area_ratio:
            stats[f"{stats_prefix}too_large"] += 1
            largest_too_large_ratio = max(largest_too_large_ratio, area_ratio)
            _record_cv2_rejection(rejected_examples, f"{stats_prefix}too_large", box, area_ratio, aspect)
            continue
        if aspect > max_aspect or aspect < min_aspect:
            stats[f"{stats_prefix}bad_aspect"] += 1
            _record_cv2_rejection(rejected_examples, f"{stats_prefix}bad_aspect", box, area_ratio, aspect)
            continue
        if x > width * 0.55 and y > height * 0.72 and area > sheet_area * 0.025:
            stats[f"{stats_prefix}title_block"] += 1
            _record_cv2_rejection(rejected_examples, f"{stats_prefix}title_block", box, area_ratio, aspect)
            continue
        stats[f"{stats_prefix}kept"] += 1
        boxes.append(box)
    return boxes, stats, rejected_examples, largest_too_large_ratio


def _merge_stats(base, extra):
    for key, value in extra.items():
        base[key] = base.get(key, 0) + value


def _merge_rejected_examples(base, extra):
    for key, values in extra.items():
        base.setdefault(key, []).extend(values)


def _unique_boxes(boxes):
    seen = set()
    unique = []
    for box in boxes:
        key = tuple(map(int, box[:4]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(list(key))
    return unique


def _cv2_candidates_detailed(
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
    combined = _cv2_candidate_mask(
        cv2,
        thresh,
        line_kernel=line_kernel,
        text_kernel=text_kernel,
        dilate_kernel=dilate_kernel,
        iterations=iterations,
    )
    external_contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes, stats, rejected_examples, largest_too_large_ratio = _filter_cv2_contours(
        cv2,
        external_contours,
        width,
        height,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        max_aspect=max_aspect,
        min_aspect=min_aspect,
    )

    # Some architects draw a full-sheet border or connected grid that wraps every
    # detail. RETR_EXTERNAL then sees only one page-sized contour and hides useful
    # internal detail contours. When that happens, inspect all contours as a fallback.
    if len(boxes) < 3 and largest_too_large_ratio > 0.80:
        all_contours, _ = cv2.findContours(combined, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        internal_boxes, internal_stats, internal_rejected, _ = _filter_cv2_contours(
            cv2,
            all_contours,
            width,
            height,
            min_area_ratio=min_area_ratio,
            max_area_ratio=max_area_ratio,
            max_aspect=max_aspect,
            min_aspect=min_aspect,
            stats_prefix="internal_",
        )
        boxes.extend(internal_boxes)
        _merge_stats(stats, internal_stats)
        _merge_rejected_examples(rejected_examples, internal_rejected)

    return _unique_boxes(boxes), stats, rejected_examples


def _cv2_candidates(
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
    boxes, _, _ = _cv2_candidates_detailed(
        cv2,
        thresh,
        width,
        height,
        line_kernel=line_kernel,
        text_kernel=text_kernel,
        dilate_kernel=dilate_kernel,
        iterations=iterations,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        max_aspect=max_aspect,
        min_aspect=min_aspect,
    )
    return boxes


def _detect_with_cv2(image_path: Path, max_boxes: int) -> list[dict] | None:
    cv2 = _cv2()
    if cv2 is None:
        return None

    img = cv2.imread(str(image_path))
    if img is None:
        return []

    orig_height, orig_width = img.shape[:2]

    target = 2200
    scale = min(1.0, target / max(orig_width, orig_height))
    if scale < 1.0:
        img = cv2.resize(img, (int(round(orig_width * scale)), int(round(orig_height * scale))), interpolation=cv2.INTER_AREA)

    height, width = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 51, 15
    )

    preview_boxes = _cv2_preview_boxes(cv2, thresh, width, height)
    packed_layout = _estimate_packed_layout(preview_boxes, width, height)

    # Multi-scale detection adapts to sheet density. Packed sheets use tighter
    # kernels/gaps and shorter label reach so adjacent details do not collapse
    # into large groups; looser sheets keep the more generous grouping needed to
    # capture sparse linework and separated labels.
    profiles = _cv2_detection_profiles(width, height, packed_layout)

    boxes = []
    for profile in profiles:
        candidate_profile = dict(profile)
        merge_dx = candidate_profile.pop("merge_dx")
        merge_dy = candidate_profile.pop("merge_dy")
        candidates = _cv2_candidates(cv2, thresh, width, height, **candidate_profile)
        boxes.extend(_merge_boxes(candidates, dx=merge_dx, dy=merge_dy))
    boxes = _merge_labels_under_details(boxes, width, height, packed_layout=packed_layout)
    boxes = _dedupe_boxes(boxes)
    boxes = _remove_composite_boxes(boxes)

    if scale < 1.0:
        inv = 1.0 / scale
        boxes = [[int(round(x * inv)), int(round(y * inv)), int(round(w * inv)), int(round(h * inv))] for x, y, w, h in boxes]

    return _format_results(boxes, orig_width, orig_height, max_boxes)


def _connected_components(mask: np.ndarray) -> list[list[int]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    boxes = []
    ys, xs = np.nonzero(mask)

    for start_x, start_y in zip(xs, ys):
        if visited[start_y, start_x] or not mask[start_y, start_x]:
            continue
        stack = [(int(start_x), int(start_y))]
        visited[start_y, start_x] = True
        min_x = max_x = int(start_x)
        min_y = max_y = int(start_y)
        pixels = 0

        while stack:
            x, y = stack.pop()
            pixels += 1
            if x < min_x: min_x = x
            if x > max_x: max_x = x
            if y < min_y: min_y = y
            if y > max_y: max_y = y

            for ny in (y - 1, y, y + 1):
                if ny < 0 or ny >= height:
                    continue
                for nx in (x - 1, x, x + 1):
                    if nx < 0 or nx >= width or visited[ny, nx] or not mask[ny, nx]:
                        continue
                    visited[ny, nx] = True
                    stack.append((nx, ny))

        boxes.append([min_x, min_y, max_x - min_x + 1, max_y - min_y + 1, pixels])
    return boxes


def _detect_with_pillow_numpy(image_path: Path, max_boxes: int) -> list[dict]:
    with Image.open(image_path) as img:
        gray = img.convert("L")
        original_width, original_height = gray.size
        scale = min(1.0, 1400 / max(original_width, original_height))
        if scale < 1.0:
            gray = gray.resize((max(1, int(original_width * scale)), max(1, int(original_height * scale))), Image.Resampling.BILINEAR)

    width, height = gray.size
    sheet_area = width * height
    arr = np.asarray(gray)

    # Construction sheets are usually black/gray linework on white paper. Use a high
    # threshold so anti-aliased PDF text/lines participate in the candidate clusters.
    foreground = (arr < 245).astype(np.uint8) * 255
    mask_img = Image.fromarray(foreground, mode="L")

    # Approximate the old OpenCV dilation/close behavior with Pillow filters. This
    # keeps auto-boxing alive on Raspberry Pi/headless hosts even if cv2 cannot load.
    dilate = max(7, int(round(min(width, height) * 0.012)))
    if dilate % 2 == 0:
        dilate += 1
    preview_boxes = _pillow_preview_boxes(mask_img, width, height, sheet_area)
    packed_layout = _estimate_packed_layout(preview_boxes, width, height)

    boxes = []
    profiles = _pillow_detection_profiles(width, height, packed_layout)
    for profile in profiles:
        pass_dilate = max(3, int(round(dilate * profile["factor"])))
        if pass_dilate % 2 == 0:
            pass_dilate += 1
        pass_img = mask_img.filter(ImageFilter.MaxFilter(pass_dilate))
        if profile["factor"] >= 1.0:
            pass_img = pass_img.filter(ImageFilter.MaxFilter(max(3, pass_dilate // 2 * 2 + 1)))
        mask = np.asarray(pass_img) > 0

        pass_boxes = []
        for x, y, w, h, pixels in _connected_components(mask):
            area = w * h
            if area < sheet_area * profile["min_area_ratio"] or area > sheet_area * profile["max_area_ratio"]:
                continue
            aspect = w / max(h, 1)
            if aspect > profile["max_aspect"] or aspect < 0.05:
                continue
            if pixels < sheet_area * 0.00018:
                continue
            if x > width * 0.55 and y > height * 0.72 and area > sheet_area * 0.025:
                continue
            pass_boxes.append([x, y, w, h])
        boxes.extend(
            _merge_boxes(
                pass_boxes,
                dx=max(3, int(profile["merge_gap"] * scale)),
                dy=max(3, int(profile["merge_gap"] * scale)),
            )
        )

    boxes = _merge_labels_under_details(boxes, width, height, packed_layout=packed_layout)
    boxes = _dedupe_boxes(boxes)
    boxes = _remove_composite_boxes(boxes)

    if scale != 1.0:
        inv = 1 / scale
        boxes = [[int(round(x * inv)), int(round(y * inv)), int(round(w * inv)), int(round(h * inv))] for x, y, w, h in boxes]

    return _format_results(boxes, original_width, original_height, max_boxes)


def detect_candidate_detail_boxes(image_path: Path, max_boxes: int = 80) -> list[dict]:
    cv2_result = _detect_with_cv2(image_path, max_boxes)
    if cv2_result is not None:
        return cv2_result
    return _detect_with_pillow_numpy(image_path, max_boxes)
