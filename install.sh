#!/usr/bin/env bash
# One command to get from a fresh clone to a working install.
#   ./install.sh
# Creates .venv if it is missing, installs the pinned stack, and checks the install works.
#
# This runs `pytest -m smoke` -- the tests that prove the install is correct. It deliberately
# does NOT run the accuracy suite: that is about twenty minutes of real detection over whole
# sheets, which answers a different question. Run `pytest` yourself when you want that.

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "creating .venv (needs Python 3.14)"
  if ! { python3.14 -m venv .venv 2>/dev/null || python3 -m venv .venv; }; then
    echo "" >&2
    echo "No usable Python found. Install Python 3.14 and run this again." >&2
    exit 1
  fi
fi

if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
if [ ! -x "$PY" ]; then
  echo "Expected an interpreter at $PY and there is none." >&2
  exit 1
fi

"$PY" -m pip install --upgrade pip --quiet

# Not --quiet. This pulls about 250 MB -- OpenCV, PyMuPDF, NumPy, Pillow -- and on a fresh
# clone a silent download reads exactly like a frozen script.
echo ""
echo "installing pinned dependencies (~250 MB on a fresh clone, this takes a while)"
"$PY" -m pip install -r requirements.txt

echo ""
echo "checking the install"
"$PY" -m pytest -m smoke

cat <<'MSG'

ready. start the viewer with:
  .venv/bin/python -m uvicorn server.app:app
  then open http://127.0.0.1:8000/?page=5

that checked the install, not the accuracy. for the full suite (~20 min):
  .venv/bin/python -m pytest
MSG
