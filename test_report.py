#!/usr/bin/env python3
"""Testovi izvještaja o cijelom portfelju (report.collect_view). Bez mreže.

    python3 -m unittest test_report -v
"""

from __future__ import annotations

import json
import unittest

import report
import view
from position import ViewGreska
from test_view import PRAVILA, Fixture, crypto_payload


class TestCollectView(Fixture):
    def _full(self, **kw):
        return report.collect_view(
            self.db_path, self.state, self.rules_path,
            kw.get("history", self.root / "view_history.db"),
        )

    def test_cijeli_portfelj(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        data = self._full()
        self.assertEqual(data["total_value_eur"], 1200.0)
        self.assertEqual(data["positions_value_eur"], 1000.0)
        self.assertEqual(data["cash_available_eur"], 200.0)
        tickeri = [p["ticker"] for p in data["positions"]]
        self.assertIn("BTC", tickeri)
        btc = next(p for p in data["positions"] if p["ticker"] == "BTC")
        self.assertEqual(btc["source"], "crypto")
        self.assertEqual(btc["category"], "kripto")
        self.assertAlmostEqual(btc["pct"], 16.67, places=2)  # 200/1200, ne 200/1000

    def test_izvori_i_svjezina(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        data = self._full()
        po_izvoru = {s["source"]: s for s in data["sources"]}
        self.assertTrue(po_izvoru["t212"]["available"])
        self.assertEqual(po_izvoru["t212"]["freshness"], "snapshot")
        self.assertTrue(po_izvoru["crypto"]["available"])
        self.assertEqual(po_izvoru["crypto"]["freshness"], "live")
        self.assertFalse(po_izvoru["zse"]["available"])

        text = report.status_text(data)
        self.assertIn("Izvori: T212 snapshot 2026-08-20 20:15", text)
        self.assertIn("Kripto live 2026-08-21 20:00", text)
        self.assertIn("ZSE n/d", text)
        self.assertIn("BTC", text)

    def test_samo_t212_bez_jsona(self):
        data = self._full()
        self.assertEqual(data["total_value_eur"], 1000.0)
        po_izvoru = {s["source"]: s for s in data["sources"]}
        self.assertFalse(po_izvoru["crypto"]["available"])
        self.assertIn("crypto.json", po_izvoru["crypto"]["error"])

    def test_digest_bez_povijesti(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        text = report.digest_text(self._full())
        self.assertIn("view.py --snapshot", text)

    def test_digest_s_povijesti(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        hist = self.root / "view_history.db"
        view.take_snapshot(self.db_path, self.state, PRAVILA, hist,
                           taken_at="2026-08-21T20:20:00Z")
        # kripto poraste 200 -> 300; ukupno 1200 -> 1300
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload(positions=[{
                "ticker": "BTC", "name": "Bitcoin", "quantity": 0.01,
                "currency": "EUR", "value_eur": 300.0, "cost_eur": 150.0,
            }])), encoding="utf-8")
        data = self._full(history=hist)
        self.assertEqual(data["previous"]["total_value_eur"], 1200.0)
        text = report.digest_text(data)
        self.assertIn("Od prošlog snapshota (2026-08-21 20:20 UTC)", text)
        self.assertIn("+100,00 EUR", text)
        self.assertIn("Kripto", text)

    def test_nesvrstan_ticker_pada(self):
        payload = crypto_payload()
        payload["positions"][0]["ticker"] = "DOGE"
        (self.state / "crypto.json").write_text(
            json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ViewGreska) as ctx:
            self._full()
        self.assertIn("DOGE", str(ctx.exception))

    def test_bez_ijednog_izvora(self):
        with self.assertRaises(ViewGreska):
            report.collect_view(self.root / "nema.db", self.root / "prazno",
                                self.rules_path, self.root / "vh.db")

    def test_read_only(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        prije = self.db_path.read_bytes()
        hist = self.root / "view_history.db"
        self._full(history=hist)
        self.assertEqual(self.db_path.read_bytes(), prije)
        self.assertFalse(hist.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
