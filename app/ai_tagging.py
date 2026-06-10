from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .env import load_env_file

load_env_file()

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

    def read_sheet_number(self, image_path: Path) -> str | None:
        """Read a sheet number from a small crop of the sheet's title block. Optional override."""
        return None


def _normalize_result(result: dict[str, Any], known_discipline: str = "unknown") -> dict[str, Any]:
    discipline = (result.get("discipline") or known_discipline or "unknown").lower()
    if discipline not in DISCIPLINES:
        discipline = "unknown"
    return {
        "detail_title": result.get("detail_title") or result.get("likely_detail_title"),
        "detail_number": result.get("detail_number"),
        "sheet_number": result.get("sheet_number"),
        "discipline": discipline,
        "csi_divisions": result.get("csi_divisions") or result.get("csi") or [],
        "tags": result.get("tags") or [],
        "summary": result.get("summary") or result.get("short_summary"),
        "assembly_system_type": result.get("assembly_system_type"),
        "searchable_description": result.get("searchable_description") or result.get("searchable_plain_english_description"),
        "confidence_score": result.get("confidence_score"),
        "warnings": result.get("warnings") or [],
    }


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


class OpenAIResponsesTaggingProvider(AITaggingProvider):
    name = "openai"

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("AI_TAGGING_MODEL", "gpt-5.2").strip() or "gpt-5.2"
        if not self.api_key:
            raise RuntimeError("AI_TAGGING_PROVIDER=openai requires OPENAI_API_KEY in the server environment or .env file.")

    def tag_detail(self, image_path: Path, context: dict[str, Any]) -> dict[str, Any]:
        image_data_url = _image_data_url(image_path)
        prompt = (
            "You are cataloguing a construction drawing detail crop. Return JSON only. "
            "If text is unreadable or the crop is incomplete, include clear warnings and lower confidence.\n\n"
            f"Context JSON:\n{json.dumps(context, indent=2)}"
        )
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_data_url, "detail": "high"},
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "detail_catalog_metadata",
                    "strict": True,
                    "schema": _detail_schema(),
                }
            },
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI tagging request failed: HTTP {exc.code} {error_body}") from exc

        output_text = body.get("output_text") or _extract_output_text(body)
        if not output_text:
            raise RuntimeError("OpenAI response did not include output_text metadata JSON.")
        return _normalize_result(json.loads(output_text), context.get("known_discipline") or "unknown")

    def read_sheet_number(self, image_path: Path) -> str | None:
        image_data_url = _image_data_url(image_path)
        prompt = (
            "This image is a small crop from the title block area of a construction drawing sheet. "
            "Return only the sheet number printed in this crop (e.g. 'A-501', 'S-201'), with no other text. "
            "If no sheet number is visible, return an empty string."
        )
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_data_url, "detail": "high"},
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError:
            return None
        text = (body.get("output_text") or _extract_output_text(body) or "").strip()
        return text or None


def _extract_output_text(response: dict[str, Any]) -> str | None:
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    return None


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _detail_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "detail_title",
            "detail_number",
            "sheet_number",
            "discipline",
            "csi_divisions",
            "tags",
            "summary",
            "assembly_system_type",
            "searchable_description",
            "confidence_score",
            "warnings",
        ],
        "properties": {
            "detail_title": _nullable({"type": "string"}),
            "detail_number": _nullable({"type": "string"}),
            "sheet_number": _nullable({"type": "string"}),
            "discipline": {"type": "string", "enum": sorted(DISCIPLINES)},
            "csi_divisions": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "summary": _nullable({"type": "string"}),
            "assembly_system_type": _nullable({"type": "string"}),
            "searchable_description": _nullable({"type": "string"}),
            "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }


def get_ai_provider() -> AITaggingProvider:
    provider = os.getenv("AI_TAGGING_PROVIDER", "stub").strip().lower()
    if provider == "openai":
        return OpenAIResponsesTaggingProvider()
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
