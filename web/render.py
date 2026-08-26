"""HTML, SVG i formatiranje za dashboard.

view.py slaže PortfolioView; ovaj sloj ga crta. HTTP je u web.server.
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone
from urllib.parse import urlencode

import view_history
from position import Allocation, PortfolioView

ChartHistory = view_history.ChartHistory

# Jedna boja po kategoriji — samo pie i legenda, ne duga po tickeru.
CATEGORY_COLORS = {
    "siroki_etf": "#3A75CD",
    "dionica": "#159E75",
    "sektorski_etf": "#D89513",
    "roba": "#D9613C",
    "kripto": "#856AE0",
    "cash": "#A2ABB8",
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
                stroke_width: float, y_ticks: bool, x_labels: bool,
                grid: bool = False) -> str:
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

    # Bez oznaka os rastežemo po širini (preserveAspectRatio=none) pa poteze
    # držimo na zadanoj debljini kroz vector-effect — inače se linija udeblja.
    stretch = not (y_ticks or x_labels)
    par = ' preserveAspectRatio="none"' if stretch else ""
    ve = ' vector-effect="non-scaling-stroke"' if stretch else ""
    parts = [
        f'<svg class="line-chart {e(cls)}" viewBox="0 0 {width} {height}"'
        f'{par} role="img">'
    ]
    if y_ticks or grid:
        for i in range(3):
            frac = i / 2
            y = top + (1 - frac) * inner_h
            val = vmin + frac * vspan
            parts.append(
                f'<line class="grid" x1="{left}" y1="{y:.1f}" '
                f'x2="{width - right}" y2="{y:.1f}"{ve}/>'
            )
            if not y_ticks:
                continue
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
    # Točka je putanja nulte duljine s okruglim krajem: ostaje okrugla i kad je
    # SVG rastegnut po širini.
    dot_w = 5.0 if stroke_width >= 1.6 else 3.5

    def _dot(x: float, y: float) -> str:
        return (
            f'<path class="{e(cls)} dot" d="M {x:.1f} {y:.1f} L {x:.1f} {y:.1f}" '
            f'fill="none" stroke-width="{dot_w}" stroke-linecap="round"{ve}/>'
        )

    if len(coords) == 1:
        parts.append(_dot(*coords[0]))
    else:
        pl = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        parts.append(
            f'<polyline class="{e(cls)}" fill="none" stroke-width="{stroke_width}" '
            f'stroke-linejoin="round" stroke-linecap="round"{ve} points="{pl}"/>'
        )
        parts.append(_dot(*coords[-1]))
    parts.append("</svg>")
    return "".join(parts)


# ── Formatiranje za dashboard ────────────────────────────────────────────────

STALE_DAYS = 3


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Hrvatska množina: 1 dan, 2-4 dana, 5+ dana (11-14 uvijek 'many')."""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


def _num(v: float | None) -> str:
    return "n/d" if v is None else _hr(f"{v:,.2f}")


def _signed_num(v: float | None) -> str:
    return "n/d" if v is None else _hr(f"{v:+,.2f}")


def _signed_pct(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:+.1f}')} %"


def _tone(v: float | None) -> str:
    if v is None or v == 0:
        return ""
    return "pos" if v > 0 else "neg"


def _stale_days(iso: str | None) -> int | None:
    """Koliko je izvor star u danima, ako je preko praga. Inače None."""
    if not iso:
        return None
    try:
        dt = _parse_at(iso)
    except ValueError:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    days = (now - dt).days
    return days if days >= STALE_DAYS else None


def _days(points: list[tuple[str, float]]) -> int:
    if len(points) < 2:
        return 0
    try:
        return (_parse_at(points[-1][0]) - _parse_at(points[0][0])).days
    except ValueError:
        return 0


def _span_label(points: list[tuple[str, float]]) -> str:
    d = _days(points)
    return f"{d} {_plural(d, 'dan', 'dana', 'dana')}" if d else ""


def _line_head(label: str, points: list[tuple[str, float]]) -> str:
    """Glava kartice izvora: naziv iznad zadnje vrijednosti."""
    e = html.escape
    last = _num(points[-1][1]) if points else "n/d"
    return (
        f'<div class="line-head"><span class="line-label">{e(label)}</span>'
        f'<span class="line-value num">{e(last)}</span></div>'
    )


def _main_head(points: list[tuple[str, float]]) -> str:
    """Glava glavnog grafa: trenutna vrijednost + promjena u rasponu."""
    e = html.escape
    last = _num(points[-1][1]) if points else "n/d"
    delta = ""
    if len(points) >= 2:
        d = points[-1][1] - points[0][1]
        pct = (100.0 * d / points[0][1]) if points[0][1] else None
        raspon = _span_label(points)
        txt = _signed_num(d)
        if pct is not None:
            txt += f" ({_signed_pct(pct)})"
        if raspon:
            txt += f" u {raspon}"
        delta = f'<span class="delta {_tone(d)}">{e(txt)}</span>'
    return (
        f'<div class="line-head main"><span class="line-total num">{e(last)}</span>'
        f"{delta}</div>"
    )


def _x_axis(points: list[tuple[str, float]], t0: datetime, t1: datetime) -> str:
    e = html.escape
    times = sorted({_parse_at(t) for t, _ in points})
    if len(times) < 2:
        times = [t0, t1] if t0 != t1 else times
    if len(times) < 2:
        return ""
    labels = _pick_times(times, 4)
    spans = "".join(f"<span>{e(t.strftime('%d.%m.'))}</span>" for t in labels)
    return f'<div class="x-axis">{spans}</div>'


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
    return f'<div class="line-panel {e(cls)}">{head}{chart}</div>'


def _main_block(points: list[tuple[str, float]], *, t0: datetime, t1: datetime) -> str:
    """Ukupno: velika brojka, linija, x-os. Jedna točka ostaje jedna točka."""
    e = html.escape
    head = _main_head(points)
    if len(points) < 2:
        return (
            f'<div class="line-card series-ukupno">{head}'
            f'<p class="muted first-point">{e(FIRST_POINT_NOTE)}</p></div>'
        )
    chart = _line_panel(
        points, cls="series-ukupno", t0=t0, t1=t1, width=640, height=150,
        stroke_width=1.75, y_ticks=False, x_labels=False, grid=True,
    )
    return (
        f'<div class="line-main">{head}'
        f'<div class="chart-wrap">{chart}</div>{_x_axis(points, t0, t1)}</div>'
    )


def render_line_svg(history: ChartHistory) -> str:
    span = _time_span(history)
    if span is None:
        return ""
    t0, t1 = span

    parts = ['<div class="line-stack">']
    if history.ukupno:
        parts.append(_main_block(history.ukupno, t0=t0, t1=t1))
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
                t0=t0, t1=t1, width=200, height=30, stroke_width=1.25,
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

def _metric(label: str, value: str, foot: str, *, cls: str = "",
            unit: str = "EUR", tone: str = "") -> str:
    e = html.escape
    unit_html = ""
    if unit and value != "n/d":
        unit_html = f' <span class="unit">{e(unit)}</span>'
    tone_cls = f" {tone}" if tone else ""
    cls_html = f" {cls}" if cls else ""
    return (
        f'<div class="metric{cls_html}">'
        f'<span class="k">{e(label)}</span>'
        f'<span class="v{tone_cls}">{e(value)}{unit_html}</span>'
        f'<span class="foot">{e(foot)}</span></div>'
    )


def _badges(view: PortfolioView) -> str:
    e = html.escape
    out = []
    for s in view.sources:
        label = SOURCE_LABEL.get(s.source, s.source)
        if not s.available:
            out.append(
                f'<span class="badge missing"><span class="dot"></span>'
                f'{e(label)} · {e(s.error or "izvor nedostaje")}</span>'
            )
            continue
        fr = FRESHNESS_LABEL.get(s.freshness or "", s.freshness or "")
        star = _stale_days(s.as_of)
        cls = "stale" if star else (s.freshness or "")
        tekst = f"{label} · {fr} · {_fmt_at(s.as_of)}"
        if star:
            tekst += f" · {star} {_plural(star, 'dan', 'dana', 'dana')} star"
        out.append(
            f'<span class="badge {e(cls)}"><span class="dot"></span>{e(tekst)}</span>'
        )
    return "".join(out)


def _metrics_block(view: PortfolioView) -> str:
    total = view.total_value_eur or 0.0
    izvori = " + ".join(
        SOURCE_LABEL.get(s.source, s.source) for s in view.sources if s.available
    )
    n = len(view.positions)
    k = len({p.category for p in view.positions})
    cash_pct = (100.0 * view.cash_eur / total) if total else None
    pnl_pct = None
    if view.total_cost_eur and view.total_pnl_eur is not None:
        pnl_pct = 100.0 * view.total_pnl_eur / view.total_cost_eur

    cells = [
        _metric("Ukupno", _num(view.total_value_eur),
                izvori or "nema dostupnog izvora", cls="metric-total"),
        _metric("Pozicije", _num(view.positions_value_eur),
                f"{n} {_plural(n, 'pozicija', 'pozicije', 'pozicija')} · "
                f"{k} {_plural(k, 'kategorija', 'kategorije', 'kategorija')}"),
        _metric("Cash", _num(view.cash_eur),
                f"{_pct(cash_pct)} portfelja" if cash_pct is not None
                else "udio nepoznat"),
        _metric("Nerealizirano", _signed_num(view.total_pnl_eur),
                f"{_signed_pct(pnl_pct)} na trošak" if pnl_pct is not None
                else "trošak nepoznat",
                tone=_tone(view.total_pnl_eur)),
    ]
    return f'<section class="panel metrics">{"".join(cells)}</section>'


def _panel_head(label: str, meta: str = "", right: str = "") -> str:
    e = html.escape
    meta_html = f'<span class="meta">{e(meta)}</span>' if meta else ""
    return (
        f'<div class="panel-head"><span class="label">{e(label)}</span>'
        f"{meta_html}{right}</div>"
    )


def _history_block(hist: ChartHistory) -> str:
    e = html.escape
    line = render_line_svg(hist)
    if not line:
        return (
            '<section class="panel history">'
            + _panel_head("Vrijednost", "EUR · dnevni snapshoti")
            + '<p class="foot-note">Nema povijesti. T212 cron i '
            "python3 view.py --snapshot pune ovaj graf.</p></section>"
        )
    if hist.ukupno_je_samo_t212:
        caption = (
            "Ukupno = samo T212 (još nema agregiranog snapshota). "
            "Cijeli portfelj: python3 view.py --snapshot."
        )
    else:
        caption = "Ukupno = T212 + kripto + ZSE."
    raspon = _span_label(hist.ukupno)
    pill = f'<span class="range-pill">{e(raspon)}</span>' if raspon else ""
    return (
        '<section class="panel history">'
        + _panel_head("Vrijednost", "EUR · dnevni snapshoti", pill)
        + line
        + f'<p class="foot-note">{e(caption)}</p></section>'
    )


def _alloc_block(view: PortfolioView, category: str | None,
                 source: str | None, currency: str | None) -> str:
    alloc = view.allocation if view else []
    if not alloc:
        return ""
    pie = render_pie_svg(alloc, category, source, currency)
    legend = render_pie_legend(alloc, category, source, currency)
    if not (pie or legend):
        return ""
    return (
        '<section class="panel alloc">'
        + _panel_head("Alokacija", "udio u portfelju")
        + '<div class="alloc-body">'
        f'<div class="pie-ring"><div class="pie-wrap">{pie}</div></div>'
        f"{legend}</div>"
        '<p class="foot-note">Težine su udio u cijelom portfelju. '
        "Klik na komad ili legendu filtrira tablicu.</p></section>"
    )


def render_html(view: PortfolioView, category: str | None = None,
                source: str | None = None, currency: str | None = None,
                error: str | None = None,
                history: ChartHistory | None = None) -> str:
    # Uvoz unutra: view uvozi ovaj modul, pa bi uvoz na razini zatvorio krug.
    from view import filter_positions

    e = html.escape
    rows = filter_positions(view, category, source, currency) if view else []
    categories = sorted({p.category for p in view.positions}) if view else []
    sources = sorted({p.source for p in view.positions}) if view else []
    currencies = sorted({p.currency for p in view.positions if p.currency}) if view else []
    filtrirano = bool(category or source or currency)

    def _select(name, current, values, labels=None, title=None):
        opts = ['<option value="">sve</option>']
        for v in values:
            label = (labels or {}).get(v, v)
            sel = " selected" if current == v else ""
            opts.append(f'<option value="{e(v)}"{sel}>{e(label)}</option>')
        cls = "sel active" if current else "sel"
        return (f'<label class="{cls}"><span class="sel-k">{e(title or name)}</span>'
                f'<select name="{e(name)}">' + "".join(opts) + "</select></label>")

    options = [
        _select("category", category, categories, CATEGORY_LABEL, "Kategorija"),
        _select("source", source, sources, SOURCE_LABEL, "Izvor"),
        _select("currency", currency, currencies, title="Valuta"),
    ]

    badges = _badges(view) if view else ""
    metrics = _metrics_block(view) if view else ""
    alloc_block = _alloc_block(view, category, source, currency)
    line_block = _history_block(history or ChartHistory())

    body_rows = []
    for p in rows:
        pnl_cls = _tone(p.pnl_eur)
        body_rows.append(
            "<tr>"
            f'<td class="num pct">{e(_pct(p.weight_pct_of_total))}</td>'
            f'<td class="ticker"><code>{e(p.ticker)}</code>'
            f'<span class="name">{e(p.name)}</span></td>'
            f'<td class="kat"><span class="chip {_cat_class(p.category)}"></span>'
            f"{e(_category_label(p.category))}</td>"
            f'<td class="dim">{e(SOURCE_LABEL.get(p.source, p.source))}</td>'
            f'<td class="dim">{e(p.currency)}</td>'
            f'<td class="num qty">{e(_qty(p.quantity))}</td>'
            f'<td class="num">{e(_num(p.value_eur))}</td>'
            f'<td class="num dim">{e(_num(p.cost_eur))}</td>'
            f'<td class="num {pnl_cls}">{e(_signed_num(p.pnl_eur))}</td>'
            f'<td class="num {pnl_cls}">{e(_signed_pct(p.pnl_pct))}</td>'
            "</tr>"
        )
    if not body_rows:
        body_rows.append(
            '<tr><td colspan="10" class="empty">Nema pozicija za ovaj filtar.</td></tr>'
        )

    zbroj_value = sum(p.value_eur for p in rows)
    zbroj_costs = [p.cost_eur for p in rows if p.cost_eur is not None]
    zbroj_pnls = [p.pnl_eur for p in rows if p.pnl_eur is not None]
    zbroj_cost = sum(zbroj_costs) if zbroj_costs else None
    zbroj_pnl = sum(zbroj_pnls) if zbroj_pnls else None
    tfoot = (
        '<tfoot><tr>'
        '<td></td><td class="ticker">Zbroj</td>'
        f'<td class="dim" colspan="4">{"filtrirano" if filtrirano else "sve pozicije"}</td>'
        f'<td class="num strong">{e(_num(zbroj_value))}</td>'
        f'<td class="num dim">{e(_num(zbroj_cost))}</td>'
        f'<td class="num {_tone(zbroj_pnl)}">{e(_signed_num(zbroj_pnl))}</td>'
        "<td></td></tr></tfoot>"
    )

    ukupno_rows = len(view.positions) if view else 0
    brojac = (
        f"{len(rows)} od {ukupno_rows} "
        f"{_plural(ukupno_rows, 'pozicije', 'pozicija', 'pozicija')}"
    )
    ocisti = ('<a class="clear-filters" href="?">Očisti filtre</a>'
              if filtrirano else "")
    err_block = f'<p class="error">{e(error)}</p>' if error else ""
    n_izvora = len([s for s in view.sources if s.available]) if view else 0
    izvora_txt = (
        f"read-only · {n_izvora} "
        f"{_plural(n_izvora, 'izvor', 'izvora', 'izvora')}"
    )

    return f"""<!DOCTYPE html>
<html lang="hr" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfelj</title>
<link rel="stylesheet" href="/static/pico.min.css">
<link rel="stylesheet" href="/static/dashboard.css">
</head>
<body>
<div class="desk">
<div class="window">
  <div class="winbar">
    <span class="lights"><i class="light l1"></i><i class="light l2"></i><i class="light l3"></i></span>
    <span class="tabs"><span class="tab active">Portfelj</span></span>
    <span class="winbar-right">
      <span class="winbar-meta">{e(izvora_txt)}</span>
      <a class="winbar-link" href="/json">JSON</a>
    </span>
  </div>
  <main class="page">
    <div class="page-head">
      <hgroup>
        <h1>Portfelj</h1>
        <p class="sub">Read-only · težine su udio u cijelom portfelju, uključujući cash.</p>
      </hgroup>
      <div class="badges">{badges}</div>
    </div>
    {err_block}
    {metrics}
    {line_block}
    {alloc_block}
    <section class="panel holdings">
      <div class="toolbar">
        <span class="label">Pozicije</span>
        <form class="filters" method="get">
          {"".join(options)}
          <button type="submit">Filtriraj</button>
        </form>
        {ocisti}
        <span class="rowcount">{e(brojac)}</span>
      </div>
      <div class="table-wrap">
      <table>
      <thead><tr>
        <th class="num">%</th><th>Pozicija</th><th>Kategorija</th><th>Izvor</th><th>Valuta</th>
        <th class="num">Količina</th><th class="num">Vrijednost</th><th class="num">Trošak</th>
        <th class="num">P&amp;L</th><th class="num">P&amp;L %</th>
      </tr></thead>
      <tbody>{"".join(body_rows)}</tbody>
      {tfoot}
      </table>
      </div>
    </section>
    <div class="page-foot">
      <span>Kod računa, LLM citira, čovjek odlučuje. Dashboard ne izvršava trgovanje.</span>
      <span>Treći kanal uz CLI i Telegram · /json za strojni pogled</span>
    </div>
  </main>
</div>
</div>
</body>
</html>
"""
