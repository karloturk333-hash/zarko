#!/usr/bin/env python3
"""Izvještaji o cijelom portfelju — deterministički, bez LLM-a i bez T212 kredencijala.

Ovo je sučelje koje zove Telegram agent. Namjerno NE dira `.env` ni Trading212:
T212 dio čita iz SQLite baze koju je napunio portfolio.py, kripto i ZSE iz
`state/*.json` koje izveze Hermes. Isti izvori kao dashboard (view.py), pa
agent citira iste brojke koje Karlo vidi u browseru.

Sve brojke dolaze iz izvora. LLM ih smije preformulirati, ne preračunati.

    python3 report.py status     # cijeli portfelj (T212 + kripto + ZSE)
    python3 report.py digest     # + promjena od prošlog agregiranog snapshota
    python3 report.py --json     # isto, strojno čitljivo
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import db

SOURCE_LABEL = {"t212": "T212", "crypto": "Kripto", "zse": "ZSE"}


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


def _zadnji_view_red(history_path: str | Path) -> dict | None:
    """Zadnji agregirani snapshot iz view_history.db (piše ga view.py --snapshot)."""
    history_path = Path(history_path)
    if not history_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            """SELECT taken_at, total_value_eur, t212_eur, crypto_eur, zse_eur
               FROM view_snapshots ORDER BY taken_at DESC, id DESC LIMIT 1"""
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return dict(row) if row else None


def collect_view(db_path: str | Path | None = None,
                 state_dir: str | Path | None = None,
                 rules_path: str | Path | None = None,
                 history_path: str | Path | None = None) -> dict:
    """Cijeli portfelj: T212 + kripto + ZSE, spojeno kroz view.assemble().

    Isti izvori i iste težine kao dashboard. Sve konekcije su read-only;
    view_history.db se samo čita (piše ga isključivo view.py --snapshot).

    Diže ViewGreska ako nema nijednog izvora ili je neki ticker nesvrstan,
    PravilaGreska ako rules.yaml ne valja.
    """
    # Uvoz unutar funkcije: view uvozi t212_adapter, koji uvozi report —
    # uvoz na razini modula bi zatvorio krug.
    import rules as rules_mod
    import view
    import view_history

    db_path = Path(db_path) if db_path else db.DEFAULT_DB
    state_dir = Path(state_dir) if state_dir else view.DEFAULT_STATE
    rules_path = Path(rules_path) if rules_path else rules_mod.RULES_PATH
    history_path = Path(history_path) if history_path else view_history.DEFAULT_DB

    pravila = rules_mod.ucitaj_pravila(rules_path)
    v = view.assemble(db_path, state_dir, pravila)

    # Promjena po T212 poziciji i FX upozorenja postoje samo u portfolio.db.
    change_by_ticker: dict[str, float | None] = {}
    fx_warnings: list[dict] = []
    fx_date = None
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            t212_data = collect(conn)
        except NoData:
            t212_data = None
        finally:
            conn.close()
        if t212_data:
            change_by_ticker = {p["ticker"]: p["change_eur"]
                                for p in t212_data["positions"]}
            fx_warnings = t212_data["fx_warnings"]
            fx_date = t212_data["fx_date"]

    return {
        "taken_at": v.as_of,
        "fx_date": fx_date,
        "total_value_eur": v.total_value_eur,
        "positions_value_eur": v.positions_value_eur,
        "cash_available_eur": v.cash_eur,
        "unrealized_pl_eur": v.total_pnl_eur,
        "total_cost_eur": v.total_cost_eur,
        "sources": [
            {
                "source": s.source,
                "freshness": s.freshness,
                "as_of": s.as_of,
                "available": s.available,
                "value_eur": s.value_eur,
                "cash_eur": s.cash_eur,
                "error": s.error,
            }
            for s in v.sources
        ],
        "previous": _zadnji_view_red(history_path),
        "positions": [
            {
                "ticker": p.ticker,
                "name": p.name,
                "category": p.category,
                "source": p.source,
                "freshness": p.freshness,
                "currency": p.currency,
                "quantity": p.quantity,
                "market_value_eur": p.value_eur,
                "total_cost_eur": p.cost_eur,
                "unrealized_pl_eur": p.pnl_eur,
                "pct": p.weight_pct_of_total,
                "change_eur": change_by_ticker.get(p.ticker),
            }
            for p in v.positions
        ],
        "fx_warnings": fx_warnings,
    }


def _hr(formatted: str) -> str:
    """1,234.50 -> 1.234,50 (hrvatski format: točka tisućice, zarez decimale)."""
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _eur(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:,.2f}')} EUR"


def _signed(v: float | None) -> str:
    return "n/d" if v is None else f"{_hr(f'{v:+,.2f}')} EUR"


def _kad(iso: str | None) -> str:
    return iso[:16].replace("T", " ") if iso else "n/d"


def _izvori_redak(data: dict) -> str | None:
    """Svježina po izvoru, da se sinoćnji T212 snapshot ne miješa s live kripto cijenom."""
    izvori = data.get("sources")
    if not izvori:
        return None
    dijelovi = []
    for s in izvori:
        label = SOURCE_LABEL.get(s["source"], s["source"])
        if not s["available"]:
            dijelovi.append(f"{label} n/d")
        else:
            dijelovi.append(f"{label} {s['freshness']} {_kad(s['as_of'])}")
    return "Izvori: " + " · ".join(dijelovi)


def status_text(data: dict) -> str:
    lines = [
        f"Portfelj — {_kad(data['taken_at'])} UTC",
        f"Ukupno: {_eur(data['total_value_eur'])}",
        f"Pozicije: {_eur(data['positions_value_eur'])}  ·  "
        f"Cash: {_eur(data['cash_available_eur'])}",
    ]
    if "realized_pl_eur" in data:
        lines.append(f"Nerealizirano: {_signed(data['unrealized_pl_eur'])}  ·  "
                     f"Realizirano: {_signed(data['realized_pl_eur'])}")
    else:
        lines.append(f"Nerealizirano: {_signed(data['unrealized_pl_eur'])}")
    izvori = _izvori_redak(data)
    if izvori:
        lines.append(izvori)
    lines.append("")
    for p in data["positions"]:
        pct = f"{p['pct']:.1f}%" if p["pct"] is not None else "n/d"
        lines.append(f"{pct:>6}  {p['ticker']:<14} {_eur(p['market_value_eur']):>14}  "
                     f"{_signed(p['unrealized_pl_eur'])}")
    return "\n".join(lines)


def _izvor_promjene(data: dict, prev: dict) -> list[str]:
    """Promjena po izvoru: trenutna vrijednost izvora protiv zadnjeg agregata."""
    redci = []
    dostupni = {s["source"]: s for s in data.get("sources") or [] if s["available"]}
    for kljuc, source in (("t212_eur", "t212"), ("crypto_eur", "crypto"),
                          ("zse_eur", "zse")):
        prije = prev.get(kljuc)
        s = dostupni.get(source)
        if prije is None or s is None:
            continue
        sad = (s["value_eur"] or 0.0) + (s["cash_eur"] or 0.0)
        delta = sad - prije
        if abs(delta) >= 0.01:
            redci.append(f"  {SOURCE_LABEL[source]:<14} {_signed(delta)}")
    return redci


def digest_text(data: dict) -> str:
    parts = [status_text(data)]

    prev = data["previous"]
    if prev and prev["total_value_eur"] is not None and data["total_value_eur"] is not None:
        delta = data["total_value_eur"] - prev["total_value_eur"]
        pct = (100.0 * delta / prev["total_value_eur"]) if prev["total_value_eur"] else 0.0
        parts.append(f"\nOd prošlog snapshota ({_kad(prev['taken_at'])} UTC): "
                     f"{_signed(delta)} ({pct:+.2f} %)")
        parts.extend(_izvor_promjene(data, prev))
        movers = [p for p in data["positions"] if p["change_eur"] is not None]
        movers.sort(key=lambda p: abs(p["change_eur"]), reverse=True)
        for p in movers[:3]:
            if abs(p["change_eur"]) >= 0.01:
                parts.append(f"  {p['ticker']:<14} {_signed(p['change_eur'])}")
    elif "sources" in data:
        parts.append("\nNema agregirane povijesti za usporedbu "
                     "(puni je cron: python3 view.py --snapshot).")
    else:
        parts.append("\nNema prethodnog snapshota za usporedbu.")

    if data["fx_warnings"]:
        parts.append("\nUPOZORENJE — provjeri valute:")
        for w in data["fx_warnings"]:
            parts.append(f"  {w['ticker']} [{w['currency']}]: odstupanje {w['delta_pct']:+.1f} %")

    if data.get("fx_date"):
        parts.append(f"\nTečajevi: ECB {data['fx_date']}")
    return "\n".join(parts)


def main() -> None:
    from position import ViewGreska  # noqa: PLC0415 — vidi collect_view
    from rules import PravilaGreska  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="status", choices=["status", "digest"])
    parser.add_argument("--db", default=str(db.DEFAULT_DB))
    parser.add_argument("--state", default=None,
                        help="mapa s crypto.json i zse.json (zadano state/ uz kod)")
    parser.add_argument("--rules", default=None, help="putanja rules.yaml")
    parser.add_argument("--history", default=None, help="putanja view_history.db")
    parser.add_argument("--json", action="store_true", help="strojno čitljiv izlaz")
    args = parser.parse_args()

    try:
        data = collect_view(args.db, args.state, args.rules, args.history)
    except (NoData, ViewGreska, PravilaGreska) as e:
        sys.exit(str(e))

    if args.json:
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print(digest_text(data) if args.command == "digest" else status_text(data))


if __name__ == "__main__":
    main()
