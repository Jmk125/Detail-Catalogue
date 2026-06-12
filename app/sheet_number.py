from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw, ImageFont, ImageOps

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
        "template_ocr": {},
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
    debug["template_ocr"] = debug_template_ocr(image_path)
    debug["tesseract"] = _run_tesseract_raw(image_path)
    debug["final_sheet_number"] = (
        debug["pdf_text"].get("parsed")
        or debug["pdf_words"].get("parsed")
        or debug["tesseract"].get("parsed")
        or debug["template_ocr"].get("parsed")
    )
    if not debug["final_sheet_number"]:
        debug["notes"].append("No sheet number was parsed from PDF text, PDF words, local Tesseract OCR, or built-in template OCR.")
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



_TEMPLATE_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-"
_TEMPLATE_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
]


def _cv2_module():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None, None
    return cv2, np


def _tight_binary_crop(array: Any) -> Any | None:
    cv2, _ = _cv2_module()
    if cv2 is None:
        return None
    coords = cv2.findNonZero(array)
    if coords is None:
        return None
    x, y, w, h = cv2.boundingRect(coords)
    return array[y:y + h, x:x + w]


def _resize_for_ocr(array: Any, width: int = 48, height: int = 72) -> Any:
    cv2, np = _cv2_module()
    if cv2 is None or np is None:
        return array
    h, w = array.shape[:2]
    if h <= 0 or w <= 0:
        return np.zeros((height, width), dtype=np.uint8)
    scale = min((width - 6) / w, (height - 6) / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(array, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width), dtype=np.uint8)
    x0 = (width - new_w) // 2
    y0 = (height - new_h) // 2
    canvas[y0:y0 + new_h, x0:x0 + new_w] = resized
    return canvas


def _template_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _build_ocr_templates() -> dict[str, list[Any]]:
    cv2, np = _cv2_module()
    if cv2 is None or np is None:
        return {}
    templates: dict[str, list[Any]] = {char: [] for char in _TEMPLATE_CHARS}
    for font_path in _TEMPLATE_FONT_PATHS:
        for size in (42, 56, 72, 96):
            font = _template_font(font_path, size)
            for char in _TEMPLATE_CHARS:
                img = Image.new("L", (size * 2, size * 2), 255)
                draw = ImageDraw.Draw(img)
                bbox = draw.textbbox((0, 0), char, font=font)
                draw.text((10 - bbox[0], 10 - bbox[1]), char, font=font, fill=0)
                arr = np.array(img)
                _, binary = cv2.threshold(arr, 180, 255, cv2.THRESH_BINARY_INV)
                crop = _tight_binary_crop(binary)
                if crop is not None:
                    templates[char].append(_resize_for_ocr(crop))
    return templates


_OCR_TEMPLATES: dict[str, list[Any]] | None = None


def _get_ocr_templates() -> dict[str, list[Any]]:
    global _OCR_TEMPLATES
    if _OCR_TEMPLATES is None:
        _OCR_TEMPLATES = _build_ocr_templates()
    return _OCR_TEMPLATES


def _sheet_number_components(image_path: Path) -> list[dict[str, Any]]:
    cv2, np = _cv2_module()
    if cv2 is None or np is None:
        return []
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    scale = max(1, int(round(360 / max(1, image.shape[0]))))
    if scale > 1:
        image = cv2.resize(image, (image.shape[1] * scale, image.shape[0] * scale), interpolation=cv2.INTER_CUBIC)
    image = cv2.GaussianBlur(image, (3, 3), 0)
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    image_area = binary.shape[0] * binary.shape[1]
    comps: list[dict[str, Any]] = []
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < max(8, image_area * 0.00003):
            continue
        if w > binary.shape[1] * 0.95 or h > binary.shape[0] * 0.95:
            continue
        comps.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "area": int(area), "image": (labels[y:y + h, x:x + w] == label).astype("uint8") * 255})
    if not comps:
        return []
    median_h = sorted(c["h"] for c in comps)[len(comps) // 2]
    substantial = [c for c in comps if c["h"] >= median_h * 0.35 or c["w"] >= c["h"] * 1.8]
    if not substantial:
        substantial = comps
    y_center = sorted((c["y"] + c["h"] / 2 for c in substantial))[len(substantial) // 2]
    line = [c for c in substantial if abs((c["y"] + c["h"] / 2) - y_center) <= max(median_h, binary.shape[0] * 0.18)]
    return sorted(line, key=lambda c: c["x"])


def _classify_component(component: dict[str, Any], median_digit_height: float) -> tuple[str | None, float]:
    cv2, np = _cv2_module()
    if cv2 is None or np is None:
        return None, 0.0
    w = component["w"]
    h = component["h"]
    if h <= max(10, median_digit_height * 0.45) and w >= h * 1.5:
        return "-", 0.95
    templates = _get_ocr_templates()
    sample = _resize_for_ocr(component["image"])
    best_char: str | None = None
    best_score = -1.0
    sample_float = (sample > 0).astype("float32")
    for char, char_templates in templates.items():
        if char == "-":
            continue
        for template in char_templates:
            template_float = (template > 0).astype("float32")
            intersection = float((sample_float * template_float).sum())
            union = float(((sample_float + template_float) > 0).sum()) or 1.0
            score = intersection / union
            if score > best_score:
                best_score = score
                best_char = char
    return best_char, best_score


def read_sheet_number_with_template_ocr(image_path: Path) -> str | None:
    """Best-effort built-in OCR for high-contrast sheet numbers when PDF text and Tesseract are unavailable."""
    components = _sheet_number_components(image_path)
    if not components:
        return None
    digit_like_heights = [c["h"] for c in components if c["h"] > c["w"] * 0.8]
    heights = digit_like_heights or [c["h"] for c in components]
    median_h = sorted(heights)[len(heights) // 2]
    chars: list[str] = []
    for component in components:
        char, score = _classify_component(component, float(median_h))
        if not char:
            continue
        if char != "-" and score < 0.18:
            continue
        chars.append(char)
    if not chars:
        return None
    return parse_sheet_number_text("".join(chars))


def debug_template_ocr(image_path: Path) -> dict[str, Any]:
    components = _sheet_number_components(image_path)
    heights = [c["h"] for c in components if c["h"] > c["w"] * 0.8] or [c["h"] for c in components] or [0]
    median_h = sorted(heights)[len(heights) // 2]
    items = []
    chars = []
    for component in components:
        char, score = _classify_component(component, float(median_h))
        if char and (char == "-" or score >= 0.18):
            chars.append(char)
        items.append({"x": component["x"], "y": component["y"], "w": component["w"], "h": component["h"], "char": char, "score": round(score, 3)})
    raw = "".join(chars)
    return {"available": bool(_cv2_module()[0]), "raw": raw, "parsed": parse_sheet_number_text(raw), "components": items}


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
