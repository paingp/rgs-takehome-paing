# One command to get from a fresh clone to a green test run.
#   .\bootstrap.ps1
# Creates .venv if it is missing, installs the pinned stack, runs the suite.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    Write-Host 'creating .venv (Python 3.14)' -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.14 -m venv .venv }
    else { python -m venv .venv }
}

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Write-Host 'installing pinned dependencies' -ForegroundColor Cyan
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt --quiet

Write-Host 'running tests' -ForegroundColor Cyan
& $py -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host 'ready. start the viewer with:' -ForegroundColor Green
Write-Host '  .\.venv\Scripts\python.exe -m uvicorn server.app:app --reload'
Write-Host '  then open http://127.0.0.1:8000/?page=5'
