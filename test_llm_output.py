#!/usr/bin/env python3
"""Testovi za validaciju LLM izlaza.

    python3 -m unittest test_llm_output -v
"""

from __future__ import annotations

import unittest

import llm_output
from llm_output import Nevaljano


class TestParsiraj(unittest.TestCase):
    # Ova dva su napisana da vidiš oblik. Ostale piši po istom uzorku.

    def test_goli_json(self):
        self.assertEqual(llm_output.parsiraj('{"a": 1}'), {"a": 1})

    def test_json_u_fence_bloku(self):
        tekst = '```json\n{"a": 1, "b": "dva"}\n```'
        self.assertEqual(llm_output.parsiraj(tekst), {"a": 1, "b": "dva"})

    # ── tvoje ────────────────────────────────────────────────────────────────
    # self.fail() je tu da test PADNE dok ga ne napišeš. Test koji ne testira
    # ništa prolazi, a to je gore od testa koji pada — lažno te smiruje.

    def test_json_s_tekstom_okolo(self):
        tekst='Evo analize portfolia:\n{"a" : 1, "b" : 2}\n nadam se da je dobro'
        self.assertEqual(llm_output.parsiraj(tekst), {"a": 1, "b": 2})

    def test_ugnijezdeni_objekt(self):
        tekst='{"a" : {"b":1}, "c":2}'
        # '{"a": {"b": 1}, "c": 2}' -> cijeli objekt, ne samo do prve '}'
        self.assertEqual(llm_output.parsiraj(tekst), {"a" : {"b":1}, "c":2})

    def test_neispravan_json_puca(self):
        # npr. '{"a": 1,}' — višak zareza. Mora dići Nevaljano, ne popraviti.
        tekst='{"a": 1,}'
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj('{"a": 1,}')

    def test_prazan_ulaz_puca(self):
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj('')

    def test_lista_na_vrhu_puca(self):
        # '[1, 2]' je valjan JSON, ali nije objekt
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj('[1, 2]')
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj('[{"a": 1}]')

    def test_bez_ijedne_viticaste_puca(self):
        # 'Nemam podataka za tu analizu.""
        with self.assertRaises(Nevaljano):
            llm_output.parsiraj("Nemam podataka za tu analizu.")

    def test_poruka_greske_je_korisna(self):
        # Poruka ide natrag modelu, pa mora reći ŠTO je falilo.
        # Uhvati iznimku i provjeri da poruka nije prazna i da spominje JSON.
        with self.assertRaises(Nevaljano) as expectedException:
            llm_output.parsiraj("Ovo nije json")

        message = str(expectedException.exception)
        self.assertIn("JSON", message)



if __name__ == "__main__":
    unittest.main(verbosity=2)
