"""Tests for the surf-forecast-uruguay pipeline (parse → score → HTML) on captured Windguru data.

Run from the repository root:
    python3 -m unittest discover -s tests

The fetch_windguru.py tests need Playwright importable (run them with the skill's venv python,
see scripts/setup.sh); they are skipped otherwise. They never open a browser.
"""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "surf-forecast-uruguay"
SCRIPTS = SKILL / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "windguru_raw.json"

sys.path.insert(0, str(SCRIPTS))
import generate_html  # noqa: E402
import parse_windguru  # noqa: E402
import score_spots  # noqa: E402

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if HAS_PLAYWRIGHT:
    import fetch_windguru  # noqa: E402


def load_fixture() -> dict:
    with open(FIXTURE) as f:
        return json.load(f)


def spots_for(*ids: str) -> list[dict]:
    with open(SKILL / "references" / "spots.json") as f:
        spots = json.load(f)
    return [s for s in spots if s["windguru_id"] in ids]


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, text=True)


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.raw = load_fixture()

    def test_wave_rows_come_from_the_paired_wave_model(self):
        station = self.raw["stations"]["313168"]
        out = parse_windguru.normalize_station("313168", station, "America/Montevideo")
        wave = station["models"]["83"]["fcst"]
        first = out["days"][0]["hours"][0]
        self.assertEqual(out["wave_model_name"], "GFS-Wave 25 km")
        self.assertEqual(first["swell_m"], wave["HTSGW"][0])
        self.assertEqual(first["swell_period_s"], wave["PERPW"][0])
        self.assertEqual(first["swell_dir"], wave["DIRPW"][0])
        self.assertEqual(first["wind_dir"], station["models"]["3"]["fcst"]["WINDDIR"][0])

    def test_wave_hours_align_by_utc_time_when_runs_differ(self):
        station = copy.deepcopy(self.raw["stations"]["313168"])
        wave = station["models"]["83"]["fcst"]
        wave["initstamp"] -= 6 * 3600
        wave["hours"] = [h + 6 for h in wave["hours"]]
        shifted = parse_windguru.normalize_station("313168", station, "America/Montevideo")
        same = parse_windguru.normalize_station("313168", self.raw["stations"]["313168"],
                                                "America/Montevideo")
        self.assertEqual(shifted["days"], same["days"])

    def test_station_without_wave_model_has_no_swell(self):
        out = parse_windguru.normalize_station("259217", self.raw["stations"]["259217"],
                                               "America/Montevideo")
        self.assertIsNone(out["wave_model_name"])
        hours = [h for d in out["days"] for h in d["hours"]]
        self.assertTrue(hours)
        self.assertTrue(all(h["swell_m"] is None for h in hours))
        self.assertTrue(all(h["wind_kt"] is not None for h in hours))

    def test_missing_model_fails_naming_the_station(self):
        raw = copy.deepcopy(self.raw)
        del raw["stations"]["313168"]["models"]["83"]
        with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
            json.dump(raw, f)
            f.flush()
            res = run("parse_windguru.py", f.name)
        self.assertEqual(res.returncode, 1)
        self.assertIn("station 313168", res.stderr)
        self.assertIn("id_model 83", res.stderr)
        self.assertEqual(res.stdout, "")


class ScoreTest(unittest.TestCase):
    def test_spanish_compound_directions(self):
        self.assertEqual(score_spots.norm_card("ONO"), "WNW")
        self.assertEqual(score_spots.norm_card("OSO"), "WSW")
        self.assertEqual(score_spots.card_distance("ONO", "WNW"), 0)

    def test_spot_without_its_station_fails_instead_of_borrowing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = load_fixture()
            del raw["stations"]["259217"]
            Path(tmp, "raw.json").write_text(json.dumps(raw))
            normalized = run("parse_windguru.py", str(Path(tmp, "raw.json")))
            self.assertEqual(normalized.returncode, 0, normalized.stderr)
            Path(tmp, "normalized.json").write_text(normalized.stdout)
            Path(tmp, "spots.json").write_text(json.dumps(spots_for("313168", "259217")))
            res = run("score_spots.py", str(Path(tmp, "normalized.json")),
                      str(Path(tmp, "spots.json")))
        self.assertEqual(res.returncode, 1)
        self.assertIn("atlantida: station 259217", res.stderr)


class EndToEndTest(unittest.TestCase):
    def test_report_from_captured_windguru_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "spots.json").write_text(json.dumps(spots_for("313168", "259217")))
            normalized = run("parse_windguru.py", str(FIXTURE))
            self.assertEqual(normalized.returncode, 0, normalized.stderr)
            (t / "normalized.json").write_text(normalized.stdout)
            scored = run("score_spots.py", str(t / "normalized.json"), str(t / "spots.json"))
            self.assertEqual(scored.returncode, 0, scored.stderr)
            (t / "scored.json").write_text(scored.stdout)
            html_run = run("generate_html.py", str(t / "scored.json"), str(t / "normalized.json"),
                           str(t / "spots.json"), "--out", str(t / "out.html"),
                           "--max-detail", "5")
            self.assertEqual(html_run.returncode, 0, html_run.stderr)
            html = (t / "out.html").read_text(encoding="utf-8")
            source = json.loads(scored.stdout)["source"]

        self.assertEqual(source["model"], "GFS 13 km + GFS-Wave 25 km")
        self.assertIn("Arroyo Pando", html)
        self.assertIn("Windguru: Desemb. Arroyo Pando (Canelones) (GFS 13 km + GFS-Wave 25 km)",
                      html)
        self.assertIn("sin oleaje en Windguru", html)
        self.assertIn("Windguru no publica oleaje para esta estación", html)


class FormatTest(unittest.TestCase):
    def test_cells_round_halves_up_like_windguru(self):
        self.assertEqual(generate_html.fmt_num(1.45, 1), "1.5")
        self.assertEqual(generate_html.fmt_num(10.5, 0), "11")
        self.assertEqual(generate_html.fmt_num(8.5, 0), "9")
        self.assertEqual(generate_html.fmt_num(2.0, 1), "2")
        self.assertEqual(generate_html.fmt_num(None, 1), "")


class FakeResponse:
    def __init__(self, body, status=200, content_type="application/json; charset=utf-8"):
        self.body = body
        self.status = status
        self.headers = {"content-type": content_type}

    def json(self):
        return self.body


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed (use the skill's venv python)")
class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.raw = load_fixture()["stations"]["313168"]
        self.spot_page = {
            "tabs": [{"id_model": 100, "id_model_wave": 83}, {"id_model": 3, "id_model_wave": 83},
                     {"id_model": 83}, {"id_model": 117, "id_model_wave": 118}],
            "spots": {"313168": {"spotname": "Desemb. Arroyo Pando (Canelones) ",
                                 "country": "Uruguay", "lat": -34.8, "lon": -55.87,
                                 "models": [100, 3, 83, 117, 118]}},
        }
        self.uruguay = fetch_windguru.Bbox("-35.2,-30.0,-58.6,-53.0")

    def test_keeps_forecasts_of_this_spot_only(self):
        cap = fetch_windguru.Capture("313168")
        cap.on_response(FakeResponse(self.raw["models"]["3"]))
        other = dict(self.raw["models"]["83"], id_spot=999)
        cap.on_response(FakeResponse(other))
        cap.on_response(FakeResponse("<html>", content_type="text/html"))
        self.assertEqual(sorted(cap.models), ["3"])

    def test_wave_model_is_the_one_the_page_pairs_with_the_wind_model(self):
        cap = fetch_windguru.Capture("313168")
        cap.on_response(FakeResponse(self.spot_page))
        self.assertEqual(cap.required_models("3", self.uruguay), ("3", "83"))
        self.assertEqual(cap.required_models("117", self.uruguay), ("117", "118"))

    def test_spot_outside_the_box_is_rejected(self):
        page = copy.deepcopy(self.spot_page)
        page["spots"]["313168"].update(lat=28.95, lon=-13.59, country="Spain")
        cap = fetch_windguru.Capture("313168")
        cap.on_response(FakeResponse(page))
        with self.assertRaises(fetch_windguru.StationError) as err:
            cap.required_models("3", self.uruguay)
        self.assertFalse(err.exception.retry)
        self.assertIn("Spain", str(err.exception))

    def test_unknown_spot_and_unoffered_model(self):
        cap = fetch_windguru.Capture("313168")
        cap.on_response(FakeResponse({"tabs": [], "spots": [{"id_spot": 0}]}))
        self.assertTrue(cap.unknown_spot)
        cap = fetch_windguru.Capture("313168")
        cap.on_response(FakeResponse(self.spot_page))
        with self.assertRaises(fetch_windguru.StationError):
            cap.required_models("45", None)

    def test_api_error_is_recorded(self):
        cap = fetch_windguru.Capture("313168")
        cap.on_response(FakeResponse({"return": "error", "message": "Unauthorized!"}, status=401))
        self.assertEqual(cap.api_errors, ["HTTP 401 Unauthorized!"])

    def test_cache_requires_matching_model_and_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch_windguru.write_json_atomic(fetch_windguru.cache_file(Path(tmp), "313168"),
                                             self.raw)
            load = fetch_windguru.load_cached
            self.assertIsNotNone(load(Path(tmp), "313168", "3", self.uruguay, 90))
            self.assertIsNone(load(Path(tmp), "313168", "117", self.uruguay, 90))
            self.assertIsNone(load(Path(tmp), "313168", "3",
                                   fetch_windguru.Bbox("0,1,0,1"), 90))
            self.assertIsNone(load(Path(tmp), "313168", "3", self.uruguay, 0))

    def test_stations_from_spots_lists_each_station_once(self):
        ids, labels = fetch_windguru.stations_from_spots(SKILL / "references" / "spots.json")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("Playa Los Botes", labels["37673"])


if __name__ == "__main__":
    unittest.main()
