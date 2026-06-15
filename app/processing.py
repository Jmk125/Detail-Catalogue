from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .database import connect, utc_now
from .detector import detect_candidate_detail_boxes
from .pdf_tools import render_pdf_page
from .settings import get_settings
from .storage import process_all_pending_ai_jobs, project_dir, update_page_failed, update_page_ready

logger = logging.getLogger(__name__)

_PROCESSOR = ThreadPoolExecutor(max_workers=int(os.getenv("DETAIL_PROCESSING_WORKERS", "1")))


def enqueue_project_processing(project_id: str) -> None:
    _PROCESSOR.submit(process_project_pages, project_id)


def recover_stalled_processing() -> None:
    """Re-queue processing for projects left with pending/processing pages after a crash or restart."""
    with connect() as conn:
        conn.execute("UPDATE pages SET status='pending', updated_at=? WHERE status='processing'", (utc_now(),))
        conn.execute("UPDATE ai_jobs SET status='pending', updated_at=? WHERE status='running'", (utc_now(),))
        conn.execute("UPDATE details SET ai_status='pending', updated_at=? WHERE ai_status='running'", (utc_now(),))
        project_ids = [
            row["project_id"]
            for row in conn.execute("SELECT DISTINCT project_id FROM pages WHERE status='pending'")
        ]
    for project_id in project_ids:
        enqueue_project_processing(project_id)
    if project_ids:
        _PROCESSOR.submit(process_all_pending_ai_jobs)


def process_project_pages(project_id: str) -> None:
    settings = get_settings()
    with connect() as conn:
        pages = conn.execute(
            """
            SELECT pages.id, pages.global_index, pages.source_page_index, source_files.storage_path
            FROM pages JOIN source_files ON source_files.id = pages.source_file_id
            WHERE pages.project_id=? AND pages.status='pending'
            ORDER BY pages.global_index
            """,
            (project_id,),
        ).fetchall()
    for page in pages:
        try:
            with connect() as conn:
                conn.execute("UPDATE pages SET status='processing', updated_at=? WHERE id=?", (utc_now(), page["id"]))
            pdir = project_dir(project_id)
            pdf_path = pdir / page["storage_path"]
            output_stem = f"page_{page['global_index'] + 1:04d}"
            info = render_pdf_page(pdf_path, pdir / "pages", page["source_page_index"], output_stem, zoom=settings.render_zoom)
            image_rel = f"pages/{Path(info['image_path']).name}"
            image_path = pdir / image_rel
            print(
                f"[detail-detect] project={project_id} page={page['global_index'] + 1} "
                f"image={image_rel} starting",
                flush=True,
            )
            boxes = detect_candidate_detail_boxes(image_path)
            print(
                f"[detail-detect] project={project_id} page={page['global_index'] + 1} "
                f"boxes={len(boxes)} image={image_rel}",
                flush=True,
            )
            update_page_ready(page["id"], image_rel, info, boxes)
        except Exception as exc:  # durable per-page failure lets rest of batch continue
            logger.exception("Failed to process project %s page %s", project_id, page["id"])
            update_page_failed(page["id"], str(exc))
    with connect() as conn:
        remaining = conn.execute(
            "SELECT count(*) AS c FROM pages WHERE project_id=? AND status IN ('pending', 'processing')",
            (project_id,),
        ).fetchone()["c"]
        conn.execute("UPDATE projects SET status=? WHERE id=?", ("ready" if remaining == 0 else "processing", project_id))
