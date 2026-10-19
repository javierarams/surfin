#!/usr/bin/env python3
"""score_spots.py — Score every spot in spots.json against the normalized Windguru forecast.

Usage:
    python3 score_spots.py <normalized.json> <spots.json>
    python3 score_spots.py <normalized.json> <spots.json> --region "Maldonado"

Input:
    normalized.json: output of parse_windguru.py (stations → days → stats)
    spots.json:      references/spots.json

Output (stdout): JSON
    {
      "by_day": [
        {
          "date": "2026-05-22",
          "weekday": "viernes",
          "best": [
            {"spot": "Montoya", "region": "Maldonado", "score": 78,
             "wind": "...", "swell": "...", "flags": ["🔥 potente"],
             "rating": "good"},
            ...
          ]
        }
      ]
    }

Scoring (per spot, per day):
  Wind match (40 pts):    direction matches viento_optimo +40, ±1 step +20, onshore -20
  Swell direction (30):   matches swell_optimo +30, ±1 step +15
  Wave size (30):         >0.5m & >8s +15, >1.0m & >10s +30, >1.5m & >12s +30 + "🔥 potente"

Threshold: score ≥ 50 AND swell_m_max > 0.3
Rating: ✅ good ≥70   ⚠️ mid 50–69   ❌ bad <50
"""

import argparse
import json
import sys

CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

# Map Spanish abbreviations (used in spots.json) to English equivalents
NORMALIZE_CARD = {"SO": "SW", "O": "W", "NO": "NW", "ESO": "WSW"}


def norm_card(c: str | None) -> str | None:
    if not c:
        return None
    c = c.upper()
    return NORMALIZE_CARD.get(c, c)


def card_index(c: str | None) -> int | None:
    c = norm_card(c)
    return CARDINALS.index(c) if c in CARDINALS else None


def card_distance(a: str, b: str) -> int | None:
    """Circular distance between two cardinals (0–8)."""
    ia, ib = card_index(a), card_index(b)
    if ia is None or ib is None:
        return None
    d = abs(ia - ib)
    return min(d, 16 - d)


def is_opposite(a: str, b: str) -> bool:
    d = card_distance(a, b)
    return d is not None and d >= 7  # 7 or 8 cardinals apart = ~opposite


def score_wind(forecast_card: str | None, optimal: list[str]) -> int:
    if not forecast_card or not optimal:
        return 0
    for o in optimal:
        if card_distance(forecast_card, o) == 0:
            return 40
    for o in optimal:
        d = card_distance(forecast_card, o)
        if d is not None and d == 1:
            return 20
    # Onshore penalty: opposite of any optimal direction
    for o in optimal:
        if is_opposite(forecast_card, o):
            return -20
    return 0


def score_swell_dir(forecast_card: str | None, optimal: list[str]) -> int:
    if not forecast_card or not optimal:
        return 0
    for o in optimal:
        if card_distance(forecast_card, o) == 0:
            return 30
    for o in optimal:
        d = card_distance(forecast_card, o)
        if d is not None and d == 1:
            return 15
    return 0


def score_wave(height_m: float | None, period_s: float | None) -> tuple[int, list[str]]:
    flags: list[str] = []
    if height_m is None or period_s is None:
        return 0, flags
    if height_m > 1.5 and period_s > 12:
        flags.append("🔥 potente")
        return 30, flags
    if height_m > 1.0 and period_s > 10:
        return 30, flags
    if height_m > 0.5 and period_s > 8:
        return 15, flags
    return 0, flags


def rate(score: int) -> str:
    if score >= 70:
        return "good"
    if score >= 50:
        return "mid"
    return "bad"


def score_spot_day(spot: dict, day: dict) -> dict:
    stats = day.get("stats", {})
    wind_card = stats.get("wind_card_dom")
    swell_card = stats.get("swell_card_dom")
    wind_score = score_wind(wind_card, spot.get("viento_optimo", []))
    swell_dir_score = score_swell_dir(swell_card, spot.get("swell_optimo", []))
    wave_score, flags = score_wave(stats.get("swell_m_max"), stats.get("swell_period_s_max"))
    total = wind_score + swell_dir_score + wave_score
    surfable = total >= 50 and (stats.get("swell_m_max") or 0) > 0.3

    return {
        "spot": spot.get("name"),
        "slug": spot.get("slug"),
        "region": spot.get("region"),
        "score": total,
        "rating": rate(total),
        "surfable": surfable,
        "components": {
            "wind": wind_score,
            "swell_dir": swell_dir_score,
            "wave": wave_score,
        },
        "wind": f"{wind_card or '?'} {stats.get('wind_kt_avg') or '?'}kt"
                f" (max {stats.get('wind_kt_max') or '?'}kt)",
        "swell": (f"{stats.get('swell_m_max')}m / "
                  f"{stats.get('swell_period_s_max')}s {swell_card or '?'}"
                  if stats.get("swell_m_max") is not None else "sin datos de oleaje"),
        "flags": flags,
        "windguru_id": spot.get("windguru_id"),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("normalized", help="Path to normalized.json from parse_windguru.py")
    ap.add_argument("spots", help="Path to spots.json")
    ap.add_argument("--region", help="Filter to spots in this region")
    ap.add_argument("--min-score", type=int, default=0,
                    help="Drop spots below this score (default: 0 = keep all)")
    ap.add_argument("--top", type=int, default=10,
                    help="Top N spots per day (default: 10)")
    args = ap.parse_args(argv)

    with open(args.normalized) as f:
        normalized = json.load(f)
    with open(args.spots) as f:
        spots = json.load(f)

    if args.region:
        spots = [s for s in spots if (s.get("region") or "").lower() == args.region.lower()]

    # Score each spot against the closest available station:
    #   1. spot.windguru_id if normalized data has it (best — exact match)
    #   2. spot.zona_station if normalized data has it
    #   3. first station with days (last resort)
    stations = normalized.get("stations", {})
    if not stations:
        sys.exit("no stations in normalized input")

    fallback_station = next(iter(stations.values()))

    def pick_station(spot: dict) -> dict:
        for key in ("windguru_id", "zona_station"):
            sid = str(spot.get(key) or "")
            if sid and sid in stations and stations[sid].get("days"):
                return stations[sid]
        return fallback_station

    # Use the fallback station as the canonical timeline.
    canonical_days = fallback_station.get("days", [])

    by_day = []
    for canonical_day in canonical_days:
        date_key = canonical_day["date"]
        scored = []
        for spot in spots:
            st = pick_station(spot)
            day = next((d for d in st.get("days", []) if d["date"] == date_key), None)
            if day is None:
                continue
            row = score_spot_day(spot, day)
            row["station_id"] = st.get("id_spot")
            scored.append(row)
        scored = [s for s in scored if s["score"] >= args.min_score]
        scored.sort(key=lambda x: x["score"], reverse=True)
        by_day.append({
            "date": date_key,
            "weekday": canonical_day["weekday"],
            "stats": canonical_day.get("stats"),
            "best": scored[: args.top],
        })

    json.dump({
        "by_day": by_day,
        "source": {
            "stations_used": sorted(stations.keys()),
            "fallback_station": fallback_station.get("id_spot"),
            "model": fallback_station.get("model_name") or fallback_station.get("model"),
            "initdate_utc": fallback_station.get("initdate_utc"),
        }
    }, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
