from pathlib import Path
import cv2


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

                # Candidate must be below the current box.
                gap = by - (cy + ch)
                if gap < -sheet_h * 0.01:
                    continue

                # Detail labels are usually short, wide-ish, and close underneath.
                overlap = _horizontal_overlap_ratio(cur, cand)
                center_close = _center_distance_x(cur, cand) < max(cw, bw) * 0.40
                near_below = gap <= sheet_h * 0.065
                label_like_height = bh <= max(ch * 0.55, sheet_h * 0.075)
                not_huge = (bw * bh) < (cw * ch) * 0.75

                # Also allow labels that are wider than the detail if centered below it.
                reasonable_width = bw <= cw * 1.65 or center_close

                if near_below and label_like_height and not_huge and reasonable_width and (overlap >= 0.18 or center_close):
                    cur = _union(cur, cand)
                    used[j] = True
                    changed = True

            new_boxes.append(cur)

        boxes = new_boxes

    return boxes


def detect_candidate_detail_boxes(image_path: Path, max_boxes: int = 80) -> list[dict]:
    img = cv2.imread(str(image_path))
    if img is None:
        return []

    height, width = img.shape[:2]
    sheet_area = width * height
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 51, 15
    )

    # First pass: linework/detail body clusters
    kernel_x = cv2.getStructuringElement(cv2.MORPH_RECT, (45, 3))
    kernel_y = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 45))
    horizontal = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_x, iterations=1)
    vertical = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_y, iterations=1)
    combined = cv2.bitwise_or(horizontal, vertical)

    # Second pass: include nearby text/title labels more aggressively.
    text_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (55, 12))
    textish = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, text_kernel, iterations=1)
    combined = cv2.bitwise_or(combined, textish)

    dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (24, 24))
    combined = cv2.dilate(combined, dilate_kernel, iterations=2)

    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < sheet_area * 0.0008:
            continue
        if area > sheet_area * 0.58:
            continue

        aspect = w / max(h, 1)
        if aspect > 12 or aspect < 0.05:
            continue

        # avoid title block
        if x > width * 0.55 and y > height * 0.72 and area > sheet_area * 0.025:
            continue

        boxes.append([x, y, w, h])

    boxes = _merge_boxes(boxes, dx=18, dy=18)
    boxes = _merge_labels_under_details(boxes, width, height)
    boxes = _merge_boxes(boxes, dx=10, dy=10)

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

    result = []
    for idx, (x, y, w, h) in enumerate(padded[:max_boxes], start=1):
        result.append({
            "id": f"det_{idx:03d}",
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "confidence": 0.50,
            "source": "detector",
        })
    return result
