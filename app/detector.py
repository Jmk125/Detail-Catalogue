from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter


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
    mask_img = mask_img.filter(ImageFilter.MaxFilter(dilate))
    mask_img = mask_img.filter(ImageFilter.MaxFilter(max(3, dilate // 2 * 2 + 1)))
    mask = np.asarray(mask_img) > 0

    boxes = []
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
        boxes.append([x, y, w, h])

    boxes = _merge_boxes(boxes, dx=max(6, int(18 * scale)), dy=max(6, int(18 * scale)))
    boxes = _merge_labels_under_details(boxes, width, height)
    boxes = _merge_boxes(boxes, dx=max(4, int(10 * scale)), dy=max(4, int(10 * scale)))

    if scale != 1.0:
        inv = 1 / scale
        boxes = [[int(round(x * inv)), int(round(y * inv)), int(round(w * inv)), int(round(h * inv))] for x, y, w, h in boxes]

    return _format_results(boxes, original_width, original_height, max_boxes)


def detect_candidate_detail_boxes(image_path: Path, max_boxes: int = 80) -> list[dict]:
    return _detect_with_pillow_numpy(image_path, max_boxes)
