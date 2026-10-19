---
name: surf-forecast-uruguay
description: >
  Check surf conditions and forecast for Uruguay beach spots. Use this skill whenever the user asks about surf conditions,
  when to surf, where to surf in Uruguay, surf forecast, good waves, or any surf-related planning.
  Triggers on "cuando va a estar bueno para surfear", "hay olas", "donde surfear", "condiciones de surf",
  "forecast de surf", "esta bueno el mar", "que spots estan buenos", "surf uruguay", "playas con olas", "voy a surfear".
  Also triggers in English on "surf conditions", "good surf", "when to surf", "surf forecast uruguay".
  Always activate when the user mentions surfing + a day, place, or general planning intent.
---

# Surf Forecast Uruguay

Find when and where conditions will be good for surfing across Uruguay's coast — from Montevideo through Costa de Oro, Maldonado, and Rocha. Data source: **Windguru** (most precise forecast for Uruguay).

---

## ⚠️ CRITICAL: Referer header required

**Every request to `windguru.cz/int/iapi.php` MUST include the `Referer` header.** Without it the API returns `401 Unauthorized`.

```
Referer: https://www.windguru.cz/282021
```

Any tool you use to fetch (curl, requests, fetch, etc.) **must** set this header. The skill's helper scripts (`scripts/fetch_windguru.sh`) already do this — prefer them over hand-rolling the call.

---

## Quick path (recommended, token-minimal)

**One command, one output line, one HTML deliverable.**

```bash
bash scripts/run_surf_html.sh
# stdout: <absolute path to surf_forecast.html>
```

That single script does everything end-to-end:
1. Fetches **GFS** (wind/temp/clouds/precip) AND **WaveWatch III** (swell height/period/dir) for the union of all unique `windguru_id` and `zona_station` IDs in `references/spots.json`. Cached 90 min.
2. Parses + merges the two model series per station, normalizes to UY local time (UTC-3).
3. Scores every spot against **its own region's station** (not a global one).
4. Builds a self-contained HTML page:
   - **Top:** cards with the best-scoring spot/day combos across the forecast window (color-coded ✅/⚠️/❌).
   - **Bottom:** up to **3 Windguru-style hourly tables** (rows: wind/gusts/dir, ola/period/dir, temp, nubosidad, precip; columns: each hour for the next 7 days). Tables are deduped by `windguru_id` and only included for spots with at least one acceptable day (score ≥ 50, swell > 0.3 m). If fewer than 3 spots are acceptable, the shortlist is filled with the best-scoring ones.

The script prints **only the absolute path** on stdout — don't echo the HTML into your reply. **Hand the user the file path with a `computer://` link** (see "Output format" below).

### Options
```bash
bash scripts/run_surf_html.sh --out /Users/me/surf.html   # explicit destination
bash scripts/run_surf_html.sh --max-detail 5              # more detail tables
bash scripts/run_surf_html.sh --days 5                    # fewer days in tables
bash scripts/run_surf_html.sh --no-cache                  # bypass 90-min cache
```

### Region- or spot-specific queries
For a focused query ("¿cómo está Punta del Este?", "¿La Moza el sábado?") you can still bypass the orchestrator and run the lower-level scripts directly:
```bash
bash scripts/fetch_windguru.sh --model 3  <id> > /tmp/wind.json
bash scripts/fetch_windguru.sh --model 83 <id> > /tmp/wave.json
python3 scripts/parse_windguru.py /tmp/wind.json --merge /tmp/wave.json > /tmp/normalized.json
python3 scripts/score_spots.py /tmp/normalized.json references/spots.json --region "Maldonado" > /tmp/scored.json
```
…then either render with `generate_html.py` or summarize in chat.

The lower-level scripts are idempotent and self-contained: caching (90 min), error retry, format normalization, and wave-model merging are all handled inside.

### Data availability vs the Windguru website
What the iapi.php endpoint exposes (and we render): wind speed, gusts, wind direction, swell height, swell period, swell direction, temperature, **total** cloud cover (TCDC), and precipitation (mm/h). The site's per-layer cloud split (`high / mid / low`) is **not** in the GFS response — we show total cloud cover only. The site's proprietary "Windguru rating" row is also not exposed by the API; we substitute our own scoring (see "Scoring rules" below).

---

## Fetch decision tree

Pick the path that matches your runtime:

### Path A — Shell available (preferred — Claude Code CLI, routines, scheduled tasks)
`bash scripts/fetch_windguru.sh <id1> <id2> ...`
- Uses `curl` with the required `Referer: https://www.windguru.cz/282021` header
- Caches responses 90 min in `/tmp/windguru_cache/`
- Falls back to HTML scrape (`https://www.windguru.cz/{ID}`) if the JSON API returns non-JSON
- Returns combined JSON: `{ "stations": { "<id>": <raw_windguru_json>, ... } }`

### Path B — Cowork with `mcp__workspace__web_fetch` (only after `windguru.cz` is allowlisted)
1. Call `mcp__workspace__web_fetch` on
   `https://www.windguru.cz/int/iapi.php?q=forecast&id_spot={ID}&id_model=3&rundef=&WGCACHEABLE=21600&stationfinder=0&log=logspot&_mha={ID}`
2. **Note**: `mcp__workspace__web_fetch` doesn't let you set custom headers, so Windguru may reject with 401. If it does → fall back to Path C.

### Path C — Claude in Chrome (last resort, no shell, no allowlist)
1. `mcp__Claude_in_Chrome__navigate` to the iapi.php URL above
2. `mcp__Claude_in_Chrome__get_page_text` returns the JSON body as text
3. Pipe the result through `python3 scripts/parse_windguru.py --stdin`
4. If a cookie/consent banner blocks the response, dismiss with `javascript_tool` then retry

---

## Windguru Stations by Zone

Use the station closest to the spots the user is asking about. For a general Uruguay forecast, use multiple stations.

### 📍 Canelones / Costa de Oro
| Station name | Windguru ID | Covers |
|---|---|---|
| Desemb. Arroyo Pando *(referencia general)* | 313168 | Costa de Oro central |
| Arroyo Pando (spot) | 34546 | Pando/El Pinar |
| Cuchilla Alta / Balneario Argentino | 422330 | Costa de Oro este |
| Santa Ana | 1311353 | Límite Canelones/Maldonado |
| Atlántida | 259217 | Atlántida |
| Parque del Plata | 212902 | Parque del Plata |
| Santa Lucía del Este | 119294 | Santa Lucía del Este |
| Jaureguiberry / Solís | 32742 | Jaureguiberry, Solís |

### 📍 Maldonado (Piriápolis → Punta del Este → José Ignacio)
| Station name | Windguru ID | Covers |
|---|---|---|
| Piriápolis (San Francisco / Faroleta) | 116713 | Piriápolis |
| Corralitos | 37672 | Corralitos |
| El Chileno | 366061 | El Chileno |
| La Virgen / El Emir | 37691 | PDE brava |
| Los Dedos / La Olla | 405894 | PDE sur |
| La Plage | 405896 | La Plage |
| Boca de la Barra | 333460 | La Barra |
| Posta del Cangrejo | 333463 | La Boya / Posta del Cangrejo |
| Montoya | 405911 | Montoya |
| Bikini | 120882 | Bikini |
| José Ignacio | 112369 | José Ignacio |

### 📍 Rocha (La Paloma → La Pedrera → Cabo Polonio → PDD → Sta. Teresa)
| Station name | Windguru ID | Covers |
|---|---|---|
| Corumbá / La Paloma | 405916 | La Paloma (Corumbá) |
| Los Botes / La Paloma | 37673 | Los Botes, Pesqueros Sta Teresa |
| Zanja Honda | 333480 | Zanja Honda |
| La Balconada | 333481 | La Balconada |
| La Aguada | 119340 | La Aguada |
| Playa del Barco / La Pedrera | 405921 | La Pedrera |
| El Desplayado | 119342 | El Desplayado |
| Cabo Polonio | 50158 | Cabo Polonio |
| Punta del Diablo | 172706 | Bahía PDD, El Rivero |
| La Viuda PDD | 467316 | La Viuda |
| La Moza / Sta. Teresa | 93781 | Parque Santa Teresa |

For a **full Uruguay overview**, fetch at minimum one station per zone:
- Canelones: `313168`
- Maldonado: `37691` + `405911`
- Rocha: `333480` + `93781`

---

## Windguru iapi.php endpoint reference

Endpoint:
```
https://www.windguru.cz/int/iapi.php?q=forecast&id_spot={ID}&id_model=3&rundef=&WGCACHEABLE=21600&stationfinder=0&log=logspot&_mha={ID}
```

Required header:
- `Referer: https://www.windguru.cz/282021`  ← **always**

Common models (`id_model`):
- `3` — GFS 13 km (wind/temp only, no waves)
- `83` — WaveWatch III (swell data)
- `63` — MFWAM (alternative swell model)

Response keys under `fcst`:
- `hours` — hours offset from init (1h step ~78h, then 3h step)
- `WINDSPD` — wind speed (knots)
- `WINDDIR` — wind direction (degrees FROM)
- `GUST` — gust (knots)
- `TMP` / `TMPE` — air temperature (°C)
- `TCDC` / `HCDC` / `MCDC` / `LCDC` — cloud cover %
- `APCP1` — precipitation per hour (mm)
- `SLP` — sea level pressure (hPa)
- `RH` — relative humidity (%)
- `SMER` — swell direction degrees *(only on wave models)*
- `SHGT` — significant wave height m *(only on wave models)*
- `SPER` — swell period s *(only on wave models)*
- `initdate` — `"YYYY-MM-DD HH:MM:SS"` UTC

For swell data, repeat the call with `id_model=83` (or `63`) and merge the arrays — align by hour offset.

---

## Scoring rules (encoded in `scripts/score_spots.py`)

For each spot in `references/spots.json` × each forecast day:

### Wind match (40 pts)
- Forecast direction matches one of `viento_optimo` → +40
- Within 1 step (e.g. optimal N, forecast NE) → +20
- Directly onshore (opposite of an optimal direction) → -20 penalty

### Swell direction match (30 pts)
- Matches `swell_optimo` → +30
- Within 1 step → +15

### Wave size (30 pts)
- height > 0.5 m AND period > 8 s → +15 (minimum surfable)
- height > 1.0 m AND period > 10 s → +30 (good)
- height > 1.5 m AND period > 12 s → +30 + flag "🔥 potente"

### Threshold
"Surfable" requires `score ≥ 50 AND swell_height > 0.3 m`.

### Cardinal conversion
| Degrees | Cardinal |
|---------|----------|
| 337.5–22.5 | N |
| 22.5–67.5 | NE |
| 67.5–112.5 | E |
| 112.5–157.5 | SE |
| 157.5–202.5 | S |
| 202.5–247.5 | SO / SW |
| 247.5–292.5 | O / W |
| 292.5–337.5 | NO / NW |

Treat `SO=SW`, `O=W`, `NO=NW` as equivalent (the site uses Spanish).

---

## Output format

**Default:** run `scripts/run_surf_html.sh` and hand the user the resulting HTML via a `computer://` link plus a 1–2 sentence summary. Do not dump the HTML, JSON, or per-day breakdowns into the reply — that defeats the whole token-saving design.

Reply template:
```
[Una oración con la conclusión: "Los mejores spots los próximos días son X, Y y Z" /
 "No hay condiciones en ningún spot esta semana"]

[Ver forecast completo](computer:///<absolute_path_returned_by_the_script>)
```

If the user explicitly asks for a chat-only / no-file answer (e.g. "decime cortito por chat"), skip the HTML and use this terse emoji format instead:
```
🌊 Forecast — próximos X días

📅 [Día, fecha]
✅ [Spot] ([región]) — viento [dir] [vel]kt, swell [altura]m / [período]s [dir]
…
```

Emoji legend (still used inside the HTML and for chat-only mode):
- ✅ Score ≥ 70 — buenas condiciones
- ⚠️ Score 50–69 — condiciones moderadas
- ❌ Score < 50 — no recomendado

---

## Context about Uruguay surf

- **Western spots (Montevideo, Costa de Oro)** sit on the Río de la Plata estuary — they need high-period swell (>12 s) to receive Atlantic energy. Less consistent than Maldonado/Rocha.
- **Maldonado and Rocha** face the Atlantic directly and receive swells much more frequently.
- **Two main swell windows**:
  - **S/SE swells** from South Atlantic storms — hit all of Uruguay
  - **E/ESE swells** — hit Maldonado/Rocha more directly (La Barra, PDE, PDD, La Pedrera)
- **El Pampero (SW wind)**: creates choppy local surf on western spots
- **Best months**: March–May and September–November
- **Onshore winds to avoid**: E for east-facing spots, SE for south-facing

---

## Spot data

All 40 spots with their optimal conditions are in `references/spots.json`. Key fields:
- `viento_optimo`: list of preferred wind directions (offshore)
- `swell_optimo`: list of preferred swell directions
- `windguru_id`: Windguru spot ID for that specific location
- `valoracion`: quality rating 1–5
- `notas`: important context

---

## Specific region / spot queries

If the user asks about a specific region ("¿cómo está Punta del Este?") or spot ("¿está buena La Moza?"):
1. Filter `spots.json` to that region/spot
2. Pass the spot's `windguru_id` to `fetch_windguru.sh` instead of (or in addition to) the reference station
3. Present a more detailed breakdown for the requested spot(s)

---

## Evals / test prompts

- "¿Cuándo va a estar bueno para surfear esta semana?"
- "¿Está bueno el mar mañana en algún lado?"
- "Quiero ir a surfear el fin de semana, ¿a dónde voy?"
- "¿Cómo están las condiciones en Punta del Este?"
- "¿La Moza va a estar buena el sábado?"
- "Vivo en Montevideo, ¿cuándo hay olas?"
