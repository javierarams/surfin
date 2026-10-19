#!/usr/bin/env python3
"""parse_windguru.py — Normalize raw Windguru iapi.php JSON into per-day buckets.

Usage:
    python3 parse_windguru.py <raw.json>            # raw can be either:
                                                    #   - fetch_windguru.sh output ({stations:{...}})
                                                    #   - a single station response
    python3 parse_windguru.py --stdin               # read JSON from stdin
    python3 parse_windguru.py raw.json --tz America/Montevideo
    python3 parse_windguru.py wind.json --merge wave.json    # merge wind model + wave model
                                                             # (e.g. GFS id=3 + WaveWatch id=83)
                                                             # series aligned by hour offset per id

Output (stdout): JSON of the form
    {
      "stations": {
        "<id_spot>": {
          "id_spot": "313168",
          "lat": -34.8, "lon": -55.87, "model": "gfs",
          "initdate_utc": "2026-05-22 12:00:00",
          "days": [
            {
              "date": "2026-05-22",
              "weekday": "viernes",
              "hours": [
                {"local": "09:00", "h_offset": 0,
                 "wind_kt": 3.9, "gust_kt": 5.7, "wind_dir": 165, "wind_card": "S",
                 "temp_c": 11.2, "cloud_pct": null, "rain_mm": null, "slp_hpa": 1030.7,
                 "swell_m": null, "swell_period_s": null, "swell_dir": null, "swell_card": null},
                ...
              ],
              "stats": {
                "wind_kt_avg": 5.4, "wind_kt_max": 9.2, "gust_kt_max": 12.4,
                "wind_dir_dom": 35, "wind_card_dom": "NE",
                "temp_min": 8.0, "temp_max": 14.1,
                "rain_total_mm": 0.1, "cloud_avg_pct": 56,
                "swell_m_max": null, "swell_period_s_max": null, "swell_card_dom": null
              }
            },
            ...
          ]
        }
      }
    }
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


def parse_initdate(s: str) -> dt.datetime:
    """Windguru initdate is 'YYYY-MM-DD HH:MM:SS' UTC."""
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


def safe(arr: list[Any] | None, i: int) -> Any:
    if arr is None or i >= len(arr):
        return None
    return arr[i]


def merge_wave_into_wind(wind_raw: dict, wave_raw: dict) -> dict:
    """Merge wave-model fcst arrays (SHGT/SMER/SPER) into wind-model raw.

    Both inputs are expected to be in fetch_windguru.sh shape:
        {"stations": {"<id>": <station_raw>, ...}, "errors": {...}}
    Single-station shape is also accepted.

    Series are aligned by hour offset (`fcst.hours`). The wave model usually
    runs at a 3h step, so most 1h slots in the wind model won't have a wave
    sample — those keep `None`. We don't interpolate; downstream code reads
    `swell_m`/`swell_period_s`/`swell_dir` and handles None gracefully.
    """

    def to_stations(raw: dict) -> dict:
        if isinstance(raw, dict) and "stations" in raw and isinstance(raw["stations"], dict):
            return raw["stations"]
        # single-station shape
        sid = str(raw.get("id_spot", "unknown"))
        return {sid: raw}

    wind_stations = to_stations(wind_raw)
    wave_stations = to_stations(wave_raw)

    for sid, wind_station in wind_stations.items():
        wave_station = wave_stations.get(sid)
        if not isinstance(wave_station, dict):
            continue
        w_fcst = wind_station.get("fcst") or {}
        v_fcst = wave_station.get("fcst") or {}
        w_hours = w_fcst.get("hours") or []
        v_hours = v_fcst.get("hours") or []
        if not w_hours or not v_hours:
            continue
        # Build offset → index map for the wave series
        wave_idx = {h: i for i, h in enumerate(v_hours)}
        n = len(w_hours)
        # Pre-allocate aligned arrays sized to wind series length
        for key in ("SHGT", "SMER", "SPER"):
            src = v_fcst.get(key) or []
            aligned = [None] * n
            for j, h in enumerate(w_hours):
                k = wave_idx.get(h)
                if k is not None and k < len(src):
                    aligned[j] = src[k]
            w_fcst[key] = aligned
        wind_station["fcst"] = w_fcst
        wind_station["_merged_wave_model"] = wave_station.get("model")

    # Preserve original wrapper shape
    if isinstance(wind_raw, dict) and "stations" in wind_raw:
        wind_raw["stations"] = wind_stations
        return wind_raw
    # Single-station — return the merged one
    return next(iter(wind_stations.values()))


def normalize_station(spot_id: str, raw: dict, tz_name: str) -> dict:
    """Normalize one station's raw Windguru response."""
    if raw.get("_format") == "html_fallback":
        return {"id_spot": spot_id, "_error": "html_fallback not yet parsed",
                "_html_len": len(raw.get("html", ""))}

    fcst = raw.get("fcst", {})
    if not fcst:
        return {"id_spot": spot_id, "_error": "no 'fcst' key in response"}

    initdate_utc = parse_initdate(raw.get("initdate") or fcst.get("initdate"))
    hours = fcst.get("hours") or fcst.get("fcst_hour") or []
    tz = ZoneInfo(tz_name)

    # Per-hour records
    records = []
    for i, h_offset in enumerate(hours):
        local = (initdate_utc + dt.timedelta(hours=h_offset)).astimezone(tz)
        wind_dir = safe(fcst.get("WINDDIR"), i)
        swell_dir = safe(fcst.get("SMER"), i)
        records.append({
            "h_offset": h_offset,
            "local_dt": local,
            "wind_kt": safe(fcst.get("WINDSPD"), i),
            "gust_kt": safe(fcst.get("GUST"), i),
            "wind_dir": wind_dir,
            "wind_card": deg_to_cardinal(wind_dir),
            "temp_c": safe(fcst.get("TMPE") or fcst.get("TMP"), i),
            "cloud_pct": safe(fcst.get("TCDC"), i),
            "rain_mm": safe(fcst.get("APCP1"), i),
            "slp_hpa": safe(fcst.get("SLP"), i),
            "rh_pct": safe(fcst.get("RH"), i),
            "swell_m": safe(fcst.get("SHGT"), i),
            "swell_period_s": safe(fcst.get("SPER"), i),
            "swell_dir": swell_dir,
            "swell_card": deg_to_cardinal(swell_dir),
        })

    # Group by local date
    by_day: dict[str, list[dict]] = {}
    for r in records:
        key = r["local_dt"].strftime("%Y-%m-%d")
        by_day.setdefault(key, []).append(r)

    days_out = []
    for date_key in sorted(by_day):
        rows = by_day[date_key]
        # Stats
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
        "id_spot": str(raw.get("id_spot", spot_id)),
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "model": raw.get("model"),
        "model_name": (raw.get("wgmodel") or {}).get("model_name"),
        "initdate_utc": initdate_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "sunrise": raw.get("sunrise"),
        "sunset": raw.get("sunset"),
        "days": days_out,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", nargs="?", help="Path to raw JSON (omit if --stdin)")
    ap.add_argument("--stdin", action="store_true", help="Read raw JSON from stdin")
    ap.add_argument("--tz", default="America/Montevideo",
                    help="Local timezone for grouping (default: America/Montevideo)")
    ap.add_argument("--merge", metavar="WAVE_JSON",
                    help="Merge a wave-model raw JSON (SHGT/SMER/SPER) into the wind raw "
                         "before normalizing. Series aligned by hour offset per station id.")
    args = ap.parse_args(argv)

    if args.stdin or not args.input:
        raw_text = sys.stdin.read()
    else:
        with open(args.input) as f:
            raw_text = f.read()

    raw = json.loads(raw_text)

    if args.merge:
        with open(args.merge) as f:
            wave_raw = json.loads(f.read())
        raw = merge_wave_into_wind(raw, wave_raw)

    # Two possible shapes:
    #  1) { "stations": { "<id>": <raw>, ... }, "errors": {...} }   (from fetch_windguru.sh)
    #  2) <raw single station>
    out_stations = {}
    out_errors = {}

    if isinstance(raw, dict) and "stations" in raw and isinstance(raw["stations"], dict):
        out_errors = raw.get("errors", {}) or {}
        for spot_id, station_raw in raw["stations"].items():
            try:
                out_stations[spot_id] = normalize_station(spot_id, station_raw, args.tz)
            except Exception as e:
                out_errors[spot_id] = f"parse error: {e}"
    else:
        spot_id = str(raw.get("id_spot", "unknown"))
        try:
            out_stations[spot_id] = normalize_station(spot_id, raw, args.tz)
        except Exception as e:
            out_errors[spot_id] = f"parse error: {e}"

    json.dump({"stations": out_stations, "errors": out_errors},
              sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
