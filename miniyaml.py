"""Minimalni YAML čitač za rules.yaml — bez dependencyja.

Podržava TOČNO ono što rules.yaml koristi:
  - komentare (# do kraja retka, i pune retke)
  - ugniježđene mape preko uvlake (2 razmaka)
  - skalare: int, float, null/~, true/false, string (s navodnicima ili bez)
  - liste skalara (- vrijednost)

Sve ostalo baca iznimku. To je namjerno: pravilo koje se krivo pročita je gore
od pravila koje se ne pročita. Ako PyYAML postoji, rules.py koristi njega —
ovo je zamjena kad ga nema.
"""

from __future__ import annotations

import re


class YamlGreska(Exception):
    """Redak koji parser ne razumije. Nikad ne pogađaj — javi i stani."""


_BROJ = re.compile(r"^-?\d+$")
_DECIMALA = re.compile(r"^-?\d+\.\d+$")


def _skalar(raw: str, redak_br: int):
    s = raw.strip()
    if not s:
        return None
    if (s[0] == '"' and s[-1] == '"' and len(s) > 1) or \
       (s[0] == "'" and s[-1] == "'" and len(s) > 1):
        return s[1:-1]
    low = s.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _BROJ.match(s):
        return int(s)
    if _DECIMALA.match(s):
        return float(s)
    if s[0] in "[{|>&*!%":
        raise YamlGreska(
            f"redak {redak_br}: '{s[:30]}' koristi YAML sintaksu koju ovaj parser "
            f"namjerno ne podržava (inline liste/mape, blokovi, sidra). "
            f"Prepiši ga jednostavnije ili instaliraj PyYAML."
        )
    return s


def _skini_komentar(linija: str) -> str:
    """Ukloni # komentar, ali ne unutar navodnika."""
    out, u_navodnicima, znak = [], False, ""
    for c in linija:
        if u_navodnicima:
            if c == znak:
                u_navodnicima = False
        elif c in "\"'":
            u_navodnicima, znak = True, c
        elif c == "#":
            break
        out.append(c)
    return "".join(out).rstrip()


def ucitaj(tekst: str) -> dict:
    korijen: dict = {}
    # stog (uvlaka, spremnik)
    stog: list[tuple[int, dict | list]] = [(-1, korijen)]

    for br, sirova in enumerate(tekst.splitlines(), 1):
        linija = _skini_komentar(sirova)
        if not linija.strip():
            continue

        # Uvlaka se mjeri SAMO razmacima. Tabulator u uvlaci je greška, ne
        # ekvivalent razmaka — inače bi dubina gnijezda ovisila o postavkama
        # editora, a s njom i to koji prag pripada kojoj kategoriji.
        vodece = linija[:len(linija) - len(linija.lstrip())]
        if "\t" in vodece:
            raise YamlGreska(f"redak {br}: tabulator u uvlaci — koristi razmake")
        uvlaka = len(vodece)
        sadrzaj = linija.strip()

        while len(stog) > 1 and uvlaka <= stog[-1][0]:
            stog.pop()
        spremnik = stog[-1][1]

        # element liste
        if sadrzaj.startswith("- "):
            if not isinstance(spremnik, list):
                raise YamlGreska(f"redak {br}: '-' izvan liste")
            spremnik.append(_skalar(sadrzaj[2:], br))
            continue

        if ":" not in sadrzaj:
            raise YamlGreska(f"redak {br}: '{sadrzaj[:40]}' nije 'kljuc: vrijednost'")

        kljuc, _, ostatak = sadrzaj.partition(":")
        kljuc = kljuc.strip().strip("\"'")
        if not kljuc:
            raise YamlGreska(f"redak {br}: prazan ključ")
        if not isinstance(spremnik, dict):
            raise YamlGreska(f"redak {br}: ključ '{kljuc}' unutar liste")
        if kljuc in spremnik:
            raise YamlGreska(f"redak {br}: ključ '{kljuc}' se ponavlja")

        ostatak = ostatak.strip()
        if ostatak:
            spremnik[kljuc] = _skalar(ostatak, br)
        else:
            # gnijezdo: mapa ili lista, ovisno o sljedećem nepraznom retku
            novi: dict | list = {}
            for buduca in tekst.splitlines()[br:]:
                b = _skini_komentar(buduca)
                if not b.strip():
                    continue
                b_uvlaka = len(b) - len(b.lstrip(" "))
                if b_uvlaka <= uvlaka:
                    break
                if b.strip().startswith("- "):
                    novi = []
                break
            spremnik[kljuc] = novi
            stog.append((uvlaka, novi))

    return korijen


def ucitaj_datoteku(putanja) -> dict:
    with open(putanja, encoding="utf-8") as f:
        return ucitaj(f.read())
