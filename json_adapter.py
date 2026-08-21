"""Čita Hermesov JSON kontrakt (crypto.json / zse.json).

zarko ne čita ~/.hermes/state — ta mapa je 750 hermes. Agent piše u
/opt/zarko/state/ (ista prava kao teze/), ovaj sloj samo čita.

Nedostajuća datoteka nije greška: izvor je prazan i UI to pokaže.
Neispravan JSON ili krivi `source` jesu greška — tiho preskakanje bi
sakrilo krive brojke.
"""

from __future__ import annotations

import json
from pathlib import Path

from position import RawPosition, SourceResult, ViewGreska

ALLOWED_FRESHNESS = {"live", "snapshot"}


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

    source = raw.get("source")
    if source != expected_source:
        raise ViewGreska(
            f"{path.name}: source={source!r}, očekujem {expected_source!r}. "
            f"Kriva datoteka ili krivi kontrakt."
        )

    freshness = raw.get("freshness")
    if freshness not in ALLOWED_FRESHNESS:
        raise ViewGreska(
            f"{path.name}: freshness mora biti 'live' ili 'snapshot', ne {freshness!r}"
        )

    as_of = raw.get("as_of")
    if not as_of or not isinstance(as_of, str):
        raise ViewGreska(f"{path.name}: nedostaje as_of (ISO-8601 string)")

    rows = raw.get("positions")
    if not isinstance(rows, list):
        raise ViewGreska(f"{path.name}: positions mora biti lista")

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


def _position(row, index: int, source: str, freshness: str, as_of: str,
              filename: str) -> RawPosition:
    if not isinstance(row, dict):
        raise ViewGreska(f"{filename}: positions[{index}] nije objekt")
    ticker = row.get("ticker")
    if not ticker or not isinstance(ticker, str):
        raise ViewGreska(f"{filename}: positions[{index}] nema ticker")
    if "value_eur" not in row:
        raise ViewGreska(f"{filename}: {ticker} nema value_eur")
    try:
        value = float(row["value_eur"])
    except (TypeError, ValueError) as e:
        raise ViewGreska(f"{filename}: {ticker}.value_eur nije broj") from e

    cost = _opt_float(row.get("cost_eur"), f"{filename}: {ticker}.cost_eur")
    pnl = _opt_float(row.get("pnl_eur"), f"{filename}: {ticker}.pnl_eur")
    if pnl is None and cost is not None:
        pnl = value - cost
    qty = _opt_float(row.get("quantity"), f"{filename}: {ticker}.quantity")
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


def _opt_float(value, label: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ViewGreska(f"{label} nije broj") from e
