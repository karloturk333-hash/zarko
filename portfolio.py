#!/usr/bin/env python3
"""Povuci stanje portfelja s Trading212, normaliziraj u EUR, ispiši JSON i/ili spremi snapshot.

Korištenje:
    python3 portfolio.py --check     # provjeri rade li kredencijali (1 poziv, ne dira bazu)
    python3 portfolio.py             # JSON na stdout, s EUR vrijednostima
    python3 portfolio.py --save      # + spremi snapshot u portfolio.db
    python3 portfolio.py --save --quiet   # za cron
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import db
from fx_conversion import FxConverter, UnknownCurrency
from t212 import T212Client, T212Error, load_env


def fetch(client: T212Client) -> dict:
    summary = client.account_summary()
    time.sleep(1)  # /summary je 1 poziv / 5 s, /positions 1 / 1 s
    return {"summary": summary, "positions": client.positions()}


def get_fx(conn) -> FxConverter:
    """ECB tečajevi; ako je izvor nedostupan, padni na zadnje spremljene (glasno)."""
    try:
        return FxConverter.fetch()
    except UnknownCurrency as e:
        cached = db.last_known_rates(conn) if conn is not None else None
        if not cached:
            raise
        rates, date = cached
        print(f"UPOZORENJE: FX izvor nedostupan ({e}). Koristim zadnje spremljene "
              f"tečajeve od {date}.", file=sys.stderr)
        return FxConverter.from_rates(rates, date=date, source="cache (zadnji snapshot)")


def build_output(data: dict, fx: FxConverter) -> dict:
    """EUR pogled na podatke, isti izračun kao pri spremanju."""
    summary = data["summary"]
    acct_ccy = summary.get("currency")
    acct_rate = fx.rate_to_eur(acct_ccy)

    positions = []
    for p in data["positions"]:
        row = db._position_row(p, fx, acct_ccy, acct_rate)
        positions.append({
            "ticker": row[0], "name": row[1], "currency": row[3], "quantity": row[4],
            "currentPriceNative": row[8], "marketValueNative": row[9],
            "marketValueEur": row[17], "marketValueEurCheck": row[18],
            "checkDeltaPct": row[19], "unrealizedPlEur": row[21],
        })
    positions.sort(key=lambda p: p["marketValueEur"] or 0, reverse=True)
    total = sum(p["marketValueEur"] or 0 for p in positions)
    for p in positions:
        p["pctOfPositions"] = round(100.0 * (p["marketValueEur"] or 0) / total, 2) if total else None

    cash = summary.get("cash") or {}
    inv = summary.get("investments") or {}
    return {
        "account": {"id": summary.get("id"), "currency": acct_ccy, "fxRateToEur": acct_rate},
        "cashEur": {k: fx.to_eur(cash.get(k), acct_ccy)
                    for k in ("availableToTrade", "inPies", "reservedForOrders")},
        "investmentsEur": {k: fx.to_eur(inv.get(k), acct_ccy)
                           for k in ("currentValue", "totalCost",
                                     "unrealizedProfitLoss", "realizedProfitLoss")},
        "totalValueEur": fx.to_eur(summary.get("totalValue"), acct_ccy),
        "positionsValueEur": total,
        "fx": {"source": fx.source, "date": fx.date},
        "positions": positions,
    }


def report_warnings(warnings: list[dict]) -> None:
    if not warnings:
        return
    print("\nUPOZORENJE — dvije metode izračuna se ne slažu (provjeri valutu!):", file=sys.stderr)
    for w in warnings:
        print(f"  {w['ticker']} [{w['currency']}]: {w['eur']:.2f} EUR vs kontrola "
              f"{w['eur_check']:.2f} EUR ({w['delta_pct']:+.1f} %)", file=sys.stderr)
    print("  Ako je odstupanje ~9900 %, minor unit (peniji) nije prepoznat — "
          "vidi MINOR_UNITS u fx_conversion.py.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true", help="spremi snapshot u SQLite")
    parser.add_argument("--db", default=str(db.DEFAULT_DB), help="put do SQLite baze")
    parser.add_argument("--quiet", action="store_true", help="bez JSON ispisa (za cron)")
    parser.add_argument("--check", action="store_true",
                        help="samo provjeri kredencijale (1 poziv, ne dira bazu)")
    args = parser.parse_args()

    load_env()

    if args.check:
        client = T212Client()
        print(f"Shema: {client.auth_scheme}, env: {client.env}", file=sys.stderr)
        try:
            s = client.account_summary()
        except T212Error as e:
            sys.exit(f"NE radi — {e}")
        print(f"Radi. Račun #{s.get('id')}, valuta {s.get('currency')}, "
              f"ukupno {s.get('totalValue')} {s.get('currency')}.")
        return

    conn = db.connect(args.db) if args.save else None
    try:
        data = fetch(T212Client())
        fx = get_fx(conn)

        if not args.quiet:
            json.dump(build_output(data, fx), sys.stdout, indent=2, ensure_ascii=False)
            print()

        if args.save:
            snapshot_id, warnings = db.save_snapshot(conn, data["summary"],
                                                     data["positions"], fx)
            row = conn.execute(
                "SELECT positions_value_eur, total_value_eur FROM snapshots WHERE id = ?",
                (snapshot_id,)).fetchone()
            print(f"Snapshot #{snapshot_id}: {len(data['positions'])} pozicija, "
                  f"pozicije {row['positions_value_eur']:.2f} EUR, "
                  f"ukupno {row['total_value_eur']:.2f} EUR [ECB {fx.date}]", file=sys.stderr)
            report_warnings(warnings)
    except T212Error as e:
        sys.exit(f"Trading212 API greška — {e}")
    except UnknownCurrency as e:
        sys.exit(f"FX greška — {e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
