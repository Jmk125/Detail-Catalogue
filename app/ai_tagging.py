from pathlib import Path
from typing import Dict, Any


def tag_detail_stub(image_path: Path, context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "detail_title": None,
        "detail_number": None,
        "sheet_number": None,
        "tags": [],
        "csi": [],
        "summary": None,
        "ai_status": "not_configured",
    }
