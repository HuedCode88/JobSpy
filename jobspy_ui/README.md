# JobSpy Console

A local web UI for [JobSpy](https://github.com/cullenwatson/JobSpy). Save searches ("profiles"), run them with a
button, browse results, download Excel/CSV.

From the repository root:

```bash
python -m venv .venv
.venv\Scripts\activate                                 # Windows
python -m pip install -e .
python jobspy_ui\app.py                                # then open http://127.0.0.1:5000
python -m pytest -q jobspy_ui\tests\test_smoke.py      # offline test; stubs jobspy
```

The UI imports the local `jobspy` package from this repository, so it always uses the
same scraper code as the personal scripts. The optional `requirements.txt` file is
for installing the UI's direct runtime, test, and packaging dependencies separately.

To build a Windows executable:

```powershell
python -m pip install -r jobspy_ui\requirements.txt
powershell -ExecutionPolicy Bypass -File jobspy_ui\build_exe.ps1
.\dist\JobSpyConsole.exe
```

The executable serves the UI at `http://127.0.0.1:5000` and stores its database and
exports in a `data\` folder next to the executable.

Data lives in `data/` (SQLite DB + one folder per run). Delete the folder to reset.
See `COPILOT_INSTRUCTIONS.md` for architecture and the extension plan.
