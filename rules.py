#!/usr/bin/env python3
"""Rules engine — provjerava portfelj protiv rules.yaml. Deterministički, bez LLM-a.

Ovo je sloj koji te treba zaustaviti. Ne daje mišljenje, ne procjenjuje, ne
ublažava: uspoređuje brojke s pragovima koje si zapisao unaprijed i javlja
odstupanja, citirajući pravilo.

Mjeri se CIJELI portfelj — T212 iz portfolio.db plus kripto i ZSE iz
state/*.json, isti izvori kao dashboard. Bez toga bi "kripto max 15 %" gledao
krivi nazivnik, a BTC uopće ne bi vidio.

    python3 rules.py provjeri                  # stanje protiv pravila
    python3 rules.py kupnja BRK_B_US_EQ 500    # bi li ta kupnja prekršila pravilo
    python3 rules.py pravila                   # ispiši pravila (za citiranje)
    python3 rules.py provjeri --json           # strojno čitljivo

Sve se otvara read-only. Kredencijali se ne diraju.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

RULES_PATH = Path(__file__).parent / "rules.yaml"
DEFAULT_DB = Path(__file__).parent / "portfolio.db"


class PravilaGreska(Exception):
    """Pravila se ne mogu pročitati ili su nepotpuna. Radije stani nego pogodi."""


# ── Učitavanje ───────────────────────────────────────────────────────────────

def ucitaj_pravila(putanja=RULES_PATH) -> dict:
    """PyYAML ako postoji, inače strogi mini-parser. Oba daju isti rezultat."""
    putanja = Path(putanja)
    if not putanja.is_file():
        raise PravilaGreska(f"Nema {putanja}")
    try:
        import yaml  # noqa: PLC0415
        with open(putanja, encoding="utf-8") as f:
            pravila = yaml.safe_load(f)
    except ImportError:
        import miniyaml  # noqa: PLC0415
        pravila = miniyaml.ucitaj_datoteku(putanja)

    for kljuc in ("klasifikacija", "kategorije", "osnovica"):
        if kljuc not in pravila:
            raise PravilaGreska(f"rules.yaml nema obavezni ključ '{kljuc}'")

    nepoznate = set(pravila["klasifikacija"].values()) - set(pravila["kategorije"])
    if nepoznate:
        raise PravilaGreska(
            f"Klasifikacija koristi kategorije koje nisu definirane: "
            f"{', '.join(sorted(nepoznate))}"
        )
    return pravila


# ── Stanje portfelja ─────────────────────────────────────────────────────────

def _check_delte(db_path: Path) -> dict[str, float | None]:
    """check_delta_pct po tickeru iz zadnjeg T212 snapshota. Kripto/ZSE ga nemaju."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        snap = conn.execute(
            "SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not snap:
            return {}
        return {
            r["ticker"]: r["check_delta_pct"]
            for r in conn.execute(
                "SELECT ticker, check_delta_pct FROM positions WHERE snapshot_id = ?",
                (snap["id"],))
        }
    finally:
        conn.close()


def stanje(db_path: str | Path = DEFAULT_DB,
           state_dir: str | Path | None = None,
           pravila: dict | None = None,
           rules_path: str | Path = RULES_PATH) -> dict:
    """Cijeli portfelj (T212 + kripto + ZSE) kroz view.assemble().

    Pragovi se mjere na istom nazivniku koji vidi dashboard — T212-ov udio
    unutar T212-a nije udio u portfelju. `taken_at` je NAJSTARIJI as_of
    dostupnih izvora, pa upozorenje o starosti reagira čim bilo koji izvor
    zastari. Sve read-only.
    """
    # Uvoz unutar funkcije: view uvozi rules, pa bi uvoz na razini modula
    # zatvorio krug.
    import view  # noqa: PLC0415
    from position import ViewGreska  # noqa: PLC0415

    if pravila is None:
        pravila = ucitaj_pravila(rules_path)
    db_path = Path(db_path)
    state_dir = Path(state_dir) if state_dir else view.DEFAULT_STATE

    try:
        v = view.assemble(db_path, state_dir, pravila)
    except ViewGreska as e:
        raise PravilaGreska(str(e)) from e

    delte = _check_delte(db_path)
    dostupni = [s for s in v.sources if s.available and s.as_of]

    return {
        "taken_at": min((s.as_of for s in dostupni), default=None),
        "ukupna_vrijednost": v.total_value_eur,
        "samo_pozicije": v.positions_value_eur,
        "cash_eur": v.cash_eur,
        "izvori": [
            {"izvor": s.source, "svjezina": s.freshness, "as_of": s.as_of,
             "dostupan": s.available, "greska": s.error}
            for s in v.sources
        ],
        "pozicije": [
            {"ticker": p.ticker, "naziv": p.name, "valuta": p.currency,
             "eur": p.value_eur, "izvor": p.source,
             "check_delta_pct": delte.get(p.ticker)}
            for p in v.positions
        ],
    }


# ── Nalazi ───────────────────────────────────────────────────────────────────

@dataclass
class Nalaz:
    ozbiljnost: str   # "krsenje" | "upozorenje"
    pravilo: str      # citat pravila, doslovno
    predmet: str      # ticker ili kategorija
    izmjereno: float
    granica: float | None
    poruka: str

    def redak(self) -> str:
        oznaka = "KRŠENJE " if self.ozbiljnost == "krsenje" else "upozorenje"
        return f"[{oznaka}] {self.poruka}\n            pravilo: {self.pravilo}"


def _postotak(eur: float, osnovica_eur: float) -> float:
    return 100.0 * eur / osnovica_eur if osnovica_eur else 0.0


def _kategorija(ticker: str, pravila: dict) -> str:
    kat = pravila["klasifikacija"].get(ticker)
    if not kat:
        raise PravilaGreska(
            f"Ticker '{ticker}' nije klasificiran u rules.yaml. "
            f"Dodaj ga pod 'klasifikacija:' — bez toga se ne zna koji prag vrijedi."
        )
    return kat


def provjeri(st: dict, pravila: dict) -> list[Nalaz]:
    osnovica_kljuc = pravila["osnovica"]
    osnovica_eur = st.get(osnovica_kljuc)
    if not osnovica_eur:
        raise PravilaGreska(f"Osnovica '{osnovica_kljuc}' je prazna ili nepoznata")

    nalazi: list[Nalaz] = []
    kategorije = pravila["kategorije"]

    # nesvrstani tickeri — glasno, prije bilo kakve provjere
    nesvrstani = [p["ticker"] for p in st["pozicije"]
                  if p["ticker"] not in pravila["klasifikacija"]]
    if nesvrstani:
        raise PravilaGreska(
            f"Nesvrstani tickeri: {', '.join(nesvrstani)}. "
            f"Dodaj ih u 'klasifikacija:' u rules.yaml prije provjere."
        )

    # 1. max po pojedinoj poziciji
    for p in st["pozicije"]:
        kat = _kategorija(p["ticker"], pravila)
        granica = kategorije[kat].get("max_po_poziciji_pct")
        if granica is None:
            continue
        pct = _postotak(p["eur"], osnovica_eur)
        if pct > granica:
            nalazi.append(Nalaz(
                ozbiljnost="krsenje",
                pravilo=f"kategorije.{kat}.max_po_poziciji_pct = {granica}",
                predmet=p["ticker"],
                izmjereno=round(pct, 2),
                granica=float(granica),
                poruka=(f"{p['ticker']} je {pct:.1f} % portfelja, "
                        f"a granica za '{kat}' je {granica} %."),
            ))

    # 2. max po kategoriji ukupno
    zbroj: dict[str, float] = {}
    for p in st["pozicije"]:
        kat = _kategorija(p["ticker"], pravila)
        zbroj[kat] = zbroj.get(kat, 0.0) + p["eur"]

    for kat, konf in kategorije.items():
        granica = konf.get("max_ukupno_pct")
        if granica is None:
            continue
        pct = _postotak(zbroj.get(kat, 0.0), osnovica_eur)
        if pct > granica:
            nalazi.append(Nalaz(
                ozbiljnost="krsenje",
                pravilo=f"kategorije.{kat}.max_ukupno_pct = {granica}",
                predmet=kat,
                izmjereno=round(pct, 2),
                granica=float(granica),
                poruka=(f"Kategorija '{kat}' je ukupno {pct:.1f} % portfelja, "
                        f"a granica je {granica} %."),
            ))

    ostalo = pravila.get("ostalo") or {}

    # 3. cash rezerva
    min_cash = ostalo.get("min_cash_eur")
    if min_cash is not None and (st["cash_eur"] or 0) < min_cash:
        nalazi.append(Nalaz(
            ozbiljnost="upozorenje",
            pravilo=f"ostalo.min_cash_eur = {min_cash}",
            predmet="cash",
            izmjereno=round(st["cash_eur"] or 0, 2),
            granica=float(min_cash),
            poruka=(f"Cash je {st['cash_eur']:.2f} EUR, "
                    f"ispod zadanog minimuma od {min_cash} EUR."),
        ))

    # 4. starost podataka
    max_sati = ostalo.get("max_starost_sati")
    if max_sati is not None:
        try:
            uzeto = datetime.strptime(st["taken_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
            sati = (datetime.now(timezone.utc) - uzeto).total_seconds() / 3600
            if sati > max_sati:
                nalazi.append(Nalaz(
                    ozbiljnost="upozorenje",
                    pravilo=f"ostalo.max_starost_sati = {max_sati}",
                    predmet="snapshot",
                    izmjereno=round(sati, 1),
                    granica=float(max_sati),
                    poruka=(f"Zadnji snapshot je star {sati:.0f} h "
                            f"(granica {max_sati} h). Provjeri cron: "
                            f"tail /opt/zarko/snapshot.log"),
                ))
        except (ValueError, TypeError):
            pass

    # 5. FX nesklad — brojke same po sebi sumnjive
    for p in st["pozicije"]:
        d = p.get("check_delta_pct")
        if d is not None and abs(d) > 1.0:
            nalazi.append(Nalaz(
                ozbiljnost="upozorenje",
                pravilo="dvije metode izračuna moraju se slagati unutar 1 %",
                predmet=p["ticker"],
                izmjereno=round(d, 2),
                granica=1.0,
                poruka=(f"{p['ticker']} [{p['valuta']}]: metode se razilaze "
                        f"{d:+.1f} %. Provjeri valutu prije nego što vjeruješ iznosu."),
            ))

    return nalazi


def simuliraj_kupnju(st: dict, pravila: dict, ticker: str,
                     iznos_eur: float) -> tuple[list[Nalaz], list[Nalaz]]:
    """Što bi ta kupnja napravila. Vraća (nalazi_prije, nalazi_poslije).

    Pretpostavka: novi novac ulazi izvana, pa ukupna vrijednost raste za iznos.
    To je scenarij "da dokupim još X?", koji je i najčešći trenutak za FOMO.
    """
    if iznos_eur <= 0:
        raise PravilaGreska("Iznos kupnje mora biti veći od nule.")
    if ticker not in pravila["klasifikacija"]:
        raise PravilaGreska(
            f"Ticker '{ticker}' nije u rules.yaml. Prije kupnje ga svrstaj u "
            f"kategoriju — inače se ne zna koji prag za njega vrijedi."
        )

    prije = provjeri(st, pravila)

    poslije_st = json.loads(json.dumps(st))  # duboka kopija
    nadeno = False
    for p in poslije_st["pozicije"]:
        if p["ticker"] == ticker:
            p["eur"] += iznos_eur
            nadeno = True
            break
    if not nadeno:
        poslije_st["pozicije"].append({
            "ticker": ticker, "naziv": "(nova pozicija)", "valuta": None,
            "eur": iznos_eur, "check_delta_pct": None,
        })
    poslije_st["ukupna_vrijednost"] += iznos_eur
    poslije_st["samo_pozicije"] += iznos_eur

    return prije, provjeri(poslije_st, pravila)


# ── CLI ──────────────────────────────────────────────────────────────────────

_IZVOR_LABEL = {"t212": "T212", "crypto": "Kripto", "zse": "ZSE"}


def _izvori_redak(st: dict) -> str:
    dijelovi = []
    for s in st.get("izvori") or []:
        label = _IZVOR_LABEL.get(s["izvor"], s["izvor"])
        if not s["dostupan"]:
            dijelovi.append(f"{label} n/d")
        else:
            kad = (s["as_of"] or "")[:16].replace("T", " ")
            dijelovi.append(f"{label} {s['svjezina']} {kad}")
    return " · ".join(dijelovi)


def _ispisi(nalazi: list[Nalaz], naslov: str) -> None:
    print(naslov)
    if not nalazi:
        print("  Sva pravila zadovoljena.")
        return
    for n in nalazi:
        print("  " + n.redak())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("naredba", nargs="?", default="provjeri",
                   choices=["provjeri", "kupnja", "pravila"])
    p.add_argument("ticker", nargs="?")
    p.add_argument("iznos", nargs="?", type=float)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--state", default=None,
                   help="mapa s crypto.json i zse.json (zadano state/ uz kod)")
    p.add_argument("--rules", default=str(RULES_PATH))
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    try:
        pravila = ucitaj_pravila(a.rules)

        if a.naredba == "pravila":
            if a.json:
                json.dump(pravila, sys.stdout, indent=2, ensure_ascii=False)
                print()
            else:
                for kat, konf in pravila["kategorije"].items():
                    print(f"{kat}: {konf.get('opis', '')}")
                    print(f"  max po poziciji: {konf.get('max_po_poziciji_pct')} %")
                    print(f"  max ukupno:      {konf.get('max_ukupno_pct')} %")
                print("\nNačela:")
                for n in pravila.get("nacela") or []:
                    print(f"  - {n}")
            return

        st = stanje(a.db, a.state, pravila=pravila)

        if a.naredba == "provjeri":
            nalazi = provjeri(st, pravila)
            if a.json:
                json.dump({"snapshot": st["taken_at"],
                           "ukupno_eur": st["ukupna_vrijednost"],
                           "izvori": st["izvori"],
                           "nalazi": [asdict(n) for n in nalazi]},
                          sys.stdout, indent=2, ensure_ascii=False)
                print()
            else:
                _ispisi(nalazi, f"Pravila — {_izvori_redak(st)}")
            sys.exit(1 if any(n.ozbiljnost == "krsenje" for n in nalazi) else 0)

        # kupnja
        if not a.ticker or a.iznos is None:
            sys.exit("Upotreba: python3 rules.py kupnja TICKER IZNOS_EUR")
        prije, poslije = simuliraj_kupnju(st, pravila, a.ticker, a.iznos)
        novi = [n for n in poslije
                if not any(s.pravilo == n.pravilo and s.predmet == n.predmet
                           for s in prije)]
        if a.json:
            json.dump({"ticker": a.ticker, "iznos_eur": a.iznos,
                       "novi_nalazi": [asdict(n) for n in novi],
                       "postojeci_nalazi": [asdict(n) for n in prije]},
                      sys.stdout, indent=2, ensure_ascii=False)
            print()
        else:
            print(f"Kupnja {a.iznos:.2f} EUR u {a.ticker}:")
            if novi:
                print("  Ta kupnja bi prekršila:")
                for n in novi:
                    print("    " + n.redak())
            else:
                print("  Ne krši nijedno pravilo.")
            if prije:
                print("\n  Već postojeći nalazi (neovisno o ovoj kupnji):")
                for n in prije:
                    print("    " + n.redak())
        sys.exit(1 if novi else 0)

    except PravilaGreska as e:
        sys.exit(f"Greška u pravilima — {e}")


if __name__ == "__main__":
    main()
