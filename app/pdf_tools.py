from pathlib import Path
import fitz


def render_pdf_pages(pdf_path: Path, pages_dir: Path, zoom: float = 2.0) -> list[dict]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    rendered = []
    matrix = fitz.Matrix(zoom, zoom)

    for page_index in range(len(doc)):
        page = doc[page_index]
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out_path = pages_dir / f"page_{page_index + 1:04d}.png"
        pix.save(str(out_path))
        rendered.append({
            "page_index": page_index,
            "page_number": page_index + 1,
            "image": f"pages/{out_path.name}",
            "width": pix.width,
            "height": pix.height,
            "pdf_width": float(page.rect.width),
            "pdf_height": float(page.rect.height),
            "zoom": zoom,
        })

    doc.close()
    return rendered
