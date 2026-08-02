#!/usr/bin/env python3
"""FX normalizacija — sve u EUR, jedna valuta u bazi, nula tihih grešaka.

Zašto postoji: Trading212 vraća cijene u valuti instrumenta. LSE dionice su
kotirane u PENIJIMA (T212 oznaka "GBX"), ne u funtama — zbrajanje GBX i EUR
iznosa daje 100× napuhanu poziciju. Isto vrijedi za ZAC (južnoafrički centi)
i ILA (izraelski agorot).

Pravila ovog modula:
  1. Nepoznata valuta => IZNIMKA, nikad tiha pretpostavka 1.0.
  2. Minor unit (peni/cent) se prepoznaje po TOČNOJ oznaci, bez .upper().
     "GBp".upper() == "GBP" — to je upravo greška koju sprječavamo.
  3. Tečajevi su ECB referentni (frankfurter.app, baza EUR, bez ključa).
     Zapisuju se u snapshot da se svaki izračun može rekonstruirati.

CLI:
    python3 fx_conversion.py              # ispiši dohvaćene tečajeve
    python3 fx_conversion.py 1234 GBX     # koliko je 1234 penija u EUR
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

# ECB referentni tečajevi direktno s izvora — baza EUR, bez ključa, bez posrednika.
# Objavljuju se radnim danom ~16:00 CET; vikendom/praznikom stoji zadnji objavljeni
# (zato uz tečajeve pamtimo i `date`).
# Preko FX_API_URL se može pokazati na JSON izvor u frankfurter formatu.
DEFAULT_FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

USER_AGENT = "zarko-portfolio/1.0 (osobni portfolio tracker)"

# Oznaka minor unita -> (major ISO oznaka, koliko minor unita ide u major).
# Ključevi se uspoređuju TOČNO, case-sensitive — vidi pravilo 2 gore.
MINOR_UNITS: dict[str, tuple[str, int]] = {
    "GBX": ("GBP", 100),  # britanski peni — T212 oznaka
    "GBx": ("GBP", 100),
    "GBp": ("GBP", 100),  # ista stvar, Yahoo/IB notacija
    "ZAC": ("ZAR", 100),  # južnoafrički centi
    "ZAc": ("ZAR", 100),
    "ILA": ("ILS", 100),  # izraelski agorot
    "ILa": ("ILS", 100),
}


class UnknownCurrency(Exception):
    """Valuta koju ne znamo pretvoriti. Namjerno glasna — ne konvertirati napamet."""


def resolve(code: str | None) -> tuple[str, int]:
    """Oznaka valute -> (major ISO oznaka, djelitelj).

    'USD' -> ('USD', 1) · 'GBX' -> ('GBP', 100) · 'GBp' -> ('GBP', 100)
    """
    if not code or not isinstance(code, str):
        raise UnknownCurrency(f"Prazna ili neispravna oznaka valute: {code!r}")
    code = code.strip()

    if code in MINOR_UNITS:
        return MINOR_UNITS[code]

    # Strogo: samo uredne velike ISO-4217 oznake. Sve ostalo (npr. 'gbp', 'GBP2')
    # radije pukne nego da se pogađa — tiha pretpostavka je ono što nas je koštalo.
    if not (len(code) == 3 and code.isalpha() and code.isupper()):
        raise UnknownCurrency(
            f"Nepoznata oznaka valute {code!r}. Ako je riječ o minor unitu "
            f"(peniji/centi/agoroti), dodaj je u MINOR_UNITS u fx_conversion.py. "
            f"NE pretvaraj je tiho u major valutu."
        )
    return code, 1


@dataclass
class FxConverter:
    """Tečajevi EUR -> X. Konverzija ide uvijek preko jedne točke: rate_to_eur()."""

    rates: dict[str, float]  # koliko X-a za 1 EUR
    date: str                # datum ECB objave
    source: str

    @classmethod
    def fetch(cls, url: str | None = None, timeout: int = 20) -> "FxConverter":
        url = url or os.environ.get("FX_API_URL", DEFAULT_FX_URL)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except (urllib.error.URLError, OSError) as e:
            raise UnknownCurrency(f"Dohvat tečajeva s {url} nije uspio: {e}") from None

        parse = cls._parse_ecb_xml if url.endswith(".xml") else cls._parse_json
        try:
            rates, date = parse(body)
        except (ET.ParseError, json.JSONDecodeError, KeyError, ValueError) as e:
            raise UnknownCurrency(f"Neispravan odgovor s {url}: {e}") from None

        if not rates:
            raise UnknownCurrency(f"Odgovor s {url} nema tečajeve")
        rates["EUR"] = 1.0
        return cls(rates=rates, date=date, source=url)

    @staticmethod
    def _parse_ecb_xml(body: bytes) -> tuple[dict[str, float], str]:
        """ECB eurofxref-daily.xml: ugniježđeni <Cube> elementi, baza je uvijek EUR."""
        root = ET.fromstring(body)
        rates, date = {}, ""
        for el in root.iter():
            if not el.tag.endswith("Cube"):
                continue
            if "time" in el.attrib:
                date = el.attrib["time"]
            if "currency" in el.attrib and "rate" in el.attrib:
                rates[el.attrib["currency"]] = float(el.attrib["rate"])
        return rates, date

    @staticmethod
    def _parse_json(body: bytes) -> tuple[dict[str, float], str]:
        """Frankfurter format: {"base": "EUR", "date": "...", "rates": {...}}."""
        data = json.loads(body.decode())
        base = data.get("base", "EUR")
        if base != "EUR":
            raise ValueError(f"očekivana baza EUR, dobiveno {base!r}")
        return dict(data.get("rates") or {}), data.get("date", "")

    @classmethod
    def from_rates(cls, rates: dict, date: str = "", source: str = "cache") -> "FxConverter":
        """Rekonstrukcija iz spremljenih tečajeva (fallback kad je FX API nedostupan)."""
        rates = dict(rates)
        rates.setdefault("EUR", 1.0)
        return cls(rates=rates, date=date, source=source)

    def rate_to_eur(self, currency: str | None) -> float:
        """Koliko EUR vrijedi 1 jedinica `currency` (uključujući /100 za penije)."""
        major, divisor = resolve(currency)
        rate = self.rates.get(major)
        if not rate:
            raise UnknownCurrency(
                f"Nema ECB tečaja za {major} (izvor: {self.source}, datum: {self.date}). "
                f"Provjeri je li valuta uopće u ECB setu."
            )
        return 1.0 / (rate * divisor)

    def to_eur(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None:
            return None
        return amount * self.rate_to_eur(currency)


def _cli(argv: list[str]) -> int:
    fx = FxConverter.fetch()
    if len(argv) == 2:
        amount, currency = float(argv[0].replace(",", ".")), argv[1]
        eur = fx.to_eur(amount, currency)
        major, divisor = resolve(currency)
        note = f" ({amount / divisor:g} {major})" if divisor != 1 else ""
        print(f"{amount:g} {currency}{note} = {eur:.2f} EUR   [ECB {fx.date}]")
    else:
        print(f"ECB tečajevi, baza EUR, datum {fx.date}")
        for code in sorted(fx.rates):
            print(f"  1 EUR = {fx.rates[code]:>12.6f} {code}")
        print("\nMinor uniti koje prepoznajemo:")
        for code, (major, div) in sorted(MINOR_UNITS.items()):
            print(f"  {code} = 1/{div} {major}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_cli(sys.argv[1:]))
    except UnknownCurrency as e:
        sys.exit(f"FX greška — {e}")
