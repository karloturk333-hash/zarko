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

HTML i HTTP su u web/; ovaj modul slaže PortfolioView i ostaje CLI ulaz.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from http.server import ThreadingHTTPServer  # noqa: F401

import crypto_adapter
import db
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
from web.render import CATEGORY_COLORS, render_html  # noqa: F401
from web.server import DEFAULT_BIND, DEFAULT_PORT, serve
from web.server import make_handler, public_bind_warning  # noqa: F401

DEFAULT_STATE = Path(__file__).parent / "state"
DEFAULT_HISTORY = view_history.DEFAULT_DB
CASH_CATEGORY = "cash"

ChartHistory = view_history.ChartHistory
load_chart_history = view_history.load_chart_history


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


def take_snapshot(db_path: str | Path, state_dir: str | Path, pravila: dict,
                  history_path: str | Path,
                  taken_at: str | None = None) -> dict:
    """Spoji trenutne izvore i spremi red u view_history.db. Ne dira portfolio.db."""
    assembled = assemble(db_path, state_dir, pravila)
    return view_history.save_from_view(history_path, assembled, taken_at=taken_at)


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
