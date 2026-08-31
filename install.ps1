# One command to get from a fresh clone to a working install.
#   .\install.ps1
# Creates .venv if it is missing, installs the pinned stack, and checks the install works.
#
# This runs `pytest -m smoke` -- the tests that prove the install is correct. It deliberately
# does NOT run the accuracy suite: that is about twenty minutes of real detection over whole
# sheets, which answers a different question. Run `pytest` yourself when you want that.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Fail($message) {
    Write-Host ''
    Write-Host $message -ForegroundColor Red
    exit 1
}

if (-not (Test-Path .venv)) {
    Write-Host 'creating .venv (needs Python 3.14)' -ForegroundColor Cyan

    # A missing interpreter fails in two different ways depending on the PowerShell edition: 5.1
    # ignores a native command's exit code under $ErrorActionPreference='Stop' and carries on,
    # while newer editions throw. Both are handled, because the failure this replaces was the
    # script continuing with no venv and dying later on "term not recognized".
    $NEEDS_PYTHON = "Python 3.14 is required and was not found.`nInstall it from https://www.python.org/downloads/ and run this again."

    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { py -3.14 -m venv .venv } catch { Fail $NEEDS_PYTHON }
        if ($LASTEXITCODE -ne 0) { Fail $NEEDS_PYTHON }
    }
    else {
        # A bare `python` on Windows with no Python installed resolves to the Microsoft Store
        # alias stub, which opens the Store and can hang. Prove the interpreter actually runs
        # before trusting it.
        $probe = $null
        try { $probe = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" } catch { }
        if ($LASTEXITCODE -ne 0 -or -not $probe) { Fail $NEEDS_PYTHON }
        Write-Host "  using python $probe" -ForegroundColor DarkGray
        try { python -m venv .venv } catch { Fail 'Could not create .venv.' }
        if ($LASTEXITCODE -ne 0) { Fail 'Could not create .venv.' }
    }
}

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Fail "Expected an interpreter at $py and there is none." }

& $py -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Fail 'Could not upgrade pip.' }

# Not --quiet. This pulls about 250 MB -- OpenCV, PyMuPDF, NumPy, Pillow -- and on a fresh
# clone a silent download reads exactly like a frozen script.
Write-Host ''
Write-Host 'installing pinned dependencies (~250 MB on a fresh clone, this takes a while)' -ForegroundColor Cyan
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'Dependency install failed.' }

Write-Host ''
Write-Host 'checking the install' -ForegroundColor Cyan
& $py -m pytest -m smoke
if ($LASTEXITCODE -ne 0) { Fail 'The install check failed. The dependencies are in, but something is wrong above.' }

Write-Host ''
Write-Host 'ready. start the viewer with:' -ForegroundColor Green
Write-Host '  .\.venv\Scripts\python.exe -m uvicorn server.app:app'
Write-Host '  then open http://127.0.0.1:8000/?page=5'
Write-Host ''
Write-Host 'that checked the install, not the accuracy. for the full suite (~20 min):' -ForegroundColor DarkGray
Write-Host '  .\.venv\Scripts\python.exe -m pytest'
