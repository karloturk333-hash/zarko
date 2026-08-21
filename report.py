#!/usr/bin/env python3
"""Izvještaji iz portfolio.db — deterministički, bez LLM-a i bez T212 kredencijala.

Ovo je sučelje koje zove Telegram agent. Namjerno NE dira `.env` ni Trading212:
čita samo SQLite bazu koju je napunio portfolio.py. Agent time ne može doći do
API ključa čak ni ako ga netko navede da izvrši nešto neočekivano.

Sve brojke ovdje dolaze iz baze. LLM ih smije preformulirati, ne preračunati.

    python3 report.py status     # trenutno stanje
    python3 report.py digest     # stanje + promjena od prošlog snapshota
    python3 report.py --json     # isto, strojno čitljivo
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import db


class NoData(Exception):
    """Baza je prazna — portfolio.py još nije napravio nijedan snapshot."""


def _latest_two(conn: sqlite3.Connection) -> tuple[sqlite3.Row, sqlite3.Row | None]:
    rows = conn.execute(
        "SELECT * FROM snapshots ORDER BY id DESC LIMIT 2").fetchall()
    if not rows:
        raise NoData("Nema nijednog snapshota u bazi. Pokreni: python3 portfolio.py --save")
    return rows[0], (rows[1] if len(rows) > 1 else None)


def collect(conn: sqlite3.Connection) -> dict:
    """Sve što izvještaji trebaju, u jednom prolazu kroz bazu."""
    latest, previous = _latest_two(conn)

    positions = conn.execute(
        """SELECT ticker, name, currency, quantity, market_value_eur,
                  total_cost_eur, unrealized_pl_eur, check_delta_pct
           FROM positions WHERE snapshot_id = ?
           ORDER BY market_value_eur DESC""", (latest["id"],)).fetchall()

    total_positions = sum(p["market_value_eur"] or 0 for p in positions)

    # Prethodne vrijednosti po tickeru, za promjenu po poziciji.
    prev_by_ticker = {}
    if previous:
        prev_by_ticker = {
            r["ticker"]: r["market_value_eur"]
            for r in conn.execute(
                "SELECT ticker, market_value_eur FROM positions WHERE snapshot_id = ?",
                (previous["id"],))
        }

    return {
        "taken_at": latest["taken_at"],
        "fx_date": latest["fx_date"],
        "account_currency": latest["account_currency"],
        "total_value_eur": latest["total_value_eur"],
        "positions_value_eur": latest["positions_value_eur"],
        "cash_available_eur": latest["cash_available_eur"],
        "unrealized_pl_eur": latest["inv_unrealized_pl_eur"],
        "realized_pl_eur": latest["inv_realized_pl_eur"],
        "total_cost_eur": latest["inv_total_cost_eur"],
        "previous": {
            "taken_at": previous["taken_at"],
            "total_value_eur": previous["total_value_eur"],
        } if previous else None,
        "positions": [
            {
                "ticker": p["ticker"],
                "name": p["name"],
                "currency": p["currency"],
                "quantity": p["quantity"],
                "market_value_eur": p["market_value_eur"],
                "total_cost_eur": p["total_cost_eur"],
                "unrealized_pl_eur": p["unrealized_pl_eur"],
                "pct": (100.0 * (p["market_value_eur"] or 0) / total_positions
                        if total_positions else None),
                "change_eur": ((p["market_value_eur"] or 0) - prev_by_ticker[p["ticker"]]
                               if p["ticker"] in prev_by_ticker
                               and prev_by_ticker[p["ticker"]] is not None else None),
                "check_delta_pct": p["check_delta_pct"],
            }
            for p in positions
        ],
        "fx_warnings": [
            {"ticker": r["ticker"], "currency": r["currency"],
             "delta_pct": r["check_delta_pct"]}
            for r in conn.execute("SELECT * FROM v_fx_sanity WHERE snapshot_id = ?",
                                  (latest["id"],))
        ],
    }


def _hr(formatted: str) -> str:
    """1,234.50 -> 1.234,50 (hrvatski format: točka tisućice, zarez decimale)."""
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _eur(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:,.2f}')} EUR"


def _signed(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:+,.2f}')} EUR"


def status_text(data: dict) -> str:
    lines = [
        f"Portfelj — {data['taken_at'][:16].replace('T', ' ')} UTC",
        f"Ukupno: {_eur(data['total_value_eur'])}",
        f"Pozicije: {_eur(data['positions_value_eur'])}  ·  "
        f"Cash: {_eur(data['cash_available_eur'])}",
        f"Nerealizirano: {_signed(data['unrealized_pl_eur'])}  ·  "
        f"Realizirano: {_signed(data['realized_pl_eur'])}",
        "",
    ]
    for p in data["positions"]:
        pct = f"{p['pct']:.1f}%" if p["pct"] is not None else "n/d"
        lines.append(f"{pct:>6}  {p['ticker']:<14} {_eur(p['market_value_eur']):>14}  "
                     f"{_signed(p['unrealized_pl_eur'])}")
    return "\n".join(lines)


def digest_text(data: dict) -> str:
    parts = [status_text(data)]

    prev = data["previous"]
    if prev and prev["total_value_eur"] is not None and data["total_value_eur"] is not None:
        delta = data["total_value_eur"] - prev["total_value_eur"]
        pct = (100.0 * delta / prev["total_value_eur"]) if prev["total_value_eur"] else 0.0
        parts.append(f"\nOd prošlog snapshota ({prev['taken_at'][:16].replace('T', ' ')} UTC): "
                     f"{_signed(delta)} ({pct:+.2f} %)")
        movers = [p for p in data["positions"] if p["change_eur"] is not None]
        movers.sort(key=lambda p: abs(p["change_eur"]), reverse=True)
        for p in movers[:3]:
            if abs(p["change_eur"]) >= 0.01:
                parts.append(f"  {p['ticker']:<14} {_signed(p['change_eur'])}")
    else:
        parts.append("\nNema prethodnog snapshota za usporedbu.")

    if data["fx_warnings"]:
        parts.append("\nUPOZORENJE — provjeri valute:")
        for w in data["fx_warnings"]:
            parts.append(f"  {w['ticker']} [{w['currency']}]: odstupanje {w['delta_pct']:+.1f} %")

    parts.append(f"\nTečajevi: ECB {data['fx_date']}")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="status", choices=["status", "digest"])
    parser.add_argument("--db", default=str(db.DEFAULT_DB))
    parser.add_argument("--json", action="store_true", help="strojno čitljiv izlaz")
    args = parser.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"Baza {args.db} ne postoji. Pokreni: python3 portfolio.py --save")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)  # read-only konekcija
    conn.row_factory = sqlite3.Row
    try:
        data = collect(conn)
    except NoData as e:
        sys.exit(str(e))
    finally:
        conn.close()

    if args.json:
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print(digest_text(data) if args.command == "digest" else status_text(data))


if __name__ == "__main__":
    main()
