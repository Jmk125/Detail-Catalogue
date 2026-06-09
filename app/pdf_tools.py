from __future__ import annotations

from pathlib import Path
from typing import Iterator

import fitz


def count_pdf_pages(pdf_path: Path) -> int:
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_pdf_page(pdf_path: Path, pages_dir: Path, source_page_index: int, output_stem: str, zoom: float = 2.0) -> dict:
    pages_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        page = doc[source_page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        out_path = pages_dir / f"{output_stem}.png"
        pix.save(str(out_path))
        return {
            "page_index": source_page_index,
            "page_number": source_page_index + 1,
            "image_path": out_path,
            "width": pix.width,
            "height": pix.height,
            "pdf_width": float(page.rect.width),
            "pdf_height": float(page.rect.height),
            "zoom": zoom,
        }
    finally:
        doc.close()


def render_pdf_pages(pdf_path: Path, pages_dir: Path, zoom: float = 2.0) -> list[dict]:
    rendered = []
    for page_index in range(count_pdf_pages(pdf_path)):
        info = render_pdf_page(pdf_path, pages_dir, page_index, f"page_{page_index + 1:04d}", zoom)
        rendered.append({
            "page_index": page_index,
            "page_number": page_index + 1,
            "image": f"pages/{info['image_path'].name}",
            "width": info["width"],
            "height": info["height"],
            "pdf_width": info["pdf_width"],
            "pdf_height": info["pdf_height"],
            "zoom": zoom,
        })
    return rendered
