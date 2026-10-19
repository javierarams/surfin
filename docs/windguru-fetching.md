# How the skill reads Windguru

Status: adopted 2026-10-19. Applies to `skills/surf-forecast-uruguay/scripts/fetch_windguru.py`.

## Problem

The skill used to call `https://www.windguru.cz/int/iapi.php?q=forecast...` with curl and a fixed
`Referer` header, then fell back to scraping the spot page HTML. Reproduced on 2026-10-19 with
`scripts/run_surf_html.sh`, it never produced a report:

1. On macOS (bash 3.2) `run_surf_html.sh` died at `mapfile`, and `fetch_windguru.sh` at
   `declare -A`, before any request was sent.
2. With bash 4, the endpoint answered `401 {"return":"error","message":"Unauthorized!"}` with the
   documented Referer, a spot Referer, or none. The site now loads forecasts from
   `www.windguru.net/int/iapi.php`, with `rundef` and `cachefix` parameters the page computes from
   another API call, after a token exchange (`q=token_refresh`, `WgToken`).
3. The HTML fallback was a 9 KB JavaScript shell with no forecast in it, so every station ended
   as "html_fallback not yet parsed" and the scorer produced no days.
4. Even with valid JSON, the parser read swell from `SHGT`/`SPER`/`SMER`. The wave model actually
   returns `HTSGW`/`PERPW`/`DIRPW` (and `SMER` is Windguru's legend id for *wind* direction), so
   every spot would have been scored without waves.
5. When a spot's station was missing, the scorer silently used another station.

Each failure pushed the agent using the skill toward other sources, which is what the captain saw:
"siempre termina yendo por la otra opción que no tiene buen pronóstico".

## Options considered

| Option | Verdict |
|---|---|
| Fix the curl call (new host, compute `rundef`/`cachefix`, handle the token) | Works today, but depends on undocumented parameters that already changed once without notice. The next change breaks it again, silently. |
| Scrape the rendered DOM tables | Values are rounded for display, night hours and steps depend on site options, and the markup is presentation. Fragile. |
| `chrome-devtools-axi` (Chrome DevTools MCP CLI) | Good for investigating by hand (used for this design and for the by-eye checks), but its output is formatted for an agent: the network list is truncated, paged and flooded by more than 1000 ad requests per page, response bodies need one call per request, and it bootstraps `chrome-devtools-mcp@latest` through npx on cold start. Not a stable base for an unattended pipeline. |
| Raw Chrome DevTools Protocol over a WebSocket | No dependencies, but a hand-written protocol client to maintain. |
| **Playwright driving headless Chrome, capturing the page's own forecast responses** | **Chosen.** |

## Why Playwright with the page's own responses

- **The page does the hard part.** It knows the current API host, parameters and token. We only
  listen to the JSON responses it receives (`page.on("response")`), so a change in how Windguru
  calls its API does not reach us as long as the page still shows the forecast.
- **Matching on content, not URLs.** A response counts as a forecast if its JSON has `fcst` and
  `id_model` for the station being read; the spot's model list is the response with `tabs` and
  `spots`. URL paths are not part of the contract.
- **Exactly what the site shows.** The JSON is what the page draws its tables from. A comparison
  of 252 cells (3 spots × 12 hours × 7 rows) between the report and the site's "GFS 13 km" table
  matched one to one once the report rounded halves up like the site does.
- **Pinned and scriptable.** `playwright==1.63.0` in `requirements.txt`, installed by
  `scripts/setup.sh` into `~/.cache/surf-forecast-uruguay/venv`. It drives the installed Google
  Chrome (`channel="chrome"`) and only downloads Playwright's Chromium when Chrome is missing.
- **Python**, like the rest of the pipeline.

## How it behaves

- One browser, one page, stations read one at a time with a 2 s pause. A cold run of the 31
  stations takes about 2 minutes; each station is cached 90 min, so repeated runs take seconds.
- Chrome starts with `--host-resolver-rules` that only resolve `windguru.cz`, `windguru.net` and
  `unpkg.com` (the page needs a map library from there). Ads and trackers never load, which
  keeps page loads around 2 s and avoids fake ad impressions. Request interception was not used
  because it disables the browser's HTTP cache, which would re-download Windguru's scripts for
  every station.
- The wind model is GFS 13 km (`--wind-model 3`). The wave model is the one the spot's page pairs
  with it (`tabs[].id_model_wave`): GFS-Wave 25 km for every station in `spots.json` today. A spot
  without a wave model on Windguru (Playa Buceo, Atlántida) is read wind-only and the report says
  so.
- `--bbox` (Uruguay in `run_surf_html.sh`) rejects a station whose Windguru coordinates are
  elsewhere.

## Failure policy

Windguru is the only source. If any station cannot be read, `fetch_windguru.py` retries it once,
then exits 1 with one line per station and cause, and `run_surf_html.sh` writes no report. Causes
are specific: page not loading, HTTP status, unknown spot, model not offered, coordinates outside
the box, or a timeout naming the models that did not arrive. After 3 stations in a row fail it
stops and marks the rest "not attempted" instead of waiting on each one. `SKILL.md` tells the agent
to relay the error rather than look elsewhere.

## Station data found wrong while testing

Reading every station showed four `spots.json` entries pointing at the wrong place; they were
corrected to the nearest station Windguru has:

| Spot | Old id (what it really is) | New id |
|---|---|---|
| La Pantalla (Montevideo) | 49322, "Playa Honda" in Lanzarote, Spain | 1911, Montevideo - Playa Malvin |
| Playa Carrasco (Montevideo) | 37673, Los Botes, La Paloma (Rocha) | 1911, Montevideo - Playa Malvin |
| Pesqueros, Sta. Teresa (Rocha) | 37673, Los Botes, La Paloma, about 100 km away | 93781, santa teresa - la moza |
| Arroyo Pando (Costa de Oro) | 34546, a user spot at -34.85, -56.93, about 95 km west | 313168, Desemb. Arroyo Pando (Canelones), its own `zona_station` |

Windguru has no spot named La Pantalla or Carrasco, so Playa Malvin is the closest stand-in.
