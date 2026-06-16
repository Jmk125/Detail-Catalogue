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

Signals, strongest to weakest:
  1. Explicit rectangles  -> boxed details are read straight from ``re`` items
     (and 4-line closed rectangles), no morphology guessing.
  2. Vector + text clustering -> for unboxed details, exact line/text bounding
     boxes are grouped by spatial proximity. Cleaner than raster blobbing because
     the coordinates are exact and text comes with its own location.
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
    """True when a drawing path is a plain (possibly unfilled) axis-aligned rectangle.

    Detail borders are often drawn as four line segments rather than a single
    ``re`` instruction. Curves disqualify the path; otherwise we accept it when its
    line endpoints collapse to just two distinct x and two distinct y positions.
    """
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
    """Count value clusters, treating values within ``tol`` points as the same."""
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
        # Thin-allowed bbox so single horizontal/vertical strokes still cluster.
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


def _candidates_from_rectangles(rects, width, height):
    kept = [
        box
        for box in rects
        if _detail_sized(box, width, height) and not _looks_like_title_block(box, width, height)
    ]
    if not kept:
        return []
    # Coalesce double-stroked / concentric borders that describe the same detail,
    # using a tiny gap so genuinely separate adjacent borders stay distinct.
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


def _postprocess(boxes, width, height):
    if not boxes:
        return []
    density = _layout_density(boxes, width, height, raw_count=len(boxes))
    merged = _merge_labels_under_details(boxes, width, height, density=density)
    merged = _dedupe_boxes(merged)
    return _remove_composite_boxes(merged)


def _detect_on_page(page, *, zoom, max_boxes, page_pixel_size):
    pr = page.rect
    pw, ph = float(pr.width), float(pr.height)
    if pw <= 0 or ph <= 0:
        return None

    if page_pixel_size:
        px_w, px_h = int(page_pixel_size[0]), int(page_pixel_size[1])
        scale_x = px_w / pw
        scale_y = px_h / ph
    else:
        scale_x = scale_y = float(zoom)
        px_w = int(round(pw * scale_x))
        px_h = int(round(ph * scale_y))
    if px_w <= 0 or px_h <= 0:
        return None

    rects, ink, words = _page_geometry(page, scale_x, scale_y, px_w, px_h)

    # Usability gate: scanned/flattened pages expose almost no vector paths or
    # text. Bail so the caller falls back to the raster pipeline.
    if len(ink) < 4 and len(words) < 8:
        return None

    rect_candidates = _candidates_from_rectangles(rects, px_w, px_h)
    # Several merge gaps from generous to tight; scoring picks the cleanest. Even
    # the widest gap stays well under typical detail-to-detail spacing so adjacent
    # details are not chained together.
    cluster_passes = (
        (max(10, int(px_w * 0.030)), max(10, int(px_h * 0.030))),
        (max(6, int(px_w * 0.018)), max(6, int(px_h * 0.018))),
        (max(4, int(px_w * 0.010)), max(4, int(px_h * 0.010))),
    )

    option_sets: list[list[list[int]]] = []
    if rect_candidates:
        option_sets.append(rect_candidates)
    for gap_x, gap_y in cluster_passes:
        clusters = _candidates_from_clusters(ink, words, px_w, px_h, gap_x, gap_y)
        if clusters:
            option_sets.append(clusters)
            if rect_candidates:
                option_sets.append(rect_candidates + clusters)
    if not option_sets:
        return None

    # Score every option with the same quality metric the raster path uses and
    # keep the best, so a fragmented/overlapping option cannot win on count.
    best_results: list[dict] = []
    best_score = 0.0
    for boxes in option_sets:
        processed = _postprocess(boxes, px_w, px_h)
        results = _format_results(processed, px_w, px_h, max_boxes)
        for r in results:
            r["source"] = "vector"
        score = _score_results(results, px_w, px_h)
        if score > best_score:
            best_score = score
            best_results = results
    return best_results or None


def detect_boxes_from_pdf(
    pdf_path: Path,
    source_page_index: int,
    *,
    zoom: float = 2.0,
    max_boxes: int = 80,
    page_pixel_size: tuple[int, int] | None = None,
) -> list[dict] | None:
    """Return candidate detail boxes from a PDF page's vector layer, or ``None``.

    ``None`` means the page has no usable vector content (or PyMuPDF is missing),
    signalling the caller to fall back to raster detection. Coordinates are in the
    rendered PNG's pixel space.
    """
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
        return _detect_on_page(
            page, zoom=zoom, max_boxes=max_boxes, page_pixel_size=page_pixel_size
        )
    except Exception:
        return None
    finally:
        doc.close()
