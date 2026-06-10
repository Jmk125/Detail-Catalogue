from __future__ import annotations

from .env import load_env_file

load_env_file()

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DATA_ROOT = Path(os.getenv("DETAIL_HARVESTER_DATA_ROOT", "data")).resolve()
PROJECTS_ROOT = DATA_ROOT / "projects"
DB_PATH = DATA_ROOT / "detail_catalogue.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_data_dirs() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect() -> Iterable[sqlite3.Connection]:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    ensure_data_dirs()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS design_teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                project_name TEXT,
                design_team_id INTEGER REFERENCES design_teams(id),
                discipline TEXT DEFAULT 'unknown',
                upload_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                settings_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS project_designers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                discipline TEXT NOT NULL,
                firm_name TEXT NOT NULL,
                UNIQUE(project_id, discipline)
            );

            CREATE TABLE IF NOT EXISTS source_files (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 0,
                uploaded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                source_file_id TEXT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
                global_index INTEGER NOT NULL,
                source_page_index INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                image_path TEXT,
                width INTEGER,
                height INTEGER,
                pdf_width REAL,
                pdf_height REAL,
                zoom REAL,
                boxes_json TEXT NOT NULL DEFAULT '[]',
                sheet_box_json TEXT,
                approved_box_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, global_index)
            );

            CREATE TABLE IF NOT EXISTS details (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                source_file_id TEXT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
                crop_image_path TEXT NOT NULL,
                thumbnail_path TEXT,
                crop_box_json TEXT NOT NULL,
                source_box_id TEXT,
                detail_title TEXT,
                detail_number TEXT,
                sheet_number TEXT,
                discipline TEXT DEFAULT 'unknown',
                csi_divisions_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                summary TEXT,
                assembly_system_type TEXT,
                searchable_description TEXT,
                confidence_score REAL,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                bookmarked INTEGER NOT NULL DEFAULT 0,
                ai_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_jobs (
                id TEXT PRIMARY KEY,
                detail_id TEXT NOT NULL REFERENCES details(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                provider TEXT NOT NULL DEFAULT 'stub',
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS detail_tags (
                detail_id TEXT NOT NULL REFERENCES details(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY(detail_id, tag_id)
            );

            CREATE INDEX IF NOT EXISTS idx_pages_project_status ON pages(project_id, status);
            CREATE INDEX IF NOT EXISTS idx_details_project ON details(project_id);
            CREATE INDEX IF NOT EXISTS idx_details_discipline ON details(discipline);
            CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);
            """
        )
        _ensure_column(conn, "details", "bookmarked", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "details", "notes", "TEXT")
        _ensure_column(conn, "pages", "sheet_box_json", "TEXT")
        _ensure_column(conn, "projects", "last_sheet_box_json", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_details_bookmarked ON details(bookmarked)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_project_designers_project ON project_designers(project_id)")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
