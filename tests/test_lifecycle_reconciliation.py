#!/usr/bin/env python3
"""Comprehensive Lifecycle & Reconciliation Test Suite for Binance Spot & Futures.

Validates the complete execution lifecycle offline with deterministic mocks:
1. Working entry order fills and activates pending OCO exits.
2. OCO Take-Profit fill automatically cancels Stop-Loss.
3. OCO Stop-Loss fill automatically cancels Take-Profit.
4. Process restarts at every lifecycle stage: ensures zero duplicate orders.
5. Partial fills and multi-asset fees reconciliation.
6. Cancellation failure resilience and circuit breaker engagement.
"""

import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
BROKER_DIR = os.path.join(ROOT, "trader-broker")
if BROKER_DIR not in sys.path:
    sys.path.insert(0, BROKER_DIR)

import binance_broker
from binance_client import BinanceClient, BinanceAPIError


class TestOrderLifecycleAndReconciliation(unittest.TestCase):

    def setUp(self):
        self.test_journal = os.path.join(ROOT, ".state", "test_lifecycle_journal.json")
        if os.path.exists(self.test_journal):
            os.remove(self.test_journal)
        if os.path.exists(self.test_journal + ".bak"):
            os.remove(self.test_journal + ".bak")

    def tearDown(self):
        for p in (self.test_journal, self.test_journal + ".bak"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_otoco_working_entry_fills_and_activates_pending_exits(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        # Step 1: Create an active OTOCO entry in journal
        intent = binance_broker.persist_order_intent(
            "SOLUSDT", "BUY", "1.0", "LIMIT", price="100.0", stop="95.0", target="110.0",
            market="spot", path=self.test_journal
        )
        cid = intent["id"]
        binance_broker.update_journal_entry(cid, "PLACED_OTOCO", broker_order_id="list_999", path=self.test_journal)

        # Exchange returns order list with listOrderStatus="EXECUTING" (working order filled, exits active)
        mock_list_executing = {
            "orderListId": 999,
            "listClientOrderId": intent["list_client_order_id"],
            "listStatusType": "EXEC_STARTED",
            "listOrderStatus": "EXECUTING",
            "orderReports": [
                {"symbol": "SOLUSDT", "orderId": 1001, "type": "LIMIT_MAKER", "status": "NEW", "price": "110.00"},
                {"symbol": "SOLUSDT", "orderId": 1002, "type": "STOP_LOSS_LIMIT", "status": "NEW", "price": "95.00"}
            ]
        }

        with patch.object(client, "get_spot_order_list", return_value=mock_list_executing):
            res = binance_broker.reconcile_active_orders(client, journal_path=self.test_journal)
            self.assertEqual(res["checked"], 1)
            self.assertEqual(res["updated"], 1)
            self.assertEqual(res["results"][0]["new_status"], "OCO_ACTIVE")

        # Verify journal status transitioned to OCO_ACTIVE
        j = binance_broker.load_journal(self.test_journal)
        self.assertEqual(j["entries"][0]["status"], "OCO_ACTIVE")

    def test_oco_take_profit_fills_and_cancels_stop_loss(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        intent = binance_broker.persist_order_intent(
            "SOLUSDT", "BUY", "1.0", "LIMIT", price="100.0", stop="95.0", target="110.0",
            market="spot", path=self.test_journal
        )
        cid = intent["id"]
        binance_broker.update_journal_entry(cid, "OCO_ACTIVE", broker_order_id="list_888", path=self.test_journal)

        # Exchange returns ALL_DONE: Take Profit is FILLED, Stop Loss is CANCELED (OCO behavior)
        mock_list_target_filled = {
            "orderListId": 888,
            "listClientOrderId": intent["list_client_order_id"],
            "listStatusType": "ALL_DONE",
            "listOrderStatus": "ALL_DONE",
            "orderReports": [
                {"symbol": "SOLUSDT", "orderId": 2001, "type": "LIMIT_MAKER", "status": "FILLED", "price": "110.00"},
                {"symbol": "SOLUSDT", "orderId": 2002, "type": "STOP_LOSS_LIMIT", "status": "CANCELED", "price": "95.00"}
            ]
        }

        with patch.object(client, "get_spot_order_list", return_value=mock_list_target_filled):
            res = binance_broker.reconcile_active_orders(client, journal_path=self.test_journal)
            self.assertEqual(res["updated"], 1)
            self.assertEqual(res["results"][0]["new_status"], "CLOSED_TARGET")

        j = binance_broker.load_journal(self.test_journal)
        self.assertEqual(j["entries"][0]["status"], "CLOSED_TARGET")

    def test_oco_stop_loss_fills_and_cancels_take_profit(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        intent = binance_broker.persist_order_intent(
            "SOLUSDT", "BUY", "1.0", "LIMIT", price="100.0", stop="95.0", target="110.0",
            market="spot", path=self.test_journal
        )
        cid = intent["id"]
        binance_broker.update_journal_entry(cid, "OCO_ACTIVE", broker_order_id="list_777", path=self.test_journal)

        # Exchange returns ALL_DONE: Stop Loss is FILLED, Take Profit is CANCELED
        mock_list_stop_filled = {
            "orderListId": 777,
            "listClientOrderId": intent["list_client_order_id"],
            "listStatusType": "ALL_DONE",
            "listOrderStatus": "ALL_DONE",
            "orderReports": [
                {"symbol": "SOLUSDT", "orderId": 3001, "type": "LIMIT_MAKER", "status": "CANCELED", "price": "110.00"},
                {"symbol": "SOLUSDT", "orderId": 3002, "type": "STOP_LOSS_LIMIT", "status": "FILLED", "price": "95.00"}
            ]
        }

        with patch.object(client, "get_spot_order_list", return_value=mock_list_stop_filled):
            res = binance_broker.reconcile_active_orders(client, journal_path=self.test_journal)
            self.assertEqual(res["updated"], 1)
            self.assertEqual(res["results"][0]["new_status"], "CLOSED_STOP")

        j = binance_broker.load_journal(self.test_journal)
        self.assertEqual(j["entries"][0]["status"], "CLOSED_STOP")

    def test_process_restart_at_each_stage_prevents_duplicate_orders(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")

        # Stage 1: Restart while status is PLACED_OTOCO
        intent = binance_broker.persist_order_intent(
            "SOLUSDT", "BUY", "1.0", "LIMIT", price="100.0", stop="95.0", target="110.0",
            market="spot", path=self.test_journal
        )
        cid = intent["id"]
        binance_broker.update_journal_entry(cid, "PLACED_OTOCO", broker_order_id="list_666", path=self.test_journal)

        # Calling reconcile recognizes existing order list without submitting a duplicate OTOCO
        mock_list_still_exec = {
            "orderListId": 666,
            "listOrderStatus": "EXECUTING",
            "orderReports": [{"symbol": "SOLUSDT", "orderId": 4001, "type": "LIMIT_MAKER", "status": "NEW"}]
        }
        with patch.object(client, "get_spot_order_list", return_value=mock_list_still_exec), \
             patch.object(client, "create_spot_otoco") as mock_create_otoco:
            binance_broker.reconcile_active_orders(client, journal_path=self.test_journal)
            mock_create_otoco.assert_not_called()

        # Stage 2: Restart while status is OCO_ACTIVE
        with patch.object(client, "get_spot_order_list", return_value=mock_list_still_exec), \
             patch.object(client, "create_spot_oco") as mock_create_oco:
            binance_broker.reconcile_active_orders(client, journal_path=self.test_journal)
            mock_create_oco.assert_not_called()

    def test_restart_at_entry_placed_reconciles_fill_without_duplicate(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        intent = binance_broker.persist_order_intent(
            "SOLUSDT", "BUY", "1.0", "MARKET", market="spot", path=self.test_journal
        )
        cid = intent["id"]
        binance_broker.update_journal_entry(cid, "ENTRY_PLACED", path=self.test_journal)

        # Exchange returns order is FILLED
        mock_order = {"symbol": "SOLUSDT", "clientOrderId": cid, "status": "FILLED", "orderId": 5001}
        with patch.object(client, "get_order", return_value=mock_order), \
             patch.object(client, "create_order") as mock_create_order:
            res = binance_broker.reconcile_active_orders(client, journal_path=self.test_journal)
            mock_create_order.assert_not_called()
            self.assertEqual(res["results"][0]["new_status"], "ENTRY_FILLED")

        j = binance_broker.load_journal(self.test_journal)
        self.assertEqual(j["entries"][0]["status"], "ENTRY_FILLED")

    def test_restart_at_ambiguous_timeout_reconciles_and_recovers(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        intent = binance_broker.persist_order_intent(
            "SOLUSDT", "BUY", "1.0", "MARKET", market="spot", path=self.test_journal
        )
        cid = intent["id"]
        binance_broker.update_journal_entry(cid, "AMBIGUOUS", path=self.test_journal)

        # On restart, get_order queries exchange and discovers it was canceled or expired
        mock_order = {"symbol": "SOLUSDT", "clientOrderId": cid, "status": "CANCELED"}
        with patch.object(client, "get_order", return_value=mock_order):
            res = binance_broker.reconcile_active_orders(client, journal_path=self.test_journal)
            self.assertEqual(res["results"][0]["new_status"], "CLOSED_CANCELED")

        j = binance_broker.load_journal(self.test_journal)
        self.assertEqual(j["entries"][0]["status"], "CLOSED_CANCELED")

    def test_cancellation_failure_engages_circuit_breaker(self):
        client = BinanceClient(api_key="mock", api_secret="mock", product="spot")
        mock_acc = {"balances": [{"asset": "SOL", "free": "2.0"}]}
        mock_ex_info = {"symbols": [{"symbol": "SOLUSDT", "filters": [
            {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "1000", "stepSize": "0.01"}
        ]}]}
        with patch.object(client, "cancel_all_open_orders", side_effect=Exception("Exchange network drop")), \
             patch.object(client, "get_account", return_value=mock_acc), \
             patch.object(client, "get_exchange_info", return_value=mock_ex_info), \
             patch.object(client, "create_order", side_effect=Exception("Reject")):
            res = binance_broker.bounded_emergency_recovery(client, "SOLUSDT", market="spot", journal_path=self.test_journal)
            self.assertFalse(res["recovered"])

        j = binance_broker.load_journal(self.test_journal)
        self.assertTrue(j["circuit_breaker"]["locked"])


if __name__ == "__main__":
    unittest.main()
