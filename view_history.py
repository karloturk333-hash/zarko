"""Povijest agregiranog pogleda (T212 + kripto + ZSE).

Piše samo `python3 view.py --snapshot` (cron). HTTP handler samo čita.
Ne dira portfolio.db, rules.yaml ni .env.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from position import PortfolioView

DEFAULT_DB = Path(__file__).parent / "view_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS view_snapshots (
    id               INTEGER PRIMARY KEY,
    taken_at         TEXT NOT NULL,
    total_value_eur  REAL NOT NULL,
    t212_eur         REAL,
    crypto_eur       REAL,
    zse_eur          REAL,
    cash_eur         REAL
);
CREATE INDEX IF NOT EXISTS idx_view_snapshots_taken_at
    ON view_snapshots(taken_at);
"""


@dataclass
class ChartHistory:
    ukupno: list[tuple[str, float]] = field(default_factory=list)
    t212: list[tuple[str, float]] = field(default_factory=list)
    crypto: list[tuple[str, float]] = field(default_factory=list)
    zse: list[tuple[str, float]] = field(default_factory=list)
    ukupno_je_samo_t212: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _source_eur(view: PortfolioView, name: str) -> float | None:
    for s in view.sources:
        if s.source == name:
            if not s.available:
                return None
            return round(s.value_eur + (s.cash_eur or 0.0), 4)
    return None


def _readonly(path: Path, sql: str):
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def save_from_view(path: str | Path, view: PortfolioView,
                   taken_at: str | None = None) -> dict:
    """Zapiši jedan agregirani red. Jedino mjesto koje piše view_history.db."""
    taken_at = taken_at or _now()
    row = {
        "taken_at": taken_at,
        "total_value_eur": round(view.total_value_eur, 4),
        "t212_eur": _source_eur(view, "t212"),
        "crypto_eur": _source_eur(view, "crypto"),
        "zse_eur": _source_eur(view, "zse"),
        "cash_eur": round(view.cash_eur, 4),
    }
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            """INSERT INTO view_snapshots (
                   taken_at, total_value_eur, t212_eur, crypto_eur, zse_eur, cash_eur)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                row["taken_at"], row["total_value_eur"], row["t212_eur"],
                row["crypto_eur"], row["zse_eur"], row["cash_eur"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return row


def load_t212_history(db_path: str | Path) -> list[tuple[str, float]]:
    rows = _readonly(
        Path(db_path),
        "SELECT taken_at, total_value_eur FROM snapshots "
        "WHERE total_value_eur IS NOT NULL ORDER BY taken_at, id",
    )
    return [(r["taken_at"], float(r["total_value_eur"])) for r in rows]


def load_chart_history(portfolio_db: str | Path,
                       history_db: str | Path) -> ChartHistory:
    """T212 iz portfolio.db; kripto/ZSE/pravi zbroj iz view_history.db."""
    t212 = load_t212_history(portfolio_db)
    rows = _readonly(
        Path(history_db),
        "SELECT taken_at, total_value_eur, t212_eur, crypto_eur, zse_eur "
        "FROM view_snapshots ORDER BY taken_at, id",
    )
    crypto: list[tuple[str, float]] = []
    zse: list[tuple[str, float]] = []
    ukupno: list[tuple[str, float]] = []
    seen_t212 = {t for t, _ in t212}
    for r in rows:
        at = r["taken_at"]
        if r["total_value_eur"] is not None:
            ukupno.append((at, float(r["total_value_eur"])))
        if r["crypto_eur"] is not None:
            crypto.append((at, float(r["crypto_eur"])))
        if r["zse_eur"] is not None:
            zse.append((at, float(r["zse_eur"])))
        if r["t212_eur"] is not None and at not in seen_t212:
            t212.append((at, float(r["t212_eur"])))
            seen_t212.add(at)
    t212.sort(key=lambda p: p[0])
    samo = len(rows) == 0
    if samo:
        ukupno = list(t212)
    return ChartHistory(
        ukupno=ukupno, t212=t212, crypto=crypto, zse=zse,
        ukupno_je_samo_t212=samo,
    )
