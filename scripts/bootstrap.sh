#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

COPILOT_PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    COPILOT_PYTHON="$candidate"
    break
  fi
done
if [ -z "$COPILOT_PYTHON" ]; then
  echo "Python 3.11 or newer is required. Install it, then rerun this command." >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  "$COPILOT_PYTHON" -m venv .venv
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m ensurepip --upgrade
fi
.venv/bin/python -m pip install --disable-pip-version-check --require-hashes -r requirements.lock
.venv/bin/python -m copilot seed
.venv/bin/python -m pytest -q
.venv/bin/python -m copilot eval --mode replay --runs 3 --output artifacts/local/eval-replay.json
echo "Ready. Run ./run demo or see README.md for explicit preview/confirmation commands."
