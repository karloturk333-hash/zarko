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

    def test_hermesov_crypto_oblik(self):
        (self.state / "crypto.json").write_text(json.dumps({
            "source": "Trading212 app, rucni unos, 2026-08-02",
            "positions": [{
                "ticker": "BTC", "name": "Bitcoin", "quantity": 0.00751633,
                "avg_price_eur": 64056.53, "current_value_eur": 502.65,
                "price_source": "CoinGecko live",
                "price_fetched_at": "2026-08-21",
            }],
            "last_updated": "2026-08-21",
        }), encoding="utf-8")
        r = crypto_adapter.load(self.state / "crypto.json")
        self.assertTrue(r.available)
        self.assertEqual(r.source, "crypto")
        self.assertEqual(r.freshness, "live")
        self.assertEqual(r.as_of, "2026-08-21T00:00:00Z")
        btc = r.positions[0]
        self.assertEqual(btc.value_eur, 502.65)
        self.assertAlmostEqual(btc.cost_eur, 0.00751633 * 64056.53)
        self.assertAlmostEqual(btc.pnl_eur, btc.value_eur - btc.cost_eur)

    def test_hermesov_zse_oblik(self):
        (self.state / "zse.json").write_text(json.dumps({
            "positions": [
                {
                    "ticker": "BSQR", "quantity": 2, "avg_price_eur": 29.0,
                    "current_value_eur": 58.80, "price_source": "rest.zse.hr live",
                },
                {
                    "ticker": "ZSE_7CRO_GENIUS", "invested_eur": 280.0,
                    "current_value_eur": 287.20, "unrealized_pl_eur": 7.20,
                    "price_fetched_at": "2026-08-21",
                },
            ],
            "last_updated": "2026-08-21",
        }), encoding="utf-8")
        r = zse_adapter.load(self.state / "zse.json")
        self.assertTrue(r.available)
        self.assertEqual(r.source, "zse")
        by = {p.ticker: p for p in r.positions}
        self.assertEqual(by["BSQR"].cost_eur, 58.0)
        self.assertAlmostEqual(by["BSQR"].pnl_eur, 0.80)
        self.assertIsNone(by["ZSE_7CRO_GENIUS"].quantity)
        self.assertEqual(by["ZSE_7CRO_GENIUS"].cost_eur, 280.0)
        self.assertEqual(by["ZSE_7CRO_GENIUS"].pnl_eur, 7.20)

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
        self.assertIn("/static/pico.min.css", page)
        self.assertIn("/static/dashboard.css", page)
        self.assertIn("Široki ETF", page)
        self.assertIn("pico", page.lower())

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


class TestChartsAndHistory(Fixture):
    def test_pie_sadrzi_kategorije(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        v = view.assemble(self.db_path, self.state, PRAVILA)
        page = view.render_html(v)
        self.assertIn('class="pie"', page)
        for kat in ("siroki_etf", "dionica", "kripto", "cash"):
            self.assertIn(f'data-category="{kat}"', page)
            self.assertIn(f"cat-{kat}", page)
        for kat in ("siroki_etf", "dionica", "sektorski_etf", "roba", "kripto", "cash"):
            self.assertIn(kat, view.CATEGORY_COLORS)

    def test_pie_href_filter(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        v = view.assemble(self.db_path, self.state, PRAVILA)
        page = view.render_html(v, source="crypto", currency="EUR")
        self.assertIn(
            "category=siroki_etf&amp;source=crypto&amp;currency=EUR", page)
        self.assertIn(
            "category=kripto&amp;source=crypto&amp;currency=EUR", page)
        page2 = view.render_html(v, category="dionica")
        self.assertIn("BRK_B_US_EQ", page2)
        self.assertNotIn("VWCEd_EQ", page2)
        self.assertIn("siroki_etf", page2)  # pie ostaje cijeli portfelj
        self.assertIn("category=siroki_etf", page2)
        self.assertIn('class="slice cat-dionica active"', page2)
        self.assertIn('href="?" data-category="dionica"', page2)

    def test_snapshot_pise_i_cita(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        before = self.db_path.read_bytes()
        hist = self.root / "view_history.db"
        row = view.take_snapshot(
            self.db_path, self.state, PRAVILA, hist,
            taken_at="2026-08-22T20:20:00Z",
        )
        self.assertTrue(hist.exists())
        self.assertEqual(row["taken_at"], "2026-08-22T20:20:00Z")
        self.assertEqual(row["total_value_eur"], 1200.0)
        self.assertEqual(row["t212_eur"], 1000.0)
        self.assertEqual(row["crypto_eur"], 200.0)
        self.assertIsNone(row["zse_eur"])
        self.assertEqual(row["cash_eur"], 200.0)
        self.assertEqual(self.db_path.read_bytes(), before)

        ch = view.load_chart_history(self.db_path, hist)
        self.assertFalse(ch.ukupno_je_samo_t212)
        self.assertEqual(ch.ukupno[-1], ("2026-08-22T20:20:00Z", 1200.0))
        self.assertEqual(ch.t212[0][1], 1000.0)
        self.assertEqual(ch.crypto[-1][1], 200.0)

    def test_linija_serije(self):
        v = view.assemble(self.db_path, self.state, PRAVILA)
        ch = view.ChartHistory(
            ukupno=[
                ("2026-08-21T20:20:00Z", 1100.0),
                ("2026-08-22T20:20:00Z", 1200.0),
            ],
            t212=[
                ("2026-08-21T20:15:00Z", 900.0),
                ("2026-08-22T20:15:00Z", 1000.0),
            ],
            crypto=[
                ("2026-08-21T20:20:00Z", 150.0),
                ("2026-08-22T20:20:00Z", 200.0),
            ],
            zse=[
                ("2026-08-21T20:20:00Z", 50.0),
                ("2026-08-22T20:20:00Z", 50.0),
            ],
            ukupno_je_samo_t212=False,
        )
        page = view.render_html(v, history=ch)
        self.assertIn("series-ukupno", page)
        self.assertIn("series-t212", page)
        self.assertIn("series-crypto", page)
        self.assertIn("series-zse", page)
        self.assertIn("<polyline", page)
        self.assertIn("sources-grid", page)
        self.assertIn("T212 + kripto + ZSE", page)

    def test_linija_caption_samo_t212(self):
        ch = view.load_chart_history(self.db_path, self.root / "nema.db")
        self.assertTrue(ch.ukupno_je_samo_t212)
        self.assertEqual(ch.ukupno, ch.t212)
        self.assertEqual(ch.t212[0][1], 1000.0)
        page = view.render_html(view.assemble(self.db_path, self.state, PRAVILA),
                               history=ch)
        self.assertIn("samo T212", page)
        self.assertIn("series-ukupno", page)
        self.assertIn("series-t212", page)
        self.assertIn("prva točka — linija nakon sljedećeg snapshot-a", page)
        self.assertNotIn("<polyline", page)

    def test_jedna_tocka_je_kartica(self):
        v = view.assemble(self.db_path, self.state, PRAVILA)
        ch = view.ChartHistory(
            ukupno=[("2026-08-22T20:20:00Z", 1200.0)],
            t212=[("2026-08-22T20:15:00Z", 1000.0)],
            crypto=[("2026-08-22T20:20:00Z", 200.0)],
            zse=[],
            ukupno_je_samo_t212=False,
        )
        page = view.render_html(v, history=ch)
        self.assertIn("prva točka — linija nakon sljedećeg snapshot-a", page)
        self.assertIn("line-card", page)
        self.assertIn("sources-grid", page)
        self.assertNotIn("<polyline", page)


class TestHttp(Fixture):
    def _start(self, history=None):
        hist = history or (self.root / "view_history.db")
        handler = view.make_handler(
            self.db_path, self.state, self.rules_path, hist)
        httpd = view.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd

    def test_json_i_html_i_readonly(self):
        (self.state / "crypto.json").write_text(
            json.dumps(crypto_payload()), encoding="utf-8")
        hist = self.root / "view_history.db"
        httpd = self._start(hist)
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
            self.assertIn("category=kripto", body)
            self.assertIn("samo T212", body)
            self.assertFalse(hist.exists())

            c.request("GET", "/?category=dionica")
            res = c.getresponse()
            body = res.read().decode()
            self.assertEqual(res.status, 200)
            self.assertIn("BRK_B_US_EQ", body)
            self.assertNotIn("VWCEd_EQ", body)
            self.assertFalse(hist.exists())

            c.request("POST", "/")
            self.assertEqual(c.getresponse().status, 405)
            self.assertFalse(hist.exists())

            c.request("GET", "/static/pico.min.css")
            res = c.getresponse()
            self.assertEqual(res.status, 200)
            pico = res.read()
            self.assertIn(b"Pico CSS", pico)
            self.assertIn("text/css", res.getheader("Content-Type", ""))

            c.request("GET", "/static/dashboard.css")
            res = c.getresponse()
            self.assertEqual(res.status, 200)
            dash = res.read()
            self.assertIn(b".metrics", dash)
            self.assertIn("text/css", res.getheader("Content-Type", ""))

            c.request("GET", "/static/../view.py")
            self.assertEqual(c.getresponse().status, 404)
            c.request("GET", "/static/nema.css")
            self.assertEqual(c.getresponse().status, 404)

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
