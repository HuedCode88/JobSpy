# COPILOT INSTRUCTIONS — JobSpy Console

You are integrating a local web UI into an existing JobSpy project. **A working scaffold already exists in this
folder and passes an offline smoke test.** Your job is to merge it into the user's repo, run it against real
JobSpy, fix whatever real-world data breaks, and keep the architecture intact so a daily scheduler can be added later.

Read this whole file before changing anything.

---

## 1. What we're building (and why it looks like this)

**Problem:** the user has several near-identical scripts. Each loops over a list of search terms, calls
`jobspy.scrape_jobs(...)` with slightly different parameters, concatenates results, dedupes by `job_url`, and writes
CSV + a formatted XLSX. They run them from VS Code.

**Solution:** each script becomes a **profile** (a saved row in SQLite holding a JSON config). A button runs a
profile in a background thread. Results are viewable in-browser and downloadable. New profiles are created from a form
or by importing an existing script.

**Key decision: scripts are data, not code.** Because all the scripts share one shape, we store their *parameters*
instead of their source. This means: no `exec`/`eval`/subprocess of user code, a form can edit everything, and the
future scheduler is just "enqueue profile N". If the user later needs a profile that does something the config can't
express, add a config field. Do **not** add "paste arbitrary Python and run it" — it is a security and maintenance trap.

**Stack (chosen to minimise moving parts):** Flask + SQLite (stdlib `sqlite3`) + vanilla JS (no build step, no
npm). One process, `python app.py`. Do not introduce React/Vite/SQLAlchemy/Celery/Redis unless the user asks.

---

## 2. File map

```
jobspy_ui/
├── app.py            Flask app: JSON API + serves static/. Entry point.
├── config.py         DEFAULT_CONFIG, site list, validate_config()  ← single source of truth for profile fields
├── db.py             SQLite schema, connect()/query()/execute(), init_db() (+ seeds an example profile)
├── runner.py         Background worker thread + queue. enqueue(), request_cancel(), the scrape→dedupe→export pipeline
├── exporter.py       write_csv(), write_xlsx()  (the user's original openpyxl formatting, parametrised)
├── importer.py       AST-based "import from script" (never executes the script)
├── static/
│   ├── index.html    Shell + <template id="tpl-form"> for the settings form + dialogs
│   ├── style.css     Light/dark via prefers-color-scheme
│   └── app.js        All UI logic. Vanilla. State object + render functions.
├── tests/test_smoke.py   Offline end-to-end test with a stub `jobspy` module
├── data/             (created at runtime) app.db + outputs/<profile-slug>/run<id>_<timestamp>/{jobs.csv,jobs.xlsx,results.json}
└── requirements.txt
```

---

## 3. Integrating into the user's repo

1. Put this folder at the repo root as `jobspy_ui/` (do **not** copy files into the root; JobSpy's own package is
   named `jobspy`, so avoid any module named `jobspy` in this folder).
2. If the user's repo has the JobSpy source checked out locally (not pip-installed), make sure `import jobspy` resolves
   from the venv that runs `app.py` (e.g. `pip install -e <path-to-repo>`).
3. `pip install -r requirements.txt`, `python app.py`, open http://127.0.0.1:5000.
4. First launch seeds a profile "Miami Dev Jobs (example)" from the user's original script
   (6 terms, Miami FL, 20 results, 24 h). Run it for real as the first acceptance test.
5. Optionally use **Import from script…** to convert the user's other scripts, then verify each profile's Settings
   against the source script and fix `importer.py` if some argument isn't picked up.

---

## 4. Data model

```
profiles(id, name UNIQUE, description, config JSON, schedule_enabled, schedule_cron, created_at, updated_at)
runs(id, profile_id→profiles, trigger 'manual'|'schedule', status, started_at, finished_at, progress_done,
     progress_total, message, log, job_count, new_count, out_dir, error, cancel_requested, config_snapshot JSON)
seen_jobs(profile_id, job_url, first_seen_run_id)   PK(profile_id, job_url)
```

* `config` keys are exactly `config.DEFAULT_CONFIG`: `search_terms[]`, `sites[]`, `location`, `country_indeed`,
  `results_wanted`, `hours_old`, `distance`, `job_type`, `is_remote`, `easy_apply`, `fetch_description`,
  `description_format`, `enforce_annual_salary`, `proxies[]`, `only_new`.
* **Adding a new setting** = (1) add to `DEFAULT_CONFIG` + validate in `validate_config`, (2) pass it in
  `runner._scrape_term`'s `call()`, (3) add an input to `#tpl-form` and wire it in `buildForm()` (both the fill and
  the `payload`). Those are the only 3 places. Old profiles missing the key get the default automatically because
  `validate_config` merges over defaults.
* `config_snapshot` records the config used for each run, so history stays accurate after a profile is edited.
* `seen_jobs` powers the **NEW** badge and the "only new" option, and is what makes daily runs useful later.
* Output files are referenced by `runs.out_dir` (relative to `data/outputs`). Downloads are built from the DB row,
  never from a user-supplied path — keep it that way (path traversal).

---

## 5. API contract (all JSON, errors are `{"error": "message"}` with a 4xx status)

| Method & path | Purpose |
|---|---|
| `GET /api/meta` | sites, countries, job types, defaults, `jobspy_installed` |
| `GET /api/profiles` | all profiles, each with `last_run` summary |
| `POST /api/profiles` | create `{name, description, config, schedule_*}` → 201 (409 duplicate name, 400 validation) |
| `GET/PUT/DELETE /api/profiles/<id>` | read / update / delete (delete cascades runs, seen_jobs, files; 409 if run active) |
| `POST /api/profiles/<id>/duplicate` | copy as "<name> (copy)" |
| `POST /api/profiles/<id>/run` | enqueue → 202 + run (409 if this profile already has a queued/running run) |
| `POST /api/import-script` | `{source}` → `{config, warnings[]}` (does not save; UI prefills the New form) |
| `GET /api/runs?profile_id=&active=1&limit=` | list runs |
| `GET /api/runs/<id>` | run + `log` + `config_snapshot` |
| `POST /api/runs/<id>/cancel` | cooperative cancel (checked between search terms) |
| `GET /api/runs/<id>/results` | `{rows:[...]}` from `results.json` |
| `GET /api/runs/<id>/download/<xlsx\|csv>` | file download |

The UI polls `GET /api/runs?active=1` every 1.5 s only while something is running (`pollActive`). No websockets/SSE
needed; don't add them.

---

## 6. The run pipeline (`runner._execute`) — read carefully

1. A **single worker thread** consumes a queue → runs are serialized on purpose (job boards rate-limit per IP).
   `enqueue()` rejects a second active run for the same profile.
2. For each search term → `_scrape_term(cfg, term)` → `scrape_jobs(...)` with all selected boards.
   **JobSpy gotcha:** `scrape_jobs` runs boards in a `ThreadPoolExecutor` and calls `future.result()`, so **one board
   raising discards every board's results**. `_scrape_term` therefore catches the failure and retries board-by-board,
   keeping whatever succeeds. A failing *term* is logged and skipped; it does not fail the run.
3. Add a `search_term` column, concat, `drop_duplicates(subset=["job_url"])`.
4. Compare to `seen_jobs` → insert `is_new` as the first column; `INSERT OR IGNORE` the URLs. If `only_new`, filter.
5. Write `jobs.csv`, `jobs.xlsx` (skipped when 0 rows) and `results.json` (what the browser table reads).
6. Status → `done` with counts. Any uncaught exception → `failed` with traceback in `error`/`log`.
7. On app start, runs left in `queued/running` are marked `failed: Interrupted by app restart`.
8. A logging handler on the root logger copies records whose logger name contains "jobspy" into the run log.
   **Verify with a real run** that JobSpy's loggers (`create_logger(...)` in `jobspy/util.py`) show up in the
   "Live log"; if the name differs, adjust the filter in `_RunLogHandler.emit`.

### Real-world issues to expect (and where to fix them)

* **Excel illegal characters.** Scraped descriptions contain control chars → `openpyxl.IllegalCharacterError`.
  Handled in `exporter._clean_for_excel` (also truncates to Excel's 32,767-char cell limit). It covers pandas 3's
  `str` dtype as well as `object`; this was a real bug the smoke test caught.
* **NaN/NaT in JSON.** `to_json` converts them to `null`; the browser treats missing values as empty.
* **Rate limiting (HTTP 429).** LinkedIn and Glassdoor will block. Symptoms: that board returns 0 rows and the log shows
  an error. Mitigation lives in config: fewer `results_wanted`, `fetch_description` off, or `proxies`. Do **not** add
  retry loops around `scrape_jobs`; JobSpy has its own backoff for LinkedIn.
* **`fetch_description=True` is slow** (one request per job). The UI says so. A 6-term × 4-board × 20-result run can take
  many minutes; that's why it's a background job with progress.
* **`hours_old`**: Indeed filters by when it was *indexed*, so `date_posted` can be older than the window. Not a bug.
* **Country names**: `country_indeed` must match JobSpy's `Country.from_string`. The dropdown is built from
  `jobspy.model.Country`, so it can't drift.
* **Column set**: `scrape_jobs` returns JobSpy's `desired_order` columns. The table in `app.js` only uses
  `title, company, location, date_posted, site, min_amount, max_amount, interval, currency, job_url, description,
  is_remote, job_type, job_url_direct, is_new, search_term`. If a column is renamed upstream, fix there.

---

## 7. Frontend rules

* **Scraped data is untrusted.** Descriptions come from arbitrary websites. Build DOM with `h()` (uses `textContent`,
  `.value`, `setAttribute`). **Never** `innerHTML` anything derived from job data. Links use
  `target=_blank rel="noopener noreferrer"`. Consider only allowing `http(s)://` hrefs if you touch link code.
* The settings form is a `<template>` filled with `.value`/`.checked`, not string-built HTML.
* While the Settings tab is open, polling does **not** re-render (it would wipe unsaved edits). Preserve that.
* Design: neutral light/dark, one accent colour, left sidebar of profiles with a status dot, main area =
  header (name, summary pills, big **Run now**) → progress card (only while active) → tabs **Results / History /
  Settings**. Results = filterable/sortable table, NEW badge, click row → right-hand drawer with the full description.
  Keep it simple; resist adding pages.
* Don't add a bundler. If the JS grows past ~700 lines, split into ES modules loaded with `<script type="module">`.

---

## 8. Security & robustness checklist (do not regress)

- Server binds to `127.0.0.1` only; there is **no auth**. If the user wants LAN/remote access, add auth first.
- Never `exec`/`eval`/`subprocess` anything from a profile or imported script. `importer.py` uses `ast.literal_eval` only.
- Download endpoint resolves paths from DB rows only.
- Proxy credentials are stored in plain text in `data/app.db`. Keep `data/` in `.gitignore`. Never echo proxies to logs.
- `app.run(..., use_reloader=False)`: the Flask reloader would start two runner threads. Keep it off, or guard `runner.start()`.
- SQLite is opened per call with a 30 s timeout and WAL mode; keep writes short.

---

## 9. Stretch goal: daily automatic runs (design now, build later)

The scaffold is already shaped for this; **do not build it unless asked**, but don't block it:

* `profiles.schedule_enabled` / `schedule_cron` columns exist and the (disabled) form fields are there.
* The only entry point a scheduler needs is `runner.enqueue(profile_id, trigger="schedule")`. It already serializes,
  de-duplicates concurrent runs of one profile (raises `ActiveRunExists` — catch and log it), records `trigger`,
  and the History tab labels scheduled runs "(auto)".
* Recommended implementation: **APScheduler `BackgroundScheduler`** started in `app.py` next to `runner.start()`.
  On startup and after every profile save/delete, reconcile jobs: for each profile with `schedule_enabled=1`,
  `scheduler.add_job(runner.enqueue, CronTrigger.from_crontab(cron), args=[id, "schedule"], id=f"p{id}",
  replace_existing=True, coalesce=True, misfire_grace_time=3600)`. Validate cron with `CronTrigger.from_crontab`
  in `_save_profile` and return 400 on failure. Replace the free-text cron box with presets (Daily 7:00 / Weekdays 8:00 /
  Custom) and a time picker; enable the two form fields.
* Daily runs should usually set `only_new = True` and `hours_old = 24` so each day's file contains just new jobs.
* Caveat to surface in the UI: scheduled runs only fire while the app/computer is running. If the user needs true
  unattended runs, the same app can run on an always-on box/VPS (then add auth) — or use OS cron/Task Scheduler to
  `curl -X POST localhost:5000/api/profiles/<id>/run`, which already works today with no code changes.
* Possible follow-ups once scheduled: desktop/email notification when `new_count > 0`; "last scheduled run" in the sidebar.

---

## 10. Testing & definition of done

Automated: `python tests/test_smoke.py` (stubs `jobspy`; covers create/validate/duplicate-name, per-board fallback,
dedupe, `is_new` across two runs, Excel illegal chars, downloads, script import, cascade delete). Keep it green and
extend it when you add features.

Manual acceptance (real network, in this order):
1. `python app.py` → page loads, seeded profile appears, no banner about jobspy.
2. Run the seeded profile with **only Indeed** ticked, 1 term, 5 results → Results table fills, NEW badges appear,
   Excel opens with the blue header, frozen row, working hyperlinks, wrapped descriptions.
3. Run it again → NEW badges gone, `new_count` 0.
4. Tick all four boards; if one fails the others' rows still appear and the log explains the failure.
5. Click **Cancel** mid-run → status `cancelled`, partial work not exported (acceptable).
6. Import each of the user's real scripts via **Import from script…**; confirm Settings match; Save; Run.
7. Create a profile from scratch; Duplicate it; Delete the duplicate.
8. Kill the server mid-run, restart → that run shows `failed: Interrupted by app restart`.
9. Resize to a narrow window → layout stacks and stays usable.

Done = all 9 pass, smoke test green, no `innerHTML` with job data, `data/` is gitignored.

---

## 11. Likely follow-up requests (and where they go)

| Request | Where |
|---|---|
| Mark jobs "applied / ignored / starred" | new `job_status(profile_id, job_url, status)` table; join in `/results`; buttons in the drawer |
| Keyword include/exclude filters after scraping | config fields + a pandas filter step in `runner._execute` between dedupe and `seen_jobs` |
| Merge results from all profiles | new endpoint reading latest `results.json` of each profile |
| Export presets / column picker | `exporter.py` takes a column list from config |
| Run all profiles | loop `runner.enqueue` (worker already serializes) |
| Packaging as desktop app | wrap with `pywebview` or ship a `start.bat`/`start.command` that activates the venv and runs `app.py` |
