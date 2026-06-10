from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .ai_tagging import build_ai_prompt_context, get_ai_provider
from .database import PROJECTS_ROOT, connect, json_loads, row_to_dict, utc_now
from .settings import StorageSettings, get_settings


def project_dir(project_id: str) -> Path:
    return PROJECTS_ROOT / project_id


def rel_url_path(path: Path, project_id: str) -> str:
    return path.relative_to(project_dir(project_id)).as_posix()


def get_or_create_design_team(name: str | None) -> int | None:
    clean = (name or "").strip()
    if not clean:
        return None
    now = utc_now()
    with connect() as conn:
        row = conn.execute("SELECT id FROM design_teams WHERE lower(name)=lower(?)", (clean,)).fetchone()
        if row:
            conn.execute("UPDATE design_teams SET last_used_at=? WHERE id=?", (now, row["id"]))
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO design_teams(name, created_at, last_used_at) VALUES(?, ?, ?)",
            (clean, now, now),
        )
        return int(cur.lastrowid)


def list_design_teams(q: str = "") -> list[dict[str, Any]]:
    like = f"%{q.strip()}%"
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, created_at, last_used_at
            FROM design_teams
            WHERE ? = '%%' OR name LIKE ?
            ORDER BY last_used_at DESC, name ASC
            LIMIT 50
            """,
            (like, like),
        ).fetchall()
        return [dict(r) for r in rows]


def create_project_record(project_id: str, project_name: str, design_team: str, discipline: str, settings: StorageSettings | None = None) -> None:
    settings = settings or get_settings()
    pdir = project_dir(project_id)
    (pdir / "sources").mkdir(parents=True, exist_ok=True)
    (pdir / "pages").mkdir(parents=True, exist_ok=True)
    (pdir / "crops").mkdir(parents=True, exist_ok=True)
    (pdir / "thumbs").mkdir(parents=True, exist_ok=True)
    design_team_id = get_or_create_design_team(design_team)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO projects(id, project_name, design_team_id, discipline, upload_date, status, settings_json)
            VALUES(?, ?, ?, ?, ?, 'processing', ?)
            """,
            (project_id, project_name.strip(), design_team_id, discipline or "unknown", utc_now(), json.dumps(settings.as_dict())),
        )


def add_source_file(project_id: str, source_file_id: str, filename: str, storage_rel_path: str, page_count: int) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO source_files(id, project_id, filename, storage_path, page_count, uploaded_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (source_file_id, project_id, filename, storage_rel_path, page_count, utc_now()),
        )


def add_page_record(project_id: str, source_file_id: str, global_index: int, source_page_index: int) -> int:
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO pages(project_id, source_file_id, global_index, source_page_index, page_number, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (project_id, source_file_id, global_index, source_page_index, source_page_index + 1, now, now),
        )
        return int(cur.lastrowid)


def update_page_ready(page_id: int, image_rel: str, page_info: dict[str, Any], boxes: list[dict[str, Any]]) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE pages
            SET status='ready', image_path=?, width=?, height=?, pdf_width=?, pdf_height=?, zoom=?, boxes_json=?, updated_at=?
            WHERE id=?
            """,
            (
                image_rel,
                page_info.get("width"),
                page_info.get("height"),
                page_info.get("pdf_width"),
                page_info.get("pdf_height"),
                page_info.get("zoom"),
                json.dumps(boxes),
                utc_now(),
                page_id,
            ),
        )


def update_page_failed(page_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE pages SET status='failed', error=?, updated_at=? WHERE id=?", (error, utc_now(), page_id))


def page_to_api(row: Any) -> dict[str, Any]:
    d = dict(row)
    return {
        "id": d["id"],
        "page_index": d["global_index"],
        "page_number": d["page_number"],
        "source_page_index": d["source_page_index"],
        "source_file_id": d["source_file_id"],
        "source_filename": d.get("filename"),
        "status": d["status"],
        "image": d["image_path"],
        "width": d["width"],
        "height": d["height"],
        "pdf_width": d["pdf_width"],
        "pdf_height": d["pdf_height"],
        "zoom": d["zoom"],
        "boxes": json_loads(d["boxes_json"], []),
        "approved": d["status"] == "approved",
        "approved_box_count": d["approved_box_count"],
        "error": d["error"],
    }


def get_project_manifest(project_id: str) -> dict[str, Any]:
    with connect() as conn:
        project = conn.execute(
            """
            SELECT p.*, dt.name AS design_team
            FROM projects p LEFT JOIN design_teams dt ON dt.id = p.design_team_id
            WHERE p.id=?
            """,
            (project_id,),
        ).fetchone()
        if not project:
            raise FileNotFoundError(project_id)
        pages = conn.execute(
            """
            SELECT pages.*, source_files.filename
            FROM pages JOIN source_files ON source_files.id = pages.source_file_id
            WHERE pages.project_id=?
            ORDER BY pages.global_index
            """,
            (project_id,),
        ).fetchall()
        sources = conn.execute("SELECT * FROM source_files WHERE project_id=? ORDER BY uploaded_at", (project_id,)).fetchall()
        status = get_project_status(project_id, conn)
        p = dict(project)
        return {
            "project_id": p["id"],
            "project_name": p["project_name"],
            "design_team": p["design_team"],
            "discipline": p["discipline"],
            "upload_date": p["upload_date"],
            "status": p["status"],
            "settings": json_loads(p["settings_json"], {}),
            "source_files": [dict(s) for s in sources],
            "pages": [page_to_api(r) for r in pages],
            "processing_status": status,
        }


def get_project_status(project_id: str, conn=None) -> dict[str, Any]:
    own = conn is None
    if own:
        ctx = connect()
        conn = ctx.__enter__()
    try:
        counts = {"pending": 0, "processing": 0, "ready": 0, "approved": 0, "skipped": 0, "failed": 0}
        for row in conn.execute("SELECT status, count(*) AS c FROM pages WHERE project_id=? GROUP BY status", (project_id,)):
            counts[row["status"]] = row["c"]
        ai = {"pending": 0, "running": 0, "complete": 0, "failed": 0}
        for row in conn.execute(
            """
            SELECT ai_jobs.status, count(*) AS c
            FROM ai_jobs JOIN details ON details.id = ai_jobs.detail_id
            WHERE details.project_id=? GROUP BY ai_jobs.status
            """,
            (project_id,),
        ):
            ai[row["status"]] = row["c"]
        total = sum(counts.values())
        return {
            "total_pages": total,
            "pages_ready": counts["ready"],
            "pages_processing": counts["pending"] + counts["processing"],
            "pages_approved": counts["approved"],
            "pages_skipped": counts["skipped"],
            "pages_failed": counts["failed"],
            "ai_jobs": ai,
        }
    finally:
        if own:
            ctx.__exit__(None, None, None)


def get_next_ready_page(project_id: str, after_index: int = -1) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT pages.*, source_files.filename
            FROM pages JOIN source_files ON source_files.id = pages.source_file_id
            WHERE pages.project_id=? AND pages.status='ready' AND pages.global_index>?
            ORDER BY pages.global_index LIMIT 1
            """,
            (project_id, after_index),
        ).fetchone()
        if not row:
            row = conn.execute(
                """
                SELECT pages.*, source_files.filename
                FROM pages JOIN source_files ON source_files.id = pages.source_file_id
                WHERE pages.project_id=? AND pages.status='ready'
                ORDER BY pages.global_index LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return page_to_api(row) if row else None


def cleanup_page_image(project_id: str, image_rel: str | None, settings: StorageSettings) -> None:
    if not image_rel or settings.retain_temporary_page_images or not settings.automatic_temp_cleanup:
        return
    path = project_dir(project_id) / image_rel
    if path.exists():
        path.unlink()


def save_crop_image(crop: Image.Image, path: Path, settings: StorageSettings) -> None:
    fmt = settings.normalized_format()
    if crop.width > settings.crop_max_width:
        ratio = settings.crop_max_width / crop.width
        crop = crop.resize((settings.crop_max_width, max(1, int(crop.height * ratio))), Image.Resampling.LANCZOS)
    save_kwargs: dict[str, Any] = {}
    if fmt in {"webp", "jpeg", "jpg"}:
        save_kwargs["quality"] = settings.image_quality
        save_kwargs["optimize"] = True
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt in {"jpeg", "webp"} and crop.mode not in {"RGB", "L"}:
        crop = crop.convert("RGB")
    crop.save(path, format=fmt.upper() if fmt != "jpg" else "JPEG", **save_kwargs)


def save_thumbnail(crop_path: Path, thumb_path: Path) -> None:
    with Image.open(crop_path) as img:
        img.thumbnail((420, 300), Image.Resampling.LANCZOS)
        if img.mode not in {"RGB", "L"}:
            img = img.convert("RGB")
        img.save(thumb_path, format="WEBP", quality=70, optimize=True)


def save_approved_crops(project_id: str, page_id: int, boxes: list[dict[str, Any]], settings: StorageSettings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    with connect() as conn:
        page = conn.execute(
            """
            SELECT pages.*, source_files.filename, projects.project_name, projects.discipline, dt.name AS design_team
            FROM pages
            JOIN source_files ON source_files.id = pages.source_file_id
            JOIN projects ON projects.id = pages.project_id
            LEFT JOIN design_teams dt ON dt.id = projects.design_team_id
            WHERE pages.id=? AND pages.project_id=?
            """,
            (page_id, project_id),
        ).fetchone()
        if not page:
            raise FileNotFoundError("Page not found")
        if page["status"] not in {"ready", "approved"}:
            raise ValueError("Page is not ready for approval")
        image_rel = page["image_path"]

    if not image_rel:
        raise FileNotFoundError("Rendered page image is missing")
    page_img_path = project_dir(project_id) / image_rel
    if not page_img_path.exists():
        raise FileNotFoundError("Rendered page image has already been cleaned up")

    records = []
    crop_ext = settings.extension()
    with Image.open(page_img_path) as img:
        for i, box in enumerate(boxes, start=1):
            x = int(round(box["x"])); y = int(round(box["y"])); w = int(round(box["w"])); h = int(round(box["h"]))
            x0 = max(0, x); y0 = max(0, y); x1 = min(img.width, x + w); y1 = min(img.height, y + h)
            if x1 <= x0 or y1 <= y0:
                continue
            detail_id = uuid4().hex[:16]
            crop = img.crop((x0, y0, x1, y1))
            crop_rel = f"crops/page_{page['global_index'] + 1:04d}_detail_{i:03d}_{detail_id}.{crop_ext}"
            thumb_rel = f"thumbs/page_{page['global_index'] + 1:04d}_detail_{i:03d}_{detail_id}.webp"
            crop_path = project_dir(project_id) / crop_rel
            thumb_path = project_dir(project_id) / thumb_rel
            save_crop_image(crop, crop_path, settings)
            save_thumbnail(crop_path, thumb_path)
            now = utc_now()
            job_id = uuid4().hex[:16]
            crop_box = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO details(id, project_id, page_id, source_file_id, crop_image_path, thumbnail_path,
                        crop_box_json, source_box_id, discipline, ai_status, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (detail_id, project_id, page_id, page["source_file_id"], crop_rel, thumb_rel, json.dumps(crop_box), box.get("id"), page["discipline"] or "unknown", now, now),
                )
                conn.execute(
                    "INSERT INTO ai_jobs(id, detail_id, status, provider, created_at, updated_at) VALUES(?, ?, 'pending', 'stub', ?, ?)",
                    (job_id, detail_id, now, now),
                )
            records.append({"id": detail_id, "crop_image": crop_rel, "thumbnail": thumb_rel, "ai_status": "pending", "page_id": page_id, "page_index": page["global_index"], "page_number": page["page_number"]})

    with connect() as conn:
        conn.execute(
            "UPDATE pages SET status='approved', boxes_json=?, approved_box_count=?, updated_at=? WHERE id=?",
            (json.dumps(boxes), len(records), utc_now(), page_id),
        )
    cleanup_page_image(project_id, image_rel, settings)
    return records


def skip_page(project_id: str, page_id: int, settings: StorageSettings | None = None) -> None:
    settings = settings or get_settings()
    with connect() as conn:
        row = conn.execute("SELECT image_path FROM pages WHERE project_id=? AND id=?", (project_id, page_id)).fetchone()
        if not row:
            raise FileNotFoundError("Page not found")
        conn.execute("UPDATE pages SET status='skipped', updated_at=? WHERE id=?", (utc_now(), page_id))
    cleanup_page_image(project_id, row["image_path"], settings)


def process_pending_ai_jobs(limit: int = 20) -> int:
    processed = 0
    with connect() as conn:
        jobs = conn.execute(
            """
            SELECT ai_jobs.*, details.crop_image_path, details.project_id, details.id AS detail_id,
                   pages.page_number, source_files.filename, projects.project_name, projects.discipline,
                   dt.name AS design_team
            FROM ai_jobs
            JOIN details ON details.id = ai_jobs.detail_id
            JOIN pages ON pages.id = details.page_id
            JOIN source_files ON source_files.id = details.source_file_id
            JOIN projects ON projects.id = details.project_id
            LEFT JOIN design_teams dt ON dt.id = projects.design_team_id
            WHERE ai_jobs.status='pending'
            ORDER BY ai_jobs.created_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    for job in jobs:
        started = utc_now()
        with connect() as conn:
            conn.execute("UPDATE ai_jobs SET status='running', started_at=?, updated_at=? WHERE id=?", (started, started, job["id"]))
            conn.execute("UPDATE details SET ai_status='running', updated_at=? WHERE id=?", (started, job["detail_id"]))
        try:
            provider = get_ai_provider()
            crop_path = project_dir(job["project_id"]) / job["crop_image_path"]
            context = build_ai_prompt_context({
                "project_name": job["project_name"],
                "design_team": job["design_team"],
                "source_pdf_filename": job["filename"],
                "page_number": job["page_number"],
                "known_discipline": job["discipline"],
                "crop_image": crop_path,
            })
            result = provider.tag_detail(crop_path, context)
            tags = [str(t).strip() for t in result.get("tags", []) if str(t).strip()]
            completed = utc_now()
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE details SET detail_title=?, detail_number=?, sheet_number=?, discipline=?, csi_divisions_json=?,
                        tags_json=?, summary=?, assembly_system_type=?, searchable_description=?, confidence_score=?,
                        warnings_json=?, ai_status='complete', updated_at=? WHERE id=?
                    """,
                    (
                        result.get("detail_title"), result.get("detail_number"), result.get("sheet_number"), result.get("discipline") or "unknown",
                        json.dumps(result.get("csi_divisions", [])), json.dumps(tags), result.get("summary"), result.get("assembly_system_type"),
                        result.get("searchable_description"), result.get("confidence_score"), json.dumps(result.get("warnings", [])), completed, job["detail_id"],
                    ),
                )
                conn.execute("UPDATE ai_jobs SET status='complete', completed_at=?, updated_at=? WHERE id=?", (completed, completed, job["id"]))
                for tag in tags:
                    conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
                    tag_id = conn.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()["id"]
                    conn.execute("INSERT OR IGNORE INTO detail_tags(detail_id, tag_id) VALUES(?, ?)", (job["detail_id"], tag_id))
            processed += 1
        except Exception as exc:
            now = utc_now()
            with connect() as conn:
                conn.execute("UPDATE ai_jobs SET status='failed', error=?, updated_at=? WHERE id=?", (str(exc), now, job["id"]))
                conn.execute("UPDATE details SET ai_status='failed', updated_at=? WHERE id=?", (now, job["detail_id"]))
    return processed


def detail_row_to_api(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["crop_image"] = d.pop("crop_image_path")
    d["thumbnail"] = d.pop("thumbnail_path")
    d["crop_box"] = json_loads(d.pop("crop_box_json", None), {})
    d["csi_divisions"] = json_loads(d.pop("csi_divisions_json", None), [])
    d["tags"] = json_loads(d.pop("tags_json", None), [])
    d["warnings"] = json_loads(d.pop("warnings_json", None), [])
    return d


def list_details(project_id: str | None = None, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    clauses = []
    params: list[Any] = []
    if project_id:
        clauses.append("details.project_id=?"); params.append(project_id)
    if filters.get("project"):
        clauses.append("projects.project_name LIKE ?"); params.append(f"%{filters['project']}%")
    if filters.get("design_team"):
        clauses.append("dt.name LIKE ?"); params.append(f"%{filters['design_team']}%")
    if filters.get("discipline"):
        clauses.append("details.discipline=?"); params.append(filters["discipline"])
    if filters.get("tag"):
        clauses.append("details.tags_json LIKE ?"); params.append(f"%{filters['tag']}%")
    if filters.get("csi"):
        clauses.append("details.csi_divisions_json LIKE ?"); params.append(f"%{filters['csi']}%")
    if filters.get("q"):
        q = f"%{filters['q']}%"
        clauses.append("(details.detail_title LIKE ? OR details.summary LIKE ? OR details.searchable_description LIKE ? OR details.detail_number LIKE ? OR details.sheet_number LIKE ?)")
        params.extend([q, q, q, q, q])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT details.*, projects.project_name, dt.name AS design_team, source_files.filename AS source_filename, pages.page_number
            FROM details
            JOIN projects ON projects.id = details.project_id
            LEFT JOIN design_teams dt ON dt.id = projects.design_team_id
            JOIN source_files ON source_files.id = details.source_file_id
            JOIN pages ON pages.id = details.page_id
            {where}
            ORDER BY details.created_at DESC
            LIMIT 200
            """,
            params,
        ).fetchall()
        return [detail_row_to_api(r) for r in rows]


def get_detail(detail_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT details.*, projects.project_name, dt.name AS design_team, source_files.filename AS source_filename, pages.page_number
            FROM details
            JOIN projects ON projects.id = details.project_id
            LEFT JOIN design_teams dt ON dt.id = projects.design_team_id
            JOIN source_files ON source_files.id = details.source_file_id
            JOIN pages ON pages.id = details.page_id
            WHERE details.id=?
            """,
            (detail_id,),
        ).fetchone()
        return detail_row_to_api(row) if row else None


def list_library_facets() -> dict[str, Any]:
    with connect() as conn:
        return {
            "projects": [dict(r) for r in conn.execute("SELECT id, project_name FROM projects ORDER BY upload_date DESC LIMIT 100")],
            "design_teams": [dict(r) for r in conn.execute("SELECT id, name FROM design_teams ORDER BY name")],
            "disciplines": [r["discipline"] for r in conn.execute("SELECT DISTINCT discipline FROM details WHERE discipline IS NOT NULL ORDER BY discipline")],
            "tags": [r["name"] for r in conn.execute("SELECT name FROM tags ORDER BY name LIMIT 200")],
        }


def make_project_zip(project_id: str) -> Path:
    pdir = project_dir(project_id)
    zip_base = pdir.with_suffix("")
    shutil.make_archive(str(zip_base), "zip", pdir)
    return zip_base.with_suffix(".zip")
