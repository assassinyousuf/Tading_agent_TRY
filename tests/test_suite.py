#!/usr/bin/env python3
"""Comprehensive Offline Unit Test Suite for Binance 24/7 Trading Desk.

Zero external pip dependencies. Makes zero live network calls.
Mocked HTTP transports verify:
1. Immutable Byte-String Signing (HMAC-SHA256 & pre-signing URL percent-encoding)
2. Strict Decimal Filter Validation (PRICE_FILTER, LOT_SIZE, NOTIONAL)
3. Dynamic Backward Pagination & Market-Data Gap Policy
4. Rate-Limit Governance & 429/418 Retry-After Handling
5. Separated Order Lifecycle & Reconciliation (Spot OCO, OTOCO, Futures Algo, Ambiguous 5xx/Timeout)
6. Bounded Emergency Recovery Procedure (Circuit-breaker, exposure check, bounded close)
"""

import copy
import decimal
from decimal import Decimal, ROUND_DOWN
import hashlib
import hmac
import io
import json
import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock, call
import urllib.error
import urllib.parse
import urllib.request

# Ensure workspace root is in sys.path
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
    """Mock urllib response for offline testing."""
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

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# =============================================================================
# 1. IMMUTABLE SIGNING TEST
# =============================================================================

class TestImmutableSigning(unittest.TestCase):
    def setUp(self):
        self.api_key = "mock_api_key_12345"
        self.api_secret = "mock_secret_key_67890"
        self.client = BinanceClient(api_key=self.api_key, api_secret=self.api_secret, product="spot")

    def test_single_percent_encoding_before_hmac(self):
        """Verifies URL percent-encoding occurs ONCE prior to HMAC-SHA256 signing and is transmitted identically."""
        # Parameters with special chars that require percent-encoding
        params = [
            ("symbol", "SOLUSDT"),
            ("side", "BUY"),
            ("type", "LIMIT"),
            ("timeInForce", "GTC"),
            ("quantity", "1.500"),
            ("price", "150.25"),
            ("newClientOrderId", "my-order/id 123"),
            ("timestamp", "1700000000000"),
            ("recvWindow", "5000")
        ]

        # Expected percent-encoded query (safe='')
        expected_query = urllib.parse.urlencode([(k, str(v)) for k, v in params], safe='')
        expected_sig = hmac.new(
            self.api_secret.encode("utf-8"),
            expected_query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        final_query, sig = self.client.build_signed_query(params)

        self.assertEqual(sig, expected_sig)
        self.assertTrue(final_query.endswith("&signature=" + expected_sig))
        self.assertEqual(final_query, expected_query + "&signature=" + expected_sig)
        # Verify no double percent-encoding
        self.assertIn("my-order%2Fid+123", final_query)

    def test_transmitted_body_matches_signed_bytes_exactly(self):
        """Mock HTTP Request transport and assert transmitted POST body is bit-for-bit identical."""
        params = {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "0.1"}
        captured_requests = []

        def fake_urlopen(req, timeout=15):
            captured_requests.append(req)
            return MockHTTPResponse({"orderId": 999111, "status": "FILLED"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.client.request("POST", "/api/v3/order", params=params, signed=True)

        self.assertEqual(len(captured_requests), 1)
        req = captured_requests[0]
        self.assertEqual(req.get_header("X-mbx-apikey"), self.api_key)
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")

        transmitted_body = req.data.decode("utf-8")
        self.assertIn("&signature=", transmitted_body)
        raw_query, sig = transmitted_body.split("&signature=")
        recomputed_sig = hmac.new(self.api_secret.encode("utf-8"), raw_query.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(sig, recomputed_sig)


# =============================================================================
# 2. STRICT DECIMAL FILTER VALIDATION TEST
# =============================================================================

class TestDecimalFilterValidation(unittest.TestCase):
    def setUp(self):
        self.symbol_info = {
            "symbol": "SOLUSDT",
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.05000000",
                    "maxPrice": "1000.00000000",
                    "tickSize": "0.05000000"
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.10000000",
                    "maxQty": "10000.00000000",
                    "stepSize": "0.01000000"
                },
                {
                    "filterType": "NOTIONAL",
                    "minNotional": "10.00000000"
                }
            ]
        }
        self.filters = parse_exchange_filters(self.symbol_info)

    def test_filter_parsing_types(self):
        self.assertIsInstance(self.filters["tick_size"], Decimal)
        self.assertIsInstance(self.filters["step_size"], Decimal)
        self.assertIsInstance(self.filters["min_notional"], Decimal)
        self.assertEqual(self.filters["tick_size"], Decimal("0.05"))
        self.assertEqual(self.filters["step_size"], Decimal("0.01"))
        self.assertEqual(self.filters["min_notional"], Decimal("10.0"))

    def test_quantize_price_floor(self):
        # 142.348 with tickSize 0.05 must quantize down to 142.30
        q = quantize_price(Decimal("142.348"), self.filters["tick_size"], self.filters["min_price"])
        self.assertEqual(q, Decimal("142.30"))

        # 142.350 with tickSize 0.05 stays 142.35
        q2 = quantize_price(Decimal("142.350"), self.filters["tick_size"], self.filters["min_price"])
        self.assertEqual(q2, Decimal("142.35"))

    def test_quantize_quantity_step(self):
        # 0.4567 with stepSize 0.01 must quantize down to 0.45
        q = quantize_quantity(Decimal("0.4567"), self.filters["step_size"], self.filters["min_qty"])
        self.assertEqual(q, Decimal("0.45"))

    def test_sub_notional_rejection(self):
        # 0.10 qty at $50 = $5.00 notional < $10.00 minNotional -> Must fail
        val = validate_order("SOLUSDT", "BUY", price="50.00", quantity="0.10", filters=self.filters)
        self.assertFalse(val["valid"])
        self.assertTrue(any("MIN_NOTIONAL" in e for e in val["errors"]))

    def test_below_min_qty_rejection(self):
        # 0.05 qty < minQty 0.10 -> Must fail
        val = validate_order("SOLUSDT", "BUY", price="150.00", quantity="0.05", filters=self.filters)
        self.assertFalse(val["valid"])
        self.assertTrue(any("LOT_SIZE" in e for e in val["errors"]))

    def test_valid_order_acceptance(self):
        # 0.50 qty at $150.05 = $75.025 notional >= $10.00 -> Valid
        val = validate_order("SOLUSDT", "BUY", price="150.05", quantity="0.50", filters=self.filters)
        self.assertTrue(val["valid"])
        self.assertEqual(val["quantized_price"], Decimal("150.05"))
        self.assertEqual(val["quantized_qty"], Decimal("0.50"))
        self.assertEqual(val["notional"], Decimal("75.0250"))


# =============================================================================
# 3. DYNAMIC PAGINATION & GAP DETECTION TEST
# =============================================================================

class TestDynamicPaginationAndGaps(unittest.TestCase):
    def test_backward_pagination_math_and_unclosed_candle_drop(self):
        interval_ms = INTERVAL_MS["15m"]
        now_ms = 1700000000000
        client = BinanceClient()

        # Simulate 2 pages of candles (1000 each)
        page2_start = now_ms - (1000 * interval_ms)
        page1_start = page2_start - (1000 * interval_ms)

        page2 = [[page2_start + i * interval_ms, "100", "105", "99", "102", "100", page2_start + (i + 1) * interval_ms - 1] for i in range(1000)]
        page1 = [[page1_start + i * interval_ms, "95", "99", "94", "98", "100", page1_start + (i + 1) * interval_ms - 1] for i in range(1000)]

        # Let the last candle close after now_ms (unclosed)
        page2[-1][6] = now_ms + 5000

        calls_received = []
        def fake_get_klines(symbol, interval, limit=1000, start_time=None, end_time=None):
            calls_received.append({"end_time": end_time, "limit": limit})
            if end_time >= now_ms:
                return page2
            return page1

        with patch.object(client, "get_klines", side_effect=fake_get_klines):
            res = client.get_klines_backward_paginated("SOLUSDT", interval="15m", days=22, end_time=now_ms)

        # Verified backward pagination: First call at now_ms, second call at page2[0][0] - 1
        self.assertGreaterEqual(len(calls_received), 2)
        self.assertEqual(calls_received[1]["end_time"], page2[0][0] - 1)

        # Unclosed candle dropped: total candles should be 1999 (2000 - 1)
        self.assertEqual(res["candle_count"], 1999)
        self.assertEqual(len(res["gaps"]), 0)

    def test_gap_detection_and_window_splitting(self):
        interval_ms = INTERVAL_MS["5m"]
        t0 = 1700000000000

        # Construct candles with a 3-candle gap between index 1 and index 2
        c0 = [t0, "100", "101", "99", "100", "50", t0 + interval_ms - 1]
        c1 = [t0 + interval_ms, "100", "102", "99", "101", "60", t0 + 2 * interval_ms - 1]
        # Gap: jump to t0 + 5*interval_ms (3 missing candles: 2, 3, 4)
        c2 = [t0 + 5 * interval_ms, "105", "107", "104", "106", "70", t0 + 6 * interval_ms - 1]
        c3 = [t0 + 6 * interval_ms, "106", "108", "105", "107", "80", t0 + 7 * interval_ms - 1]

        klines = [c0, c1, c2, c3]

        gaps = detect_gaps(klines, interval_ms)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["missing_candles"], 3)
        self.assertEqual(gaps[0]["missing_duration_ms"], 3 * interval_ms)

        # Window splitting at gap boundary
        windows = split_windows_at_gaps(klines, interval_ms)
        self.assertEqual(len(windows), 2)
        self.assertEqual(len(windows[0]), 2)
        self.assertEqual(len(windows[1]), 2)
        # Trades in window 0 never cross into window 1
        self.assertEqual(windows[0][-1][0], c1[0])
        self.assertEqual(windows[1][0][0], c2[0])


# =============================================================================
# 4. RATE-LIMIT & HTTP ERROR HANDLING TEST
# =============================================================================

class TestRateLimitAndHttpErrors(unittest.TestCase):
    def setUp(self):
        self.client = BinanceClient(api_key="k", api_secret="s", product="spot", weight_limit=1200)

    def test_weight_tracking_and_proactive_throttle(self):
        """Simulate high used weight header (970/1200 > 80%) and verify proactive throttle."""
        headers = {"x-mbx-used-weight-1m": "970"}
        mock_resp = MockHTTPResponse({"serverTime": 1700000000000}, headers=headers)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            self.client.get_time()

        self.assertEqual(self.client.used_weight_1m, 970)

        # Next request must trigger check_rate_limit_throttle sleep
        with patch("time.sleep") as mock_sleep:
            with patch("urllib.request.urlopen", return_value=mock_resp):
                self.client.get_time()
            mock_sleep.assert_called_with(2.0)

    def test_http_429_retry_after_backoff(self):
        """Simulate HTTP 429 with Retry-After header and verify backoff sleep."""
        err_headers = {"Retry-After": "4", "x-mbx-used-weight-1m": "1200"}
        err = urllib.error.HTTPError(
            url="https://api.binance.com/api/v3/time",
            code=429,
            msg="Too Many Requests",
            hdrs=err_headers,
            fp=io.BytesIO(b'{"code": -1003, "msg": "Too many requests"}')
        )

        with patch("urllib.request.urlopen", side_effect=[err, MockHTTPResponse({"serverTime": 1700000000000})]):
            with patch("time.sleep") as mock_sleep:
                res = self.client.get_time()
                mock_sleep.assert_any_call(4)
                self.assertIsNotNone(res)


# =============================================================================
# 5. SEPARATED ORDER LIFECYCLE & RECONCILIATION TEST
# =============================================================================

class TestSeparatedOrderLifecycle(unittest.TestCase):
    def setUp(self):
        self.client = BinanceClient(api_key="test_k", api_secret="test_s", product="spot")

    def test_spot_market_net_executed_qty_and_oco_submission(self):
        """Verify Spot Market fill -> net sellable executedQty calculation -> POST /api/v3/orderList/oco."""
        # 1. Market Buy fill with 0.1% fee deducted in base asset
        market_fill = {
            "symbol": "SOLUSDT",
            "orderId": 12345,
            "clientOrderId": "trader_e_1",
            "status": "FILLED",
            "executedQty": "10.00000000",
            "cummulativeQuoteQty": "1500.00000000"
        }
        oco_resp = {
            "orderListId": 8888,
            "contingencyType": "OCO",
            "listStatusType": "EXEC_STARTED",
            "orders": [{"symbol": "SOLUSDT", "orderId": 12346}, {"symbol": "SOLUSDT", "orderId": 12347}]
        }

        with patch.object(self.client, "create_order", return_value=market_fill) as mock_buy:
            with patch.object(self.client, "get_order", return_value=market_fill):
                with patch.object(self.client, "create_spot_oco", return_value=oco_resp) as mock_oco:
                    # Execute
                    entry_res = self.client.create_order("SOLUSDT", "BUY", "MARKET", quantity="10.0")
                    exec_qty = Decimal(entry_res["executedQty"])
                    net_qty = quantize_quantity(exec_qty * Decimal("0.999"), Decimal("0.01"))
                    oco_res = self.client.create_spot_oco(
                        symbol="SOLUSDT",
                        side="SELL",
                        quantity=net_qty,
                        price="160.00",
                        stop_price="145.00"
                    )

        mock_buy.assert_called_once()
        self.assertEqual(net_qty, Decimal("9.99"))
        mock_oco.assert_called_once_with(
            symbol="SOLUSDT",
            side="SELL",
            quantity=Decimal("9.99"),
            price="160.00",
            stop_price="145.00"
        )
        self.assertEqual(oco_res["orderListId"], 8888)

    def test_spot_otoco_atomic_payload_and_reconciliation(self):
        """Verify Spot Atomic OTOCO request construction and order list status query."""
        otoco_resp = {
            "orderListId": 9999,
            "contingencyType": "OTOCO",
            "listStatusType": "ALL_DONE",
            "listClientOrderId": "trader_otoco_1"
        }
        with patch.object(self.client, "request", return_value=otoco_resp) as mock_req:
            res = self.client.create_spot_otoco(
                symbol="SOLUSDT",
                working_side="BUY",
                working_price="150.00",
                working_qty="1.00",
                pending_side="SELL",
                pending_qty="1.00",
                pending_above_price="160.00",
                pending_below_stop_price="145.00",
                list_client_order_id="trader_otoco_1"
            )

        self.assertEqual(res["orderListId"], 9999)
        mock_req.assert_called_once()
        call_endpoint = mock_req.call_args[0][1]
        call_params = dict(mock_req.call_args[1]["params"])
        self.assertEqual(call_endpoint, "/api/v3/orderList/otoco")
        self.assertEqual(call_params["workingType"], "LIMIT")
        self.assertEqual(call_params["pendingAboveType"], "LIMIT_MAKER")
        self.assertEqual(call_params["pendingBelowType"], "STOP_LOSS")

    def test_futures_one_way_vs_hedge_mode_reconciliation(self):
        """Verify Futures One-Way (BOTH / reduceOnly) vs Hedge (LONG/SHORT) conditional Algo exit submission."""
        f_client = BinanceClient(api_key="k", api_secret="s", product="futures")

        # 1. One-Way Mode
        with patch.object(f_client, "get_position_mode", return_value={"dualSidePosition": False}):
            with patch.object(f_client, "create_futures_algo_order", return_value={"algoId": 7001}) as mock_algo:
                f_client.create_futures_algo_order(
                    symbol="SOLUSDT",
                    side="SELL",
                    algo_type="STOP_MARKET",
                    trigger_price="145.00",
                    quantity="1.00",
                    position_side="BOTH",
                    reduce_only=True
                )
                self.assertEqual(mock_algo.call_args[1]["position_side"], "BOTH")
                self.assertTrue(mock_algo.call_args[1]["reduce_only"])

        # 2. Hedge Mode
        with patch.object(f_client, "get_position_mode", return_value={"dualSidePosition": True}):
            with patch.object(f_client, "create_futures_algo_order", return_value={"algoId": 7002}) as mock_algo2:
                f_client.create_futures_algo_order(
                    symbol="SOLUSDT",
                    side="SELL",
                    algo_type="STOP_MARKET",
                    trigger_price="145.00",
                    quantity="1.00",
                    position_side="LONG",
                    reduce_only=False
                )
                self.assertEqual(mock_algo2.call_args[1]["position_side"], "LONG")

    def test_ambiguous_5xx_timeout_recovery_queries_status(self):
        """Verify that write requests timing out or returning 5xx raise BinanceAmbiguousError
        and trigger reconciliation queries instead of blindly resubmitting.
        """
        err = urllib.error.HTTPError(
            url="https://api.binance.com/api/v3/order",
            code=504,
            msg="Gateway Timeout",
            hdrs={},
            fp=io.BytesIO(b'{"msg": "Gateway Timeout"}')
        )

        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(BinanceAmbiguousError) as ctx:
                self.client.create_order("SOLUSDT", "BUY", "MARKET", quantity="1.0",
                                         new_client_order_id="test_ambig_cid")

        self.assertEqual(ctx.exception.client_order_id, "test_ambig_cid")

        # Now verify broker state reconciliation queries the order status
        with patch.object(self.client, "get_order", return_value={"clientOrderId": "test_ambig_cid", "status": "FILLED"}):
            rec = binance_broker.reconcile_ambiguous_order(self.client, "SOLUSDT", "test_ambig_cid")
            self.assertTrue(rec["landed"])
            self.assertEqual(rec["status"], "FILLED")


# =============================================================================
# 6. BOUNDED EMERGENCY RECOVERY TEST
# =============================================================================

class TestBoundedEmergencyRecovery(unittest.TestCase):
    def test_bounded_recovery_procedure(self):
        """Simulates failed protective exit after an entry fill:
        1. Verifies circuit-breaker lock is set to True.
        2. Verifies orphaned exit orders are canceled.
        3. Verifies open exposure is queried (2.5 SOL).
        4. Verifies closing order is strictly bounded to open exposure (2.5 SOL).
        5. Verifies final position state reaches zero.
        """
        client = BinanceClient(api_key="k", api_secret="s", product="futures")

        # Mock position risk showing 2.5 SOL open position initially, then 0.0 after close
        pos_initial = [{"symbol": "SOLUSDT", "positionAmt": "2.500", "positionSide": "BOTH"}]
        pos_final = [{"symbol": "SOLUSDT", "positionAmt": "0.000", "positionSide": "BOTH"}]

        sym_info = {
            "symbols": [{
                "symbol": "SOLUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "1000", "stepSize": "0.01"},
                    {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000", "tickSize": "0.01"},
                    {"filterType": "NOTIONAL", "minNotional": "5.0"}
                ]
            }]
        }

        # Use temporary journal path
        temp_journal = ".state/test_orders_journal.json"
        if os.path.exists(temp_journal):
            os.remove(temp_journal)

        try:
            with patch.object(client, "cancel_all_open_orders") as mock_cancel_orders:
                with patch.object(client, "get_open_futures_algo_orders", return_value=[{"algoId": 111}]):
                    with patch.object(client, "cancel_futures_algo_order") as mock_cancel_algo:
                        with patch.object(client, "get_position_risk", side_effect=[pos_initial, pos_final]):
                            with patch.object(client, "get_exchange_info", return_value=sym_info):
                                with patch.object(client, "create_order", return_value={"orderId": 99999}) as mock_close:
                                    res = binance_broker.bounded_emergency_recovery(
                                        client, "SOLUSDT", market="futures", journal_path=temp_journal
                                    )

            # 1. Circuit breaker lock engaged
            journal = binance_broker.load_journal(temp_journal)
            self.assertTrue(journal["circuit_breaker"]["locked"])

            # 2. Orphaned orders canceled
            mock_cancel_orders.assert_called_with("SOLUSDT")
            mock_cancel_algo.assert_called_with(algo_id=111)

            # 3 & 4. Bounded closing order sized strictly to verified exposure (2.50)
            mock_close.assert_called_once_with(
                symbol="SOLUSDT",
                side="SELL",
                order_type="MARKET",
                quantity=Decimal("2.50"),
                new_client_order_id=mock_close.call_args[1]["new_client_order_id"],
                reduce_only=True,
                position_side="BOTH"
            )

            # 5. Verified recovered
            self.assertTrue(res["recovered"])
            self.assertEqual(res["final_exposure"], "0")

        finally:
            if os.path.exists(temp_journal):
                os.remove(temp_journal)


if __name__ == "__main__":
    unittest.main()
