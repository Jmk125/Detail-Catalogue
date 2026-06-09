from pathlib import Path
import json
from PIL import Image
from datetime import datetime, timezone
from .ai_tagging import tag_detail_stub


DATA_ROOT = Path("data/projects")


def project_dir(project_id: str) -> Path:
    return DATA_ROOT / project_id


def load_manifest(project_id: str) -> dict:
    path = project_dir(project_id) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(project_id: str, manifest: dict) -> None:
    p = project_dir(project_id)
    p.mkdir(parents=True, exist_ok=True)
    (p / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def save_approved_crops(project_id: str, page_index: int, boxes: list[dict]) -> list[dict]:
    pdir = project_dir(project_id)
    manifest = load_manifest(project_id)
    page = manifest["pages"][page_index]
    page_img_path = pdir / page["image"]
    crops_dir = pdir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(page_img_path)
    records = []

    for i, box in enumerate(boxes, start=1):
        x = int(round(box["x"]))
        y = int(round(box["y"]))
        w = int(round(box["w"]))
        h = int(round(box["h"]))

        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(img.width, x + w)
        y1 = min(img.height, y + h)

        if x1 <= x0 or y1 <= y0:
            continue

        crop = img.crop((x0, y0, x1, y1))
        crop_name = f"page_{page_index + 1:04d}_detail_{i:03d}.png"
        crop_rel = f"crops/{crop_name}"
        crop_path = pdir / crop_rel
        crop.save(crop_path)

        context = {
            "project_id": project_id,
            "project_name": manifest.get("project_name"),
            "design_team": manifest.get("design_team"),
            "source_filename": manifest.get("filename"),
            "page_index": page_index,
            "page_number": page["page_number"],
            "crop_box": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
        }
        ai = tag_detail_stub(crop_path, context)

        record = {
            **context,
            "sheet_image": page["image"],
            "crop_image": crop_rel,
            "source_box_id": box.get("id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            **ai,
        }
        records.append(record)

    details_path = pdir / "details.jsonl"
    with details_path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    page["approved"] = True
    page["approved_box_count"] = len(records)
    page["boxes"] = boxes
    save_manifest(project_id, manifest)
    return records
