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
    """Prefer smaller, more precise boxes when multi-scale passes overlap heavily."""
    kept = []
    for box in sorted([list(map(int, b[:4])) for b in boxes], key=lambda b: (b[2] * b[3], b[1], b[0])):
        area = max(1, box[2] * box[3])
        duplicate = False
        for existing in kept:
            existing_area = max(1, existing[2] * existing[3])
            overlap = _intersection_area(box, existing) / min(area, existing_area)
            if overlap >= overlap_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return sorted(kept, key=lambda b: (b[1], b[0]))


def _horizontal_overlap_ratio(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    return overlap / max(1, min(aw, bw))


def _center_distance_x(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return abs((ax + aw / 2) - (bx + bw / 2))


def _merge_labels_under_details(boxes, sheet_w, sheet_h):
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
                near_below = gap <= sheet_h * 0.065
                label_like_height = bh <= max(ch * 0.55, sheet_h * 0.075)
                not_huge = (bw * bh) < (cw * ch) * 0.75
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


def _cv2_candidates(cv2, thresh, width, height, *, line_kernel=45, text_kernel=(55, 12), dilate_kernel=24, iterations=2):
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
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < sheet_area * 0.0008 or area > sheet_area * 0.58:
            continue
        aspect = w / max(h, 1)
        if aspect > 12 or aspect < 0.05:
            continue
        if x > width * 0.55 and y > height * 0.72 and area > sheet_area * 0.025:
            continue
        boxes.append([x, y, w, h])
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

    # Multi-scale detection: the coarse pass behaves like the original detector,
    # while the fine pass keeps packed detail sheets from collapsing into a few
    # large blobs when details are tight together.
    coarse = _cv2_candidates(cv2, thresh, width, height, line_kernel=45, text_kernel=(55, 12), dilate_kernel=24, iterations=2)
    fine = _cv2_candidates(cv2, thresh, width, height, line_kernel=25, text_kernel=(35, 8), dilate_kernel=10, iterations=1)

    boxes = _merge_boxes(coarse, dx=14, dy=14) + _merge_boxes(fine, dx=5, dy=5)
    boxes = _merge_labels_under_details(boxes, width, height)
    boxes = _dedupe_boxes(boxes)

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
    boxes = []
    for factor, merge_gap in ((1.0, 14), (0.45, 5)):
        pass_dilate = max(3, int(round(dilate * factor)))
        if pass_dilate % 2 == 0:
            pass_dilate += 1
        pass_img = mask_img.filter(ImageFilter.MaxFilter(pass_dilate))
        if factor >= 1.0:
            pass_img = pass_img.filter(ImageFilter.MaxFilter(max(3, pass_dilate // 2 * 2 + 1)))
        mask = np.asarray(pass_img) > 0

        pass_boxes = []
        for x, y, w, h, pixels in _connected_components(mask):
            area = w * h
            if area < sheet_area * 0.0008 or area > sheet_area * 0.58:
                continue
            aspect = w / max(h, 1)
            if aspect > 12 or aspect < 0.05:
                continue
            if pixels < sheet_area * 0.00025:
                continue
            if x > width * 0.55 and y > height * 0.72 and area > sheet_area * 0.025:
                continue
            pass_boxes.append([x, y, w, h])
        boxes.extend(_merge_boxes(pass_boxes, dx=max(3, int(merge_gap * scale)), dy=max(3, int(merge_gap * scale))))

    boxes = _merge_labels_under_details(boxes, width, height)
    boxes = _dedupe_boxes(boxes)

    if scale != 1.0:
        inv = 1 / scale
        boxes = [[int(round(x * inv)), int(round(y * inv)), int(round(w * inv)), int(round(h * inv))] for x, y, w, h in boxes]

    return _format_results(boxes, original_width, original_height, max_boxes)


def detect_candidate_detail_boxes(image_path: Path, max_boxes: int = 80) -> list[dict]:
    cv2_result = _detect_with_cv2(image_path, max_boxes)
    if cv2_result is not None:
        return cv2_result
    return _detect_with_pillow_numpy(image_path, max_boxes)
