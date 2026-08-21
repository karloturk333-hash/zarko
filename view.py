#!/usr/bin/env python3
"""Unificirani read-only pogled na portfelj (T212 + kripto + ZSE).

Ne dira portfolio.db, rules.yaml ni .env. Ne pokreće portfolio.py.
Kripto i ZSE čita iz /opt/zarko/state/*.json (Hermes piše, zarko čita) —
nikad iz ~/.hermes/state.

    python3 view.py --json              # strojno čitljiv PortfolioView
    python3 view.py serve               # HTML na 127.0.0.1:8787
    python3 view.py serve --port 8787 --bind 127.0.0.1
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import crypto_adapter
import rules
import t212_adapter
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
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8787
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
CASH_CATEGORY = "cash"


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


# ── HTML ─────────────────────────────────────────────────────────────────────

def render_html(view: PortfolioView, category: str | None = None,
                source: str | None = None, currency: str | None = None,
                error: str | None = None) -> str:
    e = html.escape
    rows = filter_positions(view, category, source, currency) if view else []
    categories = sorted({p.category for p in view.positions}) if view else []
    sources = sorted({p.source for p in view.positions}) if view else []
    currencies = sorted({p.currency for p in view.positions if p.currency}) if view else []

    options = []
    def _select(name, current, values, labels=None):
        opts = ['<option value="">sve</option>']
        for v in values:
            label = (labels or {}).get(v, v)
            sel = " selected" if current == v else ""
            opts.append(f'<option value="{e(v)}"{sel}>{e(label)}</option>')
        return (f'<label>{e(name)} <select name="{e(name)}">'
                + "".join(opts) + "</select></label>")

    options.append(_select("category", category, categories))
    options.append(_select("source", source, sources, SOURCE_LABEL))
    options.append(_select("currency", currency, currencies))

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

    bars = []
    if view:
        for a in view.allocation:
            bars.append(
                f'<div class="bar-row"><span class="bar-label">{e(a.category)}</span>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{a.weight_pct:.2f}%"></div></div>'
                f'<span class="bar-pct">{e(_pct(a.weight_pct))}</span>'
                f'<span class="bar-eur">{e(_eur(a.value_eur))}</span></div>'
            )

    body_rows = []
    for p in rows:
        pnl_cls = "neg" if (p.pnl_eur or 0) < 0 else ("pos" if (p.pnl_eur or 0) > 0 else "")
        body_rows.append(
            "<tr>"
            f"<td class=\"num\">{e(_pct(p.weight_pct_of_total))}</td>"
            f"<td><code>{e(p.ticker)}</code><div class=\"muted\">{e(p.name)}</div></td>"
            f"<td>{e(p.category)}</td>"
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
<section class="totals">
  <div><div class="k">Ukupno</div><div class="v">{e(_eur(view.total_value_eur))}</div></div>
  <div><div class="k">Pozicije</div><div class="v">{e(_eur(view.positions_value_eur))}</div></div>
  <div><div class="k">Cash</div><div class="v">{e(_eur(view.cash_eur))}</div></div>
  <div><div class="k">Nerealizirano</div><div class="v">{e(_signed(view.total_pnl_eur))}</div></div>
</section>"""

    return f"""<!DOCTYPE html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfelj</title>
<style>
  :root {{ color-scheme: light dark; --fg: #1a1a1a; --muted: #666; --line: #ddd;
           --bg: #fff; --fill: #1a1a1a; --pos: #0a7a32; --neg: #b42318; --err: #b42318; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg: #eee; --muted: #aaa; --line: #333; --bg: #111; --fill: #ddd; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0 auto; max-width: 960px; padding: 1rem; font: 16px/1.4 system-ui, sans-serif;
          color: var(--fg); background: var(--bg); }}
  h1 {{ font-size: 1.25rem; font-weight: 600; margin: 0 0 0.5rem; }}
  .muted {{ color: var(--muted); font-size: 0.85rem; }}
  .badges {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.75rem 0; }}
  .badge {{ font-size: 0.8rem; border: 1px solid var(--line); padding: 0.2rem 0.5rem; }}
  .badge.live {{ border-color: var(--pos); }}
  .badge.missing {{ color: var(--muted); }}
  .error {{ color: var(--err); }}
  .totals {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
             gap: 0.75rem; margin: 1rem 0; }}
  .totals .k {{ color: var(--muted); font-size: 0.8rem; }}
  .totals .v {{ font-variant-numeric: tabular-nums; font-size: 1.1rem; }}
  form {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: end; margin: 1rem 0; }}
  label {{ font-size: 0.8rem; color: var(--muted); display: flex; flex-direction: column; gap: 0.2rem; }}
  select {{ font: inherit; min-height: 2.25rem; }}
  .bars {{ margin: 1rem 0 1.5rem; }}
  .bar-row {{ display: grid; grid-template-columns: 7.5rem 1fr 4.5rem 8rem; gap: 0.5rem;
              align-items: center; margin: 0.35rem 0; font-size: 0.9rem; }}
  .bar-track {{ background: var(--line); height: 0.5rem; }}
  .bar-fill {{ background: var(--fill); height: 100%; }}
  .bar-pct, .bar-eur, .num {{ font-variant-numeric: tabular-nums; text-align: right; }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ border-bottom: 1px solid var(--line); padding: 0.45rem 0.4rem; text-align: left;
            vertical-align: top; }}
  th {{ color: var(--muted); font-weight: 500; font-size: 0.75rem; text-transform: uppercase; }}
  .pos {{ color: var(--pos); }}
  .neg {{ color: var(--neg); }}
  code {{ font-size: 0.9rem; }}
</style>
</head>
<body>
<h1>Portfelj</h1>
<p class="muted">Read-only · težine su udio u cijelom portfelju (uključujući cash)</p>
{err_block}
<div class="badges">{"".join(badges)}</div>
{totals}
<section class="bars">{"".join(bars)}</section>
<form method="get">
  {"".join(options)}
  <button type="submit">Filtriraj</button>
</form>
<div class="table-wrap">
<table>
<thead><tr>
  <th>%</th><th>Pozicija</th><th>Kategorija</th><th>Izvor</th><th>Valuta</th>
  <th class="num">Količina</th><th class="num">Vrijednost</th><th class="num">Trošak</th>
  <th class="num">P&L</th><th class="num">P&L %</th>
</tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table>
</div>
</body>
</html>
"""


# ── HTTP ─────────────────────────────────────────────────────────────────────

def make_handler(db_path: Path, state_dir: Path, rules_path: Path):
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
            parsed = urlparse(self.path)
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
                )
                self._send(500, page.encode("utf-8"), "text/html; charset=utf-8")
                return

            if parsed.path == "/json":
                payload = json.dumps(view.to_dict(), ensure_ascii=False, indent=2).encode()
                self._send(200, payload, "application/json; charset=utf-8")
                return

            page = render_html(view, category=category, source=source, currency=currency)
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
          rules_path: Path) -> None:
    warning = public_bind_warning(bind)
    if warning:
        print(warning, file=sys.stderr)
    httpd = ThreadingHTTPServer((bind, port), make_handler(db_path, state_dir, rules_path))
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
    a = p.parse_args()

    command = "json" if a.json else a.command

    if command == "serve":
        serve(a.bind, a.port, Path(a.db), Path(a.state), Path(a.rules))
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
