#!/usr/bin/env python3
"""Adversarial and Edge-Case Test Suite for Binance 24/7 Trading Desk.

Tests strictly offline with mocked transports. No live network calls.
Covers:
1. Misaligned tick and step sizes (min bounds not integer multiples of increment).
2. Directional rounding and boundary validations.
3. Invalid symbol metadata and filter parsing resilience.
4. Empty and incomplete historical pages.
5. Missing candles and gap window splitting.
6. Server timeout after order submission and ambiguous recovery.
7. Partial fills and base asset commission net sellable sizing.
8. Protective-exit rejection and bounded emergency recovery.
9. Position-mode mismatch (One-Way vs Hedge mode).
10. Journal crash-consistency and atomic backup recovery.
11. Secrets protection in logs and representations.
12. Safety gates preventing accidental live production access.
"""

import copy
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import io
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

# Ensure root in sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
broker_dir = os.path.join(ROOT, "trader-broker")
if broker_dir not in sys.path:
    sys.path.insert(0, broker_dir)

import binance_client
from binance_client import (
    BinanceClient,
    BinanceAPIError,
    BinanceRateLimitError,
    BinanceAmbiguousError,
    parse_exchange_filters,
    quantize_price,
    quantize_quantity,
    validate_order,
    detect_gaps,
    split_windows_at_gaps,
    INTERVAL_MS
)
import binance_broker


class MockHTTPResponse:
    def __init__(self, data, code=200, headers=None):
        self.code = code
        self.headers = headers or {}
        if isinstance(data, (dict, list)):
            self.body = json.dumps(data).encode("utf-8")
        elif isinstance(data, str):
            self.body = data.encode("utf-8")
        else:
            self.body = data

    def read(self):
        return self.body

    def decode(self, *args, **kwargs):
        return self.body.decode(*args, **kwargs)


class TestDecimalExchangeFilterArithmetic(unittest.TestCase):
    """Rigorous audit of Decimal quantization, grid origin, and directional rounding."""

    def test_min_bounds_not_integer_multiples_of_increment(self):
        # minPrice = 0.05, tickSize = 0.02.
        # 0.05 is NOT an integer multiple of 0.02 (0.02 * 2 = 0.04, 0.02 * 3 = 0.06).
        # Standard Binance rule: price must be a multiple of tickSize and >= minPrice.
        # Under the flawed formula ((raw - min)//ts * ts + min):
        # (1.00 - 0.05)//0.02 * 0.02 + 0.05 = 0.94 + 0.05 = 0.99 (NOT multiple of 0.02!).
        # Under the correct formula (raw // ts * ts):
        # 1.00 // 0.02 * 0.02 = 1.00 (EXACT multiple of 0.02!).
        raw_price = Decimal("1.00")
        tick_size = Decimal("0.02")
        min_price = Decimal("0.05")

        q_price = quantize_price(raw_price, tick_size)
        self.assertEqual(q_price, Decimal("1.00"))
        self.assertEqual(q_price % tick_size, Decimal("0"))
        self.assertTrue(q_price >= min_price)

        # Same for quantity: minQty = 0.07, stepSize = 0.05.
        raw_qty = Decimal("1.00")
        step_size = Decimal("0.05")
        min_qty = Decimal("0.07")

        q_qty = quantize_quantity(raw_qty, step_size)
        self.assertEqual(q_qty, Decimal("1.00"))
        self.assertEqual(q_qty % step_size, Decimal("0"))
        self.assertTrue(q_qty >= min_qty)

    def test_directional_rounding(self):
        ts = Decimal("0.01")
        # DOWN
        self.assertEqual(quantize_price("102.329", ts, rounding=ROUND_DOWN), Decimal("102.32"))
        self.assertEqual(quantize_price("102.321", ts, rounding=ROUND_DOWN), Decimal("102.32"))
        self.assertEqual(quantize_price("102.320", ts, rounding=ROUND_DOWN), Decimal("102.32"))

        # UP
        self.assertEqual(quantize_price("102.321", ts, rounding=ROUND_UP), Decimal("102.33"))
        self.assertEqual(quantize_price("102.329", ts, rounding=ROUND_UP), Decimal("102.33"))
        self.assertEqual(quantize_price("102.320", ts, rounding=ROUND_UP), Decimal("102.32"))

    def test_invalid_and_boundary_increments(self):
        # Zero or negative tick size returns raw price
        self.assertEqual(quantize_price("100.5", "0"), Decimal("100.5"))
        self.assertEqual(quantize_price("100.5", "-0.01"), Decimal("100.5"))
        self.assertEqual(quantize_price("100.5", None), Decimal("100.5"))

        # Negative price returns 0
        self.assertEqual(quantize_price("-10.0", "0.01"), Decimal("0"))
        self.assertEqual(quantize_quantity("-5.0", "0.01"), Decimal("0"))

    def test_notional_and_lot_size_boundary_validation(self):
        filters = {
            "tick_size": Decimal("0.01"),
            "min_price": Decimal("10.00"),
            "max_price": Decimal("1000.00"),
            "step_size": Decimal("0.01"),
            "min_qty": Decimal("0.10"),
            "max_qty": Decimal("100.00"),
            "min_notional": Decimal("10.00")
        }

        # Quantity exactly at min_qty
        v = validate_order("SOLUSDT", "BUY", price="100.00", quantity="0.10", filters=filters)
        self.assertTrue(v["valid"])

        # Quantity just below min_qty
        v = validate_order("SOLUSDT", "BUY", price="100.00", quantity="0.099", filters=filters)
        self.assertFalse(v["valid"])
        self.assertTrue(any("LOT_SIZE" in e for e in v["errors"]))

        # Notional just below min_notional: 0.10 * 99.90 = $9.99 < $10.00
        v = validate_order("SOLUSDT", "BUY", price="99.90", quantity="0.10", filters=filters)
        self.assertFalse(v["valid"])
        self.assertTrue(any("MIN_NOTIONAL" in e for e in v["errors"]))

        # Price exceeding maxPrice
        v = validate_order("SOLUSDT", "BUY", price="1000.01", quantity="1.0", filters=filters)
        self.assertFalse(v["valid"])
        self.assertTrue(any("PRICE_FILTER" in e for e in v["errors"]))

    def test_resilience_to_malformed_filters(self):
        empty_info = {"symbol": "TESTUSDT", "filters": []}
        f = parse_exchange_filters(empty_info)
        self.assertGreater(f["tick_size"], Decimal("0"))
        self.assertGreater(f["step_size"], Decimal("0"))
        self.assertGreater(f["min_notional"], Decimal("0"))


class TestAdversarialBrokerSafety(unittest.TestCase):
    """Audit safety invariants, recovery, and circuit breakers under failure conditions."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.journal_path = os.path.join(self.temp_dir.name, "orders_journal.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dry_run_is_default_and_sends_no_requests(self):
        client = BinanceClient(api_key="mock", api_secret="mock")
        with patch.object(client, "request") as mock_req:
            cfg = {"paper": True}
            res = binance_broker.execute_bracket_order(
                symbol="SOLUSDT", side="BUY", qty=1.0, entry=150.0, stop=147.0, target=155.0,
                dry_run=True, cfg=cfg, journal_path=self.journal_path
            )
            self.assertEqual(res, 0)
            mock_req.assert_not_called()

    def test_live_production_requires_explicit_confirmation(self):
        # Configuration set to paper: false (Live), but confirm_live flag is False
        cfg = {"paper": False}
        res = binance_broker.execute_bracket_order(
            symbol="SOLUSDT", side="BUY", qty=1.0, entry=150.0, stop=147.0, target=155.0,
            dry_run=False, confirm_live=False, cfg=cfg, journal_path=self.journal_path
        )
        # Must be rejected by Safety Gate 1
        self.assertEqual(res, 1)

    def test_circuit_breaker_halts_all_new_entries(self):
        binance_broker.set_circuit_breaker(True, reason="Unit test emergency halt", path=self.journal_path)
        cfg = {"paper": True}
        res = binance_broker.execute_bracket_order(
            symbol="SOLUSDT", side="BUY", qty=1.0, entry=150.0, stop=147.0, target=155.0,
            dry_run=False, cfg=cfg, journal_path=self.journal_path
        )
        self.assertEqual(res, 1)

    def test_crash_consistency_and_atomic_journal_writes(self):
        # Write valid journal
        intent = binance_broker.persist_order_intent(
            "SOLUSDT", "BUY", Decimal("1.5"), "MARKET", path=self.journal_path
        )
        self.assertTrue(os.path.exists(self.journal_path))

        # Check that backup exists or can recover from corrupted file
        corrupted_content = "INVALID_JSON_CORRUPTED_DURING_CRASH"
        with open(self.journal_path, "w", encoding="utf-8") as fh:
            fh.write(corrupted_content)

        # load_journal should safely fallback to .bak if available, or return default with no crash
        j = binance_broker.load_journal(self.journal_path)
        self.assertIsInstance(j, dict)

    def test_ambiguous_5xx_timeout_reconciles_order_list_and_standard_order(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_order = {"symbol": "SOLUSDT", "clientOrderId": "cid_123", "status": "FILLED"}

        with patch.object(client, "get_order", return_value=mock_order):
            rec = binance_broker.reconcile_ambiguous_order(client, "SOLUSDT", "cid_123", is_algo=False)
            self.assertTrue(rec["landed"])
            self.assertEqual(rec["status"], "FILLED")

        # Test order list reconciliation
        mock_list = {"orderListId": 888, "listClientOrderId": "list_123", "listOrderStatus": "EXEC_STARTED"}
        with patch.object(client, "get_spot_order_list", return_value=mock_list):
            rec_list = binance_broker.reconcile_ambiguous_order(client, "SOLUSDT", "list_123", is_order_list=True)
            self.assertTrue(rec_list["landed"])
            self.assertEqual(rec_list["status"], "EXEC_STARTED")

    def test_actual_spot_commission_base_asset(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_entry = {
            "orderId": 1234,
            "fills": [
                {"qty": "2.0000", "price": "150.00", "commission": "0.0020", "commissionAsset": "SOL"}
            ]
        }
        mock_acc = {"balances": [{"asset": "SOL", "free": "2.5000", "locked": "0.0000"}]}
        filters = {"step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "min_notional": Decimal("5.0"),
                   "base_asset": "SOL", "quote_asset": "USDT"}

        with patch.object(client, "get_account", return_value=mock_acc):
            recon = binance_broker.reconcile_spot_fill_and_available_qty(
                client, "SOLUSDT", 1234, entry_res=mock_entry, filters=filters, stop_price="145.0"
            )
            self.assertTrue(recon["valid"])
            # 2.0 - 0.002 = 1.998 -> quantized down to 1.99
            self.assertEqual(recon["sellable_qty"], Decimal("1.99"))
            self.assertEqual(recon["base_commission_deducted"], Decimal("0.0020"))

    def test_actual_spot_commission_quote_asset(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_entry = {
            "orderId": 1235,
            "fills": [
                {"qty": "2.0000", "price": "150.00", "commission": "0.3000", "commissionAsset": "USDT"}
            ]
        }
        mock_acc = {"balances": [{"asset": "SOL", "free": "2.0000", "locked": "0.0000"}]}
        filters = {"step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "min_notional": Decimal("5.0"),
                   "base_asset": "SOL", "quote_asset": "USDT"}

        with patch.object(client, "get_account", return_value=mock_acc):
            recon = binance_broker.reconcile_spot_fill_and_available_qty(
                client, "SOLUSDT", 1235, entry_res=mock_entry, filters=filters, stop_price="145.0"
            )
            self.assertTrue(recon["valid"])
            # Fee in USDT does not diminish SOL balance: full 2.00 SOL sellable
            self.assertEqual(recon["sellable_qty"], Decimal("2.00"))
            self.assertEqual(recon["base_commission_deducted"], Decimal("0"))

    def test_actual_spot_commission_bnb_discount(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_entry = {
            "orderId": 1236,
            "fills": [
                {"qty": "2.0000", "price": "150.00", "commission": "0.0005", "commissionAsset": "BNB"}
            ]
        }
        mock_acc = {"balances": [{"asset": "SOL", "free": "2.0000", "locked": "0.0000"}]}
        filters = {"step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "min_notional": Decimal("5.0"),
                   "base_asset": "SOL", "quote_asset": "USDT"}

        with patch.object(client, "get_account", return_value=mock_acc):
            recon = binance_broker.reconcile_spot_fill_and_available_qty(
                client, "SOLUSDT", 1236, entry_res=mock_entry, filters=filters, stop_price="145.0"
            )
            self.assertTrue(recon["valid"])
            # Fee in BNB does not diminish SOL balance: full 2.00 SOL sellable
            self.assertEqual(recon["sellable_qty"], Decimal("2.00"))
            self.assertEqual(recon["base_commission_deducted"], Decimal("0"))

    def test_actual_spot_multiple_partial_fills(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_entry = {
            "orderId": 1237,
            "fills": [
                {"qty": "0.5000", "price": "150.00", "commission": "0.0005", "commissionAsset": "SOL"},
                {"qty": "0.8000", "price": "150.00", "commission": "0.0008", "commissionAsset": "SOL"},
                {"qty": "0.7000", "price": "150.00", "commission": "0.0007", "commissionAsset": "SOL"}
            ]
        }
        mock_acc = {"balances": [{"asset": "SOL", "free": "2.0000", "locked": "0.0000"}]}
        filters = {"step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "min_notional": Decimal("5.0"),
                   "base_asset": "SOL", "quote_asset": "USDT"}

        with patch.object(client, "get_account", return_value=mock_acc):
            recon = binance_broker.reconcile_spot_fill_and_available_qty(
                client, "SOLUSDT", 1237, entry_res=mock_entry, filters=filters, stop_price="145.0"
            )
            self.assertTrue(recon["valid"])
            self.assertEqual(recon["fills_count"], 3)
            self.assertEqual(recon["total_exec_qty"], Decimal("2.0000"))
            self.assertEqual(recon["base_commission_deducted"], Decimal("0.0020"))
            # 2.0 - 0.002 = 1.998 -> quantized to 1.99
            self.assertEqual(recon["sellable_qty"], Decimal("1.99"))

    def test_actual_spot_missing_fee_data_halts_without_guessing(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        # Fill is missing commissionAsset
        bad_entry = {
            "orderId": 1238,
            "fills": [
                {"qty": "1.0000", "price": "150.00", "commission": "0.001"} # Missing commissionAsset!
            ]
        }
        filters = {"step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "min_notional": Decimal("5.0")}
        recon = binance_broker.reconcile_spot_fill_and_available_qty(
            client, "SOLUSDT", 1238, entry_res=bad_entry, filters=filters
        )
        # MUST NOT GUESS 0.1% FEE!
        self.assertFalse(recon["valid"])
        self.assertEqual(recon["reason"], "MISSING_FEE_FIELD")

    def test_actual_spot_quantity_and_notional_filter_failures(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_entry = {
            "orderId": 1239,
            "fills": [{"qty": "0.005", "price": "150.00", "commission": "0.000005", "commissionAsset": "SOL"}]
        }
        mock_acc = {"balances": [{"asset": "SOL", "free": "1.0000", "locked": "0.0000"}]}
        # min_qty is 0.01, but available qty is only 0.005
        filters_qty = {"step_size": Decimal("0.001"), "min_qty": Decimal("0.01"), "min_notional": Decimal("0.1"),
                       "base_asset": "SOL", "quote_asset": "USDT"}

        with patch.object(client, "get_account", return_value=mock_acc):
            recon = binance_broker.reconcile_spot_fill_and_available_qty(
                client, "SOLUSDT", 1239, entry_res=mock_entry, filters=filters_qty, stop_price="145.0"
            )
            self.assertFalse(recon["valid"])
            self.assertEqual(recon["reason"], "QTY_FILTER_BELOW_MIN")

        # Notional filter failure at stop price: 0.02 * 100 = $2.00 < min_notional $5.00
        mock_entry2 = {
            "orderId": 1240,
            "fills": [{"qty": "0.02", "price": "100.00", "commission": "0.00002", "commissionAsset": "SOL"}]
        }
        filters_notional = {"step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "min_notional": Decimal("5.0"),
                            "base_asset": "SOL", "quote_asset": "USDT"}
        with patch.object(client, "get_account", return_value=mock_acc):
            recon = binance_broker.reconcile_spot_fill_and_available_qty(
                client, "SOLUSDT", 1240, entry_res=mock_entry2, filters=filters_notional, stop_price="100.0"
            )
            self.assertFalse(recon["valid"])
            self.assertEqual(recon["reason"], "NOTIONAL_FILTER_STOP_FAILED")

    def test_actual_spot_free_balance_caps_available_qty(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_entry = {
            "orderId": 1241,
            "fills": [{"qty": "2.0000", "price": "150.00", "commission": "0.30", "commissionAsset": "USDT"}]
        }
        # Account free balance is only 1.25 SOL (less than the 2.0 SOL fill, due to other orders/locks)
        mock_acc = {"balances": [{"asset": "SOL", "free": "1.2500", "locked": "0.7500"}]}
        filters = {"step_size": Decimal("0.01"), "min_qty": Decimal("0.01"), "min_notional": Decimal("5.0"),
                   "base_asset": "SOL", "quote_asset": "USDT"}

        with patch.object(client, "get_account", return_value=mock_acc):
            recon = binance_broker.reconcile_spot_fill_and_available_qty(
                client, "SOLUSDT", 1241, entry_res=mock_entry, filters=filters, stop_price="145.0"
            )
            self.assertTrue(recon["valid"])
            # Genuinely available quantity is capped to free_balance 1.25
            self.assertEqual(recon["sellable_qty"], Decimal("1.25"))

    def test_secrets_never_appear_in_repr_or_logs(self):
        client = BinanceClient(api_key="vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A",
                               api_secret="NhqPtMDsduVQKdanErVAMybgKLfvUdUlBpDuMiCd6rgeNdNkk7YnjeAnqAiDxqXl")
        r = repr(client)
        self.assertNotIn("NhqPtMDsduVQKdanErVAMybgKLfvUdUlBpDuMiCd6rgeNdNkk7YnjeAnqAiDxqXl", r)
        self.assertIn("api_secret=***", r)

    def test_position_mode_one_way_vs_hedge_parameters(self):
        # In One-Way Mode, positionSide must be BOTH and reduceOnly must be true for exits
        client = BinanceClient(api_key="mock", api_secret="mock", product="futures")
        with patch.object(client, "request") as mock_req:
            client.create_order(symbol="BTCUSDT", side="SELL", order_type="MARKET",
                                quantity="0.5", position_side="BOTH", reduce_only=True)
            args, kwargs = mock_req.call_args
            params = dict(kwargs["params"])
            self.assertEqual(params["positionSide"], "BOTH")
            self.assertEqual(params["reduceOnly"], "true")

        # In Hedge Mode, long exit positionSide must be LONG and reduceOnly must be false (or omitted)
        with patch.object(client, "request") as mock_req:
            client.create_order(symbol="BTCUSDT", side="SELL", order_type="MARKET",
                                quantity="0.5", position_side="LONG", reduce_only=False)
            args, kwargs = mock_req.call_args
            params = dict(kwargs["params"])
            self.assertEqual(params["positionSide"], "LONG")
            self.assertEqual(params["reduceOnly"], "false")

        # In Futures Algo Orders: One-Way includes reduceOnly, Hedge Mode omits reduceOnly
        with patch.object(client, "request") as mock_req:
            client.create_futures_algo_order(symbol="BTCUSDT", side="SELL", algo_type="STOP_MARKET",
                                            trigger_price="50000", position_side="BOTH", reduce_only=True)
            args, kwargs = mock_req.call_args
            params = dict(kwargs["params"])
            self.assertEqual(params["positionSide"], "BOTH")
            self.assertEqual(params["reduceOnly"], "true")

        with patch.object(client, "request") as mock_req:
            client.create_futures_algo_order(symbol="BTCUSDT", side="SELL", algo_type="STOP_MARKET",
                                            trigger_price="50000", position_side="LONG", reduce_only=False)
            args, kwargs = mock_req.call_args
            params = dict(kwargs["params"])
            self.assertEqual(params["positionSide"], "LONG")
            self.assertNotIn("reduceOnly", params)

    def test_spot_short_entry_rejected(self):
        # Attempting a SELL entry on Spot must be rejected immediately
        res = binance_broker.execute_bracket_order(
            symbol="SOLUSDT", side="SELL", qty="1.0", market="spot", dry_run=True
        )
        self.assertEqual(res, 1)

    def test_paper_flag_cli_routing(self):
        # Passing --paper must route to testnet without throwing unrecognized argument
        with patch("binance_broker.execute_bracket_order", return_value=0) as mock_exec:
            ret = binance_broker.main([
                "--symbol", "SOLUSDT", "--side", "BUY", "--qty", "1.0",
                "--entry", "150.0", "--stop", "145.0", "--target", "160.0",
                "--type", "LIMIT", "--market", "spot", "--paper"
            ])
            self.assertEqual(ret, 0)
            args, kwargs = mock_exec.call_args
            self.assertTrue(kwargs["cfg"].get("paper"))

    def test_orphaned_orders_cancelled_and_exposure_strictly_bounded(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="futures")
        mock_open_algos = [{"algoId": 1001, "symbol": "SOLUSDT"}]
        mock_pos = [{"symbol": "SOLUSDT", "positionAmt": "2.50", "positionSide": "BOTH"}]
        mock_ex_info = {"symbols": [{"symbol": "SOLUSDT", "filters": [
            {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "1000", "stepSize": "0.01"}
        ]}]}

        with patch.object(client, "cancel_all_open_orders") as mock_cancel_all, \
             patch.object(client, "get_open_futures_algo_orders", return_value=mock_open_algos), \
             patch.object(client, "cancel_futures_algo_order") as mock_cancel_algo, \
             patch.object(client, "get_position_risk", side_effect=[mock_pos, []]), \
             patch.object(client, "get_exchange_info", return_value=mock_ex_info), \
             patch.object(client, "create_order") as mock_create_order:

            rec = binance_broker.bounded_emergency_recovery(client, "SOLUSDT", market="futures", journal_path=self.journal_path)
            self.assertTrue(rec["recovered"])
            mock_cancel_all.assert_called_with("SOLUSDT")
            mock_cancel_algo.assert_called_with(algo_id=1001)
            # Must submit closing order sized strictly to verified live exposure (2.50 SOL)
            mock_create_order.assert_called_once()
            _, kwargs = mock_create_order.call_args
            self.assertEqual(kwargs["side"], "SELL")
            self.assertEqual(kwargs["quantity"], Decimal("2.50"))

    def test_failed_cancellation_handled_gracefully(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        with patch.object(client, "cancel_all_open_orders", side_effect=BinanceAPIError("Cancel failed")), \
             patch.object(client, "get_account", return_value={"balances": [{"asset": "SOL", "free": "0"}]}):
            rec = binance_broker.bounded_emergency_recovery(client, "SOLUSDT", market="spot", journal_path=self.journal_path)
            # Recovery should not crash; it logs warning and confirms zero exposure
            self.assertTrue(rec["recovered"])
            self.assertTrue(any("Warning canceling" in a for a in rec["actions"]))

    def test_testnet_never_contacts_production_hosts(self):
        spot_testnet = BinanceClient(product="spot", testnet=True)
        self.assertEqual(spot_testnet.base_url, "https://testnet.binance.vision")
        self.assertNotIn("api.binance.com", spot_testnet.base_url)

        futures_testnet = BinanceClient(product="futures", testnet=True)
        self.assertEqual(futures_testnet.base_url, "https://testnet.binancefuture.com")
        self.assertNotIn("fapi.binance.com", futures_testnet.base_url)


class TestBacktestHonestyAndGapIntegrity(unittest.TestCase):
    """Audit historical simulation, candle gap splits, and conservative fill assumptions."""

    def test_reproduce_metrics_from_trade_log(self):
        import sys
        if "trader-timemachine" not in sys.path:
            sys.path.insert(0, "trader-timemachine")
        import timemachine

        # Synthetic trade log: 6 wins of +2R, 4 losses of -1R
        mock_trades = []
        for i in range(6):
            mock_trades.append({"entry_time": i, "exit_time": i+1, "entry": 100, "exit": 104,
                                "type": "target", "net_pnl": 4.0, "net_return_pct": 4.0, "r": 2.0, "win": True})
        for i in range(6, 10):
            mock_trades.append({"entry_time": i, "exit_time": i+1, "entry": 100, "exit": 98,
                                "type": "stop", "net_pnl": -2.0, "net_return_pct": -2.0, "r": -1.0, "win": False})

        stats = timemachine._calc_stats(mock_trades)
        self.assertEqual(stats["total_trades"], 10)
        self.assertEqual(stats["wins"], 6)
        self.assertEqual(stats["losses"], 4)
        self.assertEqual(stats["win_rate_pct"], 60.0)
        # Expected R: (6 * 2.0 + 4 * -1.0) / 10 = (12 - 4) / 10 = 0.8 R
        self.assertEqual(stats["expectancy_r"], 0.8)
        # Profit factor: (6 * 4) / (4 * 2) = 24 / 8 = 3.0
        self.assertEqual(stats["profit_factor"], 3.0)

    def test_gap_detection_and_clean_splitting(self):
        interval_ms = 15 * 60 * 1000  # 15m
        t0 = 1700000000000

        # Create 10 continuous candles, then 5 missing candles, then 10 continuous candles
        seg1 = [[t0 + i * interval_ms, "100", "105", "99", "102", "1000", t0 + (i + 1) * interval_ms - 1] for i in range(10)]
        t_gap = t0 + 15 * interval_ms  # 5 missing candles
        seg2 = [[t_gap + i * interval_ms, "102", "106", "101", "104", "1200", t_gap + (i + 1) * interval_ms - 1] for i in range(10)]

        all_klines = seg1 + seg2
        gaps = detect_gaps(all_klines, interval_ms)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["missing_candles"], 5)

        windows = split_windows_at_gaps(all_klines, interval_ms)
        self.assertEqual(len(windows), 2)
        self.assertEqual(len(windows[0]), 10)
        self.assertEqual(len(windows[1]), 10)

    def test_unclosed_candle_is_dropped(self):
        interval_ms = 15 * 60 * 1000
        now_ms = 1700000000000
        # Candle ends after target_end_ms
        klines = [
            [now_ms - interval_ms, "100", "105", "99", "102", "10", now_ms - 1],
            [now_ms, "102", "106", "101", "104", "10", now_ms + interval_ms - 1] # in progress!
        ]
        client = BinanceClient()
        with patch.object(client, "get_klines", return_value=klines):
            res = client.get_klines_backward_paginated("SOLUSDT", interval="15m", days=1, end_time=now_ms)
            # The second unclosed candle must be excluded
            self.assertEqual(len(res["klines"]), 1)
            self.assertEqual(res["klines"][0][0], now_ms - interval_ms)


if __name__ == "__main__":
    unittest.main()

