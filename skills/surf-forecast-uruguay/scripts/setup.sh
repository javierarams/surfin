#!/usr/bin/env bash
# setup.sh - Prepare the Python environment that fetch_windguru.py runs in. Idempotent.
#
# Creates a virtualenv with the pinned Playwright from requirements.txt and makes sure a
# browser can be launched: the installed Google Chrome if there is one, otherwise Playwright's
# own Chromium (downloaded once). Silent on success; prints the python path with --print-python.
#
# Usage:
#   bash scripts/setup.sh                 # install or verify
#   bash scripts/setup.sh --print-python  # same, then print the venv's python on stdout
#
# Location: $SURF_FORECAST_HOME (default ~/.cache/surf-forecast-uruguay)/venv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REQUIREMENTS="$SKILL_DIR/requirements.txt"
HOME_DIR="${SURF_FORECAST_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/surf-forecast-uruguay}"
VENV="$HOME_DIR/venv"
PY="$VENV/bin/python"
STAMP="$VENV/.installed-requirements.txt"
LOG="$HOME_DIR/setup.log"

PRINT_PYTHON=0
case "${1:-}" in
  --print-python) PRINT_PYTHON=1 ;;
  "") ;;
  -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
  *) echo "Unknown arg: $1" >&2; exit 2 ;;
esac

fail() {
  echo "setup failed: $1 (log: $LOG)" >&2
  tail -n 20 "$LOG" >&2 || true
  exit 1
}

if [[ ! -x "$PY" ]] || ! cmp -s "$REQUIREMENTS" "$STAMP"; then
  mkdir -p "$HOME_DIR"
  : > "$LOG"
  command -v python3 >/dev/null || fail "python3 (3.10+) is required"
  [[ -x "$PY" ]] || python3 -m venv "$VENV" >>"$LOG" 2>&1 || fail "could not create $VENV"
  "$PY" -m pip install --disable-pip-version-check -q -r "$REQUIREMENTS" >>"$LOG" 2>&1 \
    || fail "pip install -r $REQUIREMENTS"
  if ! "$PY" "$SCRIPT_DIR/fetch_windguru.py" --check-browser >>"$LOG" 2>&1; then
    "$PY" -m playwright install chromium >>"$LOG" 2>&1 || fail "playwright install chromium"
    "$PY" "$SCRIPT_DIR/fetch_windguru.py" --check-browser >>"$LOG" 2>&1 \
      || fail "no browser can be launched"
  fi
  cp "$REQUIREMENTS" "$STAMP"
fi

if [[ "$PRINT_PYTHON" == 1 ]]; then
  echo "$PY"
fi
