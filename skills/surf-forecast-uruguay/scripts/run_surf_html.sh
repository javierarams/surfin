#!/usr/bin/env bash
# run_surf_html.sh - One-shot orchestrator: Windguru (real browser) → parse → score → HTML.
#
# Produces a Windguru-style HTML forecast for Uruguay's surf spots and prints
# ONLY the absolute path of the resulting HTML on stdout (token-friendly for
# LLM callers). All intermediate JSON files go to a temp dir under
# /tmp/surf_html/<run>/.
#
# Usage:
#   bash scripts/run_surf_html.sh                              # default output path
#   bash scripts/run_surf_html.sh --out /path/to/forecast.html # explicit path
#   bash scripts/run_surf_html.sh --max-detail 3 --days 7      # tuning
#   bash scripts/run_surf_html.sh --no-cache                   # re-read every station
#   bash scripts/run_surf_html.sh --wind-model 117             # IFS-HRES + IFS-WAM
#
# Steps:
#   1. setup.sh: virtualenv with the pinned Playwright (first run only).
#   2. fetch_windguru.py: opens windguru.cz/<id> in headless Chrome for every
#      windguru_id and zona_station in spots.json and keeps the forecast JSON the
#      page downloads: the wind model plus the wave model Windguru pairs with it
#      (cached 90 min per station, one page at a time).
#   3. parse_windguru.py: pairs wind model and wave model hours, UY local time.
#   4. score_spots.py: scores every spot against its own station.
#   5. generate_html.py: top cards + up to 3 deduped Windguru-style hourly tables.
#
# If Windguru cannot be read for any station, the run stops with exit status 1
# and stderr names each failed station and the cause. No report is written.

set -euo pipefail

# ---- paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPOTS_JSON="$SKILL_DIR/references/spots.json"
URUGUAY_BBOX="-35.2,-30.0,-58.6,-53.0"

# ---- defaults ----
OUT=""
MAX_DETAIL=3
DAYS=7
NO_CACHE=""
WIND_MODEL=3

# ---- arg parsing ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --max-detail) MAX_DETAIL="$2"; shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
    --no-cache) NO_CACHE="--no-cache"; shift ;;
    --wind-model) WIND_MODEL="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,27p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ---- temp working dir ----
RUN_ID="$(date +%s)_$$"
WORK="/tmp/surf_html/$RUN_ID"
mkdir -p "$WORK"

# Default output path: workspace folder if writable, else the scratchpad
if [[ -z "$OUT" ]]; then
  for candidate in \
      "${CLAUDE_WORKSPACE_DIR:-}/surf_forecast.html" \
      "$HOME/Projects/surfin/surf_forecast.html" \
      "$WORK/surf_forecast.html"; do
    if [[ -n "$candidate" && -d "$(dirname "$candidate")" && -w "$(dirname "$candidate")" ]]; then
      OUT="$candidate"; break
    fi
  done
  : "${OUT:=$WORK/surf_forecast.html}"
fi

PY="$(bash "$SCRIPT_DIR/setup.sh" --print-python)"

# ---- read Windguru for every station in spots.json (cached by default) ----
"$PY" "$SCRIPT_DIR/fetch_windguru.py" $NO_CACHE --spots "$SPOTS_JSON" \
  --wind-model "$WIND_MODEL" --bbox="$URUGUAY_BBOX" --out "$WORK/raw.json"

# ---- parse ----
python3 "$SCRIPT_DIR/parse_windguru.py" "$WORK/raw.json" > "$WORK/normalized.json"

# ---- score ----
python3 "$SCRIPT_DIR/score_spots.py" "$WORK/normalized.json" "$SPOTS_JSON" --top 40 \
  > "$WORK/scored.json"

# ---- render HTML ----
python3 "$SCRIPT_DIR/generate_html.py" \
  "$WORK/scored.json" "$WORK/normalized.json" "$SPOTS_JSON" \
  --out "$OUT" --max-detail "$MAX_DETAIL" --days "$DAYS" \
  > /dev/null

# Token-friendly stdout: just the absolute path
echo "$OUT"
