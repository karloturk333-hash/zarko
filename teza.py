#!/usr/bin/env python3
"""Zapisnik teza — što je agent tvrdio o poziciji, s datumom i provjerom.

Agent sastavi tezu kao JSON i proslijedi je ovamo. Ovdje se ona provjeri
(llm_output.py) i zapiše. Ako ne prođe, izlazni kod je 1 i na stderr ide
razlog — agent ga pročita i ispravi se sam.

    echo '{"ticker": "MU_US_EQ", ...}' | python3 teza.py zapisi
    python3 teza.py popis --ticker MU_US_EQ
    python3 teza.py otvorene --starije-od 90
    python3 teza.py zatvori 3 --ishod promasila --biljeska "DRAM je nastavio padati"

Granice, namjerno:
  - piše ISKLJUČIVO u teze.db; portfolio.db se otvara read-only
  - brojka koja nije u izlazu report.py ili u rules.yaml se odbija
  - ticker koji nije u rules.yaml se odbija — teza o nesvrstanoj poziciji se
    nema gdje ni provjeriti
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import db
import llm_output
import report
import rules
from llm_output import Nevaljano

SCHEMA_PATH = Path(__file__).parent / "teze.sql"

# Zaseban direktorij jer SQLite uz bazu piše i -wal/-journal datoteke, pa mu
# treba pravo pisanja na DIREKTORIJ, ne samo na datoteku. Ovako ostatak
# /opt/zarko ostaje read-only za korisnika hermes.
DEFAULT_TEZE_DB = Path(
    os.environ.get("ZARKO_TEZE_DB", Path(__file__).parent / "teze" / "teze.db")
)


def _sada() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def spoji(putanja: str | Path = DEFAULT_TEZE_DB) -> sqlite3.Connection:
    """Otvori (i po potrebi stvori) bazu teza."""
    putanja = Path(putanja)
    putanja.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(putanja)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


# ── Kontekst iz determinističkih izvora ──────────────────────────────────────


def kontekst(portfolio_db: str | Path = db.DEFAULT_DB) -> dict:
    """Sve što treba za provjeru teze: dozvoljeni tickeri, brojke i stanje.

    Brojke dolaze iz TRI izvora:
      - report.py --json    pune vrijednosti iz baze (8.436903...)
      - report.py ispis     zaokruženo, onako kako agent to VIDI (8.4 %)
      - rules.yaml          pragovi, da teza smije reći "granica je 15 posto"

    Drugi izvor nije višak. Agent čita ispis, pa citira "8.4" — a u JSON-u stoji
    puna preciznost. Bez zaokruženog oblika bi mu vlastiti izvještaj bio odbijen
    kao izmišljotina.
    """
    pravila = rules.ucitaj_pravila()

    portfolio_db = Path(portfolio_db)
    if not portfolio_db.exists():
        raise Nevaljano(
            f"Baza {portfolio_db} ne postoji, pa se brojke nemaju s čim usporediti. "
            f"Pokreni: python3 portfolio.py --save"
        )

    conn = sqlite3.connect(f"file:{portfolio_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        podaci = report.collect(conn)
    except report.NoData as e:
        raise Nevaljano(str(e)) from e
    finally:
        conn.close()

    return {
        "tickeri": set(pravila["klasifikacija"]),
        "brojke": (llm_output.dopustene_brojke(podaci)
                   | llm_output.dopustene_brojke(report.digest_text(podaci))
                   | llm_output.dopustene_brojke(pravila)),
        "podaci": podaci,
    }


def _pozicija(podaci: dict, ticker: str) -> dict | None:
    for p in podaci["positions"]:
        if p["ticker"] == ticker:
            return p
    return None


# ── Zapis ────────────────────────────────────────────────────────────────────


def zapisi(conn: sqlite3.Connection, teza: dict, ctx: dict) -> int:
    """Provjeri tezu i zapiši je. Vraća id retka.

    Uz tekst se snima i stanje pozicije u tom trenutku — vrijednost i udio.
    Bez toga se kasnije ne zna je li tvrdnja bila o poziciji od 8 % ili 33 %.
    """
    cisto = llm_output.provjeri_tezu(teza, dopusteni_tickeri=ctx["tickeri"])
    llm_output.bez_izmisljenih_brojki(cisto, ctx["brojke"])

    podaci = ctx["podaci"]
    poz = _pozicija(podaci, cisto["ticker"])

    kursor = conn.execute(
        """INSERT INTO teze (
               zapisano_at, ticker, teza, protuteza,
               sto_bi_promijenilo_misljenje, sigurnost,
               snapshot_at, vrijednost_eur, udio_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _sada(),
            cisto["ticker"],
            cisto["teza"],
            cisto["protuteza"],
            cisto["sto_bi_promijenilo_misljenje"],
            cisto["sigurnost"],
            podaci["taken_at"],
            poz["market_value_eur"] if poz else None,
            poz["pct"] if poz else None,
        ),
    )
    conn.commit()
    return kursor.lastrowid


def zatvori(conn: sqlite3.Connection, id_teze: int, ishod: str,
            biljeska: str | None = None) -> None:
    """Zabilježi ishod. Namjerno ručna radnja — presudu ne donosi agent."""
    red = conn.execute("SELECT ishod FROM teze WHERE id = ?", (id_teze,)).fetchone()
    if red is None:
        raise Nevaljano(f"Teza {id_teze} ne postoji.")
    if red["ishod"] is not None:
        raise Nevaljano(
            f"Teza {id_teze} je već zatvorena kao '{red['ishod']}'. "
            f"Ishod se ne prepisuje — to je poanta zapisnika."
        )
    conn.execute(
        "UPDATE teze SET ishod = ?, ishod_at = ?, biljeska = ? WHERE id = ?",
        (ishod, _sada(), biljeska, id_teze),
    )
    conn.commit()


# ── Ispis ────────────────────────────────────────────────────────────────────


def _redak(r: sqlite3.Row) -> str:
    udio = f"{r['udio_pct']:.1f} %" if r["udio_pct"] is not None else "n/d"
    stanje = r["ishod"] or "otvorena"
    return (
        f"#{r['id']}  {r['zapisano_at'][:10]}  {r['ticker']}  "
        f"({udio} tada, sigurnost {r['sigurnost']}, {stanje})\n"
        f"   za:    {r['teza']}\n"
        f"   protiv:{r['protuteza']}\n"
        f"   pada ako: {r['sto_bi_promijenilo_misljenje']}"
    )


def popis(conn: sqlite3.Connection, ticker: str | None = None) -> list[sqlite3.Row]:
    if ticker:
        return conn.execute(
            "SELECT * FROM teze WHERE ticker = ? ORDER BY zapisano_at DESC",
            (ticker,)).fetchall()
    return conn.execute("SELECT * FROM teze ORDER BY zapisano_at DESC").fetchall()


def otvorene(conn: sqlite3.Connection, starije_od_dana: int = 0) -> list[sqlite3.Row]:
    """Teze bez ishoda, starije od zadanog broja dana. To je red za pregled."""
    return conn.execute(
        """SELECT * FROM teze
           WHERE ishod IS NULL
             AND julianday('now') - julianday(zapisano_at) >= ?
           ORDER BY zapisano_at""",
        (starije_od_dana,)).fetchall()


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--teze-db", default=str(DEFAULT_TEZE_DB))
    parser.add_argument("--portfolio-db", default=str(db.DEFAULT_DB))
    pod = parser.add_subparsers(dest="naredba", required=True)

    pod.add_parser("zapisi", help="pročitaj JSON sa stdina, provjeri i zapiši")

    p_popis = pod.add_parser("popis", help="sve teze, najnovije prve")
    p_popis.add_argument("--ticker")
    p_popis.add_argument("--json", action="store_true")

    p_otv = pod.add_parser("otvorene", help="teze bez ishoda — red za pregled")
    p_otv.add_argument("--starije-od", type=int, default=0, metavar="DANA")

    p_zat = pod.add_parser("zatvori", help="zabilježi ishod teze")
    p_zat.add_argument("id", type=int)
    p_zat.add_argument("--ishod", required=True,
                       choices=["obistinila", "promasila", "nejasno"])
    p_zat.add_argument("--biljeska")

    args = parser.parse_args()
    conn = spoji(args.teze_db)

    try:
        if args.naredba == "zapisi":
            sirovo = sys.stdin.read()
            teza = llm_output.parsiraj(sirovo)
            ctx = kontekst(args.portfolio_db)
            id_teze = zapisi(conn, teza, ctx)
            print(f"Zapisano kao teza #{id_teze}.")

        elif args.naredba == "popis":
            redovi = popis(conn, args.ticker)
            if args.json:
                json.dump([dict(r) for r in redovi], sys.stdout,
                          indent=2, ensure_ascii=False)
                print()
            elif not redovi:
                print("Nema zapisanih teza.")
            else:
                print("\n\n".join(_redak(r) for r in redovi))

        elif args.naredba == "otvorene":
            redovi = otvorene(conn, args.starije_od)
            if not redovi:
                print("Nema otvorenih teza za pregled.")
            else:
                print("\n\n".join(_redak(r) for r in redovi))

        elif args.naredba == "zatvori":
            zatvori(conn, args.id, args.ishod, args.biljeska)
            print(f"Teza #{args.id} zatvorena kao '{args.ishod}'.")

    except Nevaljano as e:
        # Na stderr i izlazni kod 1 — agent to pročita kao poruku sebi.
        print(f"ODBIJENO: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
