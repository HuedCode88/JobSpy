"""End-to-end smoke test with a STUB jobspy (no network). Run:  python -m pytest -q   (or python tests/test_smoke.py)"""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

# ---- stub jobspy, including one control char and a duplicate URL to exercise exporter + dedupe ----
CALLS = []


def fake_scrape_jobs(**kw):
    CALLS.append(kw)
    if "glassdoor" in kw["site_name"] and len(kw["site_name"]) > 1:
        raise RuntimeError("glassdoor blocked")  # forces the per-board fallback
    rows = []
    for site in kw["site_name"]:
        if site == "glassdoor":
            raise RuntimeError("glassdoor blocked")
        rows.append({"site": site, "title": f"{kw['search_term']} @ {site}", "company": "Acme",
                     "location": "Miami, FL, USA", "job_url": f"https://x.test/{site}/same",
                     "description": "Hello\x07 world", "date_posted": "2026-11-16", "min_amount": 90000,
                     "max_amount": 120000, "interval": "yearly", "currency": "USD"})
        rows.append({"site": site, "title": f"{kw['search_term']} unique", "company": "Beta", "location": "Miami, FL",
                     "job_url": f"https://x.test/{site}/{kw['search_term'].replace(' ', '-')}",
                     "description": "desc", "date_posted": "2026-11-15"})
    return pd.DataFrame(rows)


stub = types.ModuleType("jobspy")
stub.scrape_jobs = fake_scrape_jobs
sys.modules["jobspy"] = stub

import db  # noqa: E402

tmp = Path(tempfile.mkdtemp())
db.DATA_DIR, db.OUTPUT_DIR, db.DB_PATH = tmp, tmp / "outputs", tmp / "app.db"

import app as app_module  # noqa: E402
from runner import runner  # noqa: E402


def wait_done(client, run_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/runs/{run_id}").get_json()
        if r["status"] in ("done", "failed", "cancelled"):
            return r
        time.sleep(0.1)
    raise TimeoutError


def test_flow():
    db.init_db()
    runner.start()
    c = app_module.app.test_client()

    profiles = c.get("/api/profiles").get_json()
    assert profiles and profiles[0]["config"]["search_terms"]            # seeded example

    # create
    body = {"name": "T", "config": {"search_terms": "a b\nc d\na b", "sites": ["indeed", "glassdoor"], "location": "Miami, FL"}}
    created = c.post("/api/profiles", json=body)
    assert created.status_code == 201
    pid = created.get_json()["id"]
    assert created.get_json()["config"]["search_terms"] == ["a b", "c d"]  # de-duped
    assert c.post("/api/profiles", json=body).status_code == 409           # unique name
    assert c.post("/api/profiles", json={"name": "bad", "config": {"search_terms": ""}}).status_code == 400

    # run #1
    run_id = c.post(f"/api/profiles/{pid}/run").get_json()["id"]
    assert c.post(f"/api/profiles/{pid}/run").status_code == 409           # one active run per profile
    r = wait_done(c, run_id)
    assert r["status"] == "done", r
    assert r["job_count"] == 3 and r["new_count"] == 3   # shared URL dedupes across terms
    rows = c.get(f"/api/runs/{run_id}/results").get_json()["rows"]
    assert all(row["is_new"] for row in rows)
    assert c.get(f"/api/runs/{run_id}/download/xlsx").status_code == 200   # illegal char did not break openpyxl
    assert c.get(f"/api/runs/{run_id}/download/csv").status_code == 200

    # run #2: nothing is new anymore
    run2 = c.post(f"/api/profiles/{pid}/run").get_json()["id"]
    r2 = wait_done(c, run2)
    assert r2["new_count"] == 0

    # import (ast only)
    src = 'terms = ["x", "y"]\nfor t in terms:\n    scrape_jobs(site_name=["indeed"], search_term=t, results_wanted=5, hours_old=48, location="Austin, TX")\n'
    imp = c.post("/api/import-script", json={"source": src}).get_json()
    assert imp["config"]["search_terms"] == ["x", "y"] and imp["config"]["hours_old"] == 48 and imp["config"]["sites"] == ["indeed"]

    # delete cascades
    assert c.delete(f"/api/profiles/{pid}").status_code == 200
    assert c.get(f"/api/profiles/{pid}").status_code == 404
    print("OK")


def test_remote_listing_filter():
    from runner import _remote_listing_mask

    df = pd.DataFrame([
        {"title": "Remote Python Engineer", "location": "USA", "description": "Fully remote", "is_remote": True},
        {"title": "Python Engineer", "location": "Miami, FL", "description": "Work onsite in Miami", "is_remote": False},
        {"title": "Hybrid Python Engineer", "location": "Miami, FL", "description": "Two days hybrid", "is_remote": True},
        {"title": "Backend Engineer", "location": "Remote - US", "description": "Work from home", "is_remote": False},
        {"title": "Developer", "location": "Miami, FL", "description": "In-office role", "is_remote": True},
    ])

    mask = _remote_listing_mask(df)
    assert mask.tolist() == [True, False, False, True, False]


if __name__ == "__main__":
    test_flow()
