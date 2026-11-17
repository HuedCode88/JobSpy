# PyInstaller build specification for the local JobSpy web console.
from pathlib import Path

ROOT = Path(SPECPATH).parent


a = Analysis(
    [str(ROOT / "jobspy_ui" / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "jobspy_ui" / "static"), "static")],
    hiddenimports=[
        "jobspy",
        "jobspy.model",
        "jobspy.util",
        "jobspy.exception",
        "jobspy.bayt",
        "jobspy.bdjobs",
        "jobspy.glassdoor",
        "jobspy.google",
        "jobspy.indeed",
        "jobspy.linkedin",
        "jobspy.naukri",
        "jobspy.ziprecruiter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["IPython", "matplotlib", "PyQt6", "scipy", "tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="JobSpyConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
