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

## Daily page

https://javierarams.github.io/surfin/ is rebuilt every day at 09:00 UTC (06:00 in Uruguay) by
`.github/workflows/daily-page.yml`. The workflow runs the tests, then `run_surf_html.sh` on a
GitHub-hosted runner with every station read fresh from Windguru (`--no-cache`), and deploys the
report to GitHub Pages (Pages source: GitHub Actions). The page shows when it was generated, in
Uruguay time, and links to Windguru as the data source.

If any station cannot be read, the run fails and nothing is deployed, so the site keeps the last
complete report; the failed run's "Build report" step names each station and the cause. Only the
Playwright virtualenv is cached between runs, never forecast data.

To rebuild it now: Actions → "Daily page" → "Run workflow" on `main`, or

```bash
gh workflow run daily-page.yml --ref main
```

Only runs on `main` deploy; on another branch the report is built and kept as the run's
`github-pages` artifact.

Tests (stdlib `unittest`; the fetcher tests run when Playwright is importable):

```bash
python3 -m unittest discover -s tests
```
