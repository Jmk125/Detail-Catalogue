from pathlib import Path
from uuid import uuid4
import shutil
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from .pdf_tools import render_pdf_pages
from .detector import detect_candidate_detail_boxes
from .storage import project_dir, save_manifest, load_manifest, save_approved_crops
from .models import ApproveSheetRequest


Path("data/projects").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Detail Harvester MVP")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/data", StaticFiles(directory="data"), name="data")


@app.get("/", response_class=HTMLResponse)
def index():
    return Path("app/templates/index.html").read_text(encoding="utf-8")


@app.post("/api/projects")
async def create_project(
    file: UploadFile = File(...),
    project_name: str = Form(""),
    design_team: str = Form(""),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF drawing set.")

    project_id = uuid4().hex[:12]
    pdir = project_dir(project_id)
    pages_dir = pdir / "pages"
    pdir.mkdir(parents=True, exist_ok=True)

    pdf_path = pdir / "original.pdf"
    with pdf_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    pages = render_pdf_pages(pdf_path, pages_dir)

    for page in pages:
        img_path = pdir / page["image"]
        page["boxes"] = detect_candidate_detail_boxes(img_path)
        page["approved"] = False
        page["approved_box_count"] = 0

    manifest = {
        "project_id": project_id,
        "project_name": project_name,
        "design_team": design_team,
        "filename": file.filename,
        "pages": pages,
    }
    save_manifest(project_id, manifest)
    return manifest


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return load_manifest(project_id)


@app.post("/api/approve-sheet")
def approve_sheet(req: ApproveSheetRequest):
    boxes = [b.model_dump() for b in req.boxes]
    records = save_approved_crops(req.project_id, req.page_index, boxes)
    return {"saved": len(records), "records": records}


@app.get("/api/projects/{project_id}/details")
def get_details(project_id: str):
    details_path = project_dir(project_id) / "details.jsonl"
    if not details_path.exists():
        return {"details": []}

    details = []
    for line in details_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            import json
            details.append(json.loads(line))
    return {"details": details}


@app.get("/api/projects/{project_id}/download")
def download_project(project_id: str):
    pdir = project_dir(project_id)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail="Project not found.")

    zip_path = pdir.with_suffix(".zip")
    shutil.make_archive(str(pdir), "zip", pdir)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{project_id}_details.zip")
