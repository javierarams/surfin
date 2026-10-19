#!/usr/bin/env bash
# fetch_windguru.sh — Fetch Windguru forecast JSON for one or more station IDs.
#
# Usage:
#   bash fetch_windguru.sh <id_spot> [<id_spot> ...]
#   bash fetch_windguru.sh --model 83 313168 37691     # different wave model
#   bash fetch_windguru.sh --no-cache 313168           # bypass cache
#   bash fetch_windguru.sh --max-age-min 30 313168     # custom TTL
#
# Output (stdout): JSON of the form
#   { "stations": { "<id_spot>": <raw_windguru_json>, ... },
#     "errors":   { "<id_spot>": "<error_message>", ... } }
#
# Caches per-id responses in /tmp/windguru_cache/ for 90 min by default.
# Falls back to HTML scrape if iapi.php returns non-JSON.

set -euo pipefail

# --- Config ---
REFERER='https://www.windguru.cz/282021'
USER_AGENT='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
CACHE_DIR='/tmp/windguru_cache'
MAX_AGE_MIN=90
MODEL=3
USE_CACHE=1

# --- Arg parsing ---
IDS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --max-age-min) MAX_AGE_MIN="$2"; shift 2 ;;
    --no-cache) USE_CACHE=0; shift ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    -*) echo "Unknown flag: $1" >&2; exit 2 ;;
    *) IDS+=("$1"); shift ;;
  esac
done

if [[ ${#IDS[@]} -eq 0 ]]; then
  echo "Usage: $0 <id_spot> [<id_spot> ...]" >&2
  exit 2
fi

mkdir -p "$CACHE_DIR"

# Per-run staging dir (auto-cleaned)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# --- Helpers ---
is_json_file() {
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" 2>/dev/null
}

cache_path() {
  echo "$CACHE_DIR/spot_${1}_model_${MODEL}.json"
}

cache_fresh() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  local mtime
  mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo 0)
  local age_sec=$(( $(date +%s) - mtime ))
  (( age_sec < MAX_AGE_MIN * 60 ))
}

fetch_one() {
  # Args: id_spot, out_path. Returns 0 on success.
  local id="$1"
  local out="$2"
  local url="https://www.windguru.cz/int/iapi.php?q=forecast&id_spot=${id}&id_model=${MODEL}&rundef=&WGCACHEABLE=21600&stationfinder=0&log=logspot&_mha=${id}"

  # Tier 1: JSON API with required Referer header
  local code
  code=$(curl -sS -o "$out" -w "%{http_code}" -m 20 \
    -H "Referer: $REFERER" \
    -H "User-Agent: $USER_AGENT" \
    -H 'Accept: application/json, text/plain, */*' \
    --compressed \
    "$url" 2>/dev/null || echo "000")

  if [[ "$code" == "200" ]] && is_json_file "$out"; then
    return 0
  fi

  # Tier 2 fallback: HTML scrape — wrap in tagged JSON envelope
  local html_tmp="${out}.html"
  code=$(curl -sS -o "$html_tmp" -w "%{http_code}" -m 20 \
    -H "Referer: $REFERER" \
    -H "User-Agent: $USER_AGENT" \
    --compressed \
    "https://www.windguru.cz/${id}" 2>/dev/null || echo "000")

  if [[ "$code" == "200" ]] && [[ -s "$html_tmp" ]]; then
    python3 - "$html_tmp" "$id" "$out" <<'PYEOF'
import json, sys
html_path, id_spot, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(html_path) as f:
    html = f.read()
with open(out_path, "w") as f:
    json.dump({"_format": "html_fallback", "id_spot": id_spot, "html": html}, f)
PYEOF
    return 0
  fi

  return 1
}

# --- Main: fetch each station to its own file ---
SUCCESSES=()
declare -A ERR_MSG

for id in "${IDS[@]}"; do
  cf=$(cache_path "$id")
  stage_file="$STAGE/${id}.json"

  if [[ "$USE_CACHE" == "1" ]] && cache_fresh "$cf" && is_json_file "$cf"; then
    cp "$cf" "$stage_file"
    SUCCESSES+=("$id")
    continue
  fi

  if fetch_one "$id" "$stage_file"; then
    cp "$stage_file" "$cf"
    SUCCESSES+=("$id")
  else
    ERR_MSG["$id"]="fetch failed (network or non-2xx) — verify Referer header and connectivity to windguru.cz"
  fi
done

# Persist errors as a sidecar JSON so the assembler can read them
errs_file="$STAGE/_errors.json"
python3 - <<PYEOF > "$errs_file"
import json, os
errs = {}
$(for id in "${!ERR_MSG[@]}"; do
    msg_escaped=$(printf '%s' "${ERR_MSG[$id]}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
    printf 'errs["%s"] = %s\n' "$id" "$msg_escaped"
done)
print(json.dumps(errs))
PYEOF

# --- Assemble combined output ---
python3 - "$STAGE" "$errs_file" <<'PYEOF'
import json, os, sys, glob
stage, errs_path = sys.argv[1], sys.argv[2]
stations = {}
for fp in sorted(glob.glob(os.path.join(stage, "*.json"))):
    name = os.path.basename(fp)
    if name.startswith("_"):
        continue
    spot_id = name[:-len(".json")]
    try:
        with open(fp) as f:
            stations[spot_id] = json.load(f)
    except Exception as e:
        stations[spot_id] = {"_error": f"parse failed: {e}"}

with open(errs_path) as f:
    errors = json.load(f)

print(json.dumps({"stations": stations, "errors": errors}, ensure_ascii=False))
PYEOF
