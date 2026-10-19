#!/usr/bin/env bash
# run_surf_html.sh — One-shot orchestrator: fetch → parse → score → HTML.
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
#   bash scripts/run_surf_html.sh --no-cache                   # bypass fetch cache
#
# Strategy (minimizes API calls on repeated runs thanks to fetch_windguru.sh
# 90-min cache):
#   1. Fetch GFS (model 3) + WaveWatch III (model 83) for the union of all
#      unique windguru_ids and zona_station IDs referenced by spots.json.
#   2. Merge wave data into wind data per station, then normalize.
#   3. Score every spot against its own region's station.
#   4. Render HTML (top cards + up to 3 deduped Windguru-style hourly tables).

set -euo pipefail

# ---- paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPOTS_JSON="$SKILL_DIR/references/spots.json"

# ---- defaults ----
OUT=""
MAX_DETAIL=3
DAYS=7
NO_CACHE=""
WIND_MODEL=3
WAVE_MODEL=83

# ---- arg parsing ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --max-detail) MAX_DETAIL="$2"; shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
    --no-cache) NO_CACHE="--no-cache"; shift ;;
    --wind-model) WIND_MODEL="$2"; shift 2 ;;
    --wave-model) WAVE_MODEL="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0"
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
    if [[ -n "$candidate" ]] && \
       ( [[ -d "$(dirname "$candidate")" ]] && [[ -w "$(dirname "$candidate")" ]] ); then
      OUT="$candidate"; break
    fi
  done
  : "${OUT:=$WORK/surf_forecast.html}"
fi

# ---- collect unique station ids from spots.json ----
mapfile -t IDS < <(python3 -c "
import json, sys
spots = json.load(open(sys.argv[1]))
ids = set()
for s in spots:
    for k in ('windguru_id', 'zona_station'):
        v = s.get(k)
        if v:
            ids.add(str(v))
for i in sorted(ids):
    print(i)
" "$SPOTS_JSON")

if [[ ${#IDS[@]} -eq 0 ]]; then
  echo "no station ids found in $SPOTS_JSON" >&2; exit 1
fi

# ---- fetch wind + wave for all stations (cached by default) ----
bash "$SCRIPT_DIR/fetch_windguru.sh" $NO_CACHE --model "$WIND_MODEL" "${IDS[@]}" \
  > "$WORK/wind.json"
bash "$SCRIPT_DIR/fetch_windguru.sh" $NO_CACHE --model "$WAVE_MODEL" "${IDS[@]}" \
  > "$WORK/wave.json"

# ---- parse + merge ----
python3 "$SCRIPT_DIR/parse_windguru.py" "$WORK/wind.json" --merge "$WORK/wave.json" \
  > "$WORK/normalized.json"

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
