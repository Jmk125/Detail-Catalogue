from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageOps

DIGIT_RUN = r"\d(?:\s*\d){0,3}"
SHEET_NUMBER_RE = re.compile(
    r"(?<![A-Z0-9])"
    r"(?:[A-Z]{1,4}\s*[-_.]?\s*)?"
    rf"{DIGIT_RUN}"
    rf"(?:(?:\s*[-–—−_]\s*|\.){DIGIT_RUN}){{0,3}}"
    r"[A-Z]?"
    r"(?![A-Z0-9])",
    re.IGNORECASE,
)
COMPACT_NUMERIC_RE = re.compile(r"(?<![A-Z0-9])0(?:\s*\d){4,7}[A-Z]?(?![A-Z0-9])", re.IGNORECASE)
BARE_NUMBER_RE = re.compile(r"^\d{1,4}(?:\.\d{1,3})?[A-Z]?$", re.IGNORECASE)
LABEL_RE = re.compile(r"\b(?:sheet|sht)\s*(?:no\.?|number|#)?\b", re.IGNORECASE)


def normalize_sheet_number(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().upper()
    text = re.sub(r"^[^A-Z0-9]+|[^A-Z0-9]+$", "", text)
    text = re.sub(r"[–—−]", "-", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("_", "-")
    text = re.sub(r"-{2,}", "-", text)
    if re.fullmatch(r"0\d{4,7}[A-Z]?", text):
        suffix = text[2:]
        text = f"{text[:2]}-{suffix}"
    text = re.sub(r"([A-Z]+)[.]?(\d)", r"\1\2", text)
    text = re.sub(r"([A-Z]+)(\d{2,4}[A-Z]?)$", r"\1-\2", text)
    return text or None


def _candidate_score(candidate: str, source_text: str, start: int) -> tuple[int, int, int]:
    normalized = normalize_sheet_number(candidate) or ""
    score = 0
    if re.search(r"[A-Z]", normalized):
        score += 50
    if re.search(r"[-.]", normalized):
        score += 10
    if re.match(r"^\d{1,4}-\d{1,4}[A-Z]?$", normalized):
        score += 25
    if re.match(r"^[A-Z]{1,4}-?\d", normalized):
        score += 20
    if BARE_NUMBER_RE.match(normalized):
        score -= 25
    prefix = source_text[max(0, start - 30):start]
    if LABEL_RE.search(prefix):
        score += 40
    # Prefer compact sheet-number-like tokens over long incidental numbers.
    score -= max(0, len(normalized) - 8)
    return (score, -start, -len(normalized))


def parse_sheet_number_text(text: str | None) -> str | None:
    """Return the best sheet-number-looking token from OCR/PDF text."""
    if not text:
        return None
    searchable = text.upper().replace("\n", " ")
    matches: list[tuple[tuple[int, int, int], str]] = []
    for match in list(SHEET_NUMBER_RE.finditer(searchable)) + list(COMPACT_NUMERIC_RE.finditer(searchable)):
        candidate = normalize_sheet_number(match.group(0))
        if not candidate:
            continue
        # Ignore common title-block noise that is unlikely to be a sheet number.
        if candidate in {"0", "00", "000", "0000"}:
            continue
        matches.append((_candidate_score(candidate, searchable, match.start()), candidate))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def _clip_from_image_box(sheet_box: dict[str, Any], image_width: int, image_height: int, pdf_width: float, pdf_height: float) -> fitz.Rect | None:
    try:
        x = float(sheet_box["x"])
        y = float(sheet_box["y"])
        w = float(sheet_box["w"])
        h = float(sheet_box["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or image_width <= 0 or image_height <= 0 or pdf_width <= 0 or pdf_height <= 0:
        return None
    sx = pdf_width / image_width
    sy = pdf_height / image_height
    # Give clipped text extraction a small tolerance for hand-positioned boxes.
    pad_x = min(w * 0.08, 18) * sx
    pad_y = min(h * 0.08, 18) * sy
    rect = fitz.Rect((x * sx) - pad_x, (y * sy) - pad_y, ((x + w) * sx) + pad_x, ((y + h) * sy) + pad_y)
    page_rect = fitz.Rect(0, 0, pdf_width, pdf_height)
    return rect & page_rect


def read_sheet_number_from_pdf_text(pdf_path: Path, source_page_index: int, sheet_box: dict[str, Any], image_width: int, image_height: int, pdf_width: float, pdf_height: float) -> str | None:
    """Read the sheet number from selectable PDF text inside the red sheet box."""
    clip = _clip_from_image_box(sheet_box, image_width, image_height, pdf_width, pdf_height)
    if clip is None:
        return None
    doc = fitz.open(pdf_path)
    try:
        page = doc[source_page_index]
        text = page.get_text("text", clip=clip) or ""
        result = parse_sheet_number_text(text)
        if result:
            return result
        # Some PDFs fragment title-block text oddly; words sorted in reading order
        # often reconstruct enough text for the same parser to find the value.
        words = page.get_text("words", clip=clip, sort=True) or []
        return parse_sheet_number_text(" ".join(str(word[4]) for word in words if len(word) >= 5))
    finally:
        doc.close()


def _preprocess_for_tesseract(image_path: Path) -> Path:
    image = Image.open(image_path)
    try:
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        scale = max(1, int(round(900 / max(1, image.height))))
        if scale > 1:
            image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        image = image.point(lambda p: 255 if p > 180 else 0)
        tmp = tempfile.NamedTemporaryFile(prefix="sheet_number_", suffix=".png", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        image.save(tmp_path, format="PNG")
        return tmp_path
    finally:
        image.close()


def read_sheet_number_with_tesseract(image_path: Path) -> str | None:
    """Use local Tesseract OCR when available; never calls an AI service."""
    if shutil.which("tesseract") is None:
        return None
    processed_path = _preprocess_for_tesseract(image_path)
    try:
        cmd = [
            "tesseract",
            str(processed_path),
            "stdout",
            "--psm",
            "6",
            "-c",
            "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.# ",
        ]
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=20)
        if completed.returncode != 0:
            return None
        return parse_sheet_number_text(completed.stdout)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        processed_path.unlink(missing_ok=True)
