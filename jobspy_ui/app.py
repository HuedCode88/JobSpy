"""Flask app: JSON API + static single-page UI. Run with `python app.py`."""
from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path

import psutil

from flask import Flask, abort, jsonify, request, send_from_directory

# Allow `python jobspy_ui\app.py` to use the repository's local jobspy package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import DEFAULT_CONFIG, DESCRIPTION_FORMATS, JOB_TYPES, SITES, validate_config
from importer import import_script
from runner import ActiveRunExists, runner

STATIC_DIR = (
    Path(sys._MEIPASS) / "static"
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent / "static"
)
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.json.sort_keys = False


def _process_matches_port_owner(process: "psutil.Process", frozen: bool) -> bool:
    name = process.name().lower()
    if frozen:
        if name in ("jobspyconsole.exe", "jobspyconsole"):
            return True
        if name not in ("python.exe", "python"):
            return False
        try:
            command = " ".join(process.cmdline()).lower().replace("\\", "/")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        return "jobspy_ui/app.py" in command
    return name in ("jobspyconsole.exe", "jobspyconsole")


def _stop_port_owner(port: int, frozen: bool) -> bool:
    for connection in psutil.net_connections(kind="tcp"):
        if (
            connection.status == psutil.CONN_LISTEN
            and connection.laddr
            and connection.laddr.port == port
            and connection.pid
        ):
            try:
                process = psutil.Process(connection.pid)
                if not _process_matches_port_owner(process, frozen):
                    return False
                process.terminate()
                process.wait(timeout=5)
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                return False
    return False


# ---------- helpers ----------
def err(message, status=400):
    return jsonify({"error": message}), status


def profile_out(row: dict) -> dict:
    last = db.query("SELECT * FROM runs WHERE profile_id=? ORDER BY id DESC LIMIT 1", (row["id"],), one=True)
    return {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "config": validate_config(json.loads(row["config"])),
        "schedule_enabled": bool(row["schedule_enabled"]), "schedule_cron": row["schedule_cron"],
        "updated_at": row["updated_at"],
        "last_run": run_out(last) if last else None,
    }


def run_out(r: dict, with_log=False) -> dict:
    out = {k: r[k] for k in (
        "id", "profile_id", "trigger", "status", "created_at", "started_at", "finished_at",
        "progress_done", "progress_total", "message", "job_count", "new_count", "error")}
    out["has_files"] = bool(r.get("out_dir")) and (db.OUTPUT_DIR / r["out_dir"] / "jobs.xlsx").exists()
    if with_log:
        out["log"] = r["log"]
        out["config_snapshot"] = json.loads(r["config_snapshot"]) if r["config_snapshot"] else None
    return out


def get_profile_or_404(pid):
    row = db.query("SELECT * FROM profiles WHERE id=?", (pid,), one=True)
    if not row:
        abort(404, "Profile not found")
    return row


@app.errorhandler(404)
def not_found(e):
    return err(getattr(e, "description", "Not found"), 404)


# ---------- pages & meta ----------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/meta")
def meta():
    try:
        import jobspy  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    try:
        from jobspy.model import Country
        def label(c):
            first = c.value[0].split(",")[0]
            return first.upper() if first in ("usa", "uk") else first.title()

        countries = sorted({label(c) for c in Country if c.name not in ("US_CANADA", "WORLDWIDE")})
    except Exception:
        countries = ["USA", "Canada", "UK", "Australia", "India", "Germany"]
    return jsonify({
        "sites": SITES, "job_types": JOB_TYPES, "description_formats": DESCRIPTION_FORMATS,
        "countries": countries, "defaults": DEFAULT_CONFIG, "jobspy_installed": installed,
    })


# ---------- profiles ----------
@app.get("/api/profiles")
def list_profiles():
    return jsonify([profile_out(r) for r in db.query("SELECT * FROM profiles ORDER BY name COLLATE NOCASE")])


def _save_profile(pid=None):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return err("Name is required")
    try:
        cfg = validate_config(body.get("config"))
    except ValueError as exc:
        return err(str(exc))
    desc = (body.get("description") or "").strip()
    cron = (body.get("schedule_cron") or "").strip()
    enabled = 1 if body.get("schedule_enabled") else 0
    try:
        if pid is None:
            t = db.now()
            pid = db.execute(
                "INSERT INTO profiles (name, description, config, schedule_enabled, schedule_cron, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)", (name, desc, json.dumps(cfg), enabled, cron, t, t))
        else:
            get_profile_or_404(pid)
            db.execute(
                "UPDATE profiles SET name=?, description=?, config=?, schedule_enabled=?, schedule_cron=?, updated_at=? "
                "WHERE id=?", (name, desc, json.dumps(cfg), enabled, cron, db.now(), pid))
    except sqlite3.IntegrityError:
        return err(f"A profile named “{name}” already exists", 409)
    return jsonify(profile_out(get_profile_or_404(pid)))


@app.post("/api/profiles")
def create_profile():
    resp = _save_profile()
    return (resp[0], resp[1]) if isinstance(resp, tuple) else (resp, 201)


@app.get("/api/profiles/<int:pid>")
def get_profile(pid):
    return jsonify(profile_out(get_profile_or_404(pid)))


@app.put("/api/profiles/<int:pid>")
def update_profile(pid):
    return _save_profile(pid)


@app.delete("/api/profiles/<int:pid>")
def delete_profile(pid):
    row = get_profile_or_404(pid)
    if db.query("SELECT 1 FROM runs WHERE profile_id=? AND status IN ('queued','running')", (pid,), one=True):
        return err("Cancel the active run first", 409)
    db.execute("DELETE FROM profiles WHERE id=?", (pid,))  # cascades runs + seen_jobs
    from runner import _slug
    shutil.rmtree(db.OUTPUT_DIR / _slug(row["name"]), ignore_errors=True)
    return jsonify({"ok": True})


@app.post("/api/profiles/<int:pid>/duplicate")
def duplicate_profile(pid):
    row = get_profile_or_404(pid)
    base, n = f"{row['name']} (copy)", 1
    names = {r["name"] for r in db.query("SELECT name FROM profiles")}
    name = base
    while name in names:
        n += 1
        name = f"{base} {n}"
    t = db.now()
    new_id = db.execute(
        "INSERT INTO profiles (name, description, config, created_at, updated_at) VALUES (?,?,?,?,?)",
        (name, row["description"], row["config"], t, t))
    return jsonify(profile_out(get_profile_or_404(new_id))), 201


@app.post("/api/profiles/<int:pid>/run")
def run_profile(pid):
    get_profile_or_404(pid)
    try:
        run_id = runner.enqueue(pid, "manual")
    except ActiveRunExists as exc:
        return err(str(exc), 409)
    return jsonify(run_out(db.query("SELECT * FROM runs WHERE id=?", (run_id,), one=True))), 202


@app.post("/api/import-script")
def import_script_route():
    source = (request.get_json(silent=True) or {}).get("source", "")
    if not source.strip():
        return err("Paste a script first")
    try:
        return jsonify(import_script(source))
    except ValueError as exc:
        return err(str(exc))


# ---------- runs ----------
@app.get("/api/runs")
def list_runs():
    where, params = [], []
    if request.args.get("profile_id"):
        where.append("profile_id=?")
        params.append(int(request.args["profile_id"]))
    if request.args.get("active"):
        where.append("status IN ('queued','running')")
    sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    params.append(min(int(request.args.get("limit", 50)), 200))
    return jsonify([run_out(r) for r in db.query(sql, params)])


def _run_or_404(rid):
    r = db.query("SELECT * FROM runs WHERE id=?", (rid,), one=True)
    if not r:
        abort(404, "Run not found")
    return r


@app.get("/api/runs/<int:rid>")
def get_run(rid):
    return jsonify(run_out(_run_or_404(rid), with_log=True))


@app.post("/api/runs/<int:rid>/cancel")
def cancel_run(rid):
    _run_or_404(rid)
    runner.request_cancel(rid)
    return jsonify({"ok": True})


@app.get("/api/runs/<int:rid>/results")
def run_results(rid):
    r = _run_or_404(rid)
    path = db.OUTPUT_DIR / (r["out_dir"] or "_missing") / "results.json"
    if not path.exists():
        return jsonify({"rows": []})
    return jsonify({"rows": json.loads(path.read_text(encoding="utf-8"))})


@app.get("/api/runs/<int:rid>/download/<fmt>")
def download(rid, fmt):
    if fmt not in ("xlsx", "csv"):
        abort(404)
    r = _run_or_404(rid)
    if not r["out_dir"]:
        abort(404, "No files for this run")
    folder = db.OUTPUT_DIR / r["out_dir"]  # built from DB, never from user input
    if not (folder / f"jobs.{fmt}").exists():
        abort(404, "No file (the run found 0 jobs)")
    return send_from_directory(folder, f"jobs.{fmt}", as_attachment=True,
                               download_name=f"jobs_run{rid}.{fmt}")


if __name__ == "__main__":
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 5000))
    except OSError as exc:
        probe.close()
        frozen = getattr(sys, "frozen", False)
        if not _stop_port_owner(5000, frozen):
            raise SystemExit(
                "Cannot start JobSpy on port 5000 because another process is already using it.\n"
                "Close that application or stop the process on port 5000, then try again."
            ) from exc
        stopped_name = "source app" if frozen else "JobSpyConsole.exe"
        print(f"Stopped existing {stopped_name} on port 5000.")
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 5000))
        except OSError:
            raise SystemExit("Port 5000 is still occupied after stopping the compatible app.") from exc
    finally:
        if probe.fileno() != -1:
            probe.close()

    db.init_db()
    runner.start()
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    # 127.0.0.1 only: this app has no auth. use_reloader=False: the reloader would start two runner threads.
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)
