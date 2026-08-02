"""SQLite sloj: shema, spremanje snapshota, EUR normalizacija s unakrsnom provjerom."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fx_conversion import FxConverter, UnknownCurrency

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB = Path(__file__).parent / "portfolio.db"

# Iznad ovog postotka razlike između dvije metode izračuna vrijednosti pozicije
# smatramo da nešto ne štima (tipično neprepoznat minor unit).
CHECK_TOLERANCE_PCT = 1.0


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def upsert_instruments(conn: sqlite3.Connection, instruments: list[dict]) -> int:
    """Registar instrumenata. Prima i oblik iz /positions (`currency`) i iz
    /metadata/instruments (`currencyCode`)."""
    rows = [
        (i.get("ticker"), i.get("currency") or i.get("currencyCode"),
         i.get("name"), i.get("isin"), i.get("type"))
        for i in instruments if i.get("ticker")
    ]
    conn.executemany(
        """INSERT INTO instruments (ticker, currency_code, name, isin, type, fetched_at)
           VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
           ON CONFLICT(ticker) DO UPDATE SET
               currency_code = excluded.currency_code,
               name          = excluded.name,
               isin          = excluded.isin,
               type          = COALESCE(excluded.type, instruments.type),
               fetched_at    = excluded.fetched_at""",
        rows,
    )
    conn.commit()
    return len(rows)


def last_known_rates(conn: sqlite3.Connection) -> tuple[dict, str] | None:
    """Zadnji spremljeni set tečajeva — fallback kad je ECB nedostupan (cron na serveru)."""
    row = conn.execute(
        """SELECT fx_rates_json, fx_date FROM snapshots
           WHERE fx_rates_json IS NOT NULL ORDER BY id DESC LIMIT 1""").fetchone()
    return (json.loads(row["fx_rates_json"]), row["fx_date"] or "") if row else None


def _position_row(p: dict, fx: FxConverter, acct_ccy: str, acct_rate: float) -> tuple:
    """Jedna pozicija -> redak za INSERT, s obje metode izračuna vrijednosti.

    Metoda A (mjerodavna): walletImpact.currentValue, koji je T212 već sveo na
    valutu računa -> EUR.
    Metoda B (kontrola): quantity * currentPrice u valuti instrumenta -> EUR.

    Ako se razilaze, nešto s valutama ne štima — zapisujemo postotak razlike.
    """
    instrument = p.get("instrument") or {}
    ticker = instrument.get("ticker") or p.get("ticker")
    ccy = instrument.get("currency")
    wallet = p.get("walletImpact") or {}
    wallet_ccy = wallet.get("currency") or acct_ccy

    qty = p.get("quantity") or 0
    price = p.get("currentPrice")
    market_value_native = qty * price if price is not None else None

    rate_instrument = fx.rate_to_eur(ccy)  # baca iznimku na nepoznatu valutu
    rate_wallet = acct_rate if wallet_ccy == acct_ccy else fx.rate_to_eur(wallet_ccy)

    check_eur = market_value_native * rate_instrument if market_value_native is not None else None
    current_value_acct = wallet.get("currentValue")
    authoritative_eur = current_value_acct * rate_wallet if current_value_acct is not None else None

    # Ako walletImpact fali, padni na kontrolni izračun.
    if authoritative_eur is None:
        authoritative_eur = check_eur

    delta_pct = None
    if authoritative_eur and check_eur is not None:
        delta_pct = 100.0 * (check_eur - authoritative_eur) / abs(authoritative_eur)

    return (
        ticker, instrument.get("name"), instrument.get("isin"), ccy,
        qty, p.get("quantityAvailableForTrading"), p.get("quantityInPies"),
        p.get("averagePricePaid"), price, market_value_native,
        wallet_ccy, current_value_acct, wallet.get("totalCost"),
        wallet.get("unrealizedProfitLoss"), wallet.get("fxImpact"),
        rate_instrument, rate_wallet,
        authoritative_eur, check_eur, delta_pct,
        fx.to_eur(wallet.get("totalCost"), wallet_ccy),
        fx.to_eur(wallet.get("unrealizedProfitLoss"), wallet_ccy),
        p.get("createdAt"), json.dumps(p),
    )


def save_snapshot(conn: sqlite3.Connection, summary: dict, positions: list,
                  fx: FxConverter) -> tuple[int, list[dict]]:
    """Spremi snapshot s EUR normalizacijom.

    Vraća (snapshot_id, upozorenja). Upozorenje = pozicija kod koje se dvije
    metode izračuna razilaze više od CHECK_TOLERANCE_PCT.
    """
    acct_ccy = summary.get("currency")
    if not acct_ccy:
        raise UnknownCurrency(
            "AccountSummary nema polje `currency` — bez valute računa nema pouzdane "
            "konverzije. Provjeri odgovor /equity/account/summary."
        )
    acct_rate = fx.rate_to_eur(acct_ccy)

    missing = sorted({(p.get("instrument") or {}).get("ticker") or p.get("ticker")
                      for p in positions
                      if not (p.get("instrument") or {}).get("currency")})
    if missing:
        raise UnknownCurrency(
            f"Pozicije bez valute instrumenta: {', '.join(map(str, missing))}. "
            f"Odbijam spremiti — to je izvor krivih zbrojeva."
        )

    rows = [_position_row(p, fx, acct_ccy, acct_rate) for p in positions]
    positions_value_eur = sum(r[17] for r in rows if r[17] is not None)

    cash = summary.get("cash") or {}
    inv = summary.get("investments") or {}
    to_eur = lambda v: fx.to_eur(v, acct_ccy)  # noqa: E731

    cur = conn.execute(
        """INSERT INTO snapshots (
               account_id, account_currency,
               cash_available_acct, cash_in_pies_acct, cash_reserved_acct,
               inv_current_value_acct, inv_total_cost_acct, inv_unrealized_pl_acct,
               inv_realized_pl_acct, total_value_acct,
               cash_available_eur, cash_in_pies_eur, cash_reserved_eur,
               inv_current_value_eur, inv_total_cost_eur, inv_unrealized_pl_eur,
               inv_realized_pl_eur, total_value_eur, positions_value_eur,
               account_fx_rate, fx_source, fx_date, fx_rates_json, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            summary.get("id"), acct_ccy,
            cash.get("availableToTrade"), cash.get("inPies"), cash.get("reservedForOrders"),
            inv.get("currentValue"), inv.get("totalCost"), inv.get("unrealizedProfitLoss"),
            inv.get("realizedProfitLoss"), summary.get("totalValue"),
            to_eur(cash.get("availableToTrade")), to_eur(cash.get("inPies")),
            to_eur(cash.get("reservedForOrders")),
            to_eur(inv.get("currentValue")), to_eur(inv.get("totalCost")),
            to_eur(inv.get("unrealizedProfitLoss")), to_eur(inv.get("realizedProfitLoss")),
            to_eur(summary.get("totalValue")), positions_value_eur,
            acct_rate, fx.source, fx.date, json.dumps(fx.rates), json.dumps(summary),
        ),
    )
    snapshot_id = cur.lastrowid

    conn.executemany(
        """INSERT INTO positions (
               snapshot_id, ticker, name, isin, currency,
               quantity, quantity_available, quantity_in_pies,
               average_price_native, current_price_native, market_value_native,
               wallet_currency, current_value_acct, total_cost_acct,
               unrealized_pl_acct, fx_impact_acct,
               fx_rate_instrument, fx_rate_wallet,
               market_value_eur, market_value_eur_check, check_delta_pct,
               total_cost_eur, unrealized_pl_eur, created_at, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(snapshot_id, *r) for r in rows],
    )

    upsert_instruments(conn, [p.get("instrument") or {} for p in positions])
    conn.commit()

    warnings = [
        {"ticker": r[0], "currency": r[3], "delta_pct": r[19],
         "eur": r[17], "eur_check": r[18]}
        for r in rows
        if r[19] is not None and abs(r[19]) > CHECK_TOLERANCE_PCT
    ]
    return snapshot_id, warnings
