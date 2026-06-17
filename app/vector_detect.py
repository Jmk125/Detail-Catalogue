"""Detect candidate detail boxes from a PDF page's vector/text layer.

Most construction detail sheets are exported as true vector PDFs: detail borders
are real rectangle/line instructions and detail titles/scales are real text, all
with exact coordinates. The historical detector throws that away by rasterizing
the page to a PNG and re-deriving structure from thresholded pixels. This module
reads the structure directly with PyMuPDF (``fitz``) instead.

It returns boxes in the SAME pixel coordinate space as the raster detector (the
rendered PNG at the configured zoom), so the rest of the pipeline is unchanged.
When a page has no usable vector content (a scanned/flattened image), it returns
``None`` so the caller can fall back to the raster path.

Strategies (all scored with the raster path's quality metric; the best wins):
  1. Explicit rectangles    -> boxed details read straight from ``re`` items and
     reconstructed 4-line rectangles.
  2. Text anchors           -> for unboxed details on dense, connected sheets
     (e.g. structural steel), heading-sized title/number text marks each detail;
     every bit of vector ink is assigned to its nearest anchor (a Voronoi/
     watershed partition). This avoids gap tuning entirely.
  3. Vector + text clustering -> proximity grouping of exact line/text boxes, a
     fallback for moderately separated unboxed details.
"""
from __future__ import annotations

import importlib
import math
from pathlib import Path

import numpy as np

from .detector import (
    _dedupe_boxes,
    _format_results,
    _layout_density,
    _median,
    _merge_boxes,
    _merge_labels_under_details,
    _remove_composite_boxes,
    _score_results,
)


def _fitz():
    try:
        return importlib.import_module("fitz")
    except Exception:
        return None


def _norm_px(x0, y0, x1, y1, scale_x, scale_y, *, allow_thin=False):
    """Convert a PDF-point rectangle to an integer pixel box, normalizing order.

    Horizontal/vertical strokes have a zero-thickness bounding box; ``allow_thin``
    keeps them with a 1px thickness so individual line segments still participate
    in clustering instead of being discarded.
    """
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    x = int(round(x0 * scale_x))
    y = int(round(y0 * scale_y))
    w = int(round((x1 - x0) * scale_x))
    h = int(round((y1 - y0) * scale_y))
    if allow_thin:
        w = max(1, w)
        h = max(1, h)
    elif w <= 0 or h <= 0:
        return None
    return [x, y, w, h]


def _rect_to_px(rect, scale_x, scale_y, *, allow_thin=False):
    try:
        return _norm_px(rect.x0, rect.y0, rect.x1, rect.y1, scale_x, scale_y, allow_thin=allow_thin)
    except Exception:
        return None


def _path_is_rectangleish(items) -> bool:
    """True when a drawing path is a plain (possibly unfilled) axis-aligned rectangle."""
    xs: list[float] = []
    ys: list[float] = []
    lines = 0
    for it in items:
        kind = it[0]
        if kind == "re":
            return True
        if kind == "c" or kind == "qu":
            return False
        if kind == "l":
            lines += 1
            for pt in it[1:3]:
                xs.append(pt.x)
                ys.append(pt.y)
    if lines < 3 or lines > 6:
        return False
    return _distinct_count(xs) <= 2 and _distinct_count(ys) <= 2


def _distinct_count(values, tol: float = 2.0) -> int:
    ordered = sorted(values)
    clusters = 0
    anchor = None
    for v in ordered:
        if anchor is None or v - anchor > tol:
            clusters += 1
            anchor = v
        if clusters > 3:
            break
    return clusters


def _looks_like_title_block(box, width, height) -> bool:
    x, y, w, h = box
    sheet_area = max(1, width * height)
    return x > width * 0.55 and y > height * 0.72 and (w * h) > sheet_area * 0.025


def _detail_sized(box, width, height, *, min_ratio=0.004, max_ratio=0.45, max_aspect=9.0):
    x, y, w, h = box
    sheet_area = max(1, width * height)
    area = w * h
    if area < sheet_area * min_ratio or area > sheet_area * max_ratio:
        return False
    aspect = w / max(1, h)
    if aspect > max_aspect or aspect < 1.0 / max_aspect:
        return False
    return True


def _page_geometry(page, scale_x, scale_y, width, height):
    """Collect explicit rectangles, all vector ink boxes, and word boxes (pixels)."""
    rects: list[list[int]] = []
    ink: list[list[int]] = []
    sheet_area = max(1, width * height)

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        items = drawing.get("items", [])
        ink_bbox = _rect_to_px(rect, scale_x, scale_y, allow_thin=True) if rect is not None else None
        if ink_bbox is not None and ink_bbox[2] * ink_bbox[3] <= sheet_area * 0.55:
            ink.append(ink_bbox)
        for it in items:
            if it and it[0] == "re":
                px = _rect_to_px(it[1], scale_x, scale_y)
                if px is not None:
                    rects.append(px)
        rect_bbox = _rect_to_px(rect, scale_x, scale_y) if rect is not None else None
        if rect_bbox is not None and _path_is_rectangleish(items):
            rects.append(rect_bbox)

    words: list[list[int]] = []
    try:
        for w in page.get_text("words"):
            px = _norm_px(w[0], w[1], w[2], w[3], scale_x, scale_y)
            if px is not None and px[2] * px[3] <= sheet_area * 0.10:
                words.append(px)
    except Exception:
        pass

    return rects, ink, words


def _text_spans(page, scale_x, scale_y, width, height):
    """Return text spans as ``(text, [x, y, w, h] px, font_size_px)``.

    Font size is reported in pixels (points * scale) so heading thresholds scale
    with the rendered sheet.
    """
    spans: list[tuple[str, list[int], float]] = []
    sheet_area = max(1, width * height)
    try:
        data = page.get_text("dict")
    except Exception:
        return spans
    avg_scale = (scale_x + scale_y) / 2.0
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                bbox = span.get("bbox")
                if not bbox:
                    continue
                px = _norm_px(bbox[0], bbox[1], bbox[2], bbox[3], scale_x, scale_y, allow_thin=True)
                if px is None or px[2] * px[3] > sheet_area * 0.10:
                    continue
                size = float(span.get("size", 0.0)) * avg_scale
                spans.append((text, px, size))
    return spans


def _candidates_from_rectangles(rects, width, height):
    kept = [
        box
        for box in rects
        if _detail_sized(box, width, height) and not _looks_like_title_block(box, width, height)
    ]
    if not kept:
        return []
    merged = _merge_boxes(kept, dx=2, dy=2)
    return _dedupe_boxes(merged)


def _candidates_from_clusters(ink, words, width, height, gap_x, gap_y):
    sheet_area = max(1, width * height)
    items = [b for b in (ink + words) if b[2] * b[3] <= sheet_area * 0.45]
    if not items:
        return []
    clusters = _merge_boxes(items, dx=gap_x, dy=gap_y)
    return [
        box
        for box in clusters
        if _detail_sized(box, width, height) and not _looks_like_title_block(box, width, height)
    ]


def _body_text_size(sizes):
    """The most common font size on the sheet, i.e. the body/dimension text size.

    Using the mode (rather than the median) is robust to how many headings exist:
    detail sheets always carry far more small annotation text than titles, so the
    most frequent size is the body size regardless of the heading count.
    """
    counts: dict[float, int] = {}
    for s in sizes:
        key = round(s)
        counts[key] = counts.get(key, 0) + 1
    # Prefer the most frequent size; break ties toward the smaller size.
    return min(counts, key=lambda k: (-counts[k], k))


def _heading_window(spans):
    """Return ``(body_size, low, high)`` font-size band for detail headings.

    ``body`` is the most common (body/dimension) size; detail titles/numbers sit
    modestly above it, while the sheet title/logo sits above ``high``.
    """
    sizes = [s for _, _, s in spans if s > 0]
    if len(sizes) < 3:
        return None
    body = _body_text_size(sizes)
    return body, max(body * 1.25, body + 2.0), body * 2.5


def _detail_number_spans(spans, low, high, width, height):
    """Heading-sized short tokens that look like detail numbers (e.g. 1, 12, A3).

    Detail numbers are the most reliable per-detail marker: one per detail, on a
    regular grid, and a larger font than dimension text. We require a short token
    containing a digit so dimension strings and titles are excluded.
    """
    out = []
    for text, box, size in spans:
        if not (low <= size <= high):
            continue
        if _in_title_block_strip(box, width, height):
            continue
        token = text.strip()
        if 1 <= len(token) <= 3 and any(c.isdigit() for c in token) and token.replace(".", "").isalnum():
            out.append(box)
    return out


def _cluster_1d(values, tol):
    """Average-link 1-D clustering: returns sorted cluster centers."""
    ordered = sorted(values)
    centers = []
    current = [ordered[0]]
    for v in ordered[1:]:
        if v - current[-1] <= tol:
            current.append(v)
        else:
            centers.append(sum(current) / len(current))
            current = [v]
    centers.append(sum(current) / len(current))
    return centers


def _median_pitch(centers, fallback):
    if len(centers) < 2:
        return fallback
    diffs = sorted(centers[i + 1] - centers[i] for i in range(len(centers) - 1))
    return diffs[len(diffs) // 2]


def _clamp_cell_to_ink(cell, ink, number_box):
    """Shrink a geometric grid cell to the ink it actually contains.

    Geometric cells (from the anchor grid) overshoot on irregular sheets. Clamping
    to the bounding box of the ink whose center falls inside the cell makes each
    box hug its detail and self-corrects overshoot, while always keeping the
    detail number inside.
    """
    cl, ct, cw, ch = cell
    cr, cb = cl + cw, ct + ch
    cell_area = max(1, cw * ch)
    minx = miny = None
    maxx = maxy = None
    for b in ink:
        if b[2] * b[3] > cell_area * 0.9:
            continue
        bx = b[0] + b[2] / 2.0
        by = b[1] + b[3] / 2.0
        if cl <= bx <= cr and ct <= by <= cb:
            minx = b[0] if minx is None else min(minx, b[0])
            miny = b[1] if miny is None else min(miny, b[1])
            maxx = b[0] + b[2] if maxx is None else max(maxx, b[0] + b[2])
            maxy = b[1] + b[3] if maxy is None else max(maxy, b[1] + b[3])
    if minx is None:
        return cell
    minx = min(minx, number_box[0])
    miny = min(miny, number_box[1])
    maxx = max(maxx, number_box[0] + number_box[2])
    maxy = max(maxy, number_box[1] + number_box[3])
    x0 = int(max(cl, minx))
    y0 = int(max(ct, miny))
    x1 = int(min(cr, maxx))
    y1 = int(min(cb, maxy))
    if x1 > x0 and y1 > y0:
        return [x0, y0, x1 - x0, y1 - y0]
    return cell


def _candidates_from_grid(number_boxes, ink, width, height):
    """Reconstruct detail boxes from detail-number anchors.

    Columns are clustered globally (column x is stable even when detail heights
    vary), but rows are derived PER COLUMN so a tall detail in one column does not
    distort another column's rows. Titles/numbers sit at the bottom-left of each
    detail, so a cell spans rightward from its number and upward to the previous
    detail in the same column. Each geometric cell is finally clamped to its ink.
    """
    if len(number_boxes) < 4:
        return []
    col_tol = max(40, int(width * 0.02))
    row_tol = max(40, int(height * 0.02))
    col_centers = _cluster_1d([b[0] for b in number_boxes], col_tol)
    if not col_centers:
        return []
    col_pitch = _median_pitch(col_centers, width)
    row_pitch = _median_pitch(_cluster_1d([b[1] + b[3] / 2.0 for b in number_boxes], row_tol), height)
    lead = col_pitch * 0.10
    title_bar = row_pitch * 0.12

    by_col: dict[int, list[list[int]]] = {}
    for b in number_boxes:
        ci = min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - b[0]))
        by_col.setdefault(ci, []).append(b)

    boxes = []
    for ci, members in by_col.items():
        left = col_centers[ci] - lead
        right = (col_centers[ci + 1] - lead) if ci + 1 < len(col_centers) else col_centers[ci] - lead + col_pitch
        members = sorted(members, key=lambda b: b[1] + b[3] / 2.0)
        prev_bottom = None
        for m in members:
            cy = m[1] + m[3] / 2.0
            bottom = cy + title_bar
            top = (prev_bottom) if prev_bottom is not None else (cy - row_pitch + title_bar)
            prev_bottom = bottom
            cell = [
                int(max(0, left)),
                int(max(0, top)),
                int(min(width, right) - max(0, left)),
                int(min(height, bottom) - max(0, top)),
            ]
            if cell[2] <= 0 or cell[3] <= 0:
                continue
            if not _detail_sized(cell, width, height):
                continue
            clamped = _clamp_cell_to_ink(cell, ink, m)
            # Clamp only to tighten; if it collapsed the cell (sparse ink), keep
            # the geometric cell so we never drop a genuine numbered detail.
            if clamped[2] * clamped[3] >= 0.25 * cell[2] * cell[3]:
                boxes.append(clamped)
            else:
                boxes.append(cell)
    return boxes


def _detail_anchor_points(spans, width, height):
    """One anchor point per detail: prefer numbers (clean grids), else titles.

    Returns ``[(cx, cy, box), ...]``. Numbers are used when there are enough of
    them; otherwise heading-sized non-numeric titles are used (with their stacked
    lines merged) so number-less sheets still get one anchor per detail.
    """
    window = _heading_window(spans)
    if window is None:
        return []
    body, low, high = window
    numbers = _detail_number_spans(spans, low, high, width, height)
    if len(numbers) >= 4:
        groups = _merge_boxes(numbers, dx=int(body * 0.6), dy=int(body * 0.6))
    else:
        titles = [
            box
            for (text, box, s) in spans
            if low <= s <= high
            and not _in_title_block_strip(box, width, height)
            and not (1 <= len(text.strip()) <= 3 and any(c.isdigit() for c in text.strip()))
        ]
        if len(titles) < 2:
            return []
        groups = _merge_boxes(titles, dx=int(body * 2.0), dy=int(body * 2.2))
    return [(g[0] + g[2] / 2.0, g[1] + g[3] / 2.0, g) for g in groups]


def _ink_xyxy(ink):
    if not ink:
        return np.zeros((0, 4))
    a = np.asarray(ink, dtype=float)
    return np.column_stack([a[:, 0], a[:, 1], a[:, 0] + a[:, 2], a[:, 1] + a[:, 3]])


def _density_profile(ink, lo, hi, perp_lo, perp_hi, axis, bins=400):
    """Ink coverage profile along ``axis`` within a perpendicular band."""
    if len(ink) == 0:
        return np.zeros(bins)
    x0, y0, x1, y1 = ink[:, 0], ink[:, 1], ink[:, 2], ink[:, 3]
    if axis == 0:
        keep = (y1 >= perp_lo) & (y0 <= perp_hi)
        a0, a1 = x0, x1
    else:
        keep = (x1 >= perp_lo) & (x0 <= perp_hi)
        a0, a1 = y0, y1
    a0, a1 = a0[keep], a1[keep]
    span = max(1.0, hi - lo)
    i0 = np.clip(((a0 - lo) / span * bins).astype(int), 0, bins - 1)
    i1 = np.clip(((a1 - lo) / span * bins).astype(int), 0, bins - 1)
    diff = np.zeros(bins + 1)
    np.add.at(diff, i0, 1)
    np.add.at(diff, i1 + 1, -1)
    return np.cumsum(diff[:-1])


def _widest_gutter(prof, lo, hi, anchor_positions, bins, sheet_dim):
    """Widest low-ink gap that puts at least one anchor on each side."""
    peak = prof.max()
    threshold = 0 if peak <= 0 else max(1.0, 0.06 * peak)
    empty = prof <= threshold
    span = hi - lo
    best = None
    i = 0
    while i < bins:
        if empty[i]:
            j = i
            while j < bins and empty[j]:
                j += 1
            cut = lo + (i + j) / 2.0 / bins * span
            width = (j - i) / bins * span
            left = sum(1 for p in anchor_positions if p < cut)
            if 0 < left < len(anchor_positions) and width >= max(0.012 * sheet_dim, 40):
                if best is None or width > best[1]:
                    best = (cut, width)
            i = j
        else:
            i += 1
    return best


def _xy_cut(region, anchors, ink, width, height, depth=0):
    """Recursively split a region at its widest gutter until one anchor remains."""
    x0, y0, x1, y1 = region
    inside = [a for a in anchors if x0 <= a[0] <= x1 and y0 <= a[1] <= y1]
    if len(inside) <= 1 or depth > 14:
        return [(region, inside)]
    bins = 400
    gv = _widest_gutter(_density_profile(ink, x0, x1, y0, y1, 0, bins), x0, x1, [a[0] for a in inside], bins, width)
    gh = _widest_gutter(_density_profile(ink, y0, y1, x0, x1, 1, bins), y0, y1, [a[1] for a in inside], bins, height)
    choice = None
    if gv and gh:
        choice = ("v", gv) if gv[1] >= gh[1] else ("h", gh)
    elif gv:
        choice = ("v", gv)
    elif gh:
        choice = ("h", gh)
    if not choice:
        return [(region, inside)]
    kind, (cut, _w) = choice
    if kind == "v":
        r1, r2 = (x0, y0, cut, y1), (cut, y0, x1, y1)
    else:
        r1, r2 = (x0, y0, x1, cut), (x0, cut, x1, y1)
    return _xy_cut(r1, inside, ink, width, height, depth + 1) + _xy_cut(r2, inside, ink, width, height, depth + 1)


def _candidates_from_heading_regions(spans, ink, width, height):
    """Layout-agnostic strategy: XY-cut the sheet into one region per detail heading.

    Recursively splits at the widest ink-free gutters (vertical or horizontal)
    until each region holds a single heading anchor, then clamps each region to its
    ink. Handles scattered/varied layouts that have no grid. Any region that still
    holds multiple headings (a gutter could not be found) is split by assigning its
    ink to the nearest heading -- the heading-seed fallback.
    """
    anchors = _detail_anchor_points(spans, width, height)
    if len(anchors) < 2:
        return []
    ink_arr = _ink_xyxy(ink)
    content = (0.0, 0.0, width * 0.86, height * 0.90)
    boxes = []
    for region, inside in _xy_cut(content, anchors, ink_arr, width, height):
        if not inside:
            continue
        if len(inside) == 1:
            cell = [int(region[0]), int(region[1]), int(region[2] - region[0]), int(region[3] - region[1])]
            cell = _clamp_cell_to_ink(cell, ink, inside[0][2])
            if _detail_sized(cell, width, height):
                boxes.append(cell)
        else:
            boxes.extend(_seed_split_region(region, inside, ink, width, height))
    return _dedupe_boxes(boxes)


def _seed_split_region(region, anchors, ink, width, height):
    """Heading-seed fallback: split an unsplittable region by nearest heading."""
    rx0, ry0, rx1, ry1 = region
    centers = [(a[0], a[1]) for a in anchors]
    buckets: list[list[list[int]]] = [[a[2]] for a in anchors]
    for b in ink:
        bx = b[0] + b[2] / 2.0
        by = b[1] + b[3] / 2.0
        if not (rx0 <= bx <= rx1 and ry0 <= by <= ry1):
            continue
        best_i, best_d = 0, None
        for i, (ax, ay) in enumerate(centers):
            d = (bx - ax) ** 2 + (by - ay) ** 2
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        buckets[best_i].append(b)
    boxes = []
    for group in buckets:
        x0 = min(b[0] for b in group)
        y0 = min(b[1] for b in group)
        x1 = max(b[0] + b[2] for b in group)
        y1 = max(b[1] + b[3] for b in group)
        cell = [x0, y0, x1 - x0, y1 - y0]
        if _detail_sized(cell, width, height):
            boxes.append(cell)
    return boxes


def _in_title_block_strip(box, width, height) -> bool:
    """True for text in the sheet's title-block band (right edge or bottom strip).

    Title blocks are near-universally along the right edge and/or bottom of the
    sheet. Their text (sheet name/number, firm, issuances) is not a detail anchor.
    """
    cx = box[0] + box[2] / 2.0
    cy = box[1] + box[3] / 2.0
    return cx > width * 0.86 or cy > height * 0.90


def _heading_anchors(spans, width, height):
    """Group heading-sized text into per-detail anchors.

    Detail titles/numbers are a larger font than dimension/annotation text but a
    smaller font than the sheet title/logo. We keep spans whose size is modestly
    above the body-text size (the mode, robust to heading count) and below the
    sheet-title size, excluding anything in the title-block band, then merge
    neighbouring spans into title groups; each group anchors one detail.
    """
    window = _heading_window(spans)
    if window is None:
        return [], {"body_size": 0.0, "low": 0.0, "high": 0.0, "heading_spans": 0, "anchor_groups": 0}
    body_size, low, high = window
    heads = [
        box
        for (_text, box, s) in spans
        if low <= s <= high and not _in_title_block_strip(box, width, height)
    ]
    diag = {"body_size": body_size, "low": low, "high": high, "heading_spans": len(heads), "anchor_groups": 0}
    if len(heads) < 2:
        return [], diag
    groups = _merge_boxes(heads, dx=max(8, int(width * 0.020)), dy=max(6, int(height * 0.010)))
    anchors = [(gx + gw / 2.0, gy + gh / 2.0, [gx, gy, gw, gh]) for gx, gy, gw, gh in groups]
    diag["anchor_groups"] = len(anchors)
    return anchors, diag


def _candidates_from_anchors(anchors, ink, width, height):
    """Assign each ink box to its nearest title anchor within a bounded radius.

    The radius is tied to how far apart the anchors are, so ink only joins a
    nearby detail. Without it, a lone anchor next to whitespace would sweep up a
    full-height strip of the sheet (the failure mode seen on real sheets).
    """
    if len(anchors) < 2:
        return []
    sheet_area = max(1, width * height)
    centers = [(ax, ay) for ax, ay, _ in anchors]

    spacings = []
    for i, (ax, ay) in enumerate(centers):
        nearest = None
        for j, (bx, by) in enumerate(centers):
            if i == j:
                continue
            d = math.hypot(ax - bx, ay - by)
            nearest = d if nearest is None else min(nearest, d)
        if nearest is not None:
            spacings.append(nearest)
    diagonal = math.hypot(width, height)
    radius = max(0.10 * diagonal, _median(spacings) * 1.1) if spacings else 0.20 * diagonal
    radius_sq = radius * radius

    buckets: list[list[list[int]]] = [[box] for _ax, _ay, box in anchors]
    for b in ink:
        if b[2] * b[3] > sheet_area * 0.45:
            continue
        cx = b[0] + b[2] / 2.0
        cy = b[1] + b[3] / 2.0
        best_i = 0
        best_d = None
        for i, (ax, ay) in enumerate(centers):
            d = (cx - ax) ** 2 + (cy - ay) ** 2
            if best_d is None or d < best_d:
                best_d = d
                best_i = i
        if best_d is not None and best_d <= radius_sq:
            buckets[best_i].append(b)

    boxes = []
    for group in buckets:
        x0 = min(b[0] for b in group)
        y0 = min(b[1] for b in group)
        x1 = max(b[0] + b[2] for b in group)
        y1 = max(b[1] + b[3] for b in group)
        box = [x0, y0, x1 - x0, y1 - y0]
        if _detail_sized(box, width, height) and not _looks_like_title_block(box, width, height):
            boxes.append(box)
    return boxes


def _cluster_passes(width, height):
    return (
        (max(10, int(width * 0.030)), max(10, int(height * 0.030))),
        (max(6, int(width * 0.018)), max(6, int(height * 0.018))),
        (max(4, int(width * 0.010)), max(4, int(height * 0.010))),
    )


def _build_options(page, scale_x, scale_y, width, height):
    """Return ``[(name, boxes), ...]`` candidate strategies plus a diagnostics dict."""
    rects, ink, words = _page_geometry(page, scale_x, scale_y, width, height)
    spans = _text_spans(page, scale_x, scale_y, width, height)

    options: list[tuple[str, list[list[int]]]] = []
    rect_candidates = _candidates_from_rectangles(rects, width, height)
    if rect_candidates:
        options.append(("rectangles", rect_candidates))

    # Detail-number grid: the most reliable strategy for the common case of a
    # regular grid of numbered details (boxed or not).
    grid_diag = {"number_anchors": 0, "grid_boxes": 0}
    window = _heading_window(spans)
    if window is not None:
        _body, low, high = window
        number_boxes = _detail_number_spans(spans, low, high, width, height)
        grid_diag["number_anchors"] = len(number_boxes)
        grid_candidates = _candidates_from_grid(number_boxes, ink, width, height)
        grid_diag["grid_boxes"] = len(grid_candidates)
        if grid_candidates:
            options.append(("grid_anchors", grid_candidates))

    # Layout-agnostic XY-cut + heading-seed: covers scattered/varied and
    # number-less sheets where the grid strategy cannot fire.
    region_candidates = _candidates_from_heading_regions(spans, ink, width, height)
    if region_candidates:
        options.append(("heading_regions", region_candidates))

    anchors, anchor_diag = _heading_anchors(spans, width, height)
    if anchors:
        anchor_candidates = _candidates_from_anchors(anchors, ink, width, height)
        if anchor_candidates:
            options.append(("text_anchors", anchor_candidates))

    for gap_x, gap_y in _cluster_passes(width, height):
        clusters = _candidates_from_clusters(ink, words, width, height, gap_x, gap_y)
        if clusters:
            options.append((f"clusters_{gap_x}x{gap_y}", clusters))
            if rect_candidates:
                options.append((f"rect+clusters_{gap_x}x{gap_y}", rect_candidates + clusters))

    diag = {
        "drawings": len(ink),
        "words": len(words),
        "rects_total": len(rects),
        "rect_candidates": len(rect_candidates),
        "spans": len(spans),
        **grid_diag,
        **anchor_diag,
    }
    return options, diag


def _postprocess(boxes, width, height, *, merge_labels=True):
    if not boxes:
        return []
    if not merge_labels:
        # Text-anchor regions already include each detail's title via nearest
        # assignment, so only remove duplicate/overlapping regions. Running the
        # label-merge/composite passes here would fuse vertically-adjacent details.
        return _dedupe_boxes(boxes)
    density = _layout_density(boxes, width, height, raw_count=len(boxes))
    merged = _merge_labels_under_details(boxes, width, height, density=density)
    merged = _dedupe_boxes(merged)
    return _remove_composite_boxes(merged)


def _score_option(name, boxes, width, height, max_boxes, source="vector"):
    merge_labels = not (name.startswith("text_anchors") or name.startswith("grid") or name.startswith("heading_regions"))
    processed = _postprocess(boxes, width, height, merge_labels=merge_labels)
    results = _format_results(processed, width, height, max_boxes)
    for r in results:
        r["source"] = source
    return results, _score_results(results, width, height)


def _pixel_scale(page, zoom, page_pixel_size):
    pr = page.rect
    pw, ph = float(pr.width), float(pr.height)
    if pw <= 0 or ph <= 0:
        return None
    if page_pixel_size:
        px_w, px_h = int(page_pixel_size[0]), int(page_pixel_size[1])
        return px_w, px_h, px_w / pw, px_h / ph
    px_w = int(round(pw * float(zoom)))
    px_h = int(round(ph * float(zoom)))
    return px_w, px_h, float(zoom), float(zoom)


def _detect_on_page(page, *, zoom, max_boxes, page_pixel_size):
    geom = _pixel_scale(page, zoom, page_pixel_size)
    if geom is None:
        return None
    px_w, px_h, scale_x, scale_y = geom

    options, _diag = _build_options(page, scale_x, scale_y, px_w, px_h)
    if not options:
        return None

    best_results: list[dict] = []
    best_score = 0.0
    for name, boxes in options:
        results, score = _score_option(name, boxes, px_w, px_h, max_boxes)
        if score > best_score:
            best_score = score
            best_results = results
    return best_results or None


def page_detection_report(page, *, zoom, max_boxes=80, page_pixel_size=None):
    """Per-strategy diagnostics for debug tooling (counts and scores)."""
    geom = _pixel_scale(page, zoom, page_pixel_size)
    if geom is None:
        return {"usable": False, "reason": "empty page rect"}
    px_w, px_h, scale_x, scale_y = geom
    options, diag = _build_options(page, scale_x, scale_y, px_w, px_h)

    usable = not (diag["drawings"] < 4 and diag["words"] < 8)
    strategies = []
    best_name = None
    best_results: list[dict] = []
    best_score = 0.0
    for name, boxes in options:
        results, score = _score_option(name, boxes, px_w, px_h, max_boxes)
        strategies.append({"name": name, "raw": len(boxes), "final": len(results), "score": round(score, 2)})
        if score > best_score:
            best_score = score
            best_results = results
            best_name = name

    return {
        "usable": usable,
        "pixel_size": [px_w, px_h],
        "diagnostics": diag,
        "strategies": strategies,
        "selected": best_name,
        "results": best_results,
    }


def detect_boxes_from_pdf(
    pdf_path: Path,
    source_page_index: int,
    *,
    zoom: float = 2.0,
    max_boxes: int = 80,
    page_pixel_size: tuple[int, int] | None = None,
) -> list[dict] | None:
    """Return candidate detail boxes from a PDF page's vector layer, or ``None``."""
    fitz = _fitz()
    if fitz is None:
        return None
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None
    try:
        if source_page_index < 0 or source_page_index >= len(doc):
            return None
        page = doc[source_page_index]
        # Usability gate: scanned/flattened pages expose ~no vector paths or text.
        geom = _pixel_scale(page, zoom, page_pixel_size)
        if geom is None:
            return None
        _, _, scale_x, scale_y = geom
        if len(page.get_drawings()) < 4 and len(page.get_text("words")) < 8:
            return None
        return _detect_on_page(
            page, zoom=zoom, max_boxes=max_boxes, page_pixel_size=page_pixel_size
        )
    except Exception:
        return None
    finally:
        doc.close()
