#!/usr/bin/env python3
"""generate_html.py — Build a single-file HTML surf forecast (Windguru-style).

Inputs:
  scored.json     — output of score_spots.py (by_day → best[] per day)
  normalized.json — output of parse_windguru.py (with wave model merged in)
  spots.json      — references/spots.json (40 spots metadata)

Output (stdout, or --out FILE): a self-contained HTML page with one tab per
department (Montevideo, Canelones, Maldonado, Rocha). Each tab lists every spot
of the department, ranked by its best score in the forecast window, and then one
block per Windguru station the department's spots read: the spot cards (scores,
best day, per-day scores) and the station's hourly Windguru-style table, drawn
once. A station read by several spots is labelled as shared and names them all,
including spots of other departments.

Tokens-saving design: this script does NOT echo any data to stdout besides the
HTML itself. The caller (run_surf_html.sh) only needs to read the file path —
no JSON parsing required for the LLM.

Usage:
  python3 generate_html.py scored.json normalized.json spots.json --out surf.html
  python3 generate_html.py scored.json normalized.json spots.json > surf.html
"""

import argparse
import datetime as dt
import html
import json
import math
import os
import sys
from typing import Any
from zoneinfo import ZoneInfo


CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

UY_TZ = ZoneInfo("America/Montevideo")

WEEKDAYS_SHORT_ES = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"]

WEEKDAYS_FULL_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

DEPARTMENTS = ["Montevideo", "Canelones", "Maldonado", "Rocha"]

REGION_DEPARTMENT = {"Costa de Oro": "Canelones"}


def esc(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s))


def fmt_num(v: Any, decimals: int = 1) -> str:
    if v is None:
        return ""
    try:
        scale = 10 ** decimals
        # Math.round semantics: windguru.cz rounds halves up, Python's round() to even.
        rounded = math.floor(float(v) * scale + 0.5) / scale
    except (TypeError, ValueError):
        return ""
    text = f"{rounded:.{decimals}f}"
    return text.rstrip("0").rstrip(".") if decimals else text


# ---------- color scales (CSS classes) ----------

def ws_class(v: Any) -> str:
    """Wind speed → CSS class. Knots."""
    if v is None:
        return "cell-na"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "cell-na"
    if v < 3: return "ws-1"
    if v < 6: return "ws-2"
    if v < 10: return "ws-3"
    if v < 14: return "ws-4"
    if v < 18: return "ws-5"
    if v < 22: return "ws-6"
    if v < 27: return "ws-7"
    return "ws-8"


def gust_class(v: Any) -> str:
    return ws_class(v)


def temp_class(v: Any) -> str:
    if v is None:
        return "cell-na"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "cell-na"
    if v < 5: return "t-1"
    if v < 10: return "t-2"
    if v < 14: return "t-3"
    if v < 18: return "t-4"
    if v < 22: return "t-5"
    if v < 26: return "t-6"
    if v < 30: return "t-7"
    return "t-8"


def cloud_class(v: Any) -> str:
    if v is None:
        return "cell-na"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "cell-na"
    if v < 15: return "cl-1"
    if v < 40: return "cl-2"
    if v < 70: return "cl-3"
    if v < 90: return "cl-4"
    return "cl-5"


def wave_class(v: Any) -> str:
    if v is None:
        return "cell-na"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "cell-na"
    if v < 0.3: return "wv-0"
    if v < 0.6: return "wv-1"
    if v < 1.0: return "wv-2"
    if v < 1.5: return "wv-3"
    if v < 2.0: return "wv-4"
    return "wv-5"


def period_class(v: Any) -> str:
    if v is None:
        return "cell-na"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "cell-na"
    if v < 6: return "pr-1"
    if v < 9: return "pr-2"
    if v < 12: return "pr-3"
    if v < 15: return "pr-4"
    return "pr-5"


def rain_class(v: Any) -> str:
    if v is None or float(v) == 0:
        return "rn-0"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "cell-na"
    if v < 0.5: return "rn-1"
    if v < 2: return "rn-2"
    if v < 5: return "rn-3"
    return "rn-4"


def rating_class(score: Any) -> str:
    if score is None:
        return ""
    try:
        s = int(score)
    except (TypeError, ValueError):
        return ""
    if s >= 70: return "rt-good"
    if s >= 50: return "rt-mid"
    if s >= 30: return "rt-low"
    return "rt-bad"


def cardinal(degrees: float) -> str:
    return CARDINALS[int(float(degrees) % 360 / 22.5 + 0.5) % 16]


def arrow(degrees: Any) -> str:
    """Arrow pointing where the flow IS GOING. Windguru gives the "FROM" direction,
    so the arrow is rotated by +180°; the tooltip names the FROM direction."""
    if degrees is None:
        return ""
    rot = (float(degrees) + 180.0) % 360.0
    return (f'<i class="ar" role="img" title="{cardinal(degrees)} {float(degrees):.0f}°" '
            f'style="rotate:{rot:.0f}deg"></i>')


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def day_short(value: str) -> str:
    d = parse_date(value)
    return f"{WEEKDAYS_SHORT_ES[d.weekday()]} {d.day}" if d else esc(value)


def day_long(value: str) -> str:
    d = parse_date(value)
    return f"{WEEKDAYS_FULL_ES[d.weekday()]} {d.day}/{d.month}" if d else esc(value)


# ---------- spot grouping ----------

def department_of(spot: dict) -> str:
    region = spot.get("region") or ""
    return REGION_DEPARTMENT.get(region, region)


def department_id(department: str) -> str:
    return department.lower()


def station_of(spot: dict) -> str:
    return str(spot.get("windguru_id") or "")


def split_by_department(scored: dict, spots_by_slug: dict) -> dict[str, dict]:
    """One scored-shaped dict per department, in DEPARTMENTS order, each keeping
    only the rows of that department's spots."""
    unknown = sorted({s.get("region") or "" for s in spots_by_slug.values()
                      if department_of(s) not in DEPARTMENTS})
    if unknown:
        raise ValueError(f"spots.json regions without a department: {', '.join(unknown)}")
    return {
        dep: {"by_day": [
            {**day, "best": [row for row in day.get("best", [])
                             if department_of(spots_by_slug.get(row.get("slug"), {})) == dep]}
            for day in scored.get("by_day", [])
        ]}
        for dep in DEPARTMENTS
    }


def summarize_spots(scored: dict, dates: list[str]) -> dict[str, dict]:
    """Per slug: its scored row for each date in `dates`, and the best of them
    (the earliest day wins a tie)."""
    wanted = set(dates)
    out: dict[str, dict] = {}
    for day in scored.get("by_day", []):
        if day.get("date") not in wanted:
            continue
        for row in day.get("best", []):
            slug = row.get("slug")
            if not slug:
                continue
            entry = out.setdefault(slug, {"days": {}, "best": None, "best_day": None})
            entry["days"][day["date"]] = row
            if entry["best"] is None or (row.get("score") or 0) > (entry["best"].get("score") or 0):
                entry["best"], entry["best_day"] = row, day["date"]
    return out


def best_score(summary: dict | None) -> int | None:
    if not summary or summary.get("best") is None:
        return None
    return summary["best"].get("score") or 0


def group_by_station(spots: list[dict], summaries: dict) -> list[dict]:
    """Department spots grouped by the Windguru station they read, best spot first
    inside each group and best group first. Ties keep spots.json order."""
    def key(spot: dict) -> int:
        score = best_score(summaries.get(spot.get("slug")))
        return -(score if score is not None else -10**9)

    groups: dict[str, list[dict]] = {}
    for spot in sorted(spots, key=key):
        groups.setdefault(station_of(spot), []).append(spot)
    return [{"station_id": sid, "spots": members} for sid, members in groups.items()]


# ---------- rendering ----------

CSS = """
:root {
  color-scheme: light dark;
  --bg: #f3f6fa; --surface: #ffffff; --surface-2: #f1f5f9; --surface-3: #e8eef5;
  --text: #0b1726; --muted: #556578; --faint: #64748b; --border: #e2e8f0; --border-strong: #94a3b8;
  --accent: #0e7490; --accent-soft: #e0f4f8; --accent-text: #0e6680;
  --shadow: 0 1px 2px rgba(15,23,42,.05), 0 4px 16px rgba(15,23,42,.05);
  --good: #047857; --mid: #a16207; --low: #c2410c; --bad: #b91c1c;
  --good-fg: #047857; --mid-fg: #a16207; --low-fg: #c2410c; --bad-fg: #b91c1c;
  --hero-1: #062a43; --hero-2: #0b5d7a; --hero-3: #14a3b8;
  --night: rgba(15,23,42,.05);
  --radius: 16px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #07111e; --surface: #0f1b2c; --surface-2: #142338; --surface-3: #1a2c45;
    --text: #e6edf6; --muted: #9fb0c4; --faint: #8395ab; --border: #1f3149; --border-strong: #4b6280;
    --accent: #38bdf8; --accent-soft: #0e2c42; --accent-text: #7dd3fc;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 6px 20px rgba(0,0,0,.25);
    --good-fg: #34d399; --mid-fg: #fbbf24; --low-fg: #fb923c; --bad-fg: #f87171;
    --hero-1: #031424; --hero-2: #073a55; --hero-3: #0b6f88;
    --night: rgba(0,0,0,.22);
  }
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--text); line-height: 1.45;
       font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
                    Roboto, "Helvetica Neue", Arial, sans-serif;
       font-feature-settings: "tnum" 1; -webkit-font-smoothing: antialiased; }
a { color: var(--accent-text); }
a:focus-visible, button:focus-visible, [tabindex]:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }
.wrap { max-width: 1240px; margin: 0 auto; padding: 0 24px; }

/* hero */
.hero { position: relative; color: #fff; overflow: hidden;
        background: radial-gradient(1200px 400px at 85% -20%, rgba(94,234,212,.35), transparent 60%),
                    linear-gradient(135deg, var(--hero-1) 0%, var(--hero-2) 55%, var(--hero-3) 100%); }
.hero .wrap { position: relative; padding-top: 36px; padding-bottom: 64px; }
.hero .eyebrow { margin: 0 0 6px; font-size: 13px; font-weight: 600; letter-spacing: .08em;
                 text-transform: uppercase; color: #a5f3fc; }
.hero h1 { margin: 0 0 16px; font-size: clamp(28px, 4.5vw, 42px); line-height: 1.1;
           font-weight: 800; letter-spacing: -.02em; }
.hero .meta { display: flex; flex-wrap: wrap; gap: 8px; margin: 0; padding: 0; list-style: none;
              font-size: 13px; }
.hero .meta li { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.18);
                 border-radius: 999px; padding: 5px 12px; backdrop-filter: blur(4px); }
.hero .meta a { color: #fff; font-weight: 600; }
.hero .meta a:focus-visible { outline-color: #fff; }
.hero .waves { position: absolute; left: 0; right: 0; bottom: -1px; width: 100%; height: 48px;
               display: block; color: var(--bg); }

/* tab bar */
.tabs-bar { position: sticky; top: 0; z-index: 10; padding: 10px 0;
            background: color-mix(in srgb, var(--bg) 82%, transparent);
            backdrop-filter: saturate(1.4) blur(12px); -webkit-backdrop-filter: saturate(1.4) blur(12px);
            border-bottom: 1px solid transparent; transition: border-color .2s; }
.tabs { display: flex; gap: 4px; padding: 4px; background: var(--surface); border-radius: 14px;
        border: 1px solid var(--border); box-shadow: var(--shadow); width: fit-content; max-width: 100%; }
.tabs [role="tab"] { appearance: none; border: 0; background: none; font: inherit; color: var(--muted);
  font-size: 15px; font-weight: 600; padding: 9px 16px; border-radius: 10px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 8px; white-space: nowrap;
  transition: background .15s, color .15s; }
.tabs [role="tab"]:hover { color: var(--text); background: var(--surface-2); }
.tabs [role="tab"][aria-selected="true"] { color: #fff; background: var(--accent-text); }
@media (prefers-color-scheme: dark) {
  .tabs [role="tab"][aria-selected="true"] { color: #04121f; background: var(--accent); }
}
.tab-score { font-size: 12px; font-weight: 700; color: #fff; background: var(--c, #64748b);
             border-radius: 999px; padding: 1px 8px; min-width: 30px; text-align: center;
             box-shadow: 0 0 0 1.5px rgba(255,255,255,.7); }

main.wrap { padding-top: 20px; padding-bottom: 40px; }
.tabpanel:focus-visible { outline: 2px solid var(--accent); outline-offset: 6px; border-radius: 8px; }

/* rating colors */
.rt-good { --c: var(--good); --cf: var(--good-fg); }
.rt-mid  { --c: var(--mid);  --cf: var(--mid-fg); }
.rt-low  { --c: var(--low);  --cf: var(--low-fg); }
.rt-bad  { --c: var(--bad);  --cf: var(--bad-fg); }

/* department summary */
.dep-head { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between;
            gap: 12px 24px; margin: 8px 0 18px; }
.dep-head h2 { margin: 0; font-size: 30px; font-weight: 800; letter-spacing: -.02em; line-height: 1.1; }
.dep-head .sub { margin: 4px 0 0; color: var(--muted); font-size: 14px; }
.dep-best { margin: 0; font-size: 14px; color: var(--muted); }
.dep-best a { font-weight: 700; color: var(--text); text-decoration: none; }
.dep-best a:hover { text-decoration: underline; }

.pill { display: inline-block; min-width: 34px; padding: 2px 8px; border-radius: 999px;
        background: var(--c, #64748b); color: #fff; font-weight: 700; font-size: 13px;
        text-align: center; line-height: 1.4; }
.pill.none { background: var(--surface-3); color: var(--muted); }

/* ranking */
.ranking { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
           box-shadow: var(--shadow); padding: 14px 16px 16px; margin-bottom: 28px; }
.ranking h3 { margin: 0 0 10px; font-size: 13px; font-weight: 700; letter-spacing: .06em;
              text-transform: uppercase; color: var(--muted); }
.ranking ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px;
              grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }
.ranking a { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: 10px;
             text-decoration: none; color: var(--text); background: var(--surface-2); }
.ranking a:hover { background: var(--surface-3); }
.ranking .name { font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
                 white-space: nowrap; }
.ranking small { color: var(--muted); font-size: 12px; white-space: nowrap; }

/* station group */
.station { background: var(--surface); border: 1px solid var(--border); border-radius: 20px;
           box-shadow: var(--shadow); margin-bottom: 28px; overflow: hidden; scroll-margin-top: 80px; }
.station-head { padding: 18px 20px 14px; display: flex; flex-wrap: wrap; gap: 8px 16px;
                align-items: start; justify-content: space-between; border-bottom: 1px solid var(--border); }
.station-head .kicker { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 6px;
                        font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
                        color: var(--muted); }
.badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
         font-size: 12px; font-weight: 700; letter-spacing: 0; text-transform: none; }
.badge.shared { background: var(--accent-soft); color: var(--accent-text); }
.badge.shared svg { width: 14px; height: 14px; }
.station-head h3 { margin: 0; font-size: 20px; font-weight: 750; letter-spacing: -.01em; }
.station-head .src { margin: 4px 0 0; font-size: 13px; color: var(--muted); }
.wg-link { flex: none; display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px;
           border-radius: 10px; border: 1px solid var(--border); background: var(--surface-2);
           color: var(--text); font-size: 13px; font-weight: 600; text-decoration: none; }
.wg-link:hover { background: var(--surface-3); }
.notice { margin: 8px 0 0; font-size: 13px; color: var(--mid-fg); font-weight: 600; }

.spots { display: grid; gap: 14px; padding: 16px 20px;
         grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); }

/* spot card */
.spot { background: var(--surface-2); border-radius: var(--radius); border: 1px solid var(--border);
        padding: 16px 16px 16px 20px; scroll-margin-top: 80px; display: flex; flex-direction: column;
        box-shadow: inset 4px 0 0 var(--c, var(--border-strong)); transition: box-shadow .2s, border-color .2s; }
.spot:target { border-color: var(--accent);
               box-shadow: inset 4px 0 0 var(--c, var(--border-strong)),
                           0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent); }
.spot-top { display: flex; gap: 12px; align-items: start; justify-content: space-between; }
.spot h4 { margin: 0; font-size: 18px; font-weight: 750; letter-spacing: -.01em; line-height: 1.25; }
.spot .meta { margin: 2px 0 0; font-size: 13px; color: var(--muted); }
.stars { display: block; margin-top: 2px; color: #f59e0b; letter-spacing: 1px; font-size: 13px; }
.stars span { color: var(--border-strong); }
.score { flex: none; text-align: center; min-width: 58px; padding: 6px 8px 5px; border-radius: 12px;
         background: color-mix(in srgb, var(--c, #64748b) 14%, var(--surface)); }
.score b { display: block; font-size: 26px; line-height: 1; font-weight: 800; color: var(--cf, var(--muted)); }
.score small { display: block; font-size: 10px; font-weight: 700; color: var(--muted);
               letter-spacing: .06em; text-transform: uppercase; margin-top: 3px; }
.best { margin: 12px 0 8px; font-size: 13px; color: var(--muted); }
.best strong { color: var(--text); }
.cond { margin: 0 0 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.cond div { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 7px 10px; }
.cond dt { font-size: 11px; font-weight: 700; color: var(--muted); letter-spacing: .05em; text-transform: uppercase; }
.cond dd { margin: 2px 0 0; font-size: 13px; font-weight: 600; }
.fit { margin: 0 0 12px; display: grid; grid-template-columns: auto 1fr auto; gap: 5px 10px;
       align-items: center; font-size: 12px; }
.fit dt { color: var(--muted); }
.fit dd { margin: 0; }
.fit .bar { height: 6px; border-radius: 999px; background: var(--surface-3); overflow: hidden; }
.fit .bar i { display: block; height: 100%; border-radius: inherit; background: var(--accent); }
.fit .val { font-weight: 700; text-align: right; min-width: 40px; }
.fit .neg { color: var(--bad-fg); }
.opt { margin: 0 0 12px; font-size: 12px; color: var(--muted); }
.opt strong { color: var(--text); font-weight: 600; }
.days { list-style: none; margin: 0; padding: 0; display: grid; grid-auto-flow: column;
        grid-auto-columns: minmax(0, 1fr); gap: 4px; }
.days li { text-align: center; font-size: 11px; color: var(--muted); white-space: nowrap; }
.days b { display: block; margin-top: 3px; padding: 3px 0; border-radius: 7px; font-size: 12px;
          background: var(--c, var(--surface-3)); color: #fff; }
.days li.no-score b { background: var(--surface-3); color: var(--muted); }
.days li.best-day span { color: var(--text); font-weight: 700; }
.days li.best-day b { box-shadow: 0 0 0 2px var(--surface-2), 0 0 0 3.5px var(--c); }
.flags { margin: 10px 0 0; font-size: 12px; font-weight: 600; color: var(--mid-fg); }
.notas { margin: 10px 0 0; padding-top: 10px; border-top: 1px dashed var(--border);
         font-size: 12px; color: var(--muted); }
.shares { margin: 10px 0 0; font-size: 12px; color: var(--accent-text); font-weight: 600; }
.shares a { color: inherit; }
.links { margin: auto 0 0; padding-top: 12px; }
.lineup { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px;
          background: var(--surface); border: 1px solid var(--border); color: var(--text);
          font-size: 13px; font-weight: 600; text-decoration: none; }
.lineup:hover { border-color: var(--accent); color: var(--accent-text); }
.lineup svg { width: 14px; height: 14px; color: var(--accent-text); }

/* hourly chart */
.chart { margin: 0; padding: 4px 0 16px; border-top: 1px solid var(--border); }
.chart figcaption { padding: 12px 20px 10px; font-size: 13px; color: var(--muted); }
.chart figcaption strong { color: var(--text); }
.table-wrap { overflow-x: auto; margin: 0 12px; border-radius: 12px; border: 1px solid var(--border);
              background: var(--surface); scrollbar-width: thin; }
table.wg { border-collapse: collapse; font-size: 11.5px; white-space: nowrap; min-width: 100%; }
table.wg th, table.wg td { padding: 0 2px; height: 26px; min-width: 27px; text-align: center;
  font-weight: 600; border: 1px solid var(--surface); background: var(--bg-c, transparent); color: var(--fg-c, inherit); }
table.wg col.ds { border-left: 2px solid var(--border-strong); }
table.wg col.n { background: var(--night); }
table.wg .lbl { position: sticky; left: 0; z-index: 1; min-width: 108px; padding: 0 10px; text-align: left;
                font-weight: 600; color: var(--text); background: var(--surface);
                box-shadow: 1px 0 0 var(--border); }
table.wg .lbl small { color: var(--faint); font-weight: 500; margin-left: 3px; }
table.wg thead th { font-weight: 700; color: var(--text); background: var(--surface-2); }
table.wg thead .dayh { background: var(--surface-3); font-size: 12px; text-align: left; padding-left: 8px; }
table.wg thead th.n { color: var(--muted); background: color-mix(in srgb, var(--surface-2) 70%, var(--surface-3)); }
.ar { display: inline-block; width: 15px; height: 15px; vertical-align: middle; background: currentColor;
      -webkit-mask: var(--arrow) center / contain no-repeat; mask: var(--arrow) center / contain no-repeat;
      --arrow: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 2 18.5 21 12 16.5 5.5 21Z'/%3E%3C/svg%3E"); }

/* color scales: --bg-c / --fg-c per cell */
.ws-1 { --bg-c: #ffffff; --fg-c: #64748b; } .ws-2 { --bg-c: #e3f8ee; --fg-c: #14532d; }
.ws-3 { --bg-c: #b3ecd0; --fg-c: #14532d; } .ws-4 { --bg-c: #7ddcab; --fg-c: #0f3d22; }
.ws-5 { --bg-c: #f4e35c; --fg-c: #3f3200; } .ws-6 { --bg-c: #fbab3c; --fg-c: #3d1d00; }
.ws-7 { --bg-c: #f26d5b; --fg-c: #2d0703; } .ws-8 { --bg-c: #a21caf; --fg-c: #ffffff; }
.t-1 { --bg-c: #c7d2fe; --fg-c: #1e1b4b; } .t-2 { --bg-c: #dbeafe; --fg-c: #1e3a8a; }
.t-3 { --bg-c: #fef9c3; --fg-c: #422006; } .t-4 { --bg-c: #fde68a; --fg-c: #422006; }
.t-5 { --bg-c: #fdba74; --fg-c: #431407; } .t-6 { --bg-c: #fb923c; --fg-c: #431407; }
.t-7 { --bg-c: #dc2626; --fg-c: #ffffff; } .t-8 { --bg-c: #991b1b; --fg-c: #ffffff; }
.cl-1 { --bg-c: #ffffff; --fg-c: #64748b; } .cl-2 { --bg-c: #f1f5f9; --fg-c: #475569; }
.cl-3 { --bg-c: #d5dde7; --fg-c: #334155; } .cl-4 { --bg-c: #a3b1c2; --fg-c: #0f172a; }
.cl-5 { --bg-c: #64748b; --fg-c: #ffffff; }
.wv-0 { --bg-c: #ffffff; --fg-c: #64748b; } .wv-1 { --bg-c: #dbeafe; --fg-c: #1e3a8a; }
.wv-2 { --bg-c: #93c5fd; --fg-c: #172554; } .wv-3 { --bg-c: #2563eb; --fg-c: #ffffff; }
.wv-4 { --bg-c: #1e40af; --fg-c: #ffffff; } .wv-5 { --bg-c: #5b21b6; --fg-c: #ffffff; }
.pr-1 { --bg-c: #ffffff; --fg-c: #64748b; } .pr-2 { --bg-c: #f5f5f4; --fg-c: #44403c; }
.pr-3 { --bg-c: #fde7c7; --fg-c: #431407; } .pr-4 { --bg-c: #fdba74; --fg-c: #431407; }
.pr-5 { --bg-c: #c2410c; --fg-c: #ffffff; }
.rn-0 { --bg-c: #ffffff; --fg-c: #64748b; } .rn-1 { --bg-c: #e0f2fe; --fg-c: #0c4a6e; }
.rn-2 { --bg-c: #7dd3fc; --fg-c: #082f49; } .rn-3 { --bg-c: #0369a1; --fg-c: #ffffff; }
.rn-4 { --bg-c: #1e3a8a; --fg-c: #ffffff; }
.cell-na { --bg-c: transparent; --fg-c: var(--faint); }
@media (prefers-color-scheme: dark) {
  .ws-1, .cl-1, .wv-0, .pr-1, .rn-0 { --bg-c: transparent; --fg-c: var(--faint); }
  .ws-2 { --bg-c: #16392c; --fg-c: #bbf7d0; } .ws-3 { --bg-c: #1d5a40; --fg-c: #dcfce7; }
  .cl-2 { --bg-c: #1b2a3e; --fg-c: #cbd5e1; } .cl-3 { --bg-c: #2b3d55; --fg-c: #e2e8f0; }
  .cl-4 { --bg-c: #475a73; --fg-c: #f8fafc; } .cl-5 { --bg-c: #66788f; --fg-c: #ffffff; }
  .wv-1 { --bg-c: #1a3358; --fg-c: #dbeafe; } .pr-2 { --bg-c: #23262b; --fg-c: #d6d3d1; }
  .pr-3 { --bg-c: #5a3a17; --fg-c: #ffedd5; } .rn-1 { --bg-c: #123049; --fg-c: #e0f2fe; }
  .t-1 { --bg-c: #353a7a; --fg-c: #e0e7ff; } .t-2 { --bg-c: #1e3a66; --fg-c: #dbeafe; }
}

.empty { background: var(--surface); border: 1px dashed var(--border-strong); border-radius: var(--radius);
         padding: 28px; text-align: center; color: var(--muted); }

footer { padding: 8px 0 40px; color: var(--muted); font-size: 13px; }
footer .wrap { display: flex; flex-wrap: wrap; gap: 8px 20px; justify-content: space-between;
               border-top: 1px solid var(--border); padding-top: 20px; }
footer p { margin: 0; }
footer .legend { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; }
footer .legend .pill { margin-right: 4px; }

@media (max-width: 700px) {
  .wrap { padding: 0 16px; }
  .hero .wrap { padding-top: 24px; padding-bottom: 52px; }
  .hero .meta { font-size: 12px; gap: 6px; }
  .hero .meta li { padding: 4px 10px; }
  .tabs-bar { padding: 8px 0; }
  .tabs { width: 100%; gap: 2px; }
  .tabs [role="tab"] { flex: 1 1 0; min-width: 0; flex-direction: column; justify-content: center;
                       font-size: 13px; padding: 7px 2px; gap: 3px; }
  .dep-head h2 { font-size: 26px; }
  .ranking { padding: 12px; }
  .ranking ol { grid-template-columns: 1fr; }
  .station { border-radius: 16px; }
  .station-head { padding: 16px; }
  .station-head h3 { font-size: 18px; }
  .spots { padding: 12px; gap: 12px; }
  .spot { padding: 14px 14px 14px 16px; }
  .chart figcaption { padding: 12px 16px 10px; }
  .table-wrap { margin: 0 8px; }
  table.wg .lbl { min-width: 0; padding: 0 6px; font-size: 11px; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; scroll-behavior: auto !important; } }
"""

SHARED_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
               'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
               '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/>'
               '<circle cx="18" cy="18" r="2.5"/><path d="M8.2 10.9 15.8 7.1M8.2 13.1l7.6 3.8"/></svg>')

LINEUP_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
               'stroke-linecap="round" aria-hidden="true">'
               '<path d="M2 15c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 5 2M2 20c2.5 0 2.5-2 5-2s2.5 2 '
               '5 2 2.5-2 5-2 2.5 2 5 2M12 10V3m0 0L9 6m3-3 3 3"/></svg>')

WAVES_SVG = ('<svg class="waves" viewBox="0 0 1440 48" preserveAspectRatio="none" aria-hidden="true">'
             '<path fill="currentColor" opacity=".35" d="M0 22c120 14 240 18 360 10S600 6 720 8s240 18 '
             '360 20 240-8 360-14v34H0z"/>'
             '<path fill="currentColor" d="M0 34c160 10 320 10 480 2s320-16 480-12 320 14 480 12v12H0z"/>'
             '</svg>')


def spot_name(spot: dict) -> str:
    return spot.get("name") or spot.get("slug") or ""


def spot_anchor(spot: dict) -> str:
    return f"spot-{spot.get('slug')}"


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " y " + names[-1]


def spot_link(spot: dict, with_department: bool = False) -> str:
    label = esc(spot_name(spot))
    if with_department:
        label += f" ({esc(department_of(spot))})"
    return f'<a href="#{esc(spot_anchor(spot))}">{label}</a>'


def render_stars(value: Any) -> str:
    try:
        n = max(0, min(5, int(value or 0)))
    except (TypeError, ValueError):
        return ""
    if not n:
        return ""
    return (f'<span class="stars" role="img" aria-label="Valoración {n} de 5">'
            f'{"★" * n}<span>{"★" * (5 - n)}</span></span>')


def render_fit(row: dict) -> str:
    comps = row.get("components") or {}
    parts = []
    for key, label, top in (("wind", "Viento", 40), ("swell_dir", "Dirección swell", 30),
                            ("wave", "Tamaño de ola", 30)):
        value = comps.get(key)
        if value is None:
            continue
        pct = max(0, min(100, round(100 * value / top)))
        neg = ' neg' if value < 0 else ''
        parts.append(f'<dt>{label}</dt><dd class="bar"><i style="width:{pct}%"></i></dd>'
                     f'<dd class="val{neg}">{value}/{top}</dd>')
    if not parts:
        return ""
    return f'<dl class="fit" aria-label="Puntaje del mejor día por componente">{"".join(parts)}</dl>'


def render_spot(spot: dict, summary: dict | None, dates: list[str], station_mates: list[dict]) -> str:
    best = (summary or {}).get("best")
    score = best_score(summary)
    rcls = rating_class(score)
    meta = [esc(spot.get("tipo_ola") or "")]
    if (spot.get("region") or "") != department_of(spot):
        meta.insert(0, esc(spot.get("region")))
    meta_html = " · ".join(m for m in meta if m)
    stars = render_stars(spot.get("valoracion"))

    score_html = (f'<div class="score" aria-label="Mejor puntaje de la semana: {score} de 100">'
                  f'<b>{score}</b><small>puntaje</small></div>' if score is not None else
                  '<div class="score"><b>–</b><small>sin datos</small></div>')

    body = []
    if best:
        body.append(f'<p class="best">Mejor día: <strong>{day_long(summary["best_day"])}</strong></p>')
        body.append(
            '<dl class="cond">'
            f'<div><dt>Viento</dt><dd>{esc(best.get("wind") or "–")}</dd></div>'
            f'<div><dt>Ola</dt><dd>{esc(best.get("swell") or "–")}</dd></div>'
            '</dl>')
        body.append(render_fit(best))
    opt = []
    if spot.get("viento_optimo"):
        opt.append(f'viento <strong>{esc(", ".join(spot["viento_optimo"]))}</strong>')
    if spot.get("swell_optimo"):
        opt.append(f'swell <strong>{esc(", ".join(spot["swell_optimo"]))}</strong>')
    if opt:
        body.append(f'<p class="opt">Ideal: {" · ".join(opt)}</p>')
    if summary and summary.get("days"):
        items = []
        for date in dates:
            row = summary["days"].get(date)
            if row is None:
                items.append(f'<li class="no-score" title="{esc(day_long(date))}: sin puntaje">'
                             f'<span>{day_short(date)}</span><b>–</b></li>')
                continue
            s = row.get("score") or 0
            cls = rating_class(s) + (" best-day" if date == summary["best_day"] else "")
            items.append(f'<li class="{cls.strip()}" title="{esc(day_long(date))}: {s}">'
                         f'<span>{day_short(date)}</span><b>{s}</b></li>')
        body.append(f'<ol class="days" aria-label="Puntaje por día">{"".join(items)}</ol>')
    flags = " ".join((best or {}).get("flags") or [])
    if flags:
        body.append(f'<p class="flags">{esc(flags)}</p>')
    if station_mates:
        mates = [spot_link(m, department_of(m) != department_of(spot)) for m in station_mates]
        body.append(f'<p class="shares">Comparte pronóstico con {join_names(mates)}</p>')
    if spot.get("notas"):
        body.append(f'<p class="notas">{esc(spot["notas"])}</p>')
    if spot.get("lineup_url"):
        body.append(f'<p class="links"><a class="lineup" href="{esc(spot["lineup_url"])}" '
                    f'target="_blank" rel="noopener" aria-label="Ver en Lineup: {esc(spot_name(spot))}">'
                    f'{LINEUP_ICON}Ver en Lineup'
                    f'<span aria-hidden="true"> ↗</span></a></p>')

    return (
        f'<article class="spot {rcls}" id="{esc(spot_anchor(spot))}">'
        f'<div class="spot-top"><div><h4>{esc(spot_name(spot))}</h4>'
        f'<p class="meta">{meta_html}</p>{stars}</div>{score_html}</div>'
        f'{"".join(body)}'
        f'</article>'
    )


def render_chart(station: dict, max_days: int) -> str:
    """Windguru-style hourly table for one station."""
    days = station.get("days", [])[:max_days]
    hours = [(d["date"], h) for d in days for h in d.get("hours", [])]
    if not hours:
        return '<p class="empty">Windguru no trae horas de pronóstico para esta estación.</p>'

    sunrise = station.get("sunrise") or "06:00"
    sunset = station.get("sunset") or "20:00"
    night = [not (sunrise <= h.get("local", "") < sunset) for _, h in hours]
    first = [i == 0 or hours[i - 1][0] != date for i, (date, _) in enumerate(hours)]

    cols = ['<col>'] + [
        f'<col class="{" ".join(c for c, on in (("ds", first[i]), ("n", night[i])) if on)}">'
        if first[i] or night[i] else '<col>' for i in range(len(hours))]
    day_cells = "".join(f'<th scope="colgroup" class="dayh" colspan="{len(d.get("hours", []))}">'
                        f'{day_short(d["date"])}</th>' for d in days if d.get("hours"))
    hour_cells = "".join(f'<th scope="col" class="n">{esc(h["local"][:2])}</th>' if night[i] else
                         f'<th scope="col">{esc(h["local"][:2])}</th>'
                         for i, (_, h) in enumerate(hours))

    def row(label: str, unit: str, fn) -> str:
        cells = []
        for _, h in hours:
            text, klass = fn(h)
            cells.append(f'<td class="{klass}">{text}</td>' if klass else f'<td>{text}</td>')
        unit_html = f"<small>{unit}</small>" if unit else ""
        return f'<tr><th scope="row" class="lbl">{label}{unit_html}</th>{"".join(cells)}</tr>'

    def rain(h: dict) -> tuple[str, str]:
        mm = h.get("rain_mm")
        return (fmt_num(mm, 1) if mm is not None and float(mm) > 0 else "", rain_class(mm))

    rows = [
        row("Viento", "kt", lambda h: (fmt_num(h.get("wind_kt"), 0), ws_class(h.get("wind_kt")))),
        row("Ráfagas", "kt", lambda h: (fmt_num(h.get("gust_kt"), 0), gust_class(h.get("gust_kt")))),
        row("Dir. viento", "", lambda h: (arrow(h.get("wind_dir")), "")),
        row("Ola", "m", lambda h: (fmt_num(h.get("swell_m"), 1), wave_class(h.get("swell_m")))),
        row("Período", "s", lambda h: (fmt_num(h.get("swell_period_s"), 0),
                                       period_class(h.get("swell_period_s")))),
        row("Dir. ola", "", lambda h: (arrow(h.get("swell_dir")), "")),
        row("Temp.", "°C", lambda h: (fmt_num(h.get("temp_c"), 0), temp_class(h.get("temp_c")))),
        row("Nubes", "%", lambda h: (fmt_num(h.get("cloud_pct"), 0), cloud_class(h.get("cloud_pct")))),
        row("Lluvia", "mm/h", rain),
    ]
    return (
        f'<div class="table-wrap" tabindex="0" role="region" '
        f'aria-label="Tabla horaria de {esc(station.get("spotname") or "la estación")}">'
        f'<table class="wg"><colgroup>{"".join(cols)}</colgroup>'
        f'<thead><tr><th class="lbl" scope="col">Día</th>{day_cells}</tr>'
        f'<tr><th class="lbl" scope="col">Hora</th>{hour_cells}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def render_station(department: str, group: dict, normalized: dict, summaries: dict,
                   dates: list[str], users: list[dict], days_count: int) -> str:
    sid = group["station_id"]
    spots = group["spots"]
    station = normalized.get("stations", {}).get(sid) or {}
    name = (station.get("spotname") or "").strip() or spots[0].get("zona_station_name") or sid
    wg_url = station.get("page_url") or spots[0].get("windguru_url") or f"https://www.windguru.cz/{sid}"
    elsewhere = [u for u in users if department_of(u) != department]
    total = len(spots) + len(elsewhere)
    anchor = f"st-{department_id(department)}-{sid}"

    if total > 1:
        kicker = (f'Estación Windguru <span class="badge shared">{SHARED_ICON}'
                  f'Compartida por {total} playas</span>')
    else:
        kicker = "Estación Windguru"
    if station.get("model_name"):
        models = (f"{station['model_name']} + {station['wave_model_name']}"
                  if station.get("wave_model_name") else
                  f"{station['model_name']}, sin oleaje en Windguru")
        src = f"Windguru: {esc(name)} ({esc(models)}) · id {esc(sid)}"
    else:
        src = f"Windguru id {esc(sid)}"
    notice = ""
    if station and not station.get("wave_model_name"):
        notice = ('<p class="notice">Windguru no publica oleaje para esta estación: '
                  'el puntaje solo usa el viento.</p>')

    cards = "".join(
        render_spot(s, summaries.get(s.get("slug")), dates,
                    [m for m in spots + elsewhere if m is not s])
        for s in spots)

    here = [spot_link(s) for s in spots]
    if total > 1:
        who = f"la misma para <strong>{join_names(here + [spot_link(s, True) for s in elsewhere])}</strong>"
        caption = (f"Pronóstico horario de la estación <strong>{esc(name)}</strong>, {who}. "
                   "Hora de Uruguay.")
    else:
        caption = (f"Pronóstico horario de la estación <strong>{esc(name)}</strong>, "
                   f"usada por {here[0]}. Hora de Uruguay.")

    chart = (render_chart(station, days_count) if station.get("days") else
             f'<p class="empty">Sin datos horarios para la estación {esc(sid)}.</p>')

    return (
        f'<section class="station" id="{anchor}" aria-labelledby="{anchor}-h">'
        f'<header class="station-head"><div>'
        f'<p class="kicker">{kicker}</p>'
        f'<h3 id="{anchor}-h">{esc(name)}</h3>'
        f'<p class="src">{src}</p>{notice}</div>'
        f'<a class="wg-link" href="{esc(wg_url)}" target="_blank" rel="noopener">'
        f'Ver en Windguru<span aria-hidden="true"> ↗</span></a></header>'
        f'<div class="spots">{cards}</div>'
        f'<figure class="chart"><figcaption>{caption}</figcaption>{chart}</figure>'
        f'</section>'
    )


def render_department_panel(department: str, spots: list[dict], summaries: dict,
                            normalized: dict, dates: list[str], users_by_station: dict,
                            days_count: int, selected: bool) -> str:
    dep_id = department_id(department)
    hidden = "" if selected else " hidden"
    head_open = (f'<div class="tabpanel" role="tabpanel" id="panel-{dep_id}" '
                 f'aria-labelledby="tab-{dep_id}" tabindex="0"{hidden}>')
    if not spots:
        return (f'{head_open}<div class="dep-head"><div><h2>{esc(department)}</h2></div></div>'
                f'<p class="empty">No hay playas de {esc(department)} en spots.json.</p></div>')

    groups = group_by_station(spots, summaries)
    shared = sum(1 for g in groups if len(users_by_station.get(g["station_id"], [])) > 1)
    n_spots = len(spots)
    sub = (f'{n_spots} playa{"s" if n_spots != 1 else ""} · '
           f'{len(groups)} estaci{"ones" if len(groups) != 1 else "ón"} Windguru')
    if shared:
        sub += f' · {shared} compartida{"s" if shared != 1 else ""}'

    ranked = [s for g in groups for s in g["spots"]]
    ranked.sort(key=lambda s: -(best_score(summaries.get(s.get("slug"))) or -10**9))
    top = ranked[0]
    top_summary = summaries.get(top.get("slug"))
    best_html = ""
    if best_score(top_summary) is not None:
        best_html = (f'<p class="dep-best">Mejor de la semana: {spot_link(top)} · '
                     f'{best_score(top_summary)} puntos el {day_long(top_summary["best_day"]).lower()}</p>')

    items = []
    for s in ranked:
        summ = summaries.get(s.get("slug"))
        score = best_score(summ)
        pill = (f'<span class="pill {rating_class(score)}">{score}</span>' if score is not None
                else '<span class="pill none">–</span>')
        when = f'<small>{day_short(summ["best_day"])}</small>' if score is not None else ""
        items.append(f'<li><a href="#{esc(spot_anchor(s))}">{pill}'
                     f'<span class="name">{esc(spot_name(s))}</span>{when}</a></li>')

    stations = "".join(render_station(department, g, normalized, summaries, dates,
                                      users_by_station.get(g["station_id"], []), days_count)
                       for g in groups)
    return (
        f'{head_open}'
        f'<div class="dep-head"><div><h2>{esc(department)}</h2><p class="sub">{sub}</p></div>'
        f'{best_html}</div>'
        f'<nav class="ranking" aria-labelledby="rank-{dep_id}">'
        f'<h3 id="rank-{dep_id}">Playas por puntaje, próximos {len(dates)} días</h3>'
        f'<ol>{"".join(items)}</ol></nav>'
        f'{stations}'
        f'</div>'
    )


def render_tab(department: str, best: int | None, selected: bool) -> str:
    dep_id = department_id(department)
    badge = (f'<span class="tab-score {rating_class(best)}">{best}</span>'
             if best is not None else "")
    return (
        f'<button type="button" role="tab" id="tab-{dep_id}" aria-controls="panel-{dep_id}" '
        f'aria-selected="{"true" if selected else "false"}" tabindex="{0 if selected else -1}">'
        f'{esc(department)}{badge}</button>'
    )


TABS_JS = """
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  var bar = document.querySelector('.tabs-bar');
  function select(tab, focus, keepHash) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (focus) tab.focus();
    var top = document.querySelector('.hero').offsetHeight;
    if (window.scrollY > top) window.scrollTo(0, top);
    if (!keepHash) history.replaceState(null, '', '#' + tab.id.slice(4));
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { select(tab, false); });
    tab.addEventListener('keydown', function (e) {
      var next = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1}[e.key];
      if (next === undefined) return;
      e.preventDefault();
      select(tabs[(next + tabs.length) % tabs.length], true);
    });
  });
  function fromHash() {
    var id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    var tab = document.getElementById('tab-' + id);
    if (tabs.indexOf(tab) >= 0) { select(tab, false); return; }
    var target = document.getElementById(id);
    var panel = target && target.closest('[role="tabpanel"]');
    if (!panel) return;
    if (panel.hidden) select(document.getElementById(panel.getAttribute('aria-labelledby')), false, true);
    target.scrollIntoView();
  }
  window.addEventListener('hashchange', fromHash);
  window.addEventListener('scroll', function () {
    bar.style.borderBottomColor = window.scrollY > document.querySelector('.hero').offsetHeight
      ? 'var(--border)' : 'transparent';
  }, {passive: true});
  fromHash();
})();
"""


def render_html(scored: dict, normalized: dict, spots_by_slug: dict,
                generated_at: str, days_count: int) -> str:
    source = scored.get("source", {})
    stations_used = source.get("stations_used") or []
    initdate = source.get("initdate_utc") or ""
    model = source.get("model") or ""

    by_department = split_by_department(scored, spots_by_slug)
    dates = sorted({d["date"] for d in scored.get("by_day", []) if d.get("date")})[:days_count]
    users_by_station: dict[str, list[dict]] = {}
    for spot in spots_by_slug.values():
        users_by_station.setdefault(station_of(spot), []).append(spot)

    tabs, panels = [], []
    for i, dep in enumerate(DEPARTMENTS):
        spots = [s for s in spots_by_slug.values() if department_of(s) == dep]
        summaries = summarize_spots(by_department[dep], dates)
        best = max((v for v in (best_score(summaries.get(s["slug"])) for s in spots)
                    if v is not None), default=None)
        tabs.append(render_tab(dep, best, i == 0))
        panels.append(render_department_panel(dep, spots, summaries, normalized, dates,
                                              users_by_station, days_count, i == 0))

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="color-scheme" content="light dark"/>
<meta name="description" content="Pronóstico de surf para las playas de Uruguay, por departamento, con datos de Windguru."/>
<title>Surf Uruguay · Pronóstico</title>
<style>{CSS}</style>
</head>
<body>
<header class="hero">
  <div class="wrap">
    <p class="eyebrow">Pronóstico de surf</p>
    <h1>Surf Uruguay</h1>
    <ul class="meta">
      <li>Generado: {esc(generated_at)} (hora de Uruguay)</li>
      <li>Modelo: {esc(model)}</li>
      <li>Init: {esc(initdate)} UTC</li>
      <li>{len(stations_used)} estaciones</li>
      <li>Fuente: <a href="https://www.windguru.cz" target="_blank" rel="noopener">Windguru</a></li>
    </ul>
  </div>
  {WAVES_SVG}
</header>
<div class="tabs-bar">
  <div class="wrap"><div class="tabs" role="tablist" aria-label="Departamentos">{"".join(tabs)}</div></div>
</div>
<main class="wrap">
  {"".join(panels)}
</main>
<footer>
  <div class="wrap">
    <p>Datos: <a href="https://www.windguru.cz" target="_blank" rel="noopener">windguru.cz</a> ·
       Puntaje propio: viento (40), dirección de swell (30), tamaño de ola (30)</p>
    <p class="legend"><span><span class="pill rt-good">70+</span>bueno</span>
       <span><span class="pill rt-mid">50–69</span>moderado</span>
       <span><span class="pill rt-low">30–49</span>flojo</span>
       <span><span class="pill rt-bad">&lt;30</span>no recomendado</span></p>
  </div>
</footer>
<script>{TABS_JS}</script>
</body>
</html>"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scored", help="Path to scored.json (from score_spots.py)")
    ap.add_argument("normalized", help="Path to normalized.json (from parse_windguru.py, with --merge)")
    ap.add_argument("spots", help="Path to spots.json")
    ap.add_argument("--out", help="Write HTML to this file (default: stdout)")
    ap.add_argument("--days", type=int, default=7,
                    help="Days of forecast to show: hourly tables and per-day scores (default: 7)")
    args = ap.parse_args(argv)

    with open(args.scored) as f:
        scored = json.load(f)
    with open(args.normalized) as f:
        normalized = json.load(f)
    with open(args.spots) as f:
        spots = json.load(f)

    spots_by_slug = {s["slug"]: s for s in spots if s.get("slug")}

    generated_at = dt.datetime.now(UY_TZ).strftime("%Y-%m-%d %H:%M")
    try:
        html_out = render_html(scored, normalized, spots_by_slug,
                               generated_at, args.days)
    except ValueError as err:
        sys.exit(str(err))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(html_out)
        # Print only the absolute path on stdout — token-friendly for the LLM
        print(os.path.abspath(args.out))
    else:
        sys.stdout.write(html_out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
