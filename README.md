# Detail Catalogue

Local FastAPI web app for extracting, reviewing, saving, and searching construction drawing details from PDF drawing sets.

## Run

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>.

## Storage

Set `DETAIL_HARVESTER_DATA_ROOT` to move all app data (SQLite database, uploaded PDFs, temporary page renders, approved crops, thumbnails) outside the repository:

```bash
export DETAIL_HARVESTER_DATA_ROOT=/path/to/detail-harvester-data
```

Storage-conscious defaults are intended for Raspberry Pi/SD-card use:

- Crop format: `DETAIL_CROP_FORMAT=webp`
- Crop max width: `DETAIL_CROP_MAX_WIDTH=1800`
- Crop quality: `DETAIL_IMAGE_QUALITY=82`
- Temporary page images are deleted after approval/skipping by default: `DETAIL_AUTO_TEMP_CLEANUP=true`
- Retain page renders only when explicitly requested: `DETAIL_RETAIN_PAGE_IMAGES=false`

Approved detail crops and thumbnails are the permanent visual artifacts. Full rendered sheets are treated as temporary review cache files.

## Features

- Upload one PDF or multiple PDFs as one import batch/project.
- Store project metadata, design-team records, source PDF filenames, pages, details, AI jobs, tags, and tag joins in SQLite.
- Render/detect pages incrementally in a background task so review can start as soon as the first sheet is ready.
- Detect candidate detail boxes vector-first: for true vector PDFs the detector reads detail border rectangles and title/scale text directly from the PDF (via PyMuPDF) instead of guessing from rasterized pixels, and falls back to the raster (OpenCV/Pillow) pipeline for scanned/flattened pages. Both passes are scored with the same quality metric and the stronger result wins. Inspect a page's vector layer and detection output with `python debug_vector.py <pdf> --page N --overlay`; inspect the raster pipeline with `python debug_detect.py <page_image> --overlay`.
- Review crop boxes with the existing zoom, pan, move, edge/corner resize, delete-key, and overlap-merge behavior.
- Approve or skip one sheet at a time; approval saves crops immediately and queues durable AI jobs.
- Sheet-number capture from the red review box is local and quota-free: the app first reads selectable PDF text in that box and can fall back to built-in high-contrast template OCR and then a local `tesseract` command if installed. The review sidebar previews the currently read sheet number before approval, lets you force a manual sheet-number override when OCR is wrong, and its debug button shows raw PDF text, PDF words, OCR output, and parser results for troubleshooting discipline-specific sheets.
- Browse/search the local detail library by project, design team, discipline, CSI, tag, and free text.


## API keys / `.env`

You do **not** need an `.env` file for the local stub AI tagger; approved crops and placeholder catalogue records still work without a key. For real OpenAI-backed tagging, keep the key server-side and provide it as an environment variable. This repo includes `.env.example`; copy it to `.env`, set `OPENAI_API_KEY=...`, and make sure `AI_TAGGING_PROVIDER=openai` instead of `stub`.

```bash
cp .env.example .env
# edit .env:
# OPENAI_API_KEY=sk-...
# AI_TAGGING_PROVIDER=openai
# AI_TAGGING_MODEL=gpt-5.2
```

If `AI_TAGGING_PROVIDER=stub`, the app intentionally uses placeholder metadata even if an API key is present. The real `.env` file is ignored by git so secrets are not committed.

## AI provider seam

`app/ai_tagging.py` defines an `AITaggingProvider` interface, a stub provider, and an OpenAI Responses API provider. The OpenAI provider sends the prepared prompt context (project name, design team, source PDF filename, page number, known discipline, and crop image path) with the crop image and stores structured metadata. Sheet-number capture from the red box is intentionally outside this provider path, so approving sheets can continue to populate `sheet_number` even when AI tagging is disabled, delayed, or quota-limited.

## Reverse proxy / future Node direction

The app avoids hard-coded machine paths and can be run behind Nginx, Caddy, or a future Node/Express server. A reverse proxy can forward `/` and `/api/*` to Uvicorn on port 8000 while serving TLS/static assets externally. Keep `DETAIL_HARVESTER_DATA_ROOT` on a persistent volume if proxying or containerizing.
