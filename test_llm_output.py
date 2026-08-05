#!/usr/bin/env python3
"""Testovi za validaciju LLM izlaza.

    python3 -m unittest test_llm_output -v
"""

from __future__ import annotations

import json
import unittest

import llm_output
from llm_output import Nevaljano


class TestParsiraj(unittest.TestCase):

    def test_goli_json(self):
        self.assertEqual(llm_output.parsiraj('{"a": 1}'), {"a": 1})

    def test_json_u_fence_bloku(self):
        tekst = '```json\n{"a": 1, "b": "dva"}\n```'
        self.assertEqual(llm_output.parsiraj(tekst), {"a": 1, "b": "dva"})

    def test_json_s_tekstom_okolo(self):
        tekst = 'Evo analize portfolia:\n{"a" : 1, "b" : 2}\n nadam se da je dobro'
        self.assertEqual(llm_output.parsiraj(tekst), {"a": 1, "b": 2})

    def test_ugnijezdeni_objekt(self):
        # rfind, ne find: prva '}' zatvara unutarnji objekt
        tekst = '{"a" : {"b":1}, "c":2}'
        self.assertEqual(llm_output.parsiraj(tekst), {"a": {"b": 1}, "c": 2})

    def test_neispravan_json_puca(self):
        # višak zareza — mora dići Nevaljano, ne popraviti
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj('{"a": 1,}')

    def test_prazan_ulaz_puca(self):
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj("")
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj("   ")

    def test_lista_na_vrhu_puca(self):
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj("[1, 2]")
        # ovaj je opasniji: rez od '{' do '}' bi tiho izvukao objekt iz liste
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj('[{"a": 1}]')

    def test_bez_ijedne_viticaste_puca(self):
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj("Nemam podataka za tu analizu.")

    def test_poruka_greske_je_korisna(self):
        # Poruka ide natrag modelu, pa mora reći ŠTO je falilo.
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.parsiraj("Ovo nije json")
        self.assertIn("JSON", str(ctx.exception))


def teza(**izmjene):
    """Ispravna teza. Imenovanim argumentom prepišeš jedno polje.

        teza()                  -> ispravna
        teza(sigurnost="jako")  -> ista, ali s neispravnom sigurnošću

    Isti obrazac kao stanje() u test_rules.py: svaki test mijenja jednu stvar,
    pa se iz testa odmah vidi ŠTO se testira.
    """
    osnova = {
        "ticker": "MU_US_EQ",
        "teza": "Ciklus memorije se okrece, kapaciteti su ograniceni.",
        "protuteza": "Ciklicka industrija, marze padnu brzo i duboko.",
        "sto_bi_promijenilo_misljenje": "Pad cijena DRAM-a dva kvartala zaredom.",
        "sigurnost": "srednja",
    }
    osnova.update(izmjene)
    return osnova


class TestProvjeriTezu(unittest.TestCase):

    def test_ispravna_prolazi(self):
        rezultat = llm_output.provjeri_tezu(teza())
        self.assertEqual(set(rezultat), set(llm_output.KLJUCEVI))
        self.assertEqual(rezultat["ticker"], "MU_US_EQ")
        self.assertEqual(rezultat["sigurnost"], "srednja")

    def test_nije_dict(self):
        for ulaz in ([1, 2], "tekst", 42, None):
            with self.assertRaises(Nevaljano, msg=f"{ulaz!r} je prosao"):
                llm_output.provjeri_tezu(ulaz)

    def test_fali_obavezno_polje(self):
        for polje in llm_output.KLJUCEVI:
            nepotpuna = teza()
            del nepotpuna[polje]
            with self.assertRaises(Nevaljano, msg=f"bez '{polje}' je prosao") as ctx:
                llm_output.provjeri_tezu(nepotpuna)
            self.assertIn(polje, str(ctx.exception))

    def test_visak_polja_puca(self):
        # model je napisao 'protuteze' umjesto 'protuteza' — tiho ignoriranje
        # bi značilo da ti fali protuteza, a nitko ne zna zašto
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.provjeri_tezu(teza(preporuka="kupi"))
        self.assertIn("preporuka", str(ctx.exception))

    def test_prazno_polje_puca(self):
        with self.assertRaises(Nevaljano):
            llm_output.provjeri_tezu(teza(protuteza=""))
        with self.assertRaises(Nevaljano):
            llm_output.provjeri_tezu(teza(protuteza="    "))

    def test_predugacko_polje_puca(self):
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.provjeri_tezu(teza(teza="x" * (llm_output.MAX_TEKST + 1)))
        self.assertIn("teza", str(ctx.exception))

    def test_krivi_tip_polja_puca(self):
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.provjeri_tezu(teza(teza=42))
        self.assertIn("int", str(ctx.exception))

    def test_nepoznata_sigurnost_puca(self):
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.provjeri_tezu(teza(sigurnost="vrlo visoka"))
        poruka = str(ctx.exception)
        # poruka mora nabrojati dozvoljene vrijednosti, inače model pogađa
        for dozvoljena in llm_output.SIGURNOST:
            self.assertIn(dozvoljena, poruka)

    def test_sigurnost_velikim_slovima_prolazi(self):
        rezultat = llm_output.provjeri_tezu(teza(sigurnost="VISOKA"))
        self.assertEqual(rezultat["sigurnost"], "visoka")

    def test_razmaci_se_skidaju(self):
        rezultat = llm_output.provjeri_tezu(teza(ticker="  MU_US_EQ  "))
        self.assertEqual(rezultat["ticker"], "MU_US_EQ")

    def test_nepoznat_ticker_puca(self):
        dopusteni = {"VWCEd_EQ", "BRK_B_US_EQ"}
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.provjeri_tezu(teza(), dopusteni_tickeri=dopusteni)
        self.assertIn("MU_US_EQ", str(ctx.exception))

    def test_poznat_ticker_prolazi(self):
        rezultat = llm_output.provjeri_tezu(teza(), dopusteni_tickeri={"MU_US_EQ"})
        self.assertEqual(rezultat["ticker"], "MU_US_EQ")

    def test_original_se_ne_mijenja(self):
        ulaz = teza(ticker="  MU_US_EQ  ")
        llm_output.provjeri_tezu(ulaz)
        self.assertEqual(ulaz["ticker"], "  MU_US_EQ  ")


class TestNormalizacijaBrojki(unittest.TestCase):

    def test_isti_broj_u_razlicitim_zapisima(self):
        # hrvatski (report.py), engleski i goli zapis moraju se poklopiti
        kanonski = llm_output._normaliziraj_broj("2290.39")
        for zapis in ("2.290,39", "2,290.39", "2290.39", "2290,39"):
            self.assertEqual(
                llm_output._normaliziraj_broj(zapis), kanonski,
                f"{zapis!r} se ne poklapa",
            )

    def test_tisucice_bez_decimala(self):
        self.assertEqual(
            llm_output._normaliziraj_broj("1.234.567"),
            llm_output._normaliziraj_broj("1234567"),
        )

    def test_cijeli_broj(self):
        self.assertEqual(llm_output._normaliziraj_broj("33"), "33")
        self.assertEqual(llm_output._normaliziraj_broj("33.0"), "33")

    def test_skuplja_iz_ugnijezdene_strukture(self):
        izvor = {
            "ukupno_eur": 2290.39,
            "pozicije": [{"ticker": "MU_US_EQ", "udio_pct": 33.3}],
            "napomena": "cash 5,00 EUR",
        }
        brojke = llm_output.dopustene_brojke(izvor)
        for ocekivana in ("2290.39", "33.3", "5"):
            self.assertIn(llm_output._normaliziraj_broj(ocekivana), brojke)

    def test_bool_nije_brojka(self):
        # True je u Pythonu podvrsta int-a; bez posebnog slučaja bi upao kao 1
        self.assertEqual(llm_output.dopustene_brojke({"ok": True}), set())

    def test_radi_na_stvarnom_izlazu_reporta(self):
        sirovo = json.dumps({"ukupna_vrijednost_eur": 2354.15, "broj_pozicija": 8})
        brojke = llm_output.dopustene_brojke(json.loads(sirovo))
        self.assertIn("2354.15", brojke)
        self.assertIn("8", brojke)


class TestBezIzmisljenihBrojki(unittest.TestCase):

    def test_brojka_iz_izvora_prolazi(self):
        t = teza(teza="Pozicija je 33.3 posto portfelja.")
        llm_output.bez_izmisljenih_brojki(t, {"33.3", "2290.39"})

    def test_izmisljena_brojka_puca(self):
        t = teza(teza="Pozicija je 47 posto portfelja.")
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.bez_izmisljenih_brojki(t, {"33.3"})
        self.assertIn("47", str(ctx.exception))

    def test_hrvatski_zapis_se_poklapa(self):
        # report.py ispisuje 2.290,39; baza drži 2290.39 — mora se poklopiti
        t = teza(teza="Portfelj vrijedi 2.290,39 EUR.")
        llm_output.bez_izmisljenih_brojki(t, {"2290.39"})

    def test_hvata_i_izvan_polja_teza(self):
        # provjera mora ići kroz SVA tekstualna polja, ne samo prvo
        t = teza(sto_bi_promijenilo_misljenje="Pad ispod 900 EUR.")
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.bez_izmisljenih_brojki(t, {"33.3"})
        self.assertIn("sto_bi_promijenilo_misljenje", str(ctx.exception))

    def test_tekst_bez_brojki_uvijek_prolazi(self):
        llm_output.bez_izmisljenih_brojki(teza(), set())

    def test_prazan_popis_dozvoljenog_odbija_svaku_brojku(self):
        with self.assertRaises(Nevaljano):
            llm_output.bez_izmisljenih_brojki(teza(teza="Palo je 5 posto."), set())


class LazniModel:
    """Model koji vraća unaprijed zadane odgovore i pamti primljene upite.

    Bez mreže i bez ključa — zato trazi_valjano prima funkciju izvana.
    """

    def __init__(self, *odgovori: str):
        self.odgovori = list(odgovori)
        self.upiti: list[str] = []

    def __call__(self, upit: str) -> str:
        self.upiti.append(upit)
        if not self.odgovori:
            raise AssertionError(
                f"model pozvan {len(self.upiti)}. put, a ima samo "
                f"{len(self.upiti) - 1} odgovora"
            )
        return self.odgovori.pop(0)


def kao_json(**izmjene) -> str:
    return json.dumps(teza(**izmjene))


class TestTraziValjano(unittest.TestCase):

    def test_uspjeh_iz_prvog_pokusaja(self):
        model = LazniModel(kao_json())
        rezultat = llm_output.trazi_valjano(model, "Sto mislis o MU?")
        self.assertEqual(rezultat["ticker"], "MU_US_EQ")
        self.assertEqual(len(model.upiti), 1)

    def test_uspjeh_iz_treceg_pokusaja(self):
        model = LazniModel(
            "nemam pojma",                      # nije JSON
            kao_json(sigurnost="jako visoka"),  # kriva vrijednost
            kao_json(),                         # ispravno
        )
        rezultat = llm_output.trazi_valjano(model, "Sto mislis o MU?")
        self.assertEqual(rezultat["sigurnost"], "srednja")
        self.assertEqual(len(model.upiti), 3)

    def test_svi_pokusaji_padnu(self):
        model = LazniModel("ne", "ne", "ne")
        with self.assertRaises(Nevaljano) as ctx:
            llm_output.trazi_valjano(model, "Sto mislis o MU?")
        self.assertIn("3", str(ctx.exception))

    def test_model_se_ne_zove_vise_od_pokusaja(self):
        # LazniModel digne AssertionError ako ga se zove prečesto — a to nije
        # Nevaljano, pa bi test pao s tom greškom umjesto tiho prošao
        model = LazniModel("ne", "ne")
        with self.assertRaises(Nevaljano):
            llm_output.trazi_valjano(model, "Sto mislis o MU?", pokusaja=2)
        self.assertEqual(len(model.upiti), 2)

    def test_drugi_upit_sadrzi_gresku_iz_prvog(self):
        # najvažniji test ovdje: bez ovoga bi ponavljanje bilo slijepo
        model = LazniModel(kao_json(sigurnost="jako visoka"), kao_json())
        llm_output.trazi_valjano(model, "Sto mislis o MU?")

        drugi = model.upiti[1]
        self.assertIn("Sto mislis o MU?", drugi)       # original je zadržan
        self.assertIn("jako visoka", drugi)            # što je model vratio
        self.assertIn("sigurnost", drugi)              # zašto nije prošlo
        for dozvoljena in llm_output.SIGURNOST:
            self.assertIn(dozvoljena, drugi)           # i što se očekuje

    def test_izmisljena_brojka_pokrece_ponavljanje(self):
        model = LazniModel(
            kao_json(teza="Pozicija je 47 posto portfelja."),
            kao_json(teza="Pozicija je 33.3 posto portfelja."),
        )
        rezultat = llm_output.trazi_valjano(
            model, "Sto mislis o MU?", dopustene={"33.3"}
        )
        self.assertIn("33.3", rezultat["teza"])
        self.assertIn("47", model.upiti[1])

    def test_nepoznat_ticker_pokrece_ponavljanje(self):
        model = LazniModel(kao_json(), kao_json(ticker="BRK_B_US_EQ"))
        rezultat = llm_output.trazi_valjano(
            model, "Sto mislis?", dopusteni_tickeri={"BRK_B_US_EQ"}
        )
        self.assertEqual(rezultat["ticker"], "BRK_B_US_EQ")

    def test_nula_pokusaja_je_greska_programera(self):
        # ValueError, ne Nevaljano: kriv je pozivatelj, ne model
        with self.assertRaises(ValueError):
            llm_output.trazi_valjano(LazniModel(), "upit", pokusaja=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
