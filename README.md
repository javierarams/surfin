# surfin

The `surf-forecast-uruguay` skill: reads the Windguru forecast for the Uruguayan surf spots in
`skills/surf-forecast-uruguay/references/spots.json`, scores each spot against its ideal wind and
swell, and writes an HTML report of the best spots and days.

```bash
bash skills/surf-forecast-uruguay/scripts/run_surf_html.sh --out /tmp/surf_forecast.html
```

The first run installs Playwright into `~/.cache/surf-forecast-uruguay/venv` (override with
`SURF_FORECAST_HOME`). `skills/surf-forecast-uruguay/SKILL.md` is the guide for agents using the
skill; `docs/windguru-fetching.md` explains how and why Windguru is read through a browser.

Tests (stdlib `unittest`; the fetcher tests run when Playwright is importable):

```bash
python3 -m unittest discover -s tests
```
