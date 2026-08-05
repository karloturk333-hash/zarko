"""Validacija onoga što model vrati.

Model nije izvor istine — on je izvor teksta. Ovaj modul stoji između njega i
svega ostalog i propušta samo ono što ima točan oblik i nijednu brojku koju
kod nije sam izračunao.

Tri sloja, svaki hvata drugu vrstu promašaja:

    parsiraj()               je li odgovor uopće struktura
    provjeri_tezu()          je li struktura ONA koju smo tražili
    bez_izmisljenih_brojki() ima li u njoj brojka koje nema u izvoru

trazi_valjano() ih spaja u petlju s ponavljanjem: kad nešto padne, poruka
greške ide natrag modelu kao dio sljedećeg upita.

Bez vanjskih ovisnosti, kao i ostatak projekta.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable


class Nevaljano(Exception):
    """Model je vratio nešto što ne prolazi provjeru.

    Poruka mora biti dovoljna MODELU da se ispravi, jer se doslovno šalje
    natrag u sljedećem pokušaju. "greška u validaciji" nije dovoljno — iz
    toga model ne zna što promijeniti. "Polje 'sigurnost' ima vrijednost
    'vrlo visoka', a dozvoljeno je: niska, srednja, visoka." jest.
    """


# ── 1. sloj: je li odgovor uopće struktura ───────────────────────────────────


def parsiraj(tekst: str) -> dict:
    """Izvuci JSON objekt iz odgovora modela.

    Mora podnijeti sva tri oblika u kojima model to zna vratiti:

        '{"a": 1}'                                    -> {"a": 1}
        '```json\\n{"a": 1}\\n```'                     -> {"a": 1}
        'Evo analize:\\n{"a": 1}\\nNadam se da pomaže'  -> {"a": 1}

    Sve ostalo baca Nevaljano. Neispravan JSON se NE popravlja — bez regexa
    koji briše zareze ili dodaje navodnike. Isti princip kao u miniyaml.py:
    krivo pročitano je gore od nepročitanog, jer krivo pročitano ide dalje
    kroz sustav i nitko ne zna da je krivo.
    """
    if not isinstance(tekst, str) or not tekst.strip():
        raise Nevaljano("Prazan odgovor — očekivan je JSON objekt.")

    pocetak = tekst.find("{")
    kraj = tekst.rfind("}")

    if pocetak == -1 or kraj == -1:
        raise Nevaljano(
            "U odgovoru nema vitičastih zagrada — očekivan je JSON objekt "
            "oblika {\"kljuc\": \"vrijednost\"}."
        )

    # Ono što je ostalo izvan zagrada. Kod proze ('Evo analize: ...') i kod
    # ``` bloka to je bezopasno, ali kod '[{...}]' su to uglate zagrade —
    # znak da je model vratio LISTU objekata, a rez bi je tiho progutao.
    lijevo = tekst[:pocetak]
    desno = tekst[kraj + 1:]

    if lijevo.strip().endswith("[") or desno.strip().startswith("]"):
        raise Nevaljano(
            "Odgovor je lista — očekivan je jedan JSON objekt, bez uglatih zagrada."
        )

    kandidat = tekst[pocetak:kraj + 1]

    try:
        podaci = json.loads(kandidat)
    except json.JSONDecodeError as e:
        raise Nevaljano(
            f"JSON nije valjan: {e}. Vrati isključivo ispravan JSON objekt."
        ) from e

    return podaci


# ── 2. sloj: shema teze ──────────────────────────────────────────────────────
# Teza je ono što je agent tvrdio o nekoj poziciji, na određeni datum. Zapisuje
# se u bazu da se za pola godine može provjeriti je li se obistinila — inače je
# to samo razgovor koji nestane u Telegram povijesti.
#
# Granice stoje ovdje, a ne zakopane u kodu, jer su to odluke — a odluke se
# mijenjaju i moraju se moći pročitati bez čitanja logike. Isti razlog zbog
# kojeg pragovi portfelja žive u rules.yaml, a ne u rules.py.

SIGURNOST = ("niska", "srednja", "visoka")
MAX_TEKST = 300

KLJUCEVI = (
    "ticker",
    "teza",
    "protuteza",
    "sto_bi_promijenilo_misljenje",
    "sigurnost",
)

# Sva polja osim 'sigurnost' su slobodan tekst i provjeravaju se isto.
TEKSTUALNA = ("ticker", "teza", "protuteza", "sto_bi_promijenilo_misljenje")


def _tekst(vrijednost, ime: str, najvise: int = MAX_TEKST) -> str:
    """Provjeri da je vrijednost neprazan tekst do 'najvise' znakova, i vrati ga.

    Ime polja se prosljeđuje jer bez njega model ne zna KOJE polje je krivo.
    """
    if not isinstance(vrijednost, str):
        raise Nevaljano(
            f"Polje '{ime}' mora biti tekst, a dobiveno je "
            f"{type(vrijednost).__name__}."
        )
    vrijednost = vrijednost.strip()
    if not vrijednost:
        raise Nevaljano(f"Polje '{ime}' je prazno.")
    if len(vrijednost) > najvise:
        raise Nevaljano(
            f"Polje '{ime}' ima {len(vrijednost)} znakova, "
            f"a dozvoljeno je najviše {najvise}."
        )
    return vrijednost


def provjeri_tezu(podaci: dict, dopusteni_tickeri: Iterable[str] | None = None) -> dict:
    """Provjeri da odgovor ima oblik teze i vrati očišćenu kopiju.

    Očekivani oblik — svih pet polja je obavezno:

        {
          "ticker": "MU_US_EQ",
          "teza": "Ciklus memorije se okrece, kapaciteti su ograniceni.",
          "protuteza": "Ciklicka industrija, marze padnu brzo i duboko.",
          "sto_bi_promijenilo_misljenje": "Pad cijena DRAM-a dva kvartala zaredom.",
          "sigurnost": "srednja"
        }

    'protuteza' je obavezna namjerno: teza bez protuteze je reklama, ne analiza.
    'sto_bi_promijenilo_misljenje' isto — bez njega za pola godine provjeravaš
    dojam umjesto konkretne tvrdnje.

    dopusteni_tickeri: ako je dan, ticker mora biti u njemu. Poziva se s
    ključevima iz rules.yaml (klasifikacija) — teza o nesvrstanom tickeru se
    nema gdje ni zapisati ni kasnije provjeriti.

    Vraća NOVI dict; ulazni se ne mijenja.
    """
    if not isinstance(podaci, dict):
        raise Nevaljano(
            f"Očekivan je JSON objekt, a dobiveno je {type(podaci).__name__}."
        )

    # Nepoznat ključ nije nešto što se tiho preskače: ako model napiše
    # "protuteze" umjesto "protuteza", tiho ignoriranje znači da ti fali
    # protuteza a nitko ne zna zašto. Bolje mu odmah reći da je promašio ime.
    dobiveni = set(podaci)
    ocekivani = set(KLJUCEVI)

    fale = ocekivani - dobiveni
    if fale:
        raise Nevaljano(
            f"Fale obavezna polja: {', '.join(sorted(fale))}. "
            f"Obavezna su sva: {', '.join(KLJUCEVI)}."
        )

    visak = dobiveni - ocekivani
    if visak:
        raise Nevaljano(
            f"Nepoznata polja: {', '.join(sorted(visak))}. "
            f"Dozvoljena su samo: {', '.join(KLJUCEVI)}."
        )

    cisto = {ime: _tekst(podaci[ime], ime) for ime in TEKSTUALNA}

    sigurnost = _tekst(podaci["sigurnost"], "sigurnost", najvise=20).lower()
    if sigurnost not in SIGURNOST:
        raise Nevaljano(
            f"Polje 'sigurnost' ima vrijednost '{podaci['sigurnost']}', "
            f"a dozvoljeno je: {', '.join(SIGURNOST)}."
        )
    cisto["sigurnost"] = sigurnost

    if dopusteni_tickeri is not None:
        dopusteni = set(dopusteni_tickeri)
        if cisto["ticker"] not in dopusteni:
            raise Nevaljano(
                f"Ticker '{cisto['ticker']}' nije poznat. "
                f"Dozvoljeni su: {', '.join(sorted(dopusteni))}."
            )

    return cisto


# ── 3. sloj: nijedna brojka ne smije biti modelova ───────────────────────────
# Pravilo 1 iz hermes/skills/portfelj.md ("nijedna brojka koju izgovoriš ne
# smije biti tvoja") do sada je bilo zamolba u promptu. Ovdje postaje provjera.

_BROJ_U_TEKSTU = re.compile(r"\d+(?:[.,]\d+)*")


def _normaliziraj_broj(zapis: str) -> str:
    """Svedi zapis broja na kanonski oblik, da se '2.290,39' i '2290.39' poklope.

    Bez ovoga bi usporedba ovisila o tome je li brojka prošla kroz hrvatsko
    formatiranje u report.py ili je došla ravno iz baze.
    """
    s = zapis.replace(" ", "").replace(" ", "")

    ima_tocku, ima_zarez = "." in s, "," in s
    if ima_tocku and ima_zarez:
        # Onaj koji je desnije je decimalni; drugi razdvaja tisućice.
        decimalni = "," if s.rfind(",") > s.rfind(".") else "."
        tisucice = "." if decimalni == "," else ","
        s = s.replace(tisucice, "").replace(decimalni, ".")
    elif ima_tocku or ima_zarez:
        znak = "," if ima_zarez else "."
        iza = s.rsplit(znak, 1)[-1]
        # Jedan separator i 1–2 znamenke iza njega je decimala (2,29 / 15.5).
        # Sve ostalo su tisućice (2.290 / 1,234,567).
        if s.count(znak) == 1 and len(iza) in (1, 2):
            s = s.replace(znak, ".")
        else:
            s = s.replace(znak, "")

    try:
        return f"{float(s):.10g}"
    except ValueError:
        return zapis


def dopustene_brojke(izvor) -> set[str]:
    """Skupi sve brojke iz determinističkog izvora, npr. `report.py --json`.

    Prima što god json.load vrati — dict, listu, broj, string — i prošeće kroz
    sve. Rezultat se predaje bez_izmisljenih_brojki kao popis dozvoljenog.
    """
    nadeno: set[str] = set()

    def hodaj(cvor) -> None:
        if isinstance(cvor, dict):
            for vrijednost in cvor.values():
                hodaj(vrijednost)
        elif isinstance(cvor, (list, tuple)):
            for element in cvor:
                hodaj(element)
        elif isinstance(cvor, bool):
            pass  # True/False su u Pythonu brojevi, ali nisu brojke
        elif isinstance(cvor, (int, float)):
            nadeno.add(_normaliziraj_broj(str(cvor)))
        elif isinstance(cvor, str):
            for pogodak in _BROJ_U_TEKSTU.findall(cvor):
                nadeno.add(_normaliziraj_broj(pogodak))

    hodaj(izvor)
    return nadeno


def bez_izmisljenih_brojki(teza: dict, dopustene: Iterable[str]) -> None:
    """Digni Nevaljano ako u tezi postoji brojka koje nema u 'dopustene'.

    Strogo namjerno: nema iznimke za "male" brojeve ni za godine. Model koji
    napiše "vec je 33 posto portfelja" mora tu brojku imati iz report.py, a ne
    iz vlastite procjene — a 33 je upravo veličina koja zvuči bezopasno i zato
    prođe nezapaženo.

    Posljedica koju treba znati: model mora pisati "dva kvartala", ne
    "2 kvartala". To se traži u promptu i to je prihvatljiva cijena.

    Ne vraća ništa — ili prođe, ili pukne.
    """
    dozvoljeno = {_normaliziraj_broj(str(x)) for x in dopustene}

    for polje in TEKSTUALNA:
        vrijednost = teza.get(polje)
        if not isinstance(vrijednost, str):
            continue
        for pogodak in _BROJ_U_TEKSTU.findall(vrijednost):
            if _normaliziraj_broj(pogodak) not in dozvoljeno:
                raise Nevaljano(
                    f"Polje '{polje}' sadrži brojku '{pogodak}' koje nema u "
                    f"izvornim podacima. Brojke se ne procjenjuju — koristi "
                    f"isključivo one iz izlaza report.py, ili ih izostavi."
                )


# ── Petlja s ponavljanjem ────────────────────────────────────────────────────


def _upit_s_ispravkom(upit: str, odgovor: str, greska: str) -> str:
    """Sastavi sljedeći upit: original + što je model vratio + što ne valja."""
    return (
        f"{upit}\n\n"
        f"--- Tvoj prethodni odgovor ---\n{odgovor}\n\n"
        f"--- Zašto nije prihvaćen ---\n{greska}\n\n"
        f"Vrati ispravljen odgovor. Isključivo JSON objekt, bez teksta oko njega."
    )


def trazi_valjano(
    pozovi_model: Callable[[str], str],
    upit: str,
    dopustene: Iterable[str] | None = None,
    dopusteni_tickeri: Iterable[str] | None = None,
    pokusaja: int = 3,
) -> dict:
    """Traži od modela tezu dok ne vrati valjanu, najviše 'pokusaja' puta.

    pozovi_model je funkcija (str) -> str. Namjerno se prosljeđuje izvana, a ne
    zove Anthropic API iznutra: tako testovi rade bez mreže i bez ključa, a
    funkcija je upotrebljiva s bilo kojim modelom ili gatewayem.

    Svaki neuspjeli pokušaj šalje modelu natrag njegov odgovor i razlog
    odbijanja — bez toga bi treći pokušaj bio jednako slijep kao prvi.

    Vraća očišćenu tezu ili baca Nevaljano s poviješću svih pokušaja. Nikad ne
    vraća None ni djelomičan rezultat: pozivatelj ne smije morati provjeravati
    je li dobio pravu stvar.
    """
    if pokusaja < 1:
        raise ValueError("pokusaja mora biti barem 1")

    povijest: list[str] = []
    trenutni = upit

    for redni in range(1, pokusaja + 1):
        odgovor = pozovi_model(trenutni)
        try:
            teza = provjeri_tezu(parsiraj(odgovor), dopusteni_tickeri)
            if dopustene is not None:
                bez_izmisljenih_brojki(teza, dopustene)
            return teza
        except Nevaljano as e:
            povijest.append(f"pokušaj {redni}: {e}")
            trenutni = _upit_s_ispravkom(upit, odgovor, str(e))

    raise Nevaljano(
        f"Model nije vratio valjanu tezu nakon {pokusaja} pokušaja.\n"
        + "\n".join(povijest)
    )
