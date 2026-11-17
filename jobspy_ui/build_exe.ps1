$ErrorActionPreference = "Stop"

Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

python -m PyInstaller --clean --noconfirm "jobspy_ui\JobSpyConsole.spec"

Write-Host ""
Write-Host "Built dist\JobSpyConsole.exe"
