#!/usr/bin/env bash
# One command to get from a fresh clone to a green test run.
#   ./bootstrap.sh
# Creates .venv if it is missing, installs the pinned stack, runs the suite.

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "creating .venv (Python 3.14)"
  (python3.14 -m venv .venv) || python3 -m venv .venv
fi

if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi

echo "installing pinned dependencies"
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install -r requirements.txt --quiet

echo "running tests"
"$PY" -m pytest

cat <<'MSG'

ready. start the viewer with:
  .venv/Scripts/python.exe -m uvicorn server.app:app --reload
  then open http://127.0.0.1:8000/?page=5
MSG
