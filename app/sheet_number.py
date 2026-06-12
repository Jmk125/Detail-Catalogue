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


def _format_rect(rect: fitz.Rect | None) -> dict[str, float] | None:
    if rect is None:
        return None
    return {"x0": round(rect.x0, 3), "y0": round(rect.y0, 3), "x1": round(rect.x1, 3), "y1": round(rect.y1, 3)}


def _word_to_debug(word: Any) -> dict[str, Any]:
    return {
        "x0": round(float(word[0]), 3),
        "y0": round(float(word[1]), 3),
        "x1": round(float(word[2]), 3),
        "y1": round(float(word[3]), 3),
        "text": str(word[4]) if len(word) >= 5 else "",
        "block": int(word[5]) if len(word) >= 6 else None,
        "line": int(word[6]) if len(word) >= 7 else None,
        "word": int(word[7]) if len(word) >= 8 else None,
    }


def _run_tesseract_raw(image_path: Path) -> dict[str, Any]:
    if shutil.which("tesseract") is None:
        return {"available": False, "stdout": "", "stderr": "", "returncode": None, "parsed": None}
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
        return {
            "available": True,
            "command": " ".join(cmd),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
            "parsed": parse_sheet_number_text(completed.stdout) if completed.returncode == 0 else None,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": True, "stdout": "", "stderr": str(exc), "returncode": None, "parsed": None}
    finally:
        processed_path.unlink(missing_ok=True)


def debug_sheet_number_read(
    pdf_path: Path,
    source_page_index: int,
    sheet_box: dict[str, Any],
    image_path: Path,
    image_width: int,
    image_height: int,
    pdf_width: float,
    pdf_height: float,
) -> dict[str, Any]:
    """Return local extraction diagnostics for the current red sheet-number box."""
    clip = _clip_from_image_box(sheet_box, image_width, image_height, pdf_width, pdf_height)
    debug: dict[str, Any] = {
        "sheet_box_image_pixels": sheet_box,
        "image_size": {"width": image_width, "height": image_height},
        "pdf_size": {"width": pdf_width, "height": pdf_height},
        "clip_pdf_points": _format_rect(clip),
        "pdf_text": {"raw": "", "parsed": None},
        "pdf_words": {"joined": "", "parsed": None, "count": 0, "items": []},
        "tesseract": {},
        "final_sheet_number": None,
        "notes": [],
    }
    if clip is None:
        debug["notes"].append("The red sheet-number box could not be mapped to a valid PDF clip rectangle.")
    else:
        doc = fitz.open(pdf_path)
        try:
            page = doc[source_page_index]
            text = page.get_text("text", clip=clip) or ""
            words = page.get_text("words", clip=clip, sort=True) or []
            joined_words = " ".join(str(word[4]) for word in words if len(word) >= 5)
            debug["pdf_text"] = {"raw": text, "parsed": parse_sheet_number_text(text)}
            debug["pdf_words"] = {
                "joined": joined_words,
                "parsed": parse_sheet_number_text(joined_words),
                "count": len(words),
                "items": [_word_to_debug(word) for word in words[:80]],
            }
            if not text.strip() and not words:
                debug["notes"].append("No selectable PDF text/words were found inside the red box; this sheet may require OCR.")
        finally:
            doc.close()
    debug["tesseract"] = _run_tesseract_raw(image_path)
    debug["final_sheet_number"] = (
        debug["pdf_text"].get("parsed")
        or debug["pdf_words"].get("parsed")
        or debug["tesseract"].get("parsed")
    )
    if not debug["final_sheet_number"]:
        debug["notes"].append("No sheet number was parsed from PDF text, PDF words, or local Tesseract OCR.")
    return debug


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
