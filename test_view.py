#!/usr/bin/env python3
"""Testovi unificiranog pogleda: adapteri, težine, filteri, HTML. Bez mreže.

    python3 -m unittest test_view -v
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

import crypto_adapter
import db
import json_adapter
import t212_adapter
import view
import zse_adapter
from fx_conversion import FxConverter
from position import RawPosition, SourceResult, ViewGreska
from test_fx import position as t212_position

PRAVILA = {
    "osnovica": "ukupna_vrijednost",
    "klasifikacija": {
        "VWCEd_EQ": "siroki_etf",
        "BRK_B_US_EQ": "dionica",
        "BTC": "kripto",
        "HT-R-A": "dionica",
    },
    "kategorije": {
        "siroki_etf": {"max_po_poziciji_pct": None, "max_ukupno_pct": None},
        "dionica": {"max_po_poziciji_pct": 15, "max_ukupno_pct": 40},
        "kripto": {"max_po_poziciji_pct": 10, "max_ukupno_pct": 15},
        "roba": {"max_po_poziciji_pct": 10, "max_ukupno_pct": 15},
    },
}

FX = FxConverter.from_rates({"EUR": 1.0, "USD": 1.0}, date="2026-08-20", source="test")

SUMMARY = {
    "id": 1,
    "currency": "EUR",
    "cash": {"availableToTrade": 200.0, "inPies": 0.0, "reservedForOrders": 0.0},
    "investments": {"currentValue": 800.0, "totalCost": 600.0,
                    "unrealizedProfitLoss": 200.0, "realizedProfitLoss": 0.0},
    "totalValue": 1000.0,
}

RULES_YAML = """
osnovica: ukupna_vrijednost
klasifikacija:
  VWCEd_EQ: siroki_etf
  BRK_B_US_EQ: dionica
  BTC: kripto
  HT-R-A: dionica
kategorije:
  siroki_etf:
    max_po_poziciji_pct: null
    max_ukupno_pct: null
  dionica:
    max_po_poziciji_pct: 15
    max_ukupno_pct: 40
  kripto:
    max_po_poziciji_pct: 10
    max_ukupno_pct: 15
"""


def _positions():
    return [
        t212_position("VWCEd_EQ", "EUR", 10, 40, 400.0, name="VWCE",
                      cost=300.0, pl=100.0),
        t212_position("BRK_B_US_EQ", "USD", 1, 400, 400.0, name="BRK B",
                      cost=300.0, pl=100.0),
    ]


def crypto_payload(**izmjene):
    osnova = {
        "source": "crypto",
        "as_of": "2026-08-21T20:00:00Z",
        "freshness": "live",
        "cash_eur": 0,
        "positions": [{
            "ticker": "BTC", "name": "Bitcoin", "quantity": 0.01,
            "currency": "EUR", "value_eur": 200.0, "cost_eur": 150.0,
        }],
    }
    osnova.update(izmjene)
    return osnova


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "portfolio.db"
        self.state = self.root / "state"
        self.state.mkdir()
        conn = db.connect(self.db_path)
        try:
            db.save_snapshot(conn, SUMMARY, _positions(), FX)
            conn.execute(
                "UPDATE snapshots SET taken_at = ? WHERE id = 1",
                ("2026-08-20T20:15:00Z",),
            )
            conn.commit()
        finally:
            conn.close()
        self.rules_path = self.root / "rules.yaml"
        self.rules_path.write_text(RULES_YAML, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()


class TestT212Adapter(Fixture):
    def test_cita_collect(self):
        r = t212_adapter.load(self.db_path)
        self.assertTrue(r.available)
        self.assertEqual(r.source, "t212")
        self.assertEqual(r.freshness, "snapshot")
        self.assertEqual(r.as_of, "2026-08-20T20:15:00Z")
        self.assertEqual(r.cash_eur, 200.0)
        tickers = [p.ticker for p in r.positions]
        self.assertEqual(tickers, ["VWCEd_EQ", "BRK_B_US_EQ"])
        vwce = r.positions[0]
        self.assertEqual(vwce.value_eur, 400.0)
        self.assertEqual(vwce.cost_eur, 300.0)
        self.assertEqual(vwce.pnl_eur, 100.0)

    def test_nema_baze(self):
        r = t212_adapter.load(self.root / "nema.db")
        self.assertFalse(r.available)
        self.assertEqual(r.positions, [])
        self.assertIn("Nema baze", r.error)

    def test_prazna_baza(self):
        prazna = self.root / "prazna.db"
        conn = db.connect(prazna)
        conn.close()
        r = t212_adapter.load(prazna)
        self.assertFalse(r.available)
        self.assertIn("snapshota", r.error.lower())


class TestJsonAdapter(Fixture):
    def test_nedostaje_datoteka(self):
        r = crypto_adapter.load(self.state / "crypto.json")
        self.assertFalse(r.available)
        self.assertEqual(r.positions, [])
        self.assertIn("crypto.json", r.error)

    def test_cita_kontrakt(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        r = crypto_adapter.load(self.state / "crypto.json")
        self.assertTrue(r.available)
        self.assertEqual(r.freshness, "live")
        self.assertEqual(r.positions[0].ticker, "BTC")
        self.assertEqual(r.positions[0].pnl_eur, 50.0)  # value - cost

    def test_krivi_source(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload(source="zse")), encoding="utf-8")
        with self.assertRaises(ViewGreska) as ctx:
            crypto_adapter.load(self.state / "crypto.json")
        self.assertIn("crypto", str(ctx.exception))

    def test_pokvaren_json(self):
        (self.state / "crypto.json").write_text("{nije json", encoding="utf-8")
        with self.assertRaises(ViewGreska):
            crypto_adapter.load(self.state / "crypto.json")

    def test_zse_source(self):
        payload = {
            "source": "zse", "as_of": "2026-08-21T15:30:00Z",
            "freshness": "live", "positions": [
                {"ticker": "HT-R-A", "value_eur": 400.0},
            ],
        }
        path = self.state / "zse.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        r = zse_adapter.load(path)
        self.assertEqual(r.source, "zse")
        self.assertEqual(r.positions[0].name, "HT-R-A")
        self.assertIsNone(r.positions[0].cost_eur)

    def test_nema_value_eur(self):
        (self.state / "crypto.json").write_text(json.dumps({
            "source": "crypto", "as_of": "2026-08-21T20:00:00Z",
            "freshness": "live", "positions": [{"ticker": "BTC"}],
        }), encoding="utf-8")
        with self.assertRaises(ViewGreska) as ctx:
            json_adapter.load(self.state / "crypto.json", "crypto")
        self.assertIn("value_eur", str(ctx.exception))

    def test_primjeri_u_repu_su_valjani(self):
        root = Path(__file__).parent / "state"
        crypto = crypto_adapter.load(root / "crypto.example.json")
        zse = zse_adapter.load(root / "zse.example.json")
        self.assertTrue(crypto.available)
        self.assertEqual(crypto.positions[0].ticker, "BTC")
        self.assertTrue(zse.available)
        self.assertEqual(zse.positions[0].ticker, "HT-R-A")


class TestMerge(Fixture):
    def test_tezine_nakon_spajanja(self):
        # T212 800 + cash 200 + BTC 200 = 1200. VWCE 400 → 33.33 % cijelog, ne 50 % T212-a.
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        v = view.assemble(self.db_path, self.state, PRAVILA)
        self.assertEqual(v.cash_eur, 200.0)
        self.assertEqual(v.positions_value_eur, 1000.0)
        self.assertEqual(v.total_value_eur, 1200.0)
        by = {p.ticker: p for p in v.positions}
        self.assertEqual(by["VWCEd_EQ"].weight_pct_of_total, 33.33)
        self.assertEqual(by["BTC"].weight_pct_of_total, 16.67)
        self.assertEqual(by["BTC"].category, "kripto")
        self.assertEqual(by["BTC"].freshness, "live")
        self.assertEqual(by["VWCEd_EQ"].freshness, "snapshot")

        t212 = next(s for s in v.sources if s.source == "t212")
        crypto = next(s for s in v.sources if s.source == "crypto")
        zse = next(s for s in v.sources if s.source == "zse")
        self.assertTrue(t212.available)
        self.assertEqual(t212.freshness, "snapshot")
        self.assertTrue(crypto.available)
        self.assertEqual(crypto.freshness, "live")
        self.assertFalse(zse.available)

        cash_row = next(a for a in v.allocation if a.category == "cash")
        self.assertEqual(cash_row.weight_pct, 16.67)

    def test_nesvrstan_ticker_je_greska(self):
        (self.state / "crypto.json").write_text(json.dumps(crypto_payload(
            positions=[{"ticker": "DOGE", "value_eur": 10.0}],
        )), encoding="utf-8")
        with self.assertRaises(ViewGreska) as ctx:
            view.assemble(self.db_path, self.state, PRAVILA)
        self.assertIn("DOGE", str(ctx.exception))

    def test_samo_t212_bez_crypto_datoteke(self):
        v = view.assemble(self.db_path, self.state, PRAVILA)
        self.assertEqual(len(v.positions), 2)
        self.assertEqual(v.total_value_eur, 1000.0)

    def test_nema_nista(self):
        with self.assertRaises(ViewGreska):
            view.merge([
                SourceResult("t212", "snapshot", None, [], available=False, error="x"),
                SourceResult("crypto", None, None, [], available=False, error="x"),
                SourceResult("zse", None, None, [], available=False, error="x"),
            ], PRAVILA)


class TestFilterAndHtml(Fixture):
    def test_filter_ne_mijenja_tezine(self):
        v = view.assemble(self.db_path, self.state, PRAVILA)
        samo = view.filter_positions(v, category="dionica")
        self.assertEqual([p.ticker for p in samo], ["BRK_B_US_EQ"])
        self.assertEqual(samo[0].weight_pct_of_total, 40.0)  # 400/1000, ne 100 %

    def test_html_badge_i_tablica(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        v = view.assemble(self.db_path, self.state, PRAVILA)
        page = view.render_html(v)
        self.assertIn("T212 · snapshot", page)
        self.assertIn("Kripto · live", page)
        self.assertIn("VWCEd_EQ", page)
        self.assertIn("BTC", page)
        self.assertIn('name="category"', page)
        self.assertIn('name="source"', page)
        self.assertIn('name="currency"', page)

    def test_html_filter(self):
        v = view.assemble(self.db_path, self.state, PRAVILA)
        page = view.render_html(v, category="dionica")
        self.assertIn("BRK_B_US_EQ", page)
        self.assertNotIn("VWCEd_EQ", page)

    def test_html_escape(self):
        r = SourceResult(
            source="t212", freshness="snapshot", as_of="2026-08-20T20:15:00Z",
            positions=[RawPosition(
                ticker="VWCEd_EQ", name="<script>x</script>", source="t212",
                currency="EUR", quantity=1, value_eur=100, cost_eur=100,
                pnl_eur=0, as_of="2026-08-20T20:15:00Z", freshness="snapshot",
            )],
            cash_eur=0, available=True,
        )
        v = view.merge([r], PRAVILA)
        page = view.render_html(v)
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)


class TestHttp(Fixture):
    def _start(self):
        handler = view.make_handler(self.db_path, self.state, self.rules_path)
        httpd = view.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd

    def test_json_i_html_i_readonly(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        httpd = self._start()
        host, port = httpd.server_address[:2]
        try:
            c = HTTPConnection(host, port, timeout=5)
            c.request("GET", "/health")
            self.assertEqual(c.getresponse().read(), b"ok\n")

            c.request("GET", "/json")
            res = c.getresponse()
            self.assertEqual(res.status, 200)
            data = json.loads(res.read())
            self.assertIn("BTC", [p["ticker"] for p in data["positions"]])

            c.request("GET", "/?source=crypto")
            res = c.getresponse()
            body = res.read().decode()
            self.assertEqual(res.status, 200)
            self.assertIn("BTC", body)
            self.assertNotIn("VWCEd_EQ", body)

            c.request("POST", "/")
            self.assertEqual(c.getresponse().status, 405)

            c.request("GET", "/tajne")
            self.assertEqual(c.getresponse().status, 404)
            c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestBind(unittest.TestCase):
    def test_localhost_nema_upozorenja(self):
        self.assertIsNone(view.public_bind_warning("127.0.0.1"))
        self.assertIsNone(view.public_bind_warning("localhost"))

    def test_javna_adresa_upozorava(self):
        msg = view.public_bind_warning("0.0.0.0")
        self.assertIsNotNone(msg)
        self.assertIn("UPOZORENJE", msg)
        self.assertIn("0.0.0.0", msg)


if __name__ == "__main__":
    unittest.main()
