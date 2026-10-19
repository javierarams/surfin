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

Find when and where conditions will be good for surfing across Uruguay's coast, from Montevideo through Costa de Oro, Maldonado, and Rocha. Data source: **Windguru**, read through a real browser session.

---

## Windguru is the only source

The scripts open `https://www.windguru.cz/<id>` in headless Chrome (Playwright) and keep the forecast JSON that the page itself downloads. Nothing calls Windguru's API by hand.

- **Do not** fetch Windguru with curl, `WebFetch`, `web_fetch` or "Claude in Chrome", and do not hand-build `iapi.php` URLs: the API needs parameters and a token that only the page computes.
- **Do not** substitute another forecast site, a web search, or your own estimate when the run fails. Tell the user Windguru could not be read and why (see "When the run fails").

---

## Quick path (recommended, token-minimal)

**One command, one output line, one HTML deliverable.**

```bash
bash scripts/run_surf_html.sh
# stdout: <absolute path to surf_forecast.html>
```

That single script does everything end-to-end:
1. `scripts/setup.sh` makes sure a virtualenv with the pinned Playwright exists in `~/.cache/surf-forecast-uruguay/venv` (first run only: needs network, Python 3.10+, and Google Chrome or a one-time Chromium download).
2. `scripts/fetch_windguru.py` reads every station in `references/spots.json` (each `windguru_id` and `zona_station`), one page at a time, 2 s apart, ads and trackers blocked. Each station is cached 90 min in `~/.cache/surf-forecast-uruguay/windguru/`, so a repeated run takes seconds; a cold run takes about 2 minutes.
3. Pairs the wind model hours (GFS 13 km) with the wave model Windguru shows next to it (GFS-Wave), in UY local time (UTC-3).
4. Scores every spot against **its own station** (never a substitute).
5. Builds a self-contained HTML page:
   - **Top:** cards with the best-scoring spot/day combos across the forecast window (color-coded ✅/⚠️/❌).
   - **Bottom:** up to **3 Windguru-style hourly tables** (rows: wind/gusts/dir, ola/period/dir, temp, nubosidad, precip; columns: each hour for the next 7 days). Tables are deduped by `windguru_id` and only included for spots with at least one acceptable day (score ≥ 50, swell > 0.3 m). If fewer than 3 spots are acceptable, the shortlist is filled with the best-scoring ones. Cells are rounded like windguru.cz, so they can be compared with the site's "GFS 13 km" table one to one.

The script prints **only the absolute path** on stdout; don't echo the HTML into your reply. **Hand the user the file path with a `computer://` link** (see "Output format" below).

### Options
```bash
bash scripts/run_surf_html.sh --out /Users/me/surf.html   # explicit destination
bash scripts/run_surf_html.sh --max-detail 5              # more detail tables
bash scripts/run_surf_html.sh --days 5                    # fewer days in tables
bash scripts/run_surf_html.sh --no-cache                  # re-read every station now
bash scripts/run_surf_html.sh --wind-model 117            # IFS-HRES 9 km + IFS-WAM 9 km
```

### When the run fails
The script exits non-zero, writes no report, and stderr names each station and the cause, for example:
```
Windguru could not be read for 2 of 31 stations:
  station 37691 (La Virgen, El Emir): page https://www.windguru.cz/37691 did not deliver id_model 83 within 45s (...)
  station 405911 (Montoya): not attempted: 3 stations in a row failed, Windguru looks unreachable
```
Relay that to the user in one or two sentences. Typical causes: no network or Windguru down (retry later), first-run setup failed (`bash scripts/setup.sh` prints the pip/Chrome error), or a `windguru_id` in `spots.json` pointing outside Uruguay (fix the id). Stations that did load stay cached, so a retry only re-reads the failed ones.

### Region- or spot-specific queries
For a focused query ("¿cómo está Punta del Este?", "¿La Moza el sábado?") run the steps by hand and filter the scoring. Reading all stations is cheap once cached:
```bash
PY="$(bash scripts/setup.sh --print-python)"
"$PY" scripts/fetch_windguru.py --spots references/spots.json --bbox=-35.2,-30.0,-58.6,-53.0 --out /tmp/raw.json
python3 scripts/parse_windguru.py /tmp/raw.json > /tmp/normalized.json
python3 scripts/score_spots.py /tmp/normalized.json references/spots.json --region "Maldonado" > /tmp/scored.json
```
…then either render with `generate_html.py` or summarize in chat. `fetch_windguru.py <id> [<id> ...]` reads single stations; add `--verbose` for progress lines, `--headed` to watch the browser.

### Data availability vs the Windguru website
Per station we keep every forecast model the page loads and use:
- **Wind, gusts, direction, temperature, total cloud cover, rain (mm/1h):** GFS 13 km (`id_model` 3), the site's "GFS 13 km" table.
- **Wave height, period, direction:** the wave model Windguru pairs with GFS on that spot's page, GFS-Wave 25 km (`id_model` 83). The same model fills the wave rows of the site's top "WG" table.

The site's top "WG" table blends several wind models inside the browser and is not downloaded as data, so we use GFS 13 km instead. Its per-layer cloud split (high / mid / low) and the "Windguru rating" stars are not used; we show total cloud cover and our own scoring (see "Scoring rules").

Windguru has **no wave model** for two stations: Playa Buceo (58328) and Atlántida (259217). Those spots are scored on wind only, can never reach "surfable", and the report says "Windguru no publica oleaje para esta estación".

---

## Windguru stations by zone

Every spot is scored against its `windguru_id`. "Windguru spot" is the name the site shows for that id.

### 📍 Montevideo
| Windguru spot | ID | Waves | Spots |
|---|---|---|---|
| Playa Pocitos-Montevideo-Uy | 34552 | GFS-Wave 25 km | Playa Pocitos |
| Playa Buceo | 58328 | none on Windguru | Playa Buceo |
| Montevideo - Playa Malvin | 1911 | GFS-Wave 25 km | Playa Honda; La Pantalla; Playa Carrasco |

### 📍 Canelones / Costa de Oro
| Windguru spot | ID | Waves | Spots |
|---|---|---|---|
| Canelones - Santa Lucía del Este | 119294 | GFS-Wave 25 km | Santa Lucía del Este |
| Desemb. Arroyo Pando (Canelones) | 313168 | GFS-Wave 25 km | Arroyo Pando |
| Brava, Atlántida, Costa de Oro | 259217 | none on Windguru | Atlántida |
| Parque del Plata - Solis Chico | 212902 | GFS-Wave 25 km | Parque del Plata |
| Cuchilla Alta | 422330 | GFS-Wave 25 km | Cuchilla Alta; Balneario Argentino |
| Desembocadura Solis | 32742 | GFS-Wave 25 km | Jaureguiberry; Solís |

### 📍 Maldonado (Piriápolis → Punta del Este → José Ignacio)
| Windguru spot | ID | Waves | Spots |
|---|---|---|---|
| Punta Negra | 116713 | GFS-Wave 25 km | San Francisco, Piriápolis; Faroleta, Piriápolis |
| Corralitos, Piriapolis, UY | 37672 | GFS-Wave 25 km | Corralitos |
| Punta Ballena | 366061 | GFS-Wave 25 km | El Chileno |
| El Emir, Punta del Este, UY | 37691 | GFS-Wave 25 km | La Virgen; El Emir |
| La Olla. Punta del Este | 405894 | GFS-Wave 25 km | Los Dedos; La Olla |
| La Plage. Punta del Este | 405896 | GFS-Wave 25 km | La Plage |
| La Boca de La Barra | 333460 | GFS-Wave 25 km | Boca de la Barra |
| La Posta del Cangrejo | 333463 | GFS-Wave 25 km | Posta del Cangrejo |
| Montoya.Punta del Este | 405911 | GFS-Wave 25 km | Montoya |
| Punta del Este - Playa Bikini - La Barra | 120882 | GFS-Wave 25 km | Bikini |
| Jose Ignacio | 112369 | GFS-Wave 25 km | José Ignacio |

### 📍 Rocha (La Paloma → La Pedrera → Cabo Polonio → PDD → Sta. Teresa)
| Windguru spot | ID | Waves | Spots |
|---|---|---|---|
| Corumba. La Paloma. Rocha | 405916 | GFS-Wave 25 km | Corumbá |
| Los Botes, Zanja Honda, La Balconada, La Paloma, UY | 37673 | GFS-Wave 25 km | Playa Los Botes |
| Zanja Honda | 333480 | GFS-Wave 25 km | Playa Zanja Honda |
| La Balconada | 333481 | GFS-Wave 25 km | La Balconada |
| Rocha - La Aguada | 119340 | GFS-Wave 25 km | La Aguada |
| Playa del Barco.La Pedrera.Rocha | 405921 | GFS-Wave 25 km | Playa del Barco |
| Rocha - La Pedre - Desplayado | 119342 | GFS-Wave 25 km | El Desplayado |
| Cabo Polonio | 50158 | GFS-Wave 25 km | Playa Sur, Cabo Polonio |
| Playa la Viuda - Punta del Diablo | 467316 | GFS-Wave 25 km | La Viuda PDD |
| Punta del Diablo | 172706 | GFS-Wave 25 km | Bahía Punta del Diablo; El Rivero PDD |
| santa teresa- la moza | 93781 | GFS-Wave 25 km | La Moza, Sta. Teresa; Pesqueros, Sta. Teresa |

`fetch_windguru.py --bbox` (set by `run_surf_html.sh` to Uruguay) rejects a station whose Windguru coordinates fall outside Uruguay, naming the spot, so a wrong id fails loudly instead of forecasting another coast.

---

## Windguru forecast JSON (what the page downloads)

`fetch_windguru.py` output keeps, per station, `spotname`, `lat`/`lon`, `wind_model`, `wave_model` (null when the spot has none) and `models.<id_model>`, each the page's forecast response. Under `fcst`:
- `initstamp` (Unix seconds) / `initdate` (`"YYYY-MM-DD HH:MM:SS"` UTC) and `hours` (offsets from init: 1 h steps, then 3 h)
- Wind models: `WINDSPD` (knots), `GUST` (knots), `WINDDIR` (degrees FROM), `TMP` / `TMPE` (°C), `TCDC` / `HCDC` / `MCDC` / `LCDC` (cloud %), `APCP1` (mm/1h), `SLP` (hPa), `RH` (%)
- Wave models: `HTSGW` (wave height m), `PERPW` (period s), `DIRPW` (degrees FROM); swell partitions `SWELL1`/`SWPER1`/`SWDIR1`, `SWELL2`/`SWPER2`/`SWDIR2`; wind sea `WVHGT`/`WVPER`/`WVDIR`

`parse_windguru.py` maps `HTSGW`/`PERPW`/`DIRPW` to `swell_m`/`swell_period_s`/`swell_dir` (the site's "Wave" rows) and pairs wind and wave hours by UTC time.

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

Spanish and English names are equivalent: `SO=SW`, `O=W`, `NO=NW`, `SSO=SSW`, `OSO=WSW`, `ONO=WNW`, `NNO=NNW`.

---

## Output format

**Default:** run `scripts/run_surf_html.sh` and hand the user the resulting HTML via a `computer://` link plus a 1-2 sentence summary. Do not dump the HTML, JSON, or per-day breakdowns into the reply; that defeats the whole token-saving design.

Reply template:
```
[Una oración con la conclusión: "Los mejores spots los próximos días son X, Y y Z" /
 "No hay condiciones en ningún spot esta semana"]

[Ver forecast completo](computer:///<absolute_path_returned_by_the_script>)
```

If the user explicitly asks for a chat-only / no-file answer (e.g. "decime cortito por chat"), skip the HTML and use this terse emoji format instead (get the numbers by running the steps in "Region- or spot-specific queries" without `--region` and reading `/tmp/scored.json`):
```
🌊 Forecast - próximos X días

📅 [Día, fecha]
✅ [Spot] ([región]) - viento [dir] [vel]kt, swell [altura]m / [período]s [dir]
…
```

Emoji legend (still used inside the HTML and for chat-only mode):
- ✅ Score ≥ 70 - buenas condiciones
- ⚠️ Score 50-69 - condiciones moderadas
- ❌ Score < 50 - no recomendado

---

## Context about Uruguay surf

- **Western spots (Montevideo, Costa de Oro)** sit on the Río de la Plata estuary; they need high-period swell (>12 s) to receive Atlantic energy. Less consistent than Maldonado/Rocha.
- **Maldonado and Rocha** face the Atlantic directly and receive swells much more frequently.
- **Two main swell windows**:
  - **S/SE swells** from South Atlantic storms hit all of Uruguay
  - **E/ESE swells** hit Maldonado/Rocha more directly (La Barra, PDE, PDD, La Pedrera)
- **El Pampero (SW wind)**: creates choppy local surf on western spots
- **Best months**: March-May and September-November
- **Onshore winds to avoid**: E for east-facing spots, SE for south-facing

---

## Spot data

All 40 spots with their optimal conditions are in `references/spots.json`. Key fields:
- `viento_optimo`: list of preferred wind directions (offshore)
- `swell_optimo`: list of preferred swell directions
- `windguru_id`: Windguru spot ID the spot is scored against
- `valoracion`: quality rating 1-5
- `notas`: important context

---

## Specific region / spot queries

If the user asks about a specific region ("¿cómo está Punta del Este?") or spot ("¿está buena La Moza?"):
1. Run the steps in "Region- or spot-specific queries" with `--region`, or filter `scored.json` to the spot's `slug`
2. Present a more detailed breakdown for the requested spot(s), naming the Windguru station it comes from

---

## Evals / test prompts

- "¿Cuándo va a estar bueno para surfear esta semana?"
- "¿Está bueno el mar mañana en algún lado?"
- "Quiero ir a surfear el fin de semana, ¿a dónde voy?"
- "¿Cómo están las condiciones en Punta del Este?"
- "¿La Moza va a estar buena el sábado?"
- "Vivo en Montevideo, ¿cuándo hay olas?"
