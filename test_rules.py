#!/usr/bin/env python3
"""Testovi rules enginea i mini-YAML parsera.

    python3 -m unittest test_rules -v
"""

from __future__ import annotations

import json
import unittest

import miniyaml
import rules
import test_view
from rules import PravilaGreska

PRAVILA = {
    "osnovica": "ukupna_vrijednost",
    "klasifikacija": {
        "VWCEd_EQ": "siroki_etf",
        "BRK_B_US_EQ": "dionica",
        "GOOG_US_EQ": "dionica",
        "CEBSd_EQ": "sektorski_etf",
        "BTC": "kripto",
    },
    "kategorije": {
        "siroki_etf": {"max_po_poziciji_pct": None, "max_ukupno_pct": None},
        "dionica": {"max_po_poziciji_pct": 15, "max_ukupno_pct": 40},
        "sektorski_etf": {"max_po_poziciji_pct": 10, "max_ukupno_pct": 20},
        "kripto": {"max_po_poziciji_pct": 10, "max_ukupno_pct": 15},
    },
    "ostalo": {"min_cash_eur": 0, "max_starost_sati": 96},
}


def stanje(pozicije, ukupno=None, cash=5.0, taken_at=None):
    zbroj = sum(e for _, e in pozicije)
    ukupno = ukupno if ukupno is not None else zbroj + cash
    from datetime import datetime, timedelta, timezone
    taken_at = taken_at or (datetime.now(timezone.utc) - timedelta(hours=2)
                            ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "snapshot_id": 1, "taken_at": taken_at,
        "ukupna_vrijednost": ukupno, "samo_pozicije": zbroj, "cash_eur": cash,
        "pozicije": [{"ticker": t, "naziv": t, "valuta": "EUR", "eur": e,
                      "check_delta_pct": 0.0} for t, e in pozicije],
    }


class TestMiniYaml(unittest.TestCase):
    def test_osnovno(self):
        d = miniyaml.ucitaj("""
# komentar
verzija: 1
osnovica: ukupna_vrijednost
kategorije:
  dionica:
    max_po_poziciji_pct: 15
    max_ukupno_pct: null
    opis: Pojedinacna kompanija
nacela:
  - Prvo nacelo
  - Drugo nacelo
""")
        self.assertEqual(d["verzija"], 1)
        self.assertEqual(d["osnovica"], "ukupna_vrijednost")
        self.assertEqual(d["kategorije"]["dionica"]["max_po_poziciji_pct"], 15)
        self.assertIsNone(d["kategorije"]["dionica"]["max_ukupno_pct"])
        self.assertEqual(d["nacela"], ["Prvo nacelo", "Drugo nacelo"])

    def test_komentar_na_kraju_retka(self):
        d = miniyaml.ucitaj("a: 5   # ovo je komentar\nb: tekst # i ovo")
        self.assertEqual(d["a"], 5)
        self.assertEqual(d["b"], "tekst")

    def test_hash_u_navodnicima_nije_komentar(self):
        d = miniyaml.ucitaj('a: "vrijednost # nije komentar"')
        self.assertEqual(d["a"], "vrijednost # nije komentar")

    def test_tipovi(self):
        d = miniyaml.ucitaj("i: 42\nf: 3.5\nn: null\nt: true\nff: false\ns: rijec")
        self.assertEqual(d["i"], 42)
        self.assertEqual(d["f"], 3.5)
        self.assertIsNone(d["n"])
        self.assertIs(d["t"], True)
        self.assertIs(d["ff"], False)
        self.assertEqual(d["s"], "rijec")

    def test_pukne_na_nepodrzanom(self):
        for tekst in ("a: [1, 2]", "a: {b: 1}", "a: &sidro x", "\ta: 1"):
            with self.assertRaises(miniyaml.YamlGreska, msg=f"{tekst!r} je prosao"):
                miniyaml.ucitaj(tekst)

    def test_pukne_na_duplikatu(self):
        with self.assertRaises(miniyaml.YamlGreska):
            miniyaml.ucitaj("a: 1\na: 2")

    def test_stvarni_rules_yaml(self):
        d = miniyaml.ucitaj_datoteku(rules.RULES_PATH)
        self.assertIn("klasifikacija", d)
        self.assertIn("kategorije", d)
        self.assertEqual(d["kategorije"]["dionica"]["max_po_poziciji_pct"], 15)
        self.assertIsNone(d["kategorije"]["siroki_etf"]["max_po_poziciji_pct"])


class TestUcitavanje(unittest.TestCase):
    def test_stvarna_pravila_se_ucitaju(self):
        p = rules.ucitaj_pravila()
        self.assertEqual(p["osnovica"], "ukupna_vrijednost")
        self.assertIn("VWCEd_EQ", p["klasifikacija"])

    def test_sve_klase_imaju_kategoriju(self):
        p = rules.ucitaj_pravila()
        for ticker, kat in p["klasifikacija"].items():
            self.assertIn(kat, p["kategorije"], f"{ticker} -> nepoznata kategorija {kat}")


class TestProvjera(unittest.TestCase):
    def test_siroki_etf_nije_koncentracija(self):
        # 49 % u all-world ETF-u je uredu; isti postotak u dionici nije.
        st = stanje([("VWCEd_EQ", 4900.0), ("BRK_B_US_EQ", 100.0)])
        self.assertEqual(rules.provjeri(st, PRAVILA), [])

    def test_dionica_preko_granice(self):
        st = stanje([("VWCEd_EQ", 5000.0), ("BRK_B_US_EQ", 2000.0)])
        nalazi = rules.provjeri(st, PRAVILA)
        krsenja = [n for n in nalazi if n.ozbiljnost == "krsenje"]
        self.assertTrue(any(n.predmet == "BRK_B_US_EQ" for n in krsenja))
        n = next(n for n in krsenja if n.predmet == "BRK_B_US_EQ")
        self.assertIn("max_po_poziciji_pct = 15", n.pravilo)

    def test_zbroj_kategorije(self):
        # dvije dionice, svaka ispod 15 %, ali zajedno preko 40 %
        st = stanje([("VWCEd_EQ", 2000.0), ("BRK_B_US_EQ", 1400.0),
                     ("GOOG_US_EQ", 1400.0)], ukupno=10000.0)
        st["pozicije"][1]["eur"] = 1400.0
        nalazi = rules.provjeri(st, PRAVILA)
        self.assertEqual([n for n in nalazi if n.predmet == "dionica"], [])

        st2 = stanje([("VWCEd_EQ", 1000.0), ("BRK_B_US_EQ", 1400.0),
                      ("GOOG_US_EQ", 1400.0)])
        nalazi2 = rules.provjeri(st2, PRAVILA)
        self.assertTrue(any(n.predmet == "dionica" and n.ozbiljnost == "krsenje"
                            for n in nalazi2))

    def test_kripto_granica(self):
        st = stanje([("VWCEd_EQ", 8000.0), ("BTC", 2000.0)])
        nalazi = rules.provjeri(st, PRAVILA)
        self.assertTrue(any(n.predmet == "kripto" for n in nalazi))

    def test_nesvrstan_ticker_puca(self):
        st = stanje([("VWCEd_EQ", 1000.0), ("NEPOZNAT_EQ", 100.0)])
        with self.assertRaises(PravilaGreska) as ctx:
            rules.provjeri(st, PRAVILA)
        self.assertIn("NEPOZNAT_EQ", str(ctx.exception))

    def test_stari_snapshot_upozorava(self):
        st = stanje([("VWCEd_EQ", 1000.0)], taken_at="2020-01-01T00:00:00Z")
        nalazi = rules.provjeri(st, PRAVILA)
        self.assertTrue(any(n.predmet == "snapshot" for n in nalazi))

    def test_fx_nesklad_upozorava(self):
        st = stanje([("VWCEd_EQ", 1000.0)])
        st["pozicije"][0]["check_delta_pct"] = 9900.0
        nalazi = rules.provjeri(st, PRAVILA)
        self.assertTrue(any(n.predmet == "VWCEd_EQ" and n.ozbiljnost == "upozorenje"
                            for n in nalazi))


class TestStanjeSpojeno(test_view.Fixture):
    """rules.stanje() gradi CIJELI portfelj — kripto/ZSE ulaze u nazivnik."""

    def _stanje(self):
        return rules.stanje(self.db_path, self.state, pravila=test_view.PRAVILA)

    def _upisi_crypto(self):
        (self.state / "crypto.json").write_text(
            json.dumps(test_view.crypto_payload()), encoding="utf-8")

    def test_kripto_ulazi_u_stanje_i_nazivnik(self):
        self._upisi_crypto()
        st = self._stanje()
        self.assertEqual(st["ukupna_vrijednost"], 1200.0)
        btc = next(p for p in st["pozicije"] if p["ticker"] == "BTC")
        self.assertEqual(btc["izvor"], "crypto")

        nalazi = rules.provjeri(st, test_view.PRAVILA)
        # BTC je 16.7 % ukupnog portfelja: preko max_po_poziciji (10) i
        # kategorija kripto preko max_ukupno (15). Bez spajanja bi oba prošla.
        self.assertTrue(any(n.predmet == "BTC" and n.ozbiljnost == "krsenje"
                            for n in nalazi))
        self.assertTrue(any(n.predmet == "kripto" and n.ozbiljnost == "krsenje"
                            for n in nalazi))

    def test_taken_at_je_najstariji_izvor(self):
        self._upisi_crypto()
        st = self._stanje()
        # T212 snapshot (20.08) je stariji od kripto as_of (21.08).
        self.assertEqual(st["taken_at"], "2026-08-20T20:15:00Z")

    def test_bez_jsona_radi_samo_t212(self):
        st = self._stanje()
        self.assertEqual(st["ukupna_vrijednost"], 1000.0)
        izvori = {s["izvor"]: s for s in st["izvori"]}
        self.assertFalse(izvori["crypto"]["dostupan"])

    def test_kupnja_na_spojenom_stanju(self):
        self._upisi_crypto()
        st = self._stanje()
        prije, poslije = rules.simuliraj_kupnju(
            st, test_view.PRAVILA, "BTC", 100.0)
        n = next(n for n in poslije if n.predmet == "BTC")
        # 300 / 1300 = 23.1 % — mjeri se na cijelom portfelju
        self.assertAlmostEqual(n.izmjereno, 23.08, places=2)


class TestKupnja(unittest.TestCase):
    def setUp(self):
        # BRK na 10 % od 10.000 -> ispod granice od 15 %
        self.st = stanje([("VWCEd_EQ", 8995.0), ("BRK_B_US_EQ", 1000.0)])

    def test_bezopasna_kupnja(self):
        prije, poslije = rules.simuliraj_kupnju(self.st, PRAVILA, "BRK_B_US_EQ", 100.0)
        self.assertEqual(prije, [])
        self.assertEqual(poslije, [])

    def test_kupnja_koja_prelazi_granicu(self):
        # +1500 EUR u BRK -> 2500/11500 = 21.7 % > 15 %
        prije, poslije = rules.simuliraj_kupnju(self.st, PRAVILA, "BRK_B_US_EQ", 1500.0)
        self.assertEqual(prije, [])
        self.assertTrue(any(n.predmet == "BRK_B_US_EQ" and n.ozbiljnost == "krsenje"
                            for n in poslije))

    def test_nova_pozicija(self):
        prije, poslije = rules.simuliraj_kupnju(self.st, PRAVILA, "BTC", 2000.0)
        self.assertTrue(any(n.predmet == "kripto" for n in poslije))

    def test_nesvrstan_ticker_prije_kupnje(self):
        with self.assertRaises(PravilaGreska):
            rules.simuliraj_kupnju(self.st, PRAVILA, "DOGE", 100.0)

    def test_negativan_iznos(self):
        with self.assertRaises(PravilaGreska):
            rules.simuliraj_kupnju(self.st, PRAVILA, "BRK_B_US_EQ", -50.0)

    def test_original_se_ne_mijenja(self):
        prije_eur = self.st["pozicije"][1]["eur"]
        rules.simuliraj_kupnju(self.st, PRAVILA, "BRK_B_US_EQ", 5000.0)
        self.assertEqual(self.st["pozicije"][1]["eur"], prije_eur)


if __name__ == "__main__":
    unittest.main(verbosity=2)
