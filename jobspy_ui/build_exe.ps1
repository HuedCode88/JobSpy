$ErrorActionPreference = "Stop"

Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$running = Get-Process -Name "JobSpyConsole" -ErrorAction SilentlyContinue
if ($running) {
    throw "Close JobSpyConsole.exe before rebuilding. PyInstaller cannot replace it while it is running."
}

python -m PyInstaller --clean --noconfirm "jobspy_ui\JobSpyConsole.spec"

$exe = Resolve-Path "dist\JobSpyConsole.exe"
if (-not $exe) {
    throw "The executable was not created."
}

Write-Host ""
Write-Host "Built $exe"
$sizeMB = [math]::Round((Get-Item $exe.Path).Length / 1MB, 1)
Write-Host ("Size: {0:N1} MB" -f $sizeMB)
