#!/usr/bin/env python3
"""Offline unit tests for Binance client, decimal filters, and HMAC signing."""

import decimal
from decimal import Decimal
import hmac
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import binance_client
from binance_client import (
    BinanceClient,
    parse_exchange_filters,
    quantize_price,
    quantize_quantity,
    validate_order,
    INTERVAL_MS
)


class TestBinanceFilterPrecision(unittest.TestCase):
    def setUp(self):
        self.sample_info = {
            "symbol": "SOLUSDT",
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.01000000",
                    "maxPrice": "10000.00000000",
                    "tickSize": "0.01000000"
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.01000000",
                    "maxQty": "9000.00000000",
                    "stepSize": "0.01000000"
                },
                {
                    "filterType": "NOTIONAL",
                    "minNotional": "5.00000000"
                }
            ]
        }
        self.filters = parse_exchange_filters(self.sample_info)

    def test_filter_parsing(self):
        self.assertEqual(self.filters["tick_size"], Decimal("0.01"))
        self.assertEqual(self.filters["step_size"], Decimal("0.01"))
        self.assertEqual(self.filters["min_notional"], Decimal("5.0"))

    def test_quantize_price(self):
        # Should truncate down to 2 decimals (tickSize = 0.01)
        raw_price = Decimal("142.34567")
        q_price = quantize_price(raw_price, self.filters["tick_size"])
        self.assertEqual(q_price, Decimal("142.34"))

    def test_quantize_quantity(self):
        # Should truncate down to stepSize multiple
        raw_qty = Decimal("0.1428571")
        q_qty = quantize_quantity(raw_qty, self.filters["step_size"])
        self.assertEqual(q_qty, Decimal("0.14"))

    def test_validate_order_valid(self):
        # 1.5 SOL at $140 = $210 notional (exceeds minNotional $5)
        res = validate_order("SOLUSDT", "BUY", price="140.25", quantity="1.5", filters=self.filters)
        self.assertTrue(res["valid"])
        self.assertEqual(res["quantized_price"], Decimal("140.25"))
        self.assertEqual(res["quantized_qty"], Decimal("1.50"))
        self.assertEqual(res["notional"], Decimal("210.375"))

    def test_validate_order_min_notional_failure(self):
        # 0.01 SOL at $140 = $1.40 notional (fails minNotional $5)
        res = validate_order("SOLUSDT", "BUY", price="140.00", quantity="0.01", filters=self.filters)
        self.assertFalse(res["valid"])
        self.assertTrue(any("MIN_NOTIONAL" in err for err in res["errors"]))

    def test_validate_order_lot_size_failure(self):
        # Below minQty
        res = validate_order("SOLUSDT", "BUY", price="140.00", quantity="0.005", filters=self.filters)
        self.assertFalse(res["valid"])
        self.assertTrue(any("LOT_SIZE" in err for err in res["errors"]))


class TestBinanceSigning(unittest.TestCase):
    def test_hmac_sha256_signature(self):
        key = "test_api_key"
        secret = "test_secret_key"
        client = BinanceClient(api_key=key, api_secret=secret, product="spot")

        params = {"symbol": "BTCUSDT", "side": "BUY", "timestamp": 1700000000000, "recvWindow": 5000}
        signed = client.sign_params(params)

        # Replicate expected HMAC
        expected_query = "symbol=BTCUSDT&side=BUY&timestamp=1700000000000&recvWindow=5000"
        expected_sig = hmac.new(secret.encode("utf-8"), expected_query.encode("utf-8"), hashlib.sha256).hexdigest()

        self.assertEqual(signed["signature"], expected_sig)
        self.assertIn("signature", signed)


class TestPaginationContinuity(unittest.TestCase):
    def test_gap_detection(self):
        interval_ms = INTERVAL_MS["5m"]
        t0 = 1700000000000
        # Create candles with a gap at index 2
        klines = [
            [t0, "100", "105", "99", "104", "10", t0 + interval_ms - 1],
            [t0 + interval_ms, "104", "106", "103", "105", "12", t0 + 2 * interval_ms - 1],
            # Missed t0 + 2*interval_ms
            [t0 + 3 * interval_ms, "105", "108", "104", "107", "15", t0 + 4 * interval_ms - 1],
        ]

        client = BinanceClient()
        with patch.object(client, "get_klines", return_value=klines):
            res = client.get_klines_paginated("BTCUSDT", interval="5m", days=1, end_time=t0 + 4 * interval_ms)
            self.assertEqual(len(res["gaps"]), 1)
            self.assertEqual(res["gaps"][0]["missing_candles"], 1)


class TestScannerBinanceExtensions(unittest.TestCase):
    def test_calculate_squeeze(self):
        from _load import load
        scan = load("trader-scanner", "scan")
        # 30 bars with very tight range (squeeze)
        n = 30
        c = [100.0 + (i % 2) * 0.1 for i in range(n)]
        h = [x + 0.1 for x in c]
        l = [x - 0.1 for x in c]
        v = [1000.0] * n
        series = {"c": c, "h": h, "l": l, "v": v, "t": list(range(n))}
        res = scan.calculate_squeeze(series)
        self.assertIsNotNone(res)
        self.assertIn(res["status"], ("IN_SQUEEZE", "NORMAL"))
        self.assertTrue(res["bb_width_pct"] < 2.0)

    def test_get_active_session(self):
        import datetime
        from _load import load
        scan = load("trader-scanner", "scan")
        # 02:00 UTC -> Asian open
        t_asian = datetime.datetime(2026, 10, 29, 2, 0, 0)
        label, s_open = scan.get_active_session(t_asian)
        self.assertIn("Asian", label)
        self.assertEqual(s_open.hour, 0)

        # 09:00 UTC -> London open
        t_london = datetime.datetime(2026, 10, 29, 9, 0, 0)
        label, s_open = scan.get_active_session(t_london)
        self.assertIn("London", label)
        self.assertEqual(s_open.hour, 8)

        # 14:00 UTC -> New York open
        t_ny = datetime.datetime(2026, 10, 29, 14, 0, 0)
        label, s_open = scan.get_active_session(t_ny)
        self.assertIn("New York", label)
        self.assertEqual(s_open.hour, 13)
        self.assertEqual(s_open.minute, 30)


if __name__ == "__main__":
    unittest.main()
