from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .database import DATA_ROOT, init_db
from .models import ApproveSheetRequest, DetailUpdateRequest, RedetectSheetRequest, SheetNumberDebugRequest, SheetNumberPreviewRequest, SkipSheetRequest
from .pdf_tools import count_pdf_pages
from .processing import enqueue_project_processing, recover_stalled_processing
from .settings import get_settings
from .storage import (
    add_page_record,
    add_pages_for_source,
    add_source_file,
    ai_scan_status,
    background_activity_status,
    create_project_record,
    delete_design_team_record,
    delete_detail,
    delete_project_record,
    debug_sheet_number,
    get_detail,
    get_next_ready_page,
    get_project_manifest,
    get_project_status,
    list_design_teams,
    list_details,
    details_for_project_sheet,
    list_settings_entities,
    list_library_facets,
    list_project_options,
    make_project_zip,
    process_all_pending_ai_jobs,
    process_pending_ai_jobs,
    project_dir,
    queue_unscanned_details,
    redetect_page_boxes,
    preview_sheet_number,
    rescan_detail,
    save_approved_crops,
    skip_page,
    update_detail,
)

init_db()
recover_stalled_processing()

app = FastAPI(title="Detail Catalogue")
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


@app.get("/api/background-status")
def background_status():
    return background_activity_status()


@app.get("/api/projects")
def list_projects():
    return {"projects": list_project_options()}


@app.post("/api/projects")
async def create_project(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    project_name: str = Form(""),
    design_team: str = Form(""),
    discipline: str = Form("unknown"),
    designers: str = Form("[]"),
):
    if not files:
        raise HTTPException(status_code=400, detail="Upload one or more PDF drawing sets.")
    bad = [f.filename for f in files if not (f.filename or "").lower().endswith(".pdf")]
    if bad:
        raise HTTPException(status_code=400, detail=f"Only PDF files are supported: {', '.join(bad)}")

    try:
        designer_list = json.loads(designers) if designers else []
    except json.JSONDecodeError:
        designer_list = []

    project_id = uuid4().hex[:12]
    create_project_record(project_id, project_name, design_team, discipline, get_settings(), designer_list)
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


@app.post("/api/projects/init")
async def init_project(
    project_name: str = Form(""),
    design_team: str = Form(""),
    discipline: str = Form("unknown"),
    designers: str = Form("[]"),
):
    try:
        designer_list = json.loads(designers) if designers else []
    except json.JSONDecodeError:
        designer_list = []
    project_id = uuid4().hex[:12]
    create_project_record(project_id, project_name, design_team, discipline, get_settings(), designer_list)
    return get_project_manifest(project_id)


@app.post("/api/projects/{project_id}/sources")
async def add_project_source(project_id: str, file: UploadFile = File(...), process: bool = Form(True)):
    pdir = project_dir(project_id)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail="Project not found.")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail=f"Only PDF files are supported: {file.filename}")

    source_id = uuid4().hex[:12]
    safe_name = Path(file.filename or f"source_{source_id}.pdf").name
    source_rel = f"sources/{source_id}_{safe_name}"
    source_path = pdir / source_rel
    with source_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        page_count = count_pdf_pages(source_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read {safe_name}: {exc}") from exc

    add_source_file(project_id, source_id, safe_name, source_rel, page_count)
    add_pages_for_source(project_id, source_id, page_count)
    if process:
        enqueue_project_processing(project_id)
    return get_project_manifest(project_id)


@app.post("/api/projects/{project_id}/process")
def process_project(project_id: str):
    try:
        manifest = get_project_manifest(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not manifest["pages"]:
        raise HTTPException(status_code=400, detail="Upload at least one PDF before processing.")
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


@app.post("/api/redetect-sheet")
def redetect_sheet(req: RedetectSheetRequest):
    try:
        boxes = redetect_page_boxes(req.project_id, req.page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"boxes": boxes, "processing_status": get_project_status(req.project_id)}


@app.post("/api/preview-sheet-number")
def preview_sheet_number_endpoint(req: SheetNumberPreviewRequest):
    try:
        sheet_number = preview_sheet_number(req.project_id, req.page_id, req.sheet_box)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"sheet_number": sheet_number, "existing_sheet": details_for_project_sheet(req.project_id, sheet_number, exclude_page_id=req.page_id)}


@app.get("/api/projects/{project_id}/sheet-details")
def existing_sheet_details(project_id: str, sheet_number: str = Query(""), exclude_page_id: int | None = Query(None)):
    return details_for_project_sheet(project_id, sheet_number, exclude_page_id=exclude_page_id)


@app.post("/api/debug-sheet-number")
def debug_sheet_number_endpoint(req: SheetNumberDebugRequest):
    try:
        return debug_sheet_number(req.project_id, req.page_id, req.sheet_box)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/approve-sheet")
def approve_sheet(req: ApproveSheetRequest, background_tasks: BackgroundTasks):
    boxes = [b.model_dump() for b in req.boxes]
    try:
        records = save_approved_crops(req.project_id, req.page_id, boxes, sheet_box=req.sheet_box, sheet_number_override=req.sheet_number_override)
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
    project_ids: str = "",
    design_team: str = "",
    design_teams: str = "",
    discipline: str = "",
    disciplines: str = "",
    csi: str = "",
    tag: str = "",
    q: str = "",
    bookmarked: str = "",
    sheet: str = "",
):
    return {"details": list_details(filters={"project": project, "project_ids": project_ids, "design_team": design_team, "design_teams": design_teams, "discipline": discipline, "disciplines": disciplines, "csi": csi, "tag": tag, "q": q, "bookmarked": bookmarked, "sheet": sheet})}


@app.get("/api/library/facets")
def library_facets():
    return list_library_facets()


@app.get("/api/manage/entities")
def manage_entities():
    return list_settings_entities()


@app.delete("/api/manage/projects/{project_id}")
def delete_project_manage(project_id: str, payload: dict = Body(...)):
    try:
        result = delete_project_record(
            project_id,
            delete_items=bool(payload.get("delete_items")),
            confirm_name=str(payload.get("confirm_name") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail="Project not found.")
    return result


@app.delete("/api/manage/design-teams/{design_team_id}")
def delete_design_team_manage(design_team_id: int, payload: dict = Body(...)):
    try:
        result = delete_design_team_record(
            design_team_id,
            delete_items=bool(payload.get("delete_items")),
            confirm_name=str(payload.get("confirm_name") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail="Design firm not found.")
    return result


@app.post("/api/library/scan-unscanned")
def scan_unscanned(background_tasks: BackgroundTasks):
    queued = queue_unscanned_details()
    if queued:
        background_tasks.add_task(process_all_pending_ai_jobs)
    return {"queued": queued, "status": ai_scan_status()}


@app.get("/api/library/scan-status")
def scan_status():
    return ai_scan_status()


@app.get("/api/details/{detail_id}")
def detail(detail_id: str):
    item = get_detail(detail_id)
    if not item:
        raise HTTPException(status_code=404, detail="Detail not found.")
    return item




@app.put("/api/details/{detail_id}")
def update_detail_endpoint(detail_id: str, req: DetailUpdateRequest):
    item = update_detail(detail_id, req.model_dump(exclude_unset=True))
    if not item:
        raise HTTPException(status_code=404, detail="Detail not found.")
    return item




@app.post("/api/details/{detail_id}/rescan")
def rescan_detail_endpoint(detail_id: str):
    try:
        proposal = rescan_detail(detail_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not proposal:
        raise HTTPException(status_code=404, detail="Detail not found.")
    return {"proposal": proposal}


@app.delete("/api/details/{detail_id}")
def delete_detail_endpoint(detail_id: str):
    if not delete_detail(detail_id):
        raise HTTPException(status_code=404, detail="Detail not found.")
    return {"deleted": True}


@app.get("/api/projects/{project_id}/download")
def download_project(project_id: str):
    pdir = project_dir(project_id)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail="Project not found.")
    zip_path = make_project_zip(project_id)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{project_id}_details.zip")
