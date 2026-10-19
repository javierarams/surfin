#!/usr/bin/env python3
"""fetch_windguru.py - Read Windguru forecasts through a real browser session.

For each station it opens https://www.windguru.cz/<id> in headless Chrome (Playwright) and keeps
the forecast JSON that the page itself downloads to draw its tables. The page works out the API
host, parameters and token, so this keeps working when Windguru changes them.

The wind model is chosen with --wind-model (default 3, GFS 13 km). The wave model is the one the
spot's page pairs with it in its own table (for GFS that is GFS-Wave 25 km, id 83, or 16 km, id
84). Some spots have no wave model on Windguru at all; they are read with wind only and
"wave_model" is null.

Usage:
    python3 fetch_windguru.py --spots references/spots.json --out raw.json   # every station
    python3 fetch_windguru.py <id_spot> [<id_spot> ...] > raw.json
    python3 fetch_windguru.py --wind-model 117 313168     # IFS-HRES 9 km (+ IFS-WAM 9 km)
    python3 fetch_windguru.py --bbox=-35.2,-30,-58.6,-53 313168   # reject stations outside it
    python3 fetch_windguru.py --no-cache 313168           # ignore cached stations
    python3 fetch_windguru.py --headed --verbose 313168   # watch the browser
    python3 fetch_windguru.py --check-browser             # launch and close a browser, nothing else

Output (stdout, or --out FILE):
    {
      "stations": {
        "<id_spot>": {
          "id_spot": "313168",
          "spotname": "Desemb. Arroyo Pando (Canelones)",
          "country": "Uruguay", "tz": "America/Montevideo", "lat": -34.8007, "lon": -55.8755,
          "page_url": "https://www.windguru.cz/313168",
          "fetched_at": "2026-10-19T14:05:00Z",
          "wind_model": "3", "wave_model": "83",
          "models": {"3": <iapi forecast JSON>, "83": <iapi forecast JSON>, ...}
        }
      }
    }

Every forecast model the page loaded is kept in "models".

If any station cannot be read, nothing is written, one line per failed station goes to stderr, and
the exit status is 1. There is no fallback to another data source.

Each station is cached for 90 min (--max-age-min) in --cache-dir. Stations are read one at a
time with --delay seconds between page loads; only Windguru hosts (and unpkg.com, which serves a
library the page needs) resolve, so no ads or trackers load.
"""

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Response, sync_playwright

PAGE_URL = "https://www.windguru.cz/{id_spot}"
ALLOWED_HOSTS = ("windguru.cz", "*.windguru.cz", "windguru.net", "*.windguru.net", "unpkg.com")
DEFAULT_CACHE_DIR = Path(
    os.environ.get("SURF_FORECAST_HOME")
    or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "surf-forecast-uruguay"
) / "windguru"
MAX_CONSECUTIVE_FAILURES = 3


class Bbox:
    def __init__(self, spec: str):
        try:
            self.south, self.north, self.west, self.east = (float(v) for v in spec.split(","))
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected SOUTH,NORTH,WEST,EAST, got {spec!r}")

    def contains(self, lat, lon) -> bool:
        try:
            return self.south <= float(lat) <= self.north and self.west <= float(lon) <= self.east
        except (TypeError, ValueError):
            return False

    def __str__(self) -> str:
        return f"lat {self.south}..{self.north} / lon {self.west}..{self.east}"


class StationError(Exception):
    def __init__(self, msg: str, retry: bool = True):
        super().__init__(msg)
        self.retry = retry


def log(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, file=sys.stderr, flush=True)


def launch_browser(pw, headed: bool):
    args = ["--host-resolver-rules=MAP * ~NOTFOUND, "
            + ", ".join(f"EXCLUDE {h}" for h in ALLOWED_HOSTS)]
    try:
        return pw.chromium.launch(channel="chrome", headless=not headed, args=args)
    except PlaywrightError as chrome_err:
        try:
            return pw.chromium.launch(headless=not headed, args=args)
        except PlaywrightError as chromium_err:
            raise SystemExit(
                "no usable browser: Google Chrome failed to launch "
                f"({str(chrome_err).splitlines()[0]}) and Playwright's Chromium failed too "
                f"({str(chromium_err).splitlines()[0]}). Run scripts/setup.sh."
            )


class Capture:
    """Collects the JSON responses the spot page receives for one station."""

    def __init__(self, id_spot: str):
        self.id_spot = id_spot
        self.models: dict[str, dict] = {}
        self.spot_info: dict | None = None
        self.tabs: list[dict] = []
        self.unknown_spot = False
        self.api_errors: list[str] = []

    def on_response(self, resp: Response) -> None:
        if "json" not in (resp.headers.get("content-type") or ""):
            return
        try:
            body = resp.json()
        except Exception:
            return
        if not isinstance(body, dict):
            return
        if body.get("return") == "error":
            self.api_errors.append(f"HTTP {resp.status} {body.get('message') or 'error'}")
            return
        if isinstance(body.get("fcst"), dict) and "id_model" in body:
            if str(body.get("id_spot")) == self.id_spot:
                self.models[str(body["id_model"])] = body
            return
        if "tabs" in body and "spots" in body:
            spots = body["spots"]
            if isinstance(spots, dict) and self.id_spot in spots:
                self.spot_info = spots[self.id_spot]
                self.tabs = [t for t in body["tabs"] if isinstance(t, dict)]
            elif not body["tabs"]:
                self.unknown_spot = True

    def required_models(self, wind_model: str, bbox: Bbox | None) -> tuple[str, str | None]:
        """Wind model and the wave model the page pairs with it (None if the spot has none)."""
        info = self.spot_info or {}
        if bbox and not bbox.contains(info.get("lat"), info.get("lon")):
            where = f" ({info['country']})" if info.get("country") else ""
            raise StationError(
                f"Windguru spot {(info.get('spotname') or '').strip()!r} is at "
                f"{info.get('lat')}, {info.get('lon')}{where}, outside {bbox}; "
                "fix windguru_id in spots.json", retry=False)
        tab = next((t for t in self.tabs if str(t.get("id_model")) == wind_model), None)
        if tab is None:
            offered = ", ".join(str(m) for m in info.get("models") or []) or "none"
            raise StationError(f"Windguru does not offer id_model {wind_model} for this spot "
                               f"(it offers {offered})", retry=False)
        wave = tab.get("id_model_wave")
        return wind_model, (str(wave) if wave else None)


def validate_model(id_model: str, raw: dict) -> None:
    fcst = raw.get("fcst") or {}
    if not fcst.get("hours"):
        raise StationError(f"forecast for id_model {id_model} has no hours")
    if not (fcst.get("initstamp") or fcst.get("initdate")):
        raise StationError(f"forecast for id_model {id_model} has no init time")


def read_station(page: Page, id_spot: str, wind_model: str, bbox: Bbox | None,
                 timeout_s: float) -> dict:
    url = PAGE_URL.format(id_spot=id_spot)
    cap = Capture(id_spot)
    page.on("response", cap.on_response)
    try:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        except PlaywrightError as e:
            raise StationError(f"could not load {url}: {str(e).splitlines()[0]}")
        if resp is None or resp.status >= 400:
            raise StationError(f"{url} answered HTTP {resp.status if resp else '?'}")

        needed: list[str] | None = None
        wave_model = None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if cap.unknown_spot:
                raise StationError(f"Windguru has no spot {id_spot} ({url} shows no forecast)",
                                   retry=False)
            if needed is None and cap.spot_info is not None:
                _, wave_model = cap.required_models(wind_model, bbox)
                needed = [wind_model] + ([wave_model] if wave_model else [])
            if needed is not None and all(m in cap.models for m in needed):
                break
            page.wait_for_timeout(250)
        else:
            if needed is None:
                waiting_for = "the spot's model list (forecast_spot)"
            else:
                waiting_for = "id_model " + ", ".join(m for m in needed if m not in cap.models)
            detail = f"; API errors: {'; '.join(cap.api_errors)}" if cap.api_errors else ""
            raise StationError(
                f"page {url} did not deliver {waiting_for} within {timeout_s:.0f}s "
                f"(got models: {', '.join(sorted(cap.models, key=int)) or 'none'}; "
                f"title: {page.title()!r}){detail}")
    finally:
        page.remove_listener("response", cap.on_response)

    for m in needed:
        validate_model(m, cap.models[m])

    info = cap.spot_info or {}
    return {
        "id_spot": id_spot,
        "spotname": (info.get("spotname") or "").strip() or None,
        "country": info.get("country"),
        "tz": info.get("tz"),
        "lat": info.get("lat"),
        "lon": info.get("lon"),
        "page_url": url,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wind_model": wind_model,
        "wave_model": wave_model,
        "models": cap.models,
    }


def cache_file(cache_dir: Path, id_spot: str) -> Path:
    return cache_dir / f"station_{id_spot}.json"


def load_cached(cache_dir: Path, id_spot: str, wind_model: str, bbox: Bbox | None,
                max_age_min: float) -> dict | None:
    path = cache_file(cache_dir, id_spot)
    try:
        if time.time() - path.stat().st_mtime >= max_age_min * 60:
            return None
        with open(path) as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    if record.get("wind_model") != wind_model:
        return None
    if bbox and not bbox.contains(record.get("lat"), record.get("lon")):
        return None
    needed = [m for m in (record.get("wind_model"), record.get("wave_model")) if m]
    if not all(m in (record.get("models") or {}) for m in needed):
        return None
    return record


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def stations_from_spots(path: Path) -> tuple[list[str], dict[str, str]]:
    """Station ids in spots.json order, labelled with the spots that use them."""
    with open(path) as f:
        spots = json.load(f)
    ids: list[str] = []
    users: dict[str, list[str]] = {}
    for spot in spots:
        for key in ("windguru_id", "zona_station"):
            sid = str(spot.get(key) or "")
            if not sid:
                continue
            if sid not in users:
                ids.append(sid)
                users[sid] = []
            name = spot.get("name") if key == "windguru_id" else spot.get("zona_station_name")
            if name and name not in users[sid]:
                users[sid].append(name)
    return ids, {sid: ", ".join(names) for sid, names in users.items() if names}


def describe(id_spot: str, labels: dict[str, str]) -> str:
    return f"station {id_spot} ({labels[id_spot]})" if id_spot in labels else f"station {id_spot}"


def reopen(context, page: Page) -> Page:
    try:
        page.close()
    except PlaywrightError:
        pass
    return context.new_page()


def fetch_all(ids: list[str], labels: dict[str, str], args: argparse.Namespace) -> tuple[dict, dict]:
    stations: dict[str, dict] = {}
    failures: dict[str, str] = {}
    pending: list[str] = []
    for id_spot in ids:
        cached = None if args.no_cache else load_cached(
            args.cache_dir, id_spot, args.wind_model, args.bbox, args.max_age_min)
        if cached:
            stations[id_spot] = cached
            log(args.verbose, f"{describe(id_spot, labels)}: cached")
        else:
            pending.append(id_spot)
    if not pending:
        return stations, failures

    with sync_playwright() as pw:
        browser = launch_browser(pw, args.headed)
        context = browser.new_context(locale="en-US", timezone_id="America/Montevideo")
        page = context.new_page()
        consecutive = 0
        for n, id_spot in enumerate(pending):
            if n:
                time.sleep(args.delay)
            started = time.monotonic()
            record = None
            for attempt in (1, 2):
                try:
                    record = read_station(page, id_spot, args.wind_model, args.bbox,
                                          args.timeout)
                    break
                except PlaywrightError as e:
                    err = StationError(f"browser error: {str(e).splitlines()[0]}")
                    page = reopen(context, page)
                except StationError as e:
                    err = e
                if attempt == 2 or not err.retry:
                    failures[id_spot] = str(err)
                    break
                log(args.verbose, f"{describe(id_spot, labels)}: {err}; retrying")
                time.sleep(args.delay + 3)
            if record is None:
                log(args.verbose, f"{describe(id_spot, labels)}: FAILED")
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    for rest in pending[n + 1:]:
                        failures[rest] = (f"not attempted: {consecutive} stations in a row "
                                          "failed, Windguru looks unreachable")
                    break
                continue
            consecutive = 0
            stations[id_spot] = record
            write_json_atomic(cache_file(args.cache_dir, id_spot), record)
            log(args.verbose, f"{describe(id_spot, labels)}: ok, Windguru spot "
                              f"{record.get('spotname')!r}, models {record['wind_model']}"
                              f"+{record['wave_model'] or 'no waves'} "
                              f"({time.monotonic() - started:.1f}s)")
        browser.close()
    return stations, failures


def check_browser(headed: bool) -> int:
    with sync_playwright() as pw:
        launch_browser(pw, headed).close()
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ids", nargs="*", help="Windguru spot ids (the number in windguru.cz/<id>)")
    ap.add_argument("--spots", type=Path,
                    help="Also read every windguru_id and zona_station in this spots.json")
    ap.add_argument("--wind-model", default="3",
                    help="id_model for wind (default: 3, GFS 13 km); its paired wave model "
                         "is read too")
    ap.add_argument("--bbox", type=Bbox, metavar="SOUTH,NORTH,WEST,EAST",
                    help="Fail stations whose Windguru coordinates fall outside this box")
    ap.add_argument("--out", help="Write the JSON here instead of stdout")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                    help=f"Per-station cache (default: {DEFAULT_CACHE_DIR})")
    ap.add_argument("--max-age-min", type=float, default=90, help="Cache TTL in minutes (default: 90)")
    ap.add_argument("--no-cache", action="store_true", help="Read every station from Windguru")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="Seconds to wait between page loads (default: 2)")
    ap.add_argument("--timeout", type=float, default=45.0,
                    help="Seconds to wait for one station's page and forecasts (default: 45)")
    ap.add_argument("--headed", action="store_true", help="Show the browser window")
    ap.add_argument("--verbose", action="store_true", help="Progress lines on stderr")
    ap.add_argument("--check-browser", action="store_true",
                    help="Only check that a browser can be launched")
    args = ap.parse_args(argv)
    args.wind_model = str(args.wind_model)

    if args.check_browser:
        return check_browser(args.headed)

    ids, labels = stations_from_spots(args.spots) if args.spots else ([], {})
    ids = list(dict.fromkeys(ids + [str(i) for i in args.ids]))
    if not ids:
        ap.error("give Windguru spot ids and/or --spots")

    stations, failures = fetch_all(ids, labels, args)

    if failures:
        print(f"Windguru could not be read for {len(failures)} of {len(ids)} stations:",
              file=sys.stderr)
        for id_spot in ids:
            if id_spot in failures:
                print(f"  {describe(id_spot, labels)}: {failures[id_spot]}", file=sys.stderr)
        return 1

    out = {"stations": {i: stations[i] for i in ids}}
    if args.out:
        write_json_atomic(Path(args.out), out)
    else:
        json.dump(out, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
