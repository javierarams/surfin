#!/usr/bin/env python3
"""generate_html.py — Build a single-file HTML surf forecast (Windguru-style).

Inputs:
  scored.json     — output of score_spots.py (by_day → best[] per day)
  normalized.json — output of parse_windguru.py (with wave model merged in)
  spots.json      — references/spots.json (40 spots metadata)

Output (stdout, or --out FILE): a self-contained HTML page.

  Top section — cards with the highest-scoring spots across the forecast window.
  Bottom section — up to N (default 3) Windguru-style hourly tables, one per
                   spot, dedup'd by windguru_id, only including spots that have
                   at least one "surfable" day (score >= 50 AND swell > 0.3) in
                   the next 7 days. If fewer than N spots are surfable, the
                   shortlist is filled with the best-scoring ones regardless.

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
import os
import sys
from typing import Any


CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

WEEKDAYS_ES = {
    "monday": "Lu", "tuesday": "Ma", "wednesday": "Mi", "thursday": "Ju",
    "friday": "Vi", "saturday": "Sa", "sunday": "Do",
    "lunes": "Lu", "martes": "Ma", "miércoles": "Mi", "miercoles": "Mi",
    "jueves": "Ju", "viernes": "Vi", "sábado": "Sa", "sabado": "Sa", "domingo": "Do",
}

WEEKDAYS_FULL_ES = {
    "monday": "Lunes", "tuesday": "Martes", "wednesday": "Miércoles",
    "thursday": "Jueves", "friday": "Viernes", "saturday": "Sábado", "sunday": "Domingo",
}


def esc(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s))


def fmt_num(v: Any, decimals: int = 1) -> str:
    if v is None:
        return ""
    try:
        if decimals == 0:
            return str(int(round(float(v))))
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return ""


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


def arrow_svg(degrees: float | None, size: int = 18) -> str:
    """SVG arrow pointing in the direction the flow IS GOING.
    Wind/swell direction in the API is the "FROM" direction, so we rotate by +180°
    to render where it goes (downwind / downswell)."""
    if degrees is None:
        return ""
    rot = (float(degrees) + 180.0) % 360.0
    return (
        f'<svg class="dir-arrow" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'style="transform: rotate({rot:.0f}deg);" aria-hidden="true">'
        f'<path d="M12 2 L17 14 L12 11 L7 14 Z" fill="currentColor"/>'
        f'</svg>'
    )


# ---------- spot selection ----------

def pick_detail_spots(scored: dict, spots_by_slug: dict, max_n: int = 3) -> list[dict]:
    """Pick up to max_n spots for the detail tables.

    Rule (per user spec):
      - "condiciones aceptables" in at least one of the 7 days
        (surfable=True OR score >= 50)
      - dedup by windguru_id (keep highest-scoring per windguru_id)
      - top max_n

    If fewer than max_n spots are acceptable, fill the remaining slots with the
    best-scoring spots regardless, still respecting the windguru_id dedup.
    """
    # Map slug → (best_score_across_days, best_row, acceptable_bool)
    agg: dict[str, dict] = {}
    for day in scored.get("by_day", []):
        for row in day.get("best", []):
            slug = row.get("slug")
            if not slug:
                continue
            score = row.get("score") or 0
            acceptable = bool(row.get("surfable")) or score >= 50
            entry = agg.setdefault(slug, {"best_score": -10**9, "row": None,
                                          "acceptable": False, "best_day": None})
            if score > entry["best_score"]:
                entry["best_score"] = score
                entry["row"] = row
                entry["best_day"] = day["date"]
            if acceptable:
                entry["acceptable"] = True

    # Order: acceptable first (by best_score desc), then non-acceptable (by best_score desc)
    items = []
    for slug, e in agg.items():
        spot_meta = spots_by_slug.get(slug, {})
        items.append({
            "slug": slug,
            "spot": spot_meta,
            "best_score": e["best_score"],
            "best_day": e["best_day"],
            "row": e["row"],
            "acceptable": e["acceptable"],
            "windguru_id": str(spot_meta.get("windguru_id") or e["row"].get("windguru_id") or ""),
        })
    items.sort(key=lambda x: (not x["acceptable"], -x["best_score"]))

    chosen: list[dict] = []
    seen_ids: set[str] = set()
    for it in items:
        if len(chosen) >= max_n:
            break
        wid = it["windguru_id"]
        if wid and wid in seen_ids:
            continue
        chosen.append(it)
        if wid:
            seen_ids.add(wid)
    return chosen


# ---------- rendering ----------

CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       margin: 0; background: #f1f5f9; color: #0f172a; }
header { padding: 28px 24px 22px; background: linear-gradient(135deg, #0c4a6e 0%, #0891b2 100%);
         color: white; }
header h1 { margin: 0 0 6px; font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }
header .meta { opacity: 0.9; font-size: 13px; }
main { max-width: 1500px; margin: 0 auto; padding: 24px; }

section h2 { font-size: 20px; margin: 8px 0 16px; font-weight: 600; color: #0f172a; }
section + section { margin-top: 36px; }

/* TOP — spot cards grid */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
         gap: 14px; }
.card { background: white; border-radius: 12px; padding: 16px; position: relative;
        box-shadow: 0 1px 3px rgba(15,23,42,0.06); border-top: 4px solid var(--c, #94a3b8); }
.card.rt-good { --c: #10b981; }
.card.rt-mid  { --c: #f59e0b; }
.card.rt-low  { --c: #fb923c; }
.card.rt-bad  { --c: #ef4444; }
.card h3 { margin: 0 0 2px; font-size: 16px; }
.card .region { color: #64748b; font-size: 12px; margin-bottom: 12px; }
.card .score { position: absolute; top: 14px; right: 16px;
               font-size: 26px; font-weight: 700; color: var(--c); line-height: 1; }
.card .day-when { font-size: 12px; color: #475569; margin-bottom: 10px; }
.card dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 4px 10px;
           font-size: 13px; }
.card dt { color: #64748b; }
.card dd { margin: 0; font-weight: 500; }
.card .flags { margin-top: 10px; font-size: 12px; color: #b45309; }
.card .notas { margin-top: 10px; font-size: 11px; color: #475569; font-style: italic;
               border-top: 1px solid #e2e8f0; padding-top: 8px; }

/* DETAIL — Windguru-style table */
.spot-detail { background: white; border-radius: 12px; padding: 16px 8px 8px;
               margin-bottom: 22px; box-shadow: 0 1px 3px rgba(15,23,42,0.06); }
.spot-detail .head { padding: 0 12px 10px; display: flex; align-items: baseline;
                     justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.spot-detail .head h3 { margin: 0; font-size: 18px; }
.spot-detail .head .sub { color: #64748b; font-size: 12px; }
.spot-detail .head a { color: #0891b2; text-decoration: none; font-size: 12px; }
.spot-detail .head a:hover { text-decoration: underline; }

.table-wrap { overflow-x: auto; padding: 0 4px 4px; }
table.wg { border-collapse: collapse; font-size: 11px; min-width: 100%; white-space: nowrap; }
table.wg th, table.wg td {
  padding: 3px 4px; text-align: center; border: 1px solid #e2e8f0;
  min-width: 28px; height: 24px; font-weight: 500;
}
table.wg .row-label {
  position: sticky; left: 0; background: #f8fafc; text-align: right; padding-right: 8px;
  font-weight: 600; color: #334155; min-width: 130px; z-index: 1;
}
table.wg thead th { background: #cbd5e1; color: #0f172a; font-weight: 700; }
table.wg .day-cell { background: #475569; color: white; font-weight: 700; }
table.wg .day-sep { border-left: 2px solid #334155; }

/* wind speed scale */
.ws-1 { background: #ffffff; } .ws-2 { background: #ecfeff; }
.ws-3 { background: #a5f3fc; } .ws-4 { background: #22d3ee; }
.ws-5 { background: #06b6d4; color: white; }
.ws-6 { background: #0e7490; color: white; }
.ws-7 { background: #ca8a04; color: white; }
.ws-8 { background: #b91c1c; color: white; }

/* temperature scale */
.t-1 { background: #dbeafe; } .t-2 { background: #ddd6fe; }
.t-3 { background: #fef9c3; } .t-4 { background: #fde68a; }
.t-5 { background: #fdba74; } .t-6 { background: #fb923c; color: white; }
.t-7 { background: #ef4444; color: white; } .t-8 { background: #991b1b; color: white; }

/* cloud cover scale */
.cl-1 { background: #ffffff; color: #94a3b8; } .cl-2 { background: #f1f5f9; }
.cl-3 { background: #cbd5e1; } .cl-4 { background: #64748b; color: white; }
.cl-5 { background: #334155; color: white; }

/* wave height scale */
.wv-0 { background: #ffffff; color: #94a3b8; } .wv-1 { background: #e0f2fe; }
.wv-2 { background: #7dd3fc; } .wv-3 { background: #0ea5e9; color: white; }
.wv-4 { background: #0369a1; color: white; } .wv-5 { background: #1e3a8a; color: white; }

/* period scale */
.pr-1 { background: #ffffff; color: #94a3b8; } .pr-2 { background: #f5f5f4; }
.pr-3 { background: #fed7aa; } .pr-4 { background: #fb923c; color: white; }
.pr-5 { background: #c2410c; color: white; }

/* rain scale */
.rn-0 { background: #ffffff; color: #cbd5e1; }
.rn-1 { background: #e0f2fe; } .rn-2 { background: #38bdf8; }
.rn-3 { background: #0284c7; color: white; } .rn-4 { background: #1e40af; color: white; }

/* rating cell scale */
.rt-good { color: #10b981; }
.rt-mid { color: #f59e0b; }
.rt-low { color: #fb923c; }
.rt-bad { color: #94a3b8; }

.dir-arrow { vertical-align: middle; color: #0f172a; }
.cell-na { background: #fafafa; color: #cbd5e1; }

footer { padding: 20px 24px; color: #64748b; font-size: 12px; text-align: center; }
footer a { color: #0891b2; }

@media (max-width: 700px) {
  header { padding: 18px 14px; }
  main { padding: 14px; }
  .card .score { font-size: 22px; }
}
"""


def render_card(item: dict) -> str:
    row = item["row"] or {}
    spot = item["spot"] or {}
    score = row.get("score") or 0
    rcls = rating_class(score)
    flags = " ".join(row.get("flags", []) or [])
    notas = spot.get("notas") or ""
    flags_html = f'<div class="flags">{esc(flags)}</div>' if flags else ""
    notas_html = f'<div class="notas">{esc(notas)}</div>' if notas else ""
    day_when = item.get("best_day") or ""
    weekday = ""
    # Try to compute weekday for the best_day
    try:
        d = dt.datetime.strptime(day_when, "%Y-%m-%d").date()
        weekday = WEEKDAYS_FULL_ES.get(d.strftime("%A").lower(), d.strftime("%a"))
    except Exception:
        pass

    return (
        f'<div class="card {rcls}">'
        f'  <div class="score">{esc(score)}</div>'
        f'  <h3>{esc(spot.get("name") or item.get("slug"))}</h3>'
        f'  <div class="region">{esc(spot.get("region") or "")}'
        f'     · {esc(spot.get("tipo_ola") or "")}</div>'
        f'  <div class="day-when">Mejor día: <strong>{esc(weekday)} {esc(day_when)}</strong></div>'
        f'  <dl>'
        f'    <dt>Viento</dt><dd>{esc(row.get("wind") or "—")}</dd>'
        f'    <dt>Swell</dt><dd>{esc(row.get("swell") or "—")}</dd>'
        f'    <dt>Óptimo</dt><dd>viento {esc(", ".join(spot.get("viento_optimo") or []))} '
        f'/ swell {esc(", ".join(spot.get("swell_optimo") or []))}</dd>'
        f'    <dt>Valoración</dt><dd>{"★" * int(spot.get("valoracion") or 0)}</dd>'
        f'  </dl>'
        f'  {flags_html}'
        f'  {notas_html}'
        f'</div>'
    )


def render_overview(scored: dict, spots_by_slug: dict, max_cards: int = 12) -> str:
    """Show the best <max_cards> spot/day combos across the window. We pick the
    best score per slug to avoid showing the same spot repeatedly."""
    best_per_slug: dict[str, dict] = {}
    for day in scored.get("by_day", []):
        for row in day.get("best", []):
            slug = row.get("slug")
            if not slug:
                continue
            entry = best_per_slug.setdefault(slug, {"score": -10**9, "row": None, "day": None})
            if (row.get("score") or 0) > entry["score"]:
                entry["score"] = row.get("score") or 0
                entry["row"] = row
                entry["day"] = day["date"]

    items = []
    for slug, e in best_per_slug.items():
        items.append({
            "slug": slug,
            "spot": spots_by_slug.get(slug, {}),
            "row": e["row"],
            "best_day": e["day"],
            "best_score": e["score"],
        })
    items.sort(key=lambda x: -x["best_score"])
    items = items[:max_cards]
    return '<div class="cards">' + "".join(render_card(i) for i in items) + "</div>"


def render_detail_table(item: dict, normalized: dict, max_days: int = 7) -> str:
    """Render a Windguru-style hourly table for one spot."""
    spot = item["spot"] or {}
    sid = item["windguru_id"]
    station = normalized.get("stations", {}).get(sid)
    if not station:
        return (
            f'<div class="spot-detail"><div class="head"><h3>{esc(spot.get("name"))}</h3>'
            f'<div class="sub">sin datos horarios para la estación {esc(sid)}</div></div></div>'
        )

    days = station.get("days", [])[:max_days]
    # Flatten all hours across the chosen days
    all_hours: list[dict] = []
    day_offsets: list[tuple[str, str, int, int]] = []  # (date, weekday_short, start_idx, count)
    for d in days:
        start = len(all_hours)
        for h in d.get("hours", []):
            all_hours.append({**h, "_date": d["date"], "_weekday": d["weekday"]})
        day_offsets.append((d["date"], WEEKDAYS_ES.get(d["weekday"], d["weekday"][:2].title()),
                            start, len(d.get("hours", []))))

    if not all_hours:
        return (
            f'<div class="spot-detail"><div class="head"><h3>{esc(spot.get("name"))}</h3>'
            f'<div class="sub">sin horas en el forecast</div></div></div>'
        )

    # --- Header row 1: day group cells ---
    head_day_cells = []
    for date, wd_short, _, count in day_offsets:
        try:
            dnum = dt.datetime.strptime(date, "%Y-%m-%d").day
        except Exception:
            dnum = date
        head_day_cells.append(
            f'<th class="day-cell day-sep" colspan="{count}">{esc(wd_short)} {esc(dnum)}</th>'
        )

    # --- Header row 2: hour labels (HH) ---
    head_hour_cells = []
    prev_date = None
    for h in all_hours:
        cls = "day-sep" if h["_date"] != prev_date else ""
        prev_date = h["_date"]
        head_hour_cells.append(f'<th class="{cls}">{esc(h["local"][:2])}h</th>')

    # --- Each metric row ---
    def row_cells(values_with_classes: list[tuple[str, str]]) -> str:
        # values_with_classes: list of (display, css_class). Day separators added by index.
        cells = []
        prev_date = None
        for i, (text, klass) in enumerate(values_with_classes):
            date = all_hours[i]["_date"]
            extra = " day-sep" if date != prev_date else ""
            prev_date = date
            cells.append(f'<td class="{klass}{extra}">{text}</td>')
        return "".join(cells)

    def render_row(label: str, fn) -> str:
        cells = []
        for h in all_hours:
            text, klass = fn(h)
            cells.append((text, klass))
        return (f'<tr><th class="row-label">{esc(label)}</th>{row_cells(cells)}</tr>')

    rows_html: list[str] = []
    rows_html.append(render_row("Viento (kt)",
        lambda h: (fmt_num(h.get("wind_kt"), 0), ws_class(h.get("wind_kt")))))
    rows_html.append(render_row("Ráfagas (kt)",
        lambda h: (fmt_num(h.get("gust_kt"), 0), gust_class(h.get("gust_kt")))))
    rows_html.append(render_row("Dirección viento",
        lambda h: (arrow_svg(h.get("wind_dir")), "")))
    rows_html.append(render_row("Ola (m)",
        lambda h: (fmt_num(h.get("swell_m"), 1), wave_class(h.get("swell_m")))))
    rows_html.append(render_row("Período ola (s)",
        lambda h: (fmt_num(h.get("swell_period_s"), 0), period_class(h.get("swell_period_s")))))
    rows_html.append(render_row("Dirección ola",
        lambda h: (arrow_svg(h.get("swell_dir")), "")))
    rows_html.append(render_row("Temperatura (°C)",
        lambda h: (fmt_num(h.get("temp_c"), 0), temp_class(h.get("temp_c")))))
    rows_html.append(render_row("Nubosidad (%)",
        lambda h: (fmt_num(h.get("cloud_pct"), 0), cloud_class(h.get("cloud_pct")))))
    rows_html.append(render_row("Precip. (mm/1h)",
        lambda h: (
            (fmt_num(h.get("rain_mm"), 1)
             if h.get("rain_mm") is not None and float(h["rain_mm"]) > 0 else ""),
            rain_class(h.get("rain_mm"))
        )))

    info = []
    if spot.get("viento_optimo"):
        info.append("Viento óptimo: " + ", ".join(spot["viento_optimo"]))
    if spot.get("swell_optimo"):
        info.append("Swell óptimo: " + ", ".join(spot["swell_optimo"]))
    if spot.get("valoracion"):
        info.append("Valoración: " + ("★" * int(spot["valoracion"])))
    info_str = " · ".join(info)

    wg_url = spot.get("windguru_url") or f"https://www.windguru.cz/{sid}"

    return (
        f'<div class="spot-detail">'
        f'  <div class="head">'
        f'    <h3>{esc(spot.get("name") or item.get("slug"))} '
        f'      <span class="sub">({esc(spot.get("region") or "")})</span></h3>'
        f'    <div class="sub">{esc(info_str)} · '
        f'      <a href="{esc(wg_url)}" target="_blank" rel="noopener">ver en Windguru ↗</a></div>'
        f'  </div>'
        f'  <div class="table-wrap"><table class="wg">'
        f'    <thead><tr><th class="row-label"></th>{"".join(head_day_cells)}</tr>'
        f'           <tr><th class="row-label">Hora local</th>{"".join(head_hour_cells)}</tr></thead>'
        f'    <tbody>{"".join(rows_html)}</tbody>'
        f'  </table></div>'
        f'</div>'
    )


def render_html(scored: dict, normalized: dict, spots_by_slug: dict,
                detail_spots: list[dict], generated_at: str, days_count: int) -> str:
    stations_used = scored.get("source", {}).get("stations_used") or []
    initdate = scored.get("source", {}).get("initdate_utc") or ""
    model = scored.get("source", {}).get("model") or ""

    overview = render_overview(scored, spots_by_slug)

    detail_blocks = "".join(render_detail_table(it, normalized, max_days=days_count)
                             for it in detail_spots)

    if not detail_spots:
        detail_blocks = (
            '<div class="spot-detail"><div class="head">'
            '<h3>Sin spots con condiciones aceptables en los próximos días</h3>'
            '<div class="sub">El forecast no encontró ningún spot con score ≥ 50 y altura de ola > 0.3 m.</div>'
            '</div></div>'
        )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>🌊 Surf Uruguay — Forecast</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>🌊 Surf Forecast — Uruguay</h1>
  <div class="meta">
    Generado: {esc(generated_at)} · Modelo: {esc(model)} · Init: {esc(initdate)} UTC ·
    Estaciones: {esc(", ".join(stations_used))} · Fuente: Windguru
  </div>
</header>
<main>
  <section>
    <h2>🏄 Mejores spots para los próximos días</h2>
    {overview}
  </section>
  <section>
    <h2>📊 Detalle horario por spot (próximos {days_count} días)</h2>
    {detail_blocks}
  </section>
</main>
<footer>
  Datos: <a href="https://www.windguru.cz" target="_blank">windguru.cz</a> ·
  Scoring custom: viento (40), dirección de swell (30), tamaño de ola (30) ·
  ✅ ≥70 bueno · ⚠️ 50–69 moderado · ❌ &lt;50 no recomendado
</footer>
</body>
</html>"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scored", help="Path to scored.json (from score_spots.py)")
    ap.add_argument("normalized", help="Path to normalized.json (from parse_windguru.py, with --merge)")
    ap.add_argument("spots", help="Path to spots.json")
    ap.add_argument("--out", help="Write HTML to this file (default: stdout)")
    ap.add_argument("--max-detail", type=int, default=3,
                    help="Max number of detailed spot tables (default: 3)")
    ap.add_argument("--days", type=int, default=7,
                    help="Days of hourly forecast to include in each detail table (default: 7)")
    args = ap.parse_args(argv)

    with open(args.scored) as f:
        scored = json.load(f)
    with open(args.normalized) as f:
        normalized = json.load(f)
    with open(args.spots) as f:
        spots = json.load(f)

    spots_by_slug = {s["slug"]: s for s in spots if s.get("slug")}

    detail_spots = pick_detail_spots(scored, spots_by_slug, max_n=args.max_detail)

    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip()
    html_out = render_html(scored, normalized, spots_by_slug,
                            detail_spots, generated_at, args.days)

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
