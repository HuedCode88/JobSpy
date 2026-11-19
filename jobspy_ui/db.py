"""SQLite storage. One file (data/app.db), no ORM, a connection per call (thread-safe)."""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = DATA_DIR / "outputs"
DB_PATH = DATA_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    config TEXT NOT NULL,                 -- JSON, see config.DEFAULT_CONFIG
    schedule_enabled INTEGER NOT NULL DEFAULT 0,   -- stretch goal: not acted on yet
    schedule_cron TEXT DEFAULT '',                 -- stretch goal, e.g. "0 7 * * *"
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    trigger TEXT NOT NULL DEFAULT 'manual',        -- manual | schedule
    status TEXT NOT NULL DEFAULT 'queued',         -- queued | running | done | failed | cancelled
    started_at TEXT, finished_at TEXT,
    created_at TEXT NOT NULL,
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    message TEXT DEFAULT '',
    log TEXT DEFAULT '',
    job_count INTEGER DEFAULT 0,
    new_count INTEGER DEFAULT 0,
    out_dir TEXT,                                  -- relative to data/outputs
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    config_snapshot TEXT                           -- JSON of the config actually used
);
CREATE TABLE IF NOT EXISTS seen_jobs (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    job_url TEXT NOT NULL,
    first_seen_run_id INTEGER,
    PRIMARY KEY (profile_id, job_url)
);
CREATE INDEX IF NOT EXISTS idx_runs_profile ON runs(profile_id, id DESC);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def query(sql, params=(), one=False):
    with connect() as c:
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    with connect() as c:
        return c.execute(sql, params).lastrowid


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        # A crash/restart can leave runs stuck in queued/running; they can't resume.
        c.execute(
            "UPDATE runs SET status='failed', error='Interrupted by app restart', finished_at=? "
            "WHERE status IN ('queued','running')",
            (now(),),
        )
        seeds: list[tuple[str, str, dict]] = []

        if not getattr(sys, "frozen", False):
            from config import DEFAULT_CONFIG, validate_config
            from importer import import_script

            scrapers_dir = Path(__file__).resolve().parent.parent / "Personal Scrapers"
            if scrapers_dir.exists():
                for scraper in sorted(scrapers_dir.glob("*.py")):
                    if scraper.name == "__init__.py":
                        continue
                    try:
                        imported = import_script(scraper.read_text(encoding="utf-8"))
                        seed = validate_config({**DEFAULT_CONFIG, **imported["config"]})
                        seeds.append((
                            scraper.stem,
                            f"Imported from Personal Scrapers/{scraper.name}",
                            seed,
                        ))
                    except (OSError, UnicodeDecodeError, ValueError) as exc:
                        print(f"Could not import {scraper.name}: {exc}")

        for name, description, seed in seeds:
            existing = c.execute("SELECT id FROM profiles WHERE name=?", (name,)).fetchone()
            if existing is None:
                t = now()
                c.execute(
                    "INSERT INTO profiles (name, description, config, created_at, updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (name, description, json.dumps(seed), t, t),
                )
