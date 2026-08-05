"""Validacija onoga što model vrati.

Model nije izvor istine — on je izvor teksta. Ovaj modul stoji između njega i
svega ostalog i propušta samo ono što ima točan oblik i nijednu brojku koju
kod nije sam izračunao.

Bez vanjskih ovisnosti, kao i ostatak projekta.
"""

from __future__ import annotations

import json


class Nevaljano(Exception):
    """Model je vratio nešto što ne prolazi provjeru.

    Poruka mora biti dovoljna MODELU da se ispravi, jer se doslovno šalje
    natrag u sljedećem pokušaju. "greška u validaciji" nije dovoljno — iz
    toga model ne zna što promijeniti. "Polje 'sigurnost' ima vrijednost
    'vrlo visoka', a dozvoljeno je: niska, srednja, visoka." jest.
    """


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
    # 1. Prazan ili nestring ulaz.
    #    Ovaj korak ti je napisan do kraja, kao primjer stila: provjera na
    #    vrhu funkcije, konkretna poruka, iznimka umjesto tihog None.
    if not isinstance(tekst, str) or not tekst.strip():
        raise Nevaljano("Prazan odgovor — očekivan je JSON objekt.")

    # 2. TODO: izdvoji kandidata za JSON iz teksta.
    #
    #    Dva slučaja koja moraš pokriti:
    #      a) tekst sadrži ``` blok  -> uzmi ono između prve i zadnje ```
    #         (oznaka jezika iza prvog ``` može biti "json" ili je nema)
    #      b) nema bloka             -> uzmi od prve '{' do zadnje '}'
    #
    #    Zašto zadnja '}' a ne prva: JSON objekt s ugniježđenim objektom ima
    #    više '}' i prva zatvara unutarnji. Isprobaj sam na
    #    '{"a": {"b": 1}, "c": 2}' i vidi što dobiješ svakom varijantom.
    #
    #    Ako nema ni '{' ni '}' — Nevaljano, s porukom da odgovor mora biti
    #    JSON objekt.
    #
    #    Korisno: str.find() vraća indeks prvog pojavljivanja ili -1,
    #    str.rfind() isto to od kraja, a tekst[i:j+1] je izrezani komad.
    # <- tvoj posao

    pocetak = tekst.find('{')
    kraj = tekst.rfind('}')
    
    if pocetak == -1 or kraj == -1:
        raise Nevaljano("JSON nema viticaste zagrade")
    
    lijevo = tekst[:pocetak]        # sve prije prve {
    desno = tekst[kraj + 1:]

    if lijevo.strip().endswith('[') or desno.strip().startswith(']'):
        raise Nevaljano("Vratio si listu, ocekuje se JSON")
    kandidat =  tekst[pocetak : kraj+1] 
       
                
    # 3. TODO: json.loads(kandidat) unutar try/except json.JSONDecodeError.
    #
    #    U exceptu digni Nevaljano i UKLJUČI originalnu grešku u poruku —
    #    json ti kaže redak i stupac gdje je puklo, a to je upravo ono što
    #    modelu treba da se popravi. Nemoj to progutati.
    #
    #    Koristi 'raise Nevaljano(...) from e' — inače u tracebacku izgubiš
    #    uzrok i ostaje ti samo tvoja poruka.
    try:
        podaci = json.loads(kandidat)
    except json.JSONDecodeError as e:
        raise Nevaljano(f"JSON nije valjan: {e}") from e

    


    return podaci
