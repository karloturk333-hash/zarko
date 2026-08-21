"""T212 adapter — čita zadnji snapshot iz portfolio.db, bez kredencijala.

Koristi report.collect() (isti izvor kao Telegram agent). T212 dio je
snapshot: cron ga puni radnim danom u 22:15, dashboard ga ne osvježava.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import report
from position import RawPosition, SourceResult, ViewGreska

SOURCE = "t212"
FRESHNESS = "snapshot"


def load(db_path: str | Path) -> SourceResult:
    path = Path(db_path)
    if not path.exists():
        return SourceResult(
            source=SOURCE, freshness=FRESHNESS, as_of=None,
            positions=[], cash_eur=0.0, available=False,
            error=f"Nema baze {path}. Pokreni: python3 portfolio.py --save",
        )

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            data = report.collect(conn)
        except report.NoData as e:
            return SourceResult(
                source=SOURCE, freshness=FRESHNESS, as_of=None,
                positions=[], cash_eur=0.0, available=False, error=str(e),
            )
    finally:
        conn.close()

    as_of = data["taken_at"]
    positions = []
    for p in data["positions"]:
        ticker = p.get("ticker")
        if not ticker:
            raise ViewGreska("T212 pozicija bez tickera — odbijam pogled.")
        value = p.get("market_value_eur") or 0.0
        cost = p.get("total_cost_eur")
        pnl = p.get("unrealized_pl_eur")
        positions.append(RawPosition(
            ticker=ticker,
            name=p.get("name") or ticker,
            source=SOURCE,
            currency=p.get("currency") or "",
            quantity=p.get("quantity"),
            value_eur=float(value),
            cost_eur=None if cost is None else float(cost),
            pnl_eur=None if pnl is None else float(pnl),
            as_of=as_of,
            freshness=FRESHNESS,
        ))

    cash = data.get("cash_available_eur") or 0.0
    return SourceResult(
        source=SOURCE, freshness=FRESHNESS, as_of=as_of,
        positions=positions, cash_eur=float(cash), available=True,
    )
