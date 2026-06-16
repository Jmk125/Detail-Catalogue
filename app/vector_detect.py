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
from pathlib import Path

from .detector import (
    _dedupe_boxes,
    _format_results,
    _layout_density,
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


def _heading_anchors(spans, width, height):
    """Group heading-sized text into per-detail anchors.

    Detail titles/numbers are typically a larger font than dimension/annotation
    text. We take spans whose size is clearly above the body-text size and merge
    neighbouring ones into title groups; each group anchors one detail.
    """
    sizes = [s for _, _, s in spans if s > 0]
    if len(sizes) < 3:
        return [], {"body_size": 0.0, "threshold": 0.0, "heading_spans": 0}
    body_size = _body_text_size(sizes)
    threshold = max(body_size * 1.3, body_size + 2.0)
    heads = [box for (_text, box, s) in spans if s >= threshold]
    diag = {"body_size": body_size, "threshold": threshold, "heading_spans": len(heads)}
    if len(heads) < 2:
        return [], diag
    groups = _merge_boxes(heads, dx=max(8, int(width * 0.020)), dy=max(6, int(height * 0.010)))
    anchors = [(gx + gw / 2.0, gy + gh / 2.0, [gx, gy, gw, gh]) for gx, gy, gw, gh in groups]
    diag["anchor_groups"] = len(anchors)
    return anchors, diag


def _candidates_from_anchors(anchors, ink, width, height):
    """Assign each ink box to its nearest title anchor; bound each anchor's ink."""
    if len(anchors) < 2:
        return []
    sheet_area = max(1, width * height)
    centers = [(ax, ay) for ax, ay, _ in anchors]
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
    merge_labels = not name.startswith("text_anchors")
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
