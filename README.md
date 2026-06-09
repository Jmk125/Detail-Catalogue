# Detail Harvester

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
- Review crop boxes with the existing zoom, pan, move, edge/corner resize, delete-key, and overlap-merge behavior.
- Approve or skip one sheet at a time; approval saves crops immediately and queues durable AI jobs.
- Browse/search the local detail library by project, design team, discipline, CSI, tag, and free text.


## API keys / `.env`

You do **not** need an `.env` file for the current local stub AI tagger; approved crops and placeholder catalogue records still work without a key. For a real OpenAI-backed tagger, keep the key server-side and provide it as an environment variable. This repo now includes `.env.example`; copy it to `.env` for local use and set `OPENAI_API_KEY=...`. The app loads simple `.env` values at startup without overriding variables you already exported in the shell.

```bash
cp .env.example .env
# edit .env and set OPENAI_API_KEY
```

The real `.env` file is ignored by git so secrets are not committed.

## AI provider seam

`app/ai_tagging.py` defines an `AITaggingProvider` interface and a stub provider. A future provider can use the prepared prompt context (project name, design team, source PDF filename, page number, known discipline, and crop image path) and return structured metadata.

## Reverse proxy / future Node direction

The app avoids hard-coded machine paths and can be run behind Nginx, Caddy, or a future Node/Express server. A reverse proxy can forward `/` and `/api/*` to Uvicorn on port 8000 while serving TLS/static assets externally. Keep `DETAIL_HARVESTER_DATA_ROOT` on a persistent volume if proxying or containerizing.
