#!/usr/bin/env python3
"""Testovi zapisnika teza.

    python3 -m unittest test_teza -v
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import teza
from llm_output import Nevaljano

# Kontekst se u testovima gradi ručno, bez baze i bez rules.yaml — tako testovi
# ne ovise o tome kakav je portfelj danas.
KONTEKST = {
    "tickeri": {"MU_US_EQ", "BRK_B_US_EQ"},
    "brojke": {"8.4", "2354.15", "15"},
    "podaci": {
        "taken_at": "2026-08-04T20:15:00Z",
        "positions": [
            {"ticker": "MU_US_EQ", "market_value_eur": 198.2, "pct": 8.4},
            {"ticker": "BRK_B_US_EQ", "market_value_eur": 784.0, "pct": 33.3},
        ],
    },
}


def teza_json(**izmjene):
    osnova = {
        "ticker": "MU_US_EQ",
        "teza": "Ciklus memorije se okrece, kapaciteti su ograniceni.",
        "protuteza": "Ciklicka industrija, marze padnu brzo i duboko.",
        "sto_bi_promijenilo_misljenje": "Pad cijena DRAM-a dva kvartala zaredom.",
        "sigurnost": "srednja",
    }
    osnova.update(izmjene)
    return osnova


class TestZapisnik(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = teza.spoji(Path(self.tmp.name) / "teze.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_zapis_i_citanje(self):
        id_teze = teza.zapisi(self.conn, teza_json(), KONTEKST)
        redovi = teza.popis(self.conn)
        self.assertEqual(len(redovi), 1)
        self.assertEqual(redovi[0]["id"], id_teze)
        self.assertEqual(redovi[0]["ticker"], "MU_US_EQ")
        self.assertIsNone(redovi[0]["ishod"])

    def test_snima_stanje_pozicije(self):
        # bez ovoga se za pola godine ne zna je li teza bila o 8 % ili 33 %
        teza.zapisi(self.conn, teza_json(), KONTEKST)
        red = teza.popis(self.conn)[0]
        self.assertAlmostEqual(red["udio_pct"], 8.4)
        self.assertAlmostEqual(red["vrijednost_eur"], 198.2)
        self.assertEqual(red["snapshot_at"], "2026-08-04T20:15:00Z")

    def test_nepoznat_ticker_odbijen(self):
        with self.assertRaises(Nevaljano) as ctx:
            teza.zapisi(self.conn, teza_json(ticker="DOGE"), KONTEKST)
        self.assertIn("DOGE", str(ctx.exception))
        self.assertEqual(teza.popis(self.conn), [])

    def test_izmisljena_brojka_odbijena(self):
        with self.assertRaises(Nevaljano) as ctx:
            teza.zapisi(
                self.conn,
                teza_json(teza="Cini vec 47 posto portfelja."),
                KONTEKST,
            )
        self.assertIn("47", str(ctx.exception))
        self.assertEqual(teza.popis(self.conn), [], "odbijena teza je ipak zapisana")

    def test_brojka_iz_pravila_prolazi(self):
        # 15 je prag iz rules.yaml i mora biti dozvoljen
        teza.zapisi(
            self.conn,
            teza_json(protuteza="Granica za dionicu je 15 posto."),
            KONTEKST,
        )
        self.assertEqual(len(teza.popis(self.conn)), 1)

    def test_nepotpuna_teza_odbijena(self):
        nepotpuna = teza_json()
        del nepotpuna["protuteza"]
        with self.assertRaises(Nevaljano):
            teza.zapisi(self.conn, nepotpuna, KONTEKST)

    def test_baza_odbija_krivu_sigurnost(self):
        # CHECK u teze.sql je druga brana, neovisna o Pythonu
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO teze (zapisano_at, ticker, teza, protuteza,
                       sto_bi_promijenilo_misljenje, sigurnost)
                   VALUES ('2026-01-01T00:00:00Z', 'X', 'a', 'b', 'c', 'jako')"""
            )

    def test_filtriranje_po_tickeru(self):
        teza.zapisi(self.conn, teza_json(), KONTEKST)
        teza.zapisi(self.conn, teza_json(ticker="BRK_B_US_EQ"), KONTEKST)
        self.assertEqual(len(teza.popis(self.conn, "MU_US_EQ")), 1)
        self.assertEqual(len(teza.popis(self.conn)), 2)


class TestIshod(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = teza.spoji(Path(self.tmp.name) / "teze.db")
        self.id = teza.zapisi(self.conn, teza_json(), KONTEKST)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_zatvaranje(self):
        teza.zatvori(self.conn, self.id, "promasila", "DRAM je nastavio padati")
        red = teza.popis(self.conn)[0]
        self.assertEqual(red["ishod"], "promasila")
        self.assertIsNotNone(red["ishod_at"])
        self.assertIn("DRAM", red["biljeska"])

    def test_ishod_se_ne_prepisuje(self):
        # zapisnik koji se može prepisati nije zapisnik
        teza.zatvori(self.conn, self.id, "promasila")
        with self.assertRaises(Nevaljano) as ctx:
            teza.zatvori(self.conn, self.id, "obistinila")
        self.assertIn("promasila", str(ctx.exception))

    def test_nepostojeca_teza(self):
        with self.assertRaises(Nevaljano):
            teza.zatvori(self.conn, 999, "nejasno")

    def test_otvorene_izostavljaju_zatvorene(self):
        self.assertEqual(len(teza.otvorene(self.conn)), 1)
        teza.zatvori(self.conn, self.id, "obistinila")
        self.assertEqual(teza.otvorene(self.conn), [])

    def test_otvorene_starije_od(self):
        # upravo zapisana teza nije starija od 30 dana
        self.assertEqual(teza.otvorene(self.conn, starije_od_dana=30), [])
        self.assertEqual(len(teza.otvorene(self.conn, starije_od_dana=0)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
