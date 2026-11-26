# surfin

The `surf-forecast-uruguay` skill: reads the Windguru forecast for the Uruguayan surf spots in
`skills/surf-forecast-uruguay/references/spots.json`, scores each spot against its ideal wind and
swell, and writes an HTML report with every spot, its scores and its Windguru station's hourly
forecast.

```bash
bash skills/surf-forecast-uruguay/scripts/run_surf_html.sh --out /tmp/surf_forecast.html
```

The first run installs Playwright into `~/.cache/surf-forecast-uruguay/venv` (override with
`SURF_FORECAST_HOME`). `skills/surf-forecast-uruguay/SKILL.md` is the guide for agents using the
skill; `docs/windguru-fetching.md` explains how and why Windguru is read through a browser.

## Web page

https://javierarams.github.io/surfin/ is `docs/index.html` on `main`, served by GitHub Pages from
that folder (Pages source: "Deploy from a branch", branch `main`, folder `/docs`). Nothing runs in
GitHub Actions: the report is built locally and the page is committed.

To publish a fresh report, from the repository root on an up-to-date `main`:

```bash
bash skills/surf-forecast-uruguay/scripts/run_surf_html.sh --no-cache --out docs/index.html
git add docs/index.html
git commit -m "chore: refresh surf report"
git push
```

`--no-cache` reads every station fresh from Windguru. If any station cannot be read, the script
exits non-zero, names each station and the cause, and leaves `docs/index.html` untouched, so the
site keeps the last complete report. The page shows when it was generated, in Uruguay time, and
links to Windguru as the data source.

Tests (stdlib `unittest`; the fetcher tests run when Playwright is importable):

```bash
python3 -m unittest discover -s tests
```
