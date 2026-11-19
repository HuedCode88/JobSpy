# Building the Standalone JobSpy Console

This document describes how to rebuild the Windows `.exe` and how to export it.

## What "standalone" means

The final `dist\JobSpyConsole.exe` is a one-file PyInstaller executable.
It contains the UI, JobSpy scraper implementation, CSV/Excel formatting, and
personal-scraper profiles needed at runtime.

The source `jobspy\` directory is required **while building**. It is read into
the executable and is not distributed as a separate required folder.
The `Personal Scrapers\` directory is **not** packaged or seeded in the executable.

The executable is not a portable copy of the source repository. The
following folders are not required when running it:

- `jobspy\`
- `jobspy_ui\`
- `Personal Scrapers\`
- `build\`

The executable creates `data\` beside itself for:

- `data\app.db`
- `data\outputs\`

Send a fresh copy of the executable if the recipient should start without the
author's existing profiles or run history.

## Build prerequisites

From Windows PowerShell in the repository root:

```powershell
python -m pip install -r jobspy_ui\requirements.txt
python -m pip install -e .
```

Do not run the executable while rebuilding; close it completely.

## Build the executable

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File jobspy_ui\build_exe.ps1
```

The output is:

```text
dist\JobSpyConsole.exe
```

The build script:

- Stops a build only if `JobSpyConsole.exe` is currently running.
- Uses `jobspy_ui\JobSpyConsole.spec`.
- Cleans prior PyInstaller output.
- Creates a one-file executable.
- Removes the console window and opens the UI in the default browser.
- Reports the output size.

## Verify the executable is standalone

1. Copy `dist\JobSpyConsole.exe` to an empty temporary folder.
2. Run the copied executable.
3. Wait for the browser to open `http://127.0.0.1:5000`.
4. Confirm that all imported profiles appear and `/api/meta` reports
   `"jobspy_installed": true`.
5. Run a limited real search, such as one term and one board with five results.

This test works only because the executable bundles JobSpy. The temporary
folder needs no `jobspy\`, `jobspy_ui\`, or `Personal Scrapers\` directories.

## Export for another computer

For a clean first-time release, send only:

```text
JobSpyConsole.exe
```

Place it in the recipient's Documents or Desktop. Do not place it under
`C:\Program Files`, because the app creates a writable `data\` folder beside it.

After first launch, the executable creates:

```text
JobSpyConsole.exe
data\app.db
data\outputs\
```

To preserve search profiles and history, copy both the executable and its
`data\` folder. The recipient should keep those together.

## Development workflow

To edit and test normally:

```powershell
python jobspy_ui\app.py
```

This runs source code from `jobspy_ui\` and `jobspy\`; it does not run the
previous `.exe`. Restart the Python command after source changes.

Only rebuild the `.exe` after development changes are complete.

## Package contents that stay in source

The build specification intentionally does not add `jobspy\` as external data.
It adds only:

- `jobspy_ui\static\` to provide the browser assets.
- `Personal Scrapers\` to import profile settings.

JobSpy modules are listed as hidden imports and are embedded in the executable.
