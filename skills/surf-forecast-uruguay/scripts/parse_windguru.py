#!/usr/bin/env python3
"""parse_windguru.py - Normalize the Windguru forecasts read by fetch_windguru.py into per-day buckets.

Usage:
    python3 parse_windguru.py raw.json
    python3 parse_windguru.py raw.json --tz America/Montevideo

Input: fetch_windguru.py output. Each station names its "wind_model" and "wave_model" (null when
Windguru has no wave forecast for that spot; the swell fields are then null).

Each hour of the wind model is paired with the wave-model hour at the same UTC time (the two
runs may start at different init times). Field mapping from Windguru's variables:
    wind_kt WINDSPD · gust_kt GUST · wind_dir WINDDIR · temp_c TMPE (TMP if absent)
    cloud_pct TCDC · rain_mm APCP1 · slp_hpa SLP · rh_pct RH
    swell_m HTSGW · swell_period_s PERPW · swell_dir DIRPW   (the site's "Wave" rows)
Directions are degrees the wind/waves come FROM.

Output (stdout): JSON of the form
    {
      "stations": {
        "<id_spot>": {
          "id_spot": "313168", "spotname": "Desemb. Arroyo Pando (Canelones)",
          "lat": -34.8, "lon": -55.87,
          "model_name": "GFS 13 km", "wave_model_name": "GFS-Wave 25 km",   # null: no waves
          "initdate_utc": "2026-10-19 06:00:00", "wave_initdate_utc": "2026-10-19 06:00:00",
          "days": [
            {
              "date": "2026-10-19",
              "weekday": "monday",
              "hours": [
                {"local": "09:00", "h_offset": 6,
                 "wind_kt": 3.9, "gust_kt": 5.7, "wind_dir": 165, "wind_card": "SSE",
                 "temp_c": 11.2, "cloud_pct": 12, "rain_mm": 0, "slp_hpa": 1030.7, "rh_pct": 80,
                 "swell_m": 0.9, "swell_period_s": 10.2, "swell_dir": 126, "swell_card": "SE"},
                ...
              ],
              "stats": {
                "wind_kt_avg": 5.4, "wind_kt_max": 9.2, "gust_kt_max": 12.4,
                "wind_dir_dom": 35, "wind_card_dom": "NE",
                "temp_min": 8.0, "temp_max": 14.1,
                "rain_total_mm": 0.1, "cloud_avg_pct": 56,
                "swell_m_max": 1.1, "swell_period_s_max": 10.4,
                "swell_dir_dom": 128, "swell_card_dom": "SE"
              }
            },
            ...
          ]
        }
      }
    }

Exits with status 1, naming the station, if a station lacks the forecast it names.
"""

import argparse
import datetime as dt
import json
import math
import sys
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    sys.exit("python 3.9+ required (zoneinfo)")


CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


class StationDataError(Exception):
    pass


def deg_to_cardinal(deg: float | None) -> str | None:
    if deg is None:
        return None
    deg = deg % 360
    idx = int((deg + 11.25) // 22.5) % 16
    return CARDINALS[idx]


def circular_mean(degs: list[float]) -> float | None:
    xs = [math.cos(math.radians(d)) for d in degs if d is not None]
    ys = [math.sin(math.radians(d)) for d in degs if d is not None]
    if not xs:
        return None
    m = math.degrees(math.atan2(sum(ys) / len(ys), sum(xs) / len(xs)))
    return m + 360 if m < 0 else m


def init_time(fcst: dict) -> dt.datetime:
    if fcst.get("initstamp"):
        return dt.datetime.fromtimestamp(int(fcst["initstamp"]), tz=dt.timezone.utc)
    return dt.datetime.strptime(fcst["initdate"], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=dt.timezone.utc)


def safe(arr: list[Any] | None, i: int) -> Any:
    if arr is None or i >= len(arr):
        return None
    return arr[i]


def model_fcst(station: dict, id_model: str) -> tuple[dict, dict]:
    raw = (station.get("models") or {}).get(id_model)
    if not raw or not (raw.get("fcst") or {}).get("hours"):
        have = ", ".join(sorted(station.get("models") or {})) or "none"
        raise StationDataError(f"no forecast for id_model {id_model} (have: {have})")
    return raw, raw["fcst"]


def normalize_station(spot_id: str, station: dict, tz_name: str) -> dict:
    wind_raw, wind = model_fcst(station, str(station.get("wind_model")))
    if station.get("wave_model"):
        wave_raw, wave = model_fcst(station, str(station["wave_model"]))
        wave_init = init_time(wave)
        wave_idx = {wave_init + dt.timedelta(hours=h): i for i, h in enumerate(wave["hours"])}
    else:
        wave_raw, wave, wave_init, wave_idx = {}, {}, None, {}
    tz = ZoneInfo(tz_name)

    wind_init = init_time(wind)

    records = []
    for i, h_offset in enumerate(wind["hours"]):
        valid_utc = wind_init + dt.timedelta(hours=h_offset)
        j = wave_idx.get(valid_utc)
        wind_dir = safe(wind.get("WINDDIR"), i)
        swell_dir = safe(wave.get("DIRPW"), j) if j is not None else None
        records.append({
            "h_offset": h_offset,
            "local_dt": valid_utc.astimezone(tz),
            "wind_kt": safe(wind.get("WINDSPD"), i),
            "gust_kt": safe(wind.get("GUST"), i),
            "wind_dir": wind_dir,
            "wind_card": deg_to_cardinal(wind_dir),
            "temp_c": safe(wind.get("TMPE") or wind.get("TMP"), i),
            "cloud_pct": safe(wind.get("TCDC"), i),
            "rain_mm": safe(wind.get("APCP1"), i),
            "slp_hpa": safe(wind.get("SLP"), i),
            "rh_pct": safe(wind.get("RH"), i),
            "swell_m": safe(wave.get("HTSGW"), j) if j is not None else None,
            "swell_period_s": safe(wave.get("PERPW"), j) if j is not None else None,
            "swell_dir": swell_dir,
            "swell_card": deg_to_cardinal(swell_dir),
        })

    by_day: dict[str, list[dict]] = {}
    for r in records:
        key = r["local_dt"].strftime("%Y-%m-%d")
        by_day.setdefault(key, []).append(r)

    days_out = []
    for date_key in sorted(by_day):
        rows = by_day[date_key]
        winds = [r["wind_kt"] for r in rows if r["wind_kt"] is not None]
        gusts = [r["gust_kt"] for r in rows if r["gust_kt"] is not None]
        dirs = [r["wind_dir"] for r in rows if r["wind_dir"] is not None]
        temps = [r["temp_c"] for r in rows if r["temp_c"] is not None]
        clouds = [r["cloud_pct"] for r in rows if r["cloud_pct"] is not None]
        rains = [r["rain_mm"] for r in rows if r["rain_mm"] is not None]
        swells = [r["swell_m"] for r in rows if r["swell_m"] is not None]
        periods = [r["swell_period_s"] for r in rows if r["swell_period_s"] is not None]
        sdirs = [r["swell_dir"] for r in rows if r["swell_dir"] is not None]

        wind_dom = circular_mean(dirs)
        swell_dom = circular_mean(sdirs)

        first_local = rows[0]["local_dt"]
        days_out.append({
            "date": date_key,
            "weekday": first_local.strftime("%A").lower(),
            "hours": [
                {
                    "local": r["local_dt"].strftime("%H:%M"),
                    "h_offset": r["h_offset"],
                    "wind_kt": r["wind_kt"],
                    "gust_kt": r["gust_kt"],
                    "wind_dir": r["wind_dir"],
                    "wind_card": r["wind_card"],
                    "temp_c": r["temp_c"],
                    "cloud_pct": r["cloud_pct"],
                    "rain_mm": r["rain_mm"],
                    "slp_hpa": r["slp_hpa"],
                    "rh_pct": r["rh_pct"],
                    "swell_m": r["swell_m"],
                    "swell_period_s": r["swell_period_s"],
                    "swell_dir": r["swell_dir"],
                    "swell_card": r["swell_card"],
                }
                for r in rows
            ],
            "stats": {
                "wind_kt_avg": round(sum(winds) / len(winds), 1) if winds else None,
                "wind_kt_max": round(max(winds), 1) if winds else None,
                "gust_kt_max": round(max(gusts), 1) if gusts else None,
                "wind_dir_dom": round(wind_dom) if wind_dom is not None else None,
                "wind_card_dom": deg_to_cardinal(wind_dom),
                "temp_min": round(min(temps), 1) if temps else None,
                "temp_max": round(max(temps), 1) if temps else None,
                "cloud_avg_pct": round(sum(clouds) / len(clouds)) if clouds else None,
                "rain_total_mm": round(sum(rains), 1) if rains else None,
                "swell_m_max": round(max(swells), 2) if swells else None,
                "swell_period_s_max": round(max(periods), 1) if periods else None,
                "swell_dir_dom": round(swell_dom) if swell_dom is not None else None,
                "swell_card_dom": deg_to_cardinal(swell_dom),
            },
        })

    return {
        "id_spot": str(station.get("id_spot") or spot_id),
        "spotname": station.get("spotname"),
        "country": station.get("country"),
        "page_url": station.get("page_url"),
        "fetched_at": station.get("fetched_at"),
        "lat": station.get("lat", wind_raw.get("lat")),
        "lon": station.get("lon", wind_raw.get("lon")),
        "model": wind_raw.get("model"),
        "model_name": wind.get("model_name") or (wind_raw.get("wgmodel") or {}).get("model_name"),
        "wave_model_name": wave.get("model_name") or (wave_raw.get("wgmodel") or {}).get("model_name"),
        "initdate_utc": wind_init.strftime("%Y-%m-%d %H:%M:%S"),
        "wave_initdate_utc": wave_init.strftime("%Y-%m-%d %H:%M:%S") if wave_init else None,
        "sunrise": wind_raw.get("sunrise"),
        "sunset": wind_raw.get("sunset"),
        "days": days_out,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="Path to fetch_windguru.py output")
    ap.add_argument("--tz", default="America/Montevideo",
                    help="Local timezone for grouping (default: America/Montevideo)")
    args = ap.parse_args(argv)

    with open(args.input) as f:
        raw = json.load(f)

    stations = raw.get("stations") if isinstance(raw, dict) else None
    if not isinstance(stations, dict) or not stations:
        sys.exit(f"{args.input}: no stations (expected fetch_windguru.py output)")

    out_stations = {}
    failures = []
    for spot_id, station in stations.items():
        try:
            out_stations[spot_id] = normalize_station(spot_id, station, args.tz)
        except (StationDataError, KeyError, TypeError, ValueError) as e:
            failures.append(f"  station {spot_id}: {e}")
    if failures:
        print("Windguru data could not be normalized:", *failures, sep="\n", file=sys.stderr)
        return 1

    json.dump({"stations": out_stations}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
