#!/usr/bin/env python3
"""Unificirani read-only pogled na portfelj (T212 + kripto + ZSE).

HTTP ne piše u portfolio.db, rules.yaml, .env ni view_history.db.
Ne pokreće portfolio.py. Kripto i ZSE čita iz /opt/zarko/state/*.json
(Hermes piše, zarko čita) — nikad iz ~/.hermes/state.

Agregatnu povijest (view_history.db) piše samo CLI:

    python3 view.py --snapshot          # cron, nikad GET handler

Ostalo:

    python3 view.py --json              # strojno čitljiv PortfolioView
    python3 view.py serve               # HTML na 127.0.0.1:8787
    python3 view.py serve --port 8787 --bind 127.0.0.1
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import crypto_adapter
import rules
import t212_adapter
import view_history
import zse_adapter
from position import (
    Allocation,
    PortfolioView,
    Position,
    SourceMeta,
    SourceResult,
    ViewGreska,
)
from rules import PravilaGreska, RULES_PATH

import db

DEFAULT_STATE = Path(__file__).parent / "state"
DEFAULT_HISTORY = view_history.DEFAULT_DB
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8787
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
CASH_CATEGORY = "cash"

# Jedna boja po kategoriji — samo pie i legenda, ne duga po tickeru.
CATEGORY_COLORS = {
    "siroki_etf": "#1e4d7b",
    "dionica": "#1d6b4f",
    "sektorski_etf": "#a16207",
    "roba": "#9a3412",
    "kripto": "#5b21b6",
    "cash": "#64748b",
}

# Ljudski nazivi u UI; filter i dalje ide slugom (?category=siroki_etf).
CATEGORY_LABEL = {
    "siroki_etf": "Široki ETF",
    "dionica": "Dionica",
    "sektorski_etf": "Sektorski ETF",
    "roba": "Roba",
    "kripto": "Kripto",
    "cash": "Cash",
}

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "pico.min.css": "text/css; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
}

# ── Agregacija ───────────────────────────────────────────────────────────────

def assemble(db_path: str | Path, state_dir: str | Path,
             pravila: dict) -> PortfolioView:
    """Pokreni adaptere, spoji, izračunaj težine. On-demand, bez cachea."""
    state_dir = Path(state_dir)
    results = [
        t212_adapter.load(db_path),
        crypto_adapter.load(state_dir / crypto_adapter.DEFAULT_FILENAME),
        zse_adapter.load(state_dir / zse_adapter.DEFAULT_FILENAME),
    ]
    return merge(results, pravila)


def merge(results: list[SourceResult], pravila: dict) -> PortfolioView:
    klasifikacija = pravila["klasifikacija"]
    raw = []
    cash_eur = 0.0
    sources: list[SourceMeta] = []

    for r in results:
        cash_eur += r.cash_eur
        sources.append(SourceMeta(
            source=r.source,
            freshness=r.freshness,
            as_of=r.as_of,
            available=r.available,
            n_positions=len(r.positions),
            value_eur=round(sum(p.value_eur for p in r.positions), 4),
            cash_eur=r.cash_eur,
            error=r.error,
        ))
        raw.extend(r.positions)

    if not any(s.available for s in sources) and not raw:
        raise ViewGreska(
            "Nema nijednog izvora. Treba portfolio.db ili "
            "state/crypto.json / state/zse.json."
        )

    nesvrstani = sorted({p.ticker for p in raw if p.ticker not in klasifikacija})
    if nesvrstani:
        raise ViewGreska(
            f"Nesvrstani tickeri: {', '.join(nesvrstani)}. "
            f"Dodaj ih u 'klasifikacija:' u rules.yaml — bez toga se ne zna "
            f"kategorija ni udio."
        )

    positions_value = sum(p.value_eur for p in raw)
    total = positions_value + cash_eur

    positions: list[Position] = []
    for p in raw:
        pnl = p.pnl_eur
        if pnl is None and p.cost_eur is not None:
            pnl = p.value_eur - p.cost_eur
        pnl_pct = None
        if p.cost_eur not in (None, 0) and pnl is not None:
            pnl_pct = round(100.0 * pnl / p.cost_eur, 2)
        weight = round(100.0 * p.value_eur / total, 2) if total else 0.0
        positions.append(Position(
            ticker=p.ticker,
            name=p.name,
            category=klasifikacija[p.ticker],
            source=p.source,
            currency=p.currency,
            quantity=p.quantity,
            value_eur=p.value_eur,
            cost_eur=p.cost_eur,
            pnl_eur=pnl,
            pnl_pct=pnl_pct,
            weight_pct_of_total=weight,
            as_of=p.as_of,
            freshness=p.freshness,
        ))
    positions.sort(key=lambda p: p.value_eur, reverse=True)

    costs = [p.cost_eur for p in positions if p.cost_eur is not None]
    pnls = [p.pnl_eur for p in positions if p.pnl_eur is not None]
    total_cost = sum(costs) if costs else None
    total_pnl = sum(pnls) if pnls else None

    as_of = max((s.as_of for s in sources if s.as_of), default=None)
    allocation = _allocation(positions, cash_eur, total, pravila)

    return PortfolioView(
        as_of=as_of,
        total_value_eur=total,
        positions_value_eur=positions_value,
        cash_eur=cash_eur,
        total_cost_eur=total_cost,
        total_pnl_eur=total_pnl,
        sources=sources,
        positions=positions,
        allocation=allocation,
    )


def _allocation(positions: list[Position], cash_eur: float, total: float,
                pravila: dict) -> list[Allocation]:
    zbroj: dict[str, float] = {}
    for p in positions:
        zbroj[p.category] = zbroj.get(p.category, 0.0) + p.value_eur
    if cash_eur:
        zbroj[CASH_CATEGORY] = zbroj.get(CASH_CATEGORY, 0.0) + cash_eur

    order = list(pravila.get("kategorije") or {})
    if CASH_CATEGORY not in order:
        order.append(CASH_CATEGORY)
    seen = set()
    out: list[Allocation] = []
    for kat in order + [k for k in zbroj if k not in order]:
        if kat in seen or kat not in zbroj:
            continue
        seen.add(kat)
        eur = zbroj[kat]
        pct = round(100.0 * eur / total, 2) if total else 0.0
        out.append(Allocation(category=kat, value_eur=eur, weight_pct=pct))
    return out


def filter_positions(view: PortfolioView, category: str | None = None,
                     source: str | None = None,
                     currency: str | None = None) -> list[Position]:
    """Filteri diraju samo tablicu. Težine i ukupni zbrojevi ostaju na cijelom portfelju."""
    rows = view.positions
    if category:
        rows = [p for p in rows if p.category == category]
    if source:
        rows = [p for p in rows if p.source == source]
    if currency:
        rows = [p for p in rows if p.currency == currency]
    return rows


# ── Formatiranje ─────────────────────────────────────────────────────────────

def _hr(formatted: str) -> str:
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _eur(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:,.2f}')} EUR"


def _signed(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:+,.2f}')} EUR"


def _pct(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:.1f}')} %"


def _fmt_at(iso: str | None) -> str:
    if not iso:
        return "n/d"
    try:
        dt = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%d.%m. %H:%M") + " UTC"
    except ValueError:
        return iso


def _qty(v: float | None) -> str:
    if v is None:
        return "n/d"
    if abs(v) >= 100 or v == int(v):
        return _hr(f"{v:,.4f}").rstrip("0").rstrip(",")
    return _hr(f"{v:.8f}").rstrip("0").rstrip(",")


SOURCE_LABEL = {"t212": "T212", "crypto": "Kripto", "zse": "ZSE"}
FRESHNESS_LABEL = {"snapshot": "snapshot", "live": "live"}
FIRST_POINT_NOTE = "prva točka — linija nakon sljedećeg snapshot-a"


def _category_label(category: str) -> str:
    return CATEGORY_LABEL.get(category, category)

ChartHistory = view_history.ChartHistory
load_chart_history = view_history.load_chart_history


def take_snapshot(db_path: str | Path, state_dir: str | Path, pravila: dict,
                  history_path: str | Path,
                  taken_at: str | None = None) -> dict:
    """Spoji trenutne izvore i spremi red u view_history.db. Ne dira portfolio.db."""
    assembled = assemble(db_path, state_dir, pravila)
    return view_history.save_from_view(history_path, assembled, taken_at=taken_at)


# ── SVG ──────────────────────────────────────────────────────────────────────

def _cat_class(category: str) -> str:
    return f"cat-{category}" if category in CATEGORY_COLORS else "cat-ostalo"


def _filter_href(category: str | None = None, source: str | None = None,
                 currency: str | None = None) -> str:
    q = []
    if category:
        q.append(("category", category))
    if source:
        q.append(("source", source))
    if currency:
        q.append(("currency", currency))
    return ("?" + urlencode(q)) if q else "?"


def _pie_path(cx: float, cy: float, r: float, a0: float, a1: float) -> str:
    sweep = a1 - a0
    if sweep <= 1e-12:
        return ""
    if sweep >= 2 * math.pi - 1e-9:
        return (
            f"M {cx:.2f} {cy:.2f} L {cx + r:.2f} {cy:.2f} "
            f"A {r:.2f} {r:.2f} 0 1 1 {cx - r:.2f} {cy:.2f} "
            f"A {r:.2f} {r:.2f} 0 1 1 {cx + r:.2f} {cy:.2f} Z"
        )
    x0 = cx + r * math.cos(a0)
    y0 = cy + r * math.sin(a0)
    x1 = cx + r * math.cos(a1)
    y1 = cy + r * math.sin(a1)
    large = 1 if sweep > math.pi else 0
    return (
        f"M {cx:.2f} {cy:.2f} L {x0:.2f} {y0:.2f} "
        f"A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f} Z"
    )


def render_pie_svg(allocation: list[Allocation],
                   active_category: str | None = None,
                   source: str | None = None,
                   currency: str | None = None) -> str:
    e = html.escape
    total = sum(a.value_eur for a in allocation)
    if total <= 0:
        return ""
    cx, cy, r = 120.0, 120.0, 104.0
    parts = [
        '<svg class="pie" viewBox="0 0 240 240" role="img" '
        'aria-label="Alokacija po kategoriji">',
    ]
    angle = -math.pi / 2
    for a in allocation:
        sweep = 2 * math.pi * (a.value_eur / total)
        a0, a1 = angle, angle + sweep
        angle = a1
        d = _pie_path(cx, cy, r, a0, a1)
        if not d:
            continue
        aktivno = active_category == a.category
        href = e(_filter_href(
            None if aktivno else a.category, source, currency,
        ), quote=True)
        cls = f"slice {_cat_class(a.category)}"
        transform = ""
        if aktivno:
            cls += " active"
            mid = (a0 + a1) / 2
            ox = 6 * math.cos(mid)
            oy = 6 * math.sin(mid)
            transform = f' transform="translate({ox:.2f} {oy:.2f})"'
        label = e(f"{_category_label(a.category)} {_pct(a.weight_pct)} · {_eur(a.value_eur)}")
        parts.append(
            f'<a href="{href}" data-category="{e(a.category)}">'
            f'<path class="{cls}" d="{d}"{transform}>'
            f"<title>{label}</title></path></a>"
        )
    parts.append("</svg>")
    return "".join(parts)


def render_pie_legend(allocation: list[Allocation],
                      active_category: str | None = None,
                      source: str | None = None,
                      currency: str | None = None) -> str:
    e = html.escape
    rows = []
    for a in allocation:
        aktivno = active_category == a.category
        href = e(_filter_href(
            None if aktivno else a.category, source, currency,
        ), quote=True)
        cls = "legend-row" + (" active" if aktivno else "")
        rows.append(
            f'<a class="{cls}" href="{href}" data-category="{e(a.category)}">'
            f'<span class="swatch {_cat_class(a.category)}"></span>'
            f'<span class="legend-name">{e(_category_label(a.category))}</span>'
            f'<span class="legend-pct">{e(_pct(a.weight_pct))}</span>'
            f'<span class="legend-eur">{e(_eur(a.value_eur))}</span>'
            f"</a>"
        )
    return '<div class="legend">' + "".join(rows) + "</div>"


def _parse_at(iso: str) -> datetime:
    return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")


def _pick_times(times: list[datetime], max_n: int = 4) -> list[datetime]:
    if len(times) <= max_n:
        return times
    n = len(times) - 1
    idxs = [round(i * n / (max_n - 1)) for i in range(max_n)]
    return [times[i] for i in idxs]


def _time_span(history: ChartHistory) -> tuple[datetime, datetime] | None:
    times = []
    for pts in (history.ukupno, history.t212, history.crypto, history.zse):
        times.extend(_parse_at(t) for t, _ in pts)
    if not times:
        return None
    return min(times), max(times)


def _value_span(vals: list[float]) -> tuple[float, float]:
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        pad = abs(vmin) * 0.05 or 1.0
        return vmin - pad, vmax + pad
    pad = (vmax - vmin) * 0.08
    return vmin - pad, vmax + pad


def _line_panel(points: list[tuple[str, float]], *, cls: str,
                t0: datetime, t1: datetime, width: int, height: int,
                stroke_width: float, y_ticks: bool, x_labels: bool) -> str:
    e = html.escape
    left = 52 if y_ticks else 8
    right, top = 10, 10
    bottom = 26 if x_labels else 10
    inner_w = width - left - right
    inner_h = height - top - bottom
    vmin, vmax = _value_span([v for _, v in points])
    tspan = (t1 - t0).total_seconds() or 1.0
    vspan = vmax - vmin or 1.0

    def xy(iso: str, val: float) -> tuple[float, float]:
        t = _parse_at(iso)
        x = left + ((t - t0).total_seconds() / tspan) * inner_w
        y = top + (1.0 - (val - vmin) / vspan) * inner_h
        return x, y

    parts = [
        f'<svg class="line-chart {e(cls)}" viewBox="0 0 {width} {height}" '
        f'role="img">'
    ]
    if y_ticks:
        for i in range(3):
            frac = i / 2
            y = top + (1 - frac) * inner_h
            val = vmin + frac * vspan
            parts.append(
                f'<line class="grid" x1="{left}" y1="{y:.1f}" '
                f'x2="{width - right}" y2="{y:.1f}"/>'
            )
            parts.append(
                f'<text class="axis" x="{left - 6}" y="{y:.1f}" text-anchor="end" '
                f'dominant-baseline="middle">{e(_hr(f"{val:,.0f}"))}</text>'
            )
    if x_labels:
        times = sorted({_parse_at(t) for t, _ in points})
        if len(times) == 1:
            times = [t0, t1] if t0 != t1 else times
        for t in _pick_times(times if times else [t0, t1]):
            x = left + ((t - t0).total_seconds() / tspan) * inner_w
            parts.append(
                f'<text class="axis" x="{x:.1f}" y="{height - 8}" '
                f'text-anchor="middle">{e(t.strftime("%d.%m."))}</text>'
            )
    coords = [xy(t, v) for t, v in points]
    if len(coords) == 1:
        x, y = coords[0]
        rr = 3.4 if stroke_width >= 2 else 2.4
        parts.append(
            f'<circle class="{e(cls)}" cx="{x:.1f}" cy="{y:.1f}" r="{rr}"/>'
        )
    else:
        pl = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        parts.append(
            f'<polyline class="{e(cls)}" fill="none" stroke-width="{stroke_width}" '
            f'stroke-linejoin="round" stroke-linecap="round" points="{pl}"/>'
        )
        rr = 3.0 if stroke_width >= 2 else 2.2
        x, y = coords[-1]
        parts.append(
            f'<circle class="{e(cls)}" cx="{x:.1f}" cy="{y:.1f}" r="{rr}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _line_head(label: str, points: list[tuple[str, float]]) -> str:
    e = html.escape
    last = _eur(points[-1][1]) if points else "n/d"
    return (
        f'<div class="line-head"><span>{e(label)}</span>'
        f'<span class="num">{e(last)}</span></div>'
    )


def _series_block(label: str, points: list[tuple[str, float]], *, cls: str,
                  t0: datetime, t1: datetime, width: int, height: int,
                  stroke_width: float, y_ticks: bool, x_labels: bool) -> str:
    e = html.escape
    head = _line_head(label, points)
    if len(points) < 2:
        return (
            f'<div class="line-card {e(cls)}">'
            f"{head}"
            f'<p class="muted first-point">{e(FIRST_POINT_NOTE)}</p>'
            "</div>"
        )
    chart = _line_panel(
        points, cls=cls, t0=t0, t1=t1, width=width, height=height,
        stroke_width=stroke_width, y_ticks=y_ticks, x_labels=x_labels,
    )
    return f'<div class="line-panel">{head}{chart}</div>'


def render_line_svg(history: ChartHistory) -> str:
    span = _time_span(history)
    if span is None:
        return ""
    t0, t1 = span

    parts = ['<div class="line-stack">']
    if history.ukupno:
        parts.append('<div class="line-main">')
        parts.append(
            _series_block(
                "Ukupno", history.ukupno, cls="series-ukupno",
                t0=t0, t1=t1, width=640, height=150, stroke_width=2.6,
                y_ticks=True, x_labels=True,
            )
        )
        parts.append("</div>")
    sparks = []
    for key, label, pts in (
        ("t212", "T212", history.t212),
        ("crypto", "Kripto", history.crypto),
        ("zse", "ZSE", history.zse),
    ):
        if not pts:
            continue
        sparks.append(
            _series_block(
                label, pts, cls=f"series-{key}",
                t0=t0, t1=t1, width=640, height=72, stroke_width=1.35,
                y_ticks=False, x_labels=False,
            )
        )
    if sparks:
        parts.append('<div class="sources-grid">')
        parts.extend(sparks)
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


# ── HTML ─────────────────────────────────────────────────────────────────────

def render_html(view: PortfolioView, category: str | None = None,
                source: str | None = None, currency: str | None = None,
                error: str | None = None,
                history: ChartHistory | None = None) -> str:
    e = html.escape
    rows = filter_positions(view, category, source, currency) if view else []
    categories = sorted({p.category for p in view.positions}) if view else []
    sources = sorted({p.source for p in view.positions}) if view else []
    currencies = sorted({p.currency for p in view.positions if p.currency}) if view else []

    def _select(name, current, values, labels=None, title=None):
        opts = ['<option value="">sve</option>']
        for v in values:
            label = (labels or {}).get(v, v)
            sel = " selected" if current == v else ""
            opts.append(f'<option value="{e(v)}"{sel}>{e(label)}</option>')
        return (f'<label>{e(title or name)} <select name="{e(name)}">'
                + "".join(opts) + "</select></label>")

    options = [
        _select("category", category, categories, CATEGORY_LABEL, "Kategorija"),
        _select("source", source, sources, SOURCE_LABEL, "Izvor"),
        _select("currency", currency, currencies, title="Valuta"),
    ]

    badges = []
    if view:
        for s in view.sources:
            label = SOURCE_LABEL.get(s.source, s.source)
            if not s.available:
                badges.append(
                    f'<span class="badge missing">{e(label)} · {e(s.error or "nema podataka")}</span>'
                )
            else:
                fr = FRESHNESS_LABEL.get(s.freshness or "", s.freshness or "")
                badges.append(
                    f'<span class="badge {e(s.freshness or "")}">'
                    f'{e(label)} · {e(fr)} · {e(_fmt_at(s.as_of))}</span>'
                )

    alloc = view.allocation if view else []
    pie = render_pie_svg(alloc, category, source, currency) if alloc else ""
    legend = render_pie_legend(alloc, category, source, currency) if alloc else ""
    alloc_block = ""
    if pie or legend:
        alloc_block = (
            '<article class="alloc"><h2>Alokacija</h2>'
            f'<div class="alloc-body"><div class="pie-wrap">{pie}</div>{legend}</div>'
            '<p class="muted chart-caption">Težine su udio u cijelom portfelju. '
            "Klik na komad ili legendu filtrira tablicu.</p></article>"
        )

    hist = history or ChartHistory()
    line = render_line_svg(hist)
    if line:
        if hist.ukupno_je_samo_t212:
            caption = (
                "Ukupno = samo T212 (još nema agregiranog snapshota). "
                "Cijeli portfelj: python3 view.py --snapshot."
            )
        else:
            caption = "Ukupno = T212 + kripto + ZSE."
        line_block = (
            '<article class="history"><h2>Vrijednost</h2>'
            f'<div class="line-wrap">{line}</div>'
            f'<p class="muted chart-caption">{e(caption)}</p></article>'
        )
    else:
        line_block = (
            '<article class="history"><h2>Vrijednost</h2>'
            '<p class="muted chart-caption">Nema povijesti. T212 cron i '
            "python3 view.py --snapshot pune ovaj graf.</p></article>"
        )

    body_rows = []
    for p in rows:
        pnl_cls = "neg" if (p.pnl_eur or 0) < 0 else ("pos" if (p.pnl_eur or 0) > 0 else "")
        body_rows.append(
            "<tr>"
            f"<td class=\"num\">{e(_pct(p.weight_pct_of_total))}</td>"
            f"<td><code>{e(p.ticker)}</code><div class=\"muted\">{e(p.name)}</div></td>"
            f"<td>{e(_category_label(p.category))}</td>"
            f"<td>{e(SOURCE_LABEL.get(p.source, p.source))}</td>"
            f"<td>{e(p.currency)}</td>"
            f"<td class=\"num\">{e(_qty(p.quantity))}</td>"
            f"<td class=\"num\">{e(_eur(p.value_eur))}</td>"
            f"<td class=\"num\">{e(_eur(p.cost_eur))}</td>"
            f"<td class=\"num {pnl_cls}\">{e(_signed(p.pnl_eur))}</td>"
            f"<td class=\"num {pnl_cls}\">{e(_pct(p.pnl_pct))}</td>"
            "</tr>"
        )
    if not body_rows:
        body_rows.append('<tr><td colspan="10" class="muted">Nema pozicija za ovaj filter.</td></tr>')

    err_block = f'<p class="error">{e(error)}</p>' if error else ""
    totals = ""
    if view:
        totals = f"""
<article class="metrics-card">
  <div class="metrics">
    <div class="metric metric-total"><div class="k">Ukupno</div><div class="v">{e(_eur(view.total_value_eur))}</div></div>
    <div class="metric"><div class="k">Pozicije</div><div class="v">{e(_eur(view.positions_value_eur))}</div></div>
    <div class="metric"><div class="k">Cash</div><div class="v">{e(_eur(view.cash_eur))}</div></div>
    <div class="metric"><div class="k">Nerealizirano</div><div class="v">{e(_signed(view.total_pnl_eur))}</div></div>
  </div>
</article>"""

    return f"""<!DOCTYPE html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfelj</title>
<link rel="stylesheet" href="/static/pico.min.css">
<link rel="stylesheet" href="/static/dashboard.css">
</head>
<body>
<main class="container">
<header class="dash-header">
  <hgroup>
    <h1>Portfelj</h1>
    <p>Read-only · težine su udio u cijelom portfelju (uključujući cash)</p>
  </hgroup>
  {err_block}
  <div class="badges">{"".join(badges)}</div>
</header>
{totals}
{alloc_block}
{line_block}
<article class="holdings">
  <h2>Pozicije</h2>
  <form class="filters" method="get">
    {"".join(options)}
    <button type="submit">Filtriraj</button>
  </form>
  <div class="table-wrap">
  <table class="striped">
  <thead><tr>
    <th>%</th><th>Pozicija</th><th>Kategorija</th><th>Izvor</th><th>Valuta</th>
    <th class="num">Količina</th><th class="num">Vrijednost</th><th class="num">Trošak</th>
    <th class="num">P&amp;L</th><th class="num">P&amp;L %</th>
  </tr></thead>
  <tbody>{"".join(body_rows)}</tbody>
  </table>
  </div>
</article>
</main>
</body>
</html>
"""


# ── HTTP ─────────────────────────────────────────────────────────────────────

def static_response(url_path: str) -> tuple[int, bytes, str] | None:
    """Serviraj samo allowlist iz static/. None = nije /static/ zahtjev."""
    if not url_path.startswith("/static/"):
        return None
    name = url_path[len("/static/"):]
    if name not in STATIC_FILES:
        return 404, b"nema", "text/plain; charset=utf-8"
    target = (STATIC_DIR / name).resolve()
    if target.parent != STATIC_DIR.resolve() or not target.is_file():
        return 404, b"nema", "text/plain; charset=utf-8"
    return 200, target.read_bytes(), STATIC_FILES[name]


def make_handler(db_path: Path, state_dir: Path, rules_path: Path,
                 history_path: Path | None = None):
    history_path = Path(history_path) if history_path else DEFAULT_HISTORY

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            # Read-only: nikad --snapshot, nikad pisanje u *.db / rules.yaml / .env.
            parsed = urlparse(self.path)
            static = static_response(parsed.path)
            if static is not None:
                code, body, ctype = static
                self._send(code, body, ctype)
                return
            if parsed.path not in ("/", "/json", "/health"):
                self._send(404, b"nema", "text/plain; charset=utf-8")
                return
            if parsed.path == "/health":
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
                return

            qs = parse_qs(parsed.query)
            category = (qs.get("category") or [None])[0] or None
            source = (qs.get("source") or [None])[0] or None
            currency = (qs.get("currency") or [None])[0] or None
            history = load_chart_history(db_path, history_path)

            try:
                pravila = rules.ucitaj_pravila(rules_path)
                view = assemble(db_path, state_dir, pravila)
            except (ViewGreska, PravilaGreska) as err:
                if parsed.path == "/json":
                    payload = json.dumps({"error": str(err)}, ensure_ascii=False).encode()
                    self._send(500, payload, "application/json; charset=utf-8")
                    return
                page = render_html(
                    PortfolioView(
                        as_of=None, total_value_eur=0, positions_value_eur=0,
                        cash_eur=0, total_cost_eur=None, total_pnl_eur=None,
                        sources=[], positions=[],
                    ),
                    error=str(err),
                    history=history,
                )
                self._send(500, page.encode("utf-8"), "text/html; charset=utf-8")
                return

            if parsed.path == "/json":
                payload = json.dumps(view.to_dict(), ensure_ascii=False, indent=2).encode()
                self._send(200, payload, "application/json; charset=utf-8")
                return

            page = render_html(
                view, category=category, source=source, currency=currency,
                history=history,
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            self._send(405, b"read-only\n", "text/plain; charset=utf-8")

    return Handler


def public_bind_warning(bind: str) -> str | None:
    if bind in LOOPBACK:
        return None
    return (
        f"UPOZORENJE: bind={bind} nije localhost. Hetzner firewall mora "
        f"ostati samo SSH; javni pristup ide kroz Cloudflare Tunnel ili "
        f"Tailscale, ne kroz 0.0.0.0."
    )


def serve(bind: str, port: int, db_path: Path, state_dir: Path,
          rules_path: Path, history_path: Path | None = None) -> None:
    warning = public_bind_warning(bind)
    if warning:
        print(warning, file=sys.stderr)
    httpd = ThreadingHTTPServer(
        (bind, port),
        make_handler(db_path, state_dir, rules_path, history_path),
    )
    print(f"Dashboard na http://{bind}:{port}  (Ctrl-C za prekid)", file=sys.stderr)
    print(f"JSON: http://{bind}:{port}/json", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nzaustavljeno", file=sys.stderr)
        httpd.server_close()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", default="json", choices=["json", "serve"])
    p.add_argument("--json", action="store_true",
                   help="isto kao naredba json (zadano)")
    p.add_argument("--db", default=str(db.DEFAULT_DB))
    p.add_argument("--state", default=str(DEFAULT_STATE),
                   help="mapa s crypto.json i zse.json")
    p.add_argument("--rules", default=str(RULES_PATH))
    p.add_argument("--bind", default=DEFAULT_BIND,
                   help="adresa na koju sluša serve (zadano 127.0.0.1)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--snapshot", action="store_true",
                   help="spremi agregat u view_history.db (cron; HTTP ovo ne radi)")
    p.add_argument("--history", default=str(DEFAULT_HISTORY),
                   help="putanja view_history.db")
    a = p.parse_args()

    if a.snapshot:
        if a.command == "serve":
            p.error("serve i --snapshot ne idu skupa")
        try:
            pravila = rules.ucitaj_pravila(a.rules)
            row = take_snapshot(Path(a.db), Path(a.state), pravila, Path(a.history))
        except (ViewGreska, PravilaGreska) as err:
            sys.exit(str(err))
        def iznos(v):
            return "n/d" if v is None else f"{v:.2f}"

        print(
            f"View snapshot {row['taken_at']}: ukupno {iznos(row['total_value_eur'])} EUR "
            f"(T212 {iznos(row['t212_eur'])}, kripto {iznos(row['crypto_eur'])}, "
            f"ZSE {iznos(row['zse_eur'])})",
            file=sys.stderr,
        )
        return

    command = "json" if a.json else a.command

    if command == "serve":
        serve(a.bind, a.port, Path(a.db), Path(a.state), Path(a.rules),
              Path(a.history))
        return

    try:
        pravila = rules.ucitaj_pravila(a.rules)
        view = assemble(a.db, a.state, pravila)
    except (ViewGreska, PravilaGreska) as err:
        sys.exit(str(err))

    json.dump(view.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
