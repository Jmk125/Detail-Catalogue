from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

DISCIPLINES = {
    "architectural",
    "structural",
    "civil",
    "mechanical",
    "electrical",
    "plumbing",
    "fire protection",
    "technology/security",
    "unknown",
}


class AITaggingProvider(ABC):
    name = "base"

    @abstractmethod
    def tag_detail(self, image_path: Path, context: dict[str, Any]) -> dict[str, Any]:
        """Return structured catalog metadata for one approved detail crop."""


class StubTaggingProvider(AITaggingProvider):
    name = "stub"

    def tag_detail(self, image_path: Path, context: dict[str, Any]) -> dict[str, Any]:
        known_discipline = (context.get("known_discipline") or "unknown").lower()
        if known_discipline not in DISCIPLINES:
            known_discipline = "unknown"
        return {
            "detail_title": None,
            "detail_number": None,
            "sheet_number": None,
            "discipline": known_discipline,
            "csi_divisions": [],
            "tags": ["untagged"],
            "summary": "AI tagging provider is not configured yet; this detail is saved and searchable by project/source metadata.",
            "assembly_system_type": None,
            "searchable_description": (
                f"Approved construction detail crop from {context.get('project_name') or 'unnamed project'}, "
                f"source PDF {context.get('source_pdf_filename')}, page {context.get('page_number')}."
            ),
            "confidence_score": 0.0,
            "warnings": ["AI provider not configured; metadata is placeholder only."],
        }


def get_ai_provider() -> AITaggingProvider:
    # Clean provider seam for a later local/offline model or hosted vision API.
    return StubTaggingProvider()


def build_ai_prompt_context(context: dict[str, Any]) -> dict[str, Any]:
    """Context fields sent with the crop image to any future AI provider."""
    return {
        "project_name": context.get("project_name"),
        "design_firm_or_team": context.get("design_team"),
        "source_pdf_filename": context.get("source_pdf_filename"),
        "page_number": context.get("page_number"),
        "known_discipline": context.get("known_discipline") or "unknown",
        "crop_image": str(context.get("crop_image")),
        "requested_schema": {
            "likely_detail_title": "string|null",
            "detail_number": "string|null",
            "sheet_number": "string|null",
            "discipline": sorted(DISCIPLINES),
            "csi_divisions": "array[string]",
            "tags": "array[string]",
            "short_summary": "string|null",
            "assembly_system_type": "string|null",
            "searchable_plain_english_description": "string|null",
            "confidence_score": "number 0..1",
            "warnings": "array[string]",
        },
    }
