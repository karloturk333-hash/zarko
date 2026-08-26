#!/usr/bin/env python3
"""Testovi FX normalizacije i spremanja snapshota. Pokretanje:

    python3 -m unittest tests.test_fx -v

Fiksni tečajevi, bez mreže — deterministično.
Oblici odgovora prate službeni T212 OpenAPI spec (AccountSummary, Position).
"""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

import db
from fx_conversion import MINOR_UNITS, FxConverter, UnknownCurrency, resolve
from t212 import T212Client

# 1 EUR = X
RATES = {"USD": 1.10, "GBP": 0.85, "ZAR": 20.0, "ILS": 4.0, "EUR": 1.0}
FX = FxConverter.from_rates(RATES, date="2026-08-01", source="test")


def position(ticker, ccy, qty, price, current_value_acct, name="X", **kw):
    """Pozicija u obliku koji vraća /equity/positions."""
    return {
        "instrument": {"ticker": ticker, "currency": ccy, "name": name, "isin": f"ISIN{ticker}"},
        "quantity": qty,
        "currentPrice": price,
        "averagePricePaid": kw.get("avg", price),
        "quantityAvailableForTrading": qty,
        "quantityInPies": 0,
        "createdAt": "2024-03-01T10:00:00Z",
        "walletImpact": {
            "currency": kw.get("wallet_ccy", "EUR"),
            "currentValue": current_value_acct,
            "totalCost": kw.get("cost", current_value_acct),
            "unrealizedProfitLoss": kw.get("pl", 0.0),
            "fxImpact": kw.get("fx_impact", 0.0),
        },
    }


SUMMARY = {
    "id": 12345,
    "currency": "EUR",
    "cash": {"availableToTrade": 250.0, "inPies": 10.0, "reservedForOrders": 5.0},
    "investments": {"currentValue": 5000.0, "totalCost": 4500.0,
                    "unrealizedProfitLoss": 500.0, "realizedProfitLoss": 120.0},
    "totalValue": 5250.0,
}


class TestResolve(unittest.TestCase):
    def test_major_currency(self):
        self.assertEqual(resolve("USD"), ("USD", 1))
        self.assertEqual(resolve("EUR"), ("EUR", 1))

    def test_pence_variants(self):
        for code in ("GBX", "GBx", "GBp"):
            self.assertEqual(resolve(code), ("GBP", 100), f"kriva obrada {code}")

    def test_other_minor_units(self):
        self.assertEqual(resolve("ZAC"), ("ZAR", 100))
        self.assertEqual(resolve("ILA"), ("ILS", 100))

    def test_unknown_raises(self):
        for code in (None, "", "gbp", "POUNDS", "GB", "$", "US1", 123):
            with self.assertRaises(UnknownCurrency, msg=f"{code!r} je prošao bez greške"):
                resolve(code)

    def test_whitespace_tolerated(self):
        self.assertEqual(resolve(" USD "), ("USD", 1))
        self.assertEqual(resolve("GBX "), ("GBP", 100))


class TestConversion(unittest.TestCase):
    def test_eur_identity(self):
        self.assertAlmostEqual(FX.to_eur(100, "EUR"), 100.0)

    def test_usd(self):
        self.assertAlmostEqual(FX.to_eur(110, "USD"), 100.0)

    def test_pence_is_not_pounds(self):
        # 1000 GBX = 10 GBP = 11.76 EUR, NE 1176 EUR.
        self.assertAlmostEqual(FX.to_eur(1000, "GBX"), 10 / 0.85, places=6)
        self.assertAlmostEqual(FX.to_eur(1000, "GBX") * 100, FX.to_eur(1000, "GBP"), places=6)

    def test_pence_spellings_agree(self):
        vals = [FX.to_eur(500, c) for c in ("GBX", "GBx", "GBp")]
        self.assertAlmostEqual(min(vals), max(vals))

    def test_missing_rate_raises(self):
        with self.assertRaises(UnknownCurrency):
            FX.to_eur(100, "JPY")

    def test_none_amount(self):
        self.assertIsNone(FX.to_eur(None, "USD"))


ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.europa.eu/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2026-07-31">
    <Cube currency="USD" rate="1.1486"/>
    <Cube currency="GBP" rate="0.85566"/>
    <Cube currency="ZAR" rate="20.1"/>
  </Cube></Cube>
</gesmes:Envelope>"""

FRANKFURTER_JSON = b'{"amount":1.0,"base":"EUR","date":"2026-07-31","rates":{"USD":1.1486,"GBP":0.85566}}'


class TestParsers(unittest.TestCase):
    def test_ecb_xml(self):
        rates, date = FxConverter._parse_ecb_xml(ECB_XML)
        self.assertEqual(date, "2026-07-31")
        self.assertAlmostEqual(rates["GBP"], 0.85566)
        self.assertEqual(len(rates), 3)

    def test_frankfurter_json(self):
        rates, date = FxConverter._parse_json(FRANKFURTER_JSON)
        self.assertEqual(date, "2026-07-31")
        self.assertAlmostEqual(rates["USD"], 1.1486)

    def test_parsers_agree(self):
        xml_rates, _ = FxConverter._parse_ecb_xml(ECB_XML)
        json_rates, _ = FxConverter._parse_json(FRANKFURTER_JSON)
        for code in json_rates:
            self.assertAlmostEqual(xml_rates[code], json_rates[code], places=6)

    def test_non_eur_base_rejected(self):
        with self.assertRaises(ValueError):
            FxConverter._parse_json(b'{"base":"USD","date":"x","rates":{"EUR":0.9}}')


class TestAuth(unittest.TestCase):
    def test_basic_when_secret_present(self):
        c = T212Client(api_key="kljuc", api_secret="tajna", env="live")
        expected = base64.b64encode(b"kljuc:tajna").decode()
        self.assertEqual(c.auth_header, f"Basic {expected}")

    def test_legacy_when_secret_absent(self):
        c = T212Client(api_key="stariKljuc", api_secret="", env="live")
        self.assertEqual(c.auth_header, "stariKljuc")

    def test_demo_base_url(self):
        self.assertIn("demo", T212Client(api_key="k", api_secret="s", env="demo").base)

    def test_whitespace_stripped(self):
        # Copy-paste iz T212 sučelja zna povući razmak/newline.
        c = T212Client(api_key="  kljuc\n", api_secret=" tajna ", env="live")
        self.assertEqual(c.auth_header, f"Basic {base64.b64encode(b'kljuc:tajna').decode()}")


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        self.conn = db.connect(self.path)
        self.positions = [
            position("VUAA_EQ", "EUR", 10, 100.0, 1000.0, name="S&P 500"),
            position("AAPL_US_EQ", "USD", 2, 220.0, 400.0, name="Apple"),
            # Shell na LSE: 100 kom × 2750 PENIJA = 275 000 GBX = 2750 GBP ≈ 3235 EUR
            position("SHEL_EQ", "GBX", 100, 2750.0, 3235.294118, name="Shell"),
        ]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _save(self):
        return db.save_snapshot(self.conn, SUMMARY, self.positions, FX)

    def test_pence_position_not_inflated(self):
        sid, warnings = self._save()
        rows = {r["ticker"]: r for r in self.conn.execute(
            "SELECT * FROM positions WHERE snapshot_id = ?", (sid,))}
        shel = rows["SHEL_EQ"]
        # izvorna vrijednost ostaje u penijima, uz oznaku valute
        self.assertAlmostEqual(shel["market_value_native"], 275000.0)
        self.assertEqual(shel["currency"], "GBX")
        # EUR vrijednost je ~3235, ne 323 529
        self.assertAlmostEqual(shel["market_value_eur"], 3235.294118, places=4)
        self.assertLess(shel["market_value_eur"], 5000)
        # obje metode se slažu => nema upozorenja
        self.assertLess(abs(shel["check_delta_pct"]), 0.01)
        self.assertEqual(warnings, [])

    def test_cross_check_catches_unrecognised_minor_unit(self):
        # Simulacija: valuta se tretira kao GBP iako su iznosi u penijima.
        # Kontrolni izračun tada je 100× veći i mora podići upozorenje.
        self.positions[2]["instrument"]["currency"] = "GBP"
        sid, warnings = self._save()
        self.assertEqual([w["ticker"] for w in warnings], ["SHEL_EQ"])
        self.assertGreater(warnings[0]["delta_pct"], 9000)

    def test_totals_in_eur(self):
        sid, _ = self._save()
        snap = self.conn.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
        self.assertAlmostEqual(snap["positions_value_eur"], 1000.0 + 400.0 + 3235.294118, places=4)
        self.assertEqual(snap["account_currency"], "EUR")
        self.assertAlmostEqual(snap["total_value_eur"], 5250.0)
        self.assertAlmostEqual(snap["account_fx_rate"], 1.0)

    def test_non_eur_account_currency(self):
        # Račun u USD: svi *_acct iznosi moraju se podijeliti s 1.10.
        summary = dict(SUMMARY, currency="USD")
        positions = [position("AAPL_US_EQ", "USD", 2, 220.0, 440.0, wallet_ccy="USD")]
        sid, warnings = db.save_snapshot(self.conn, summary, positions, FX)
        snap = self.conn.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
        self.assertAlmostEqual(snap["total_value_eur"], 5250.0 / 1.10)
        self.assertAlmostEqual(snap["positions_value_eur"], 440.0 / 1.10)
        self.assertEqual(warnings, [])

    def test_allocation_view(self):
        self._save()
        rows = list(self.conn.execute("SELECT * FROM v_latest_allocation"))
        self.assertEqual(rows[0]["ticker"], "SHEL_EQ")  # najveća pozicija
        self.assertAlmostEqual(sum(r["pct_of_positions"] for r in rows), 100.0, places=1)

    def test_fx_sanity_view_empty_when_consistent(self):
        self._save()
        self.assertEqual(list(self.conn.execute("SELECT * FROM v_fx_sanity")), [])

    def test_position_without_currency_rejected(self):
        self.positions.append({"instrument": {"ticker": "NEMA_EQ"}, "quantity": 1,
                               "currentPrice": 10.0})
        with self.assertRaises(UnknownCurrency):
            self._save()

    def test_summary_without_currency_rejected(self):
        with self.assertRaises(UnknownCurrency):
            db.save_snapshot(self.conn, dict(SUMMARY, currency=None), self.positions, FX)

    def test_instruments_registry_filled(self):
        self._save()
        rows = {r["ticker"]: r["currency_code"] for r in
                self.conn.execute("SELECT ticker, currency_code FROM instruments")}
        self.assertEqual(rows["SHEL_EQ"], "GBX")
        self.assertEqual(len(rows), 3)

    def test_fx_rates_stored_for_audit(self):
        sid, _ = self._save()
        row = self.conn.execute("SELECT fx_date, fx_rates_json FROM snapshots WHERE id = ?",
                                (sid,)).fetchone()
        self.assertEqual(row["fx_date"], "2026-08-01")
        self.assertIn("GBP", row["fx_rates_json"])

    def test_last_known_rates_roundtrip(self):
        self._save()
        rates, date = db.last_known_rates(self.conn)
        self.assertEqual(date, "2026-08-01")
        self.assertAlmostEqual(rates["GBP"], 0.85)

    def test_reconnect_is_idempotent(self):
        self._save()
        self.conn.close()
        self.conn = db.connect(self.path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)


class TestCoverage(unittest.TestCase):
    def test_every_minor_unit_has_ecb_major(self):
        for code, (major, _) in MINOR_UNITS.items():
            self.assertIn(major, RATES, f"{code} -> {major} nema tečaj")


if __name__ == "__main__":
    unittest.main(verbosity=2)
