"""Čita JSON iz /opt/zarko/state/ (crypto.json / zse.json).

Prihvaća dva oblika:
- kontrakt (source/freshness/as_of/value_eur) — state/*.example.json
- Hermesov postojeći holding (current_value_eur, last_updated, avg_price_eur)

zarko ne čita ~/.hermes/state — ta mapa je 750 hermes. Agent piše ovamo,
ovaj sloj samo čita.

Nedostajuća datoteka nije greška: izvor je prazan i UI to pokaže.
Neispravan JSON ili crypto.json označen kao zse jesu greška.
"""

from __future__ import annotations

import json
from pathlib import Path

from position import RawPosition, SourceResult, ViewGreska

ALLOWED_FRESHNESS = {"live", "snapshot"}
KNOWN_SOURCES = {"crypto", "zse", "t212"}


def load(path: str | Path, expected_source: str) -> SourceResult:
    path = Path(path)
    if not path.exists():
        return SourceResult(
            source=expected_source, freshness=None, as_of=None,
            positions=[], cash_eur=0.0, available=False,
            error=f"Nema {path.name} — Hermes još nije izvezao ovaj izvor.",
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ViewGreska(f"{path.name} nije čitljiv JSON: {e}") from e

    if not isinstance(raw, dict):
        raise ViewGreska(f"{path.name}: korijen mora biti objekt, ne {type(raw).__name__}")

    source_field = raw.get("source")
    if (isinstance(source_field, str)
            and source_field in KNOWN_SOURCES
            and source_field != expected_source):
        raise ViewGreska(
            f"{path.name}: source={source_field!r}, očekujem {expected_source!r}. "
            f"Kriva datoteka ili krivi kontrakt."
        )

    rows = raw.get("positions")
    if not isinstance(rows, list):
        raise ViewGreska(f"{path.name}: positions mora biti lista")

    freshness = _freshness(raw)
    as_of = _as_of(raw, rows, path.name)

    cash = raw.get("cash_eur") or 0.0
    try:
        cash_eur = float(cash)
    except (TypeError, ValueError) as e:
        raise ViewGreska(f"{path.name}: cash_eur nije broj") from e

    positions = [_position(row, i, expected_source, freshness, as_of, path.name)
                 for i, row in enumerate(rows)]
    return SourceResult(
        source=expected_source, freshness=freshness, as_of=as_of,
        positions=positions, cash_eur=cash_eur, available=True,
    )


def _freshness(raw: dict) -> str:
    fr = raw.get("freshness")
    if fr in ALLOWED_FRESHNESS:
        return fr
    if fr is not None:
        raise ViewGreska(
            f"freshness mora biti 'live' ili 'snapshot', ne {fr!r}"
        )
    blob = json.dumps(raw, ensure_ascii=False).lower()
    return "live" if "live" in blob else "snapshot"


def _as_of(raw: dict, rows: list, filename: str) -> str:
    for key in ("as_of", "last_updated"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            return _coerce_iso(v.strip())
    for row in rows:
        if isinstance(row, dict):
            v = row.get("price_fetched_at")
            if isinstance(v, str) and v.strip():
                return _coerce_iso(v.strip())
    raise ViewGreska(f"{filename}: nedostaje as_of ili last_updated")


def _coerce_iso(value: str) -> str:
    if len(value) == 10 and value[4:5] == "-" and value[7:8] == "-":
        return value + "T00:00:00Z"
    return value


def _position(row, index: int, source: str, freshness: str, as_of: str,
              filename: str) -> RawPosition:
    if not isinstance(row, dict):
        raise ViewGreska(f"{filename}: positions[{index}] nije objekt")
    ticker = row.get("ticker")
    if not ticker or not isinstance(ticker, str):
        raise ViewGreska(f"{filename}: positions[{index}] nema ticker")

    value = _first_float(row, ("value_eur", "current_value_eur"),
                         f"{filename}: {ticker} nema value_eur ni current_value_eur")

    cost = _opt_float(row.get("cost_eur"), f"{filename}: {ticker}.cost_eur")
    if cost is None:
        cost = _opt_float(row.get("invested_eur"), f"{filename}: {ticker}.invested_eur")
    qty = _opt_float(row.get("quantity"), f"{filename}: {ticker}.quantity")
    avg = _opt_float(row.get("avg_price_eur"), f"{filename}: {ticker}.avg_price_eur")
    if cost is None and qty is not None and avg is not None:
        cost = qty * avg

    pnl = _opt_float(row.get("pnl_eur"), f"{filename}: {ticker}.pnl_eur")
    if pnl is None:
        pnl = _opt_float(row.get("unrealized_pl_eur"),
                         f"{filename}: {ticker}.unrealized_pl_eur")
    if pnl is None and cost is not None:
        pnl = value - cost

    currency = row.get("currency") or "EUR"
    if not isinstance(currency, str):
        raise ViewGreska(f"{filename}: {ticker}.currency mora biti string")

    return RawPosition(
        ticker=ticker,
        name=(row.get("name") or ticker),
        source=source,
        currency=currency,
        quantity=qty,
        value_eur=value,
        cost_eur=cost,
        pnl_eur=pnl,
        as_of=as_of,
        freshness=freshness,
    )


def _first_float(row: dict, keys: tuple[str, ...], error: str) -> float:
    for key in keys:
        if key in row and row[key] is not None:
            return _req_float(row[key], error)
    raise ViewGreska(error)


def _req_float(value, error: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ViewGreska(error) from e


def _opt_float(value, label: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ViewGreska(f"{label} nije broj") from e
