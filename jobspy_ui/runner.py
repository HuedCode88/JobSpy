"""Background job runner.

One worker thread drains a queue, so runs are serialized. That is deliberate:
job boards rate-limit hard, and two concurrent runs from one IP make it worse.

Public API (the scheduler in the stretch goal only needs `enqueue`):
    runner.enqueue(profile_id, trigger="manual") -> run_id
    runner.request_cancel(run_id)
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import traceback
from datetime import datetime

import pandas as pd

import db
import exporter
from config import validate_config

LOG_CAP = 60_000


class ActiveRunExists(Exception):
    pass


class _RunLogHandler(logging.Handler):
    """Collects JobSpy's own log lines for the current run (runs are serialized, so no cross-talk)."""

    def __init__(self, sink: list[str]):
        super().__init__(level=logging.INFO)
        self.sink = sink
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record):
        if "jobspy" in record.name.lower():
            self.sink.append(self.format(record))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "profile"


def _scrape_term(cfg: dict, term: str, log) -> pd.DataFrame:
    """Scrape one term. scrape_jobs() raises if ANY board errors, discarding the others' results,
    so on failure we retry board-by-board and keep whatever works."""
    from jobspy import scrape_jobs  # lazy: UI still starts if jobspy isn't installed

    def call(sites):
        return scrape_jobs(
            site_name=sites,
            search_term=term,
            location=cfg["location"] or None,
            results_wanted=cfg["results_wanted"],
            hours_old=cfg["hours_old"],
            country_indeed=cfg["country_indeed"],
            distance=cfg["distance"],
            is_remote=cfg["is_remote"],
            job_type=cfg["job_type"],
            easy_apply=cfg["easy_apply"],
            fetch_description=cfg["fetch_description"],
            description_format=cfg["description_format"],
            enforce_annual_salary=cfg["enforce_annual_salary"],
            proxies=cfg["proxies"] or None,
            verbose=1,
        )

    try:
        return call(cfg["sites"])
    except Exception as exc:
        log(f"  ! combined search failed ({exc}); retrying each board separately")
    frames = []
    for site in cfg["sites"]:
        try:
            frames.append(call([site]))
        except Exception as exc:
            log(f"  ! {site} failed: {exc}")
    frames = [f for f in frames if f is not None and not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


class Runner:
    def __init__(self):
        self._q: queue.Queue[int] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="jobspy-runner", daemon=True)
        self._started = False

    def start(self):
        if not self._started:
            self._started = True
            self._thread.start()

    # ---- public ------------------------------------------------------
    def enqueue(self, profile_id: int, trigger: str = "manual") -> int:
        active = db.query(
            "SELECT id FROM runs WHERE profile_id=? AND status IN ('queued','running')",
            (profile_id,), one=True,
        )
        if active:
            raise ActiveRunExists(f"Run #{active['id']} is already queued or running for this profile")
        run_id = db.execute(
            "INSERT INTO runs (profile_id, trigger, status, created_at) VALUES (?,?,?,?)",
            (profile_id, trigger, "queued", db.now()),
        )
        self._q.put(run_id)
        return run_id

    def request_cancel(self, run_id: int):
        db.execute("UPDATE runs SET cancel_requested=1 WHERE id=? AND status IN ('queued','running')", (run_id,))

    # ---- worker ------------------------------------------------------
    def _loop(self):
        while True:
            run_id = self._q.get()
            try:
                self._execute(run_id)
            except Exception:  # never let the worker die
                db.execute(
                    "UPDATE runs SET status='failed', error=?, finished_at=? WHERE id=?",
                    (traceback.format_exc()[-2000:], db.now(), run_id),
                )

    def _execute(self, run_id: int):
        run = db.query("SELECT * FROM runs WHERE id=?", (run_id,), one=True)
        if not run or run["status"] != "queued":
            return  # cancelled while queued, or run row deleted
        if run["cancel_requested"]:
            db.execute("UPDATE runs SET status='cancelled', finished_at=? WHERE id=?", (db.now(), run_id))
            return
        profile = db.query("SELECT * FROM profiles WHERE id=?", (run["profile_id"],), one=True)
        if not profile:
            return
        cfg = validate_config(json.loads(profile["config"]))
        terms = cfg["search_terms"]

        lines: list[str] = []

        def log(msg: str):
            lines.append(f"[{datetime.now():%H:%M:%S}] {msg}")

        def flush(**fields):
            fields["log"] = "\n".join(lines)[-LOG_CAP:]
            sets = ", ".join(f"{k}=?" for k in fields)
            db.execute(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), run_id))

        handler = _RunLogHandler(lines)
        logging.getLogger().addHandler(handler)
        try:
            db.execute(
                "UPDATE runs SET status='running', started_at=?, progress_total=?, config_snapshot=? WHERE id=?",
                (db.now(), len(terms), json.dumps(cfg), run_id),
            )
            frames: list[pd.DataFrame] = []
            for i, term in enumerate(terms):
                if db.query("SELECT cancel_requested c FROM runs WHERE id=?", (run_id,), one=True)["c"]:
                    log("Cancelled by user")
                    flush(status="cancelled", finished_at=db.now(), message="Cancelled")
                    return
                log(f"Searching ({i + 1}/{len(terms)}): {term!r}")
                flush(message=f"Searching “{term}” ({i + 1}/{len(terms)})", progress_done=i)
                try:
                    df = _scrape_term(cfg, term, log)
                except Exception as exc:
                    log(f"  ! term failed: {exc}")
                    df = pd.DataFrame()
                if df is not None and not df.empty:
                    df = df.copy()
                    df["search_term"] = term
                    frames.append(df)
                log(f"  -> {0 if df is None else len(df)} rows")
                flush(progress_done=i + 1)

            all_jobs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if not all_jobs.empty:
                before = len(all_jobs)
                all_jobs = all_jobs.drop_duplicates(subset=["job_url"]).reset_index(drop=True)
                log(f"Removed {before - len(all_jobs)} duplicate listings")

            seen = {
                r["job_url"]
                for r in db.query("SELECT job_url FROM seen_jobs WHERE profile_id=?", (profile["id"],))
            }
            if all_jobs.empty:
                new_count = 0
            else:
                all_jobs.insert(0, "is_new", ~all_jobs["job_url"].isin(seen))
                new_count = int(all_jobs["is_new"].sum())
                with db.connect() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO seen_jobs (profile_id, job_url, first_seen_run_id) VALUES (?,?,?)",
                        [(profile["id"], u, run_id) for u in all_jobs["job_url"] if u],
                    )
                if cfg["only_new"]:
                    all_jobs = all_jobs[all_jobs["is_new"]].reset_index(drop=True)
                    log(f"only_new is on: keeping {len(all_jobs)} new listings")

            rel_dir = f"{_slug(profile['name'])}/run{run_id}_{datetime.now():%Y%m%d_%H%M%S}"
            out = db.OUTPUT_DIR / rel_dir
            out.mkdir(parents=True, exist_ok=True)
            if not all_jobs.empty:
                exporter.write_csv(all_jobs, out / "jobs.csv")
                exporter.write_xlsx(all_jobs, out / "jobs.xlsx")
            all_jobs.to_json(out / "results.json", orient="records", date_format="iso", force_ascii=False)
            log(f"Done: {len(all_jobs)} jobs ({new_count} new)")
            flush(status="done", finished_at=db.now(), job_count=len(all_jobs), new_count=new_count,
                  out_dir=rel_dir, message=f"{len(all_jobs)} jobs ({new_count} new)")
        except Exception:
            log("FAILED:\n" + traceback.format_exc())
            flush(status="failed", finished_at=db.now(), error=traceback.format_exc()[-1500:],
                  message="Run failed")
        finally:
            logging.getLogger().removeHandler(handler)


runner = Runner()
