"""Zajednička shema portfelja — adapteri pretvaraju izvore u ovo, view.py spaja.

Dashboard i `view.py --json` čitaju samo ovaj oblik. Izvorni formati (SQLite,
Hermesov JSON) ovdje ne ulaze.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


class ViewGreska(Exception):
    """Podaci se ne mogu složiti u pogled. Radije stani nego pogodi."""


@dataclass
class RawPosition:
    """Pozicija iz jednog adaptera, prije kategorije i težine u cijelom portfelju."""

    ticker: str
    name: str
    source: str          # t212 | crypto | zse
    currency: str
    quantity: float | None
    value_eur: float
    cost_eur: float | None
    pnl_eur: float | None
    as_of: str
    freshness: str       # snapshot | live


@dataclass
class Position:
    ticker: str
    name: str
    category: str
    source: str
    currency: str
    quantity: float | None
    value_eur: float
    cost_eur: float | None
    pnl_eur: float | None
    pnl_pct: float | None
    weight_pct_of_total: float | None
    as_of: str
    freshness: str


@dataclass
class SourceMeta:
    source: str
    freshness: str | None
    as_of: str | None
    available: bool
    n_positions: int
    value_eur: float
    cash_eur: float = 0.0
    error: str | None = None


@dataclass
class Allocation:
    category: str
    value_eur: float
    weight_pct: float


@dataclass
class SourceResult:
    source: str
    freshness: str | None
    as_of: str | None
    positions: list[RawPosition]
    cash_eur: float = 0.0
    available: bool = True
    error: str | None = None


@dataclass
class PortfolioView:
    as_of: str | None
    total_value_eur: float
    positions_value_eur: float
    cash_eur: float
    total_cost_eur: float | None
    total_pnl_eur: float | None
    sources: list[SourceMeta]
    positions: list[Position]
    allocation: list[Allocation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
