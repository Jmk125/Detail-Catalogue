from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .database import DATA_ROOT, init_db
from .models import ApproveSheetRequest, SkipSheetRequest
from .pdf_tools import count_pdf_pages
from .processing import enqueue_project_processing
from .settings import get_settings
from .storage import (
    add_page_record,
    add_source_file,
    create_project_record,
    get_detail,
    get_next_ready_page,
    get_project_manifest,
    get_project_status,
    list_design_teams,
    list_details,
    list_library_facets,
    make_project_zip,
    process_pending_ai_jobs,
    project_dir,
    save_approved_crops,
    skip_page,
)

init_db()

app = FastAPI(title="Detail Harvester")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/data", StaticFiles(directory=str(DATA_ROOT)), name="data")


@app.get("/", response_class=HTMLResponse)
def index():
    return Path("app/templates/index.html").read_text(encoding="utf-8")


@app.get("/api/settings")
def settings():
    return get_settings().as_dict()


@app.get("/api/design-teams")
def design_teams(q: str = ""):
    return {"design_teams": list_design_teams(q)}


@app.post("/api/projects")
async def create_project(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    project_name: str = Form(""),
    design_team: str = Form(""),
    discipline: str = Form("unknown"),
):
    if not files:
        raise HTTPException(status_code=400, detail="Upload one or more PDF drawing sets.")
    bad = [f.filename for f in files if not (f.filename or "").lower().endswith(".pdf")]
    if bad:
        raise HTTPException(status_code=400, detail=f"Only PDF files are supported: {', '.join(bad)}")

    project_id = uuid4().hex[:12]
    create_project_record(project_id, project_name, design_team, discipline, get_settings())
    pdir = project_dir(project_id)
    global_index = 0

    for upload in files:
        source_id = uuid4().hex[:12]
        safe_name = Path(upload.filename or f"source_{source_id}.pdf").name
        source_rel = f"sources/{source_id}_{safe_name}"
        source_path = pdir / source_rel
        with source_path.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        try:
            page_count = count_pdf_pages(source_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read {safe_name}: {exc}") from exc
        add_source_file(project_id, source_id, safe_name, source_rel, page_count)
        for source_page_index in range(page_count):
            add_page_record(project_id, source_id, global_index, source_page_index)
            global_index += 1

    enqueue_project_processing(project_id)
    return get_project_manifest(project_id)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    try:
        return get_project_manifest(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.")


@app.get("/api/projects/{project_id}/status")
def project_status(project_id: str):
    try:
        manifest = get_project_manifest(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"processing_status": manifest["processing_status"], "pages": manifest["pages"], "status": manifest["status"]}


@app.get("/api/projects/{project_id}/next-ready")
def next_ready_page(project_id: str, after_index: int = Query(-1)):
    page = get_next_ready_page(project_id, after_index)
    return {"page": page, "processing_status": get_project_status(project_id)}


@app.post("/api/approve-sheet")
def approve_sheet(req: ApproveSheetRequest, background_tasks: BackgroundTasks):
    boxes = [b.model_dump() for b in req.boxes]
    try:
        records = save_approved_crops(req.project_id, req.page_id, boxes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    background_tasks.add_task(process_pending_ai_jobs)
    return {"saved": len(records), "records": records, "processing_status": get_project_status(req.project_id)}


@app.post("/api/skip-sheet")
def skip_sheet(req: SkipSheetRequest):
    try:
        skip_page(req.project_id, req.page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"skipped": True, "processing_status": get_project_status(req.project_id)}


@app.get("/api/projects/{project_id}/details")
def get_details(project_id: str):
    return {"details": list_details(project_id)}


@app.get("/api/library/search")
def library_search(
    project: str = "",
    design_team: str = "",
    discipline: str = "",
    csi: str = "",
    tag: str = "",
    q: str = "",
):
    return {"details": list_details(filters={"project": project, "design_team": design_team, "discipline": discipline, "csi": csi, "tag": tag, "q": q})}


@app.get("/api/library/facets")
def library_facets():
    return list_library_facets()


@app.get("/api/details/{detail_id}")
def detail(detail_id: str):
    item = get_detail(detail_id)
    if not item:
        raise HTTPException(status_code=404, detail="Detail not found.")
    return item


@app.get("/api/projects/{project_id}/download")
def download_project(project_id: str):
    pdir = project_dir(project_id)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail="Project not found.")
    zip_path = make_project_zip(project_id)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{project_id}_details.zip")
