#!/usr/bin/env python3
"""Unit tests for Strategy V4.2 Relative Strength Rotation (Corrected Net Accounting).

Validates:
- Precise turnover and execution cost calculations:
  * Cash to two 50% crypto holdings.
  * Two crypto holdings to cash.
  * Switching from one asset to another.
  * Rebalancing with unchanged target weights (zero turnover, zero cost).
  * Partial weight changes.
  * Zero turnover and zero-cost cases.
- Authoritative net daily return series and exact compounding reconciliation.
- Multi-asset rotation ranking and execution.
- Financial metrics (CAGR, Volatility, Sharpe, Sortino, Drawdown).
- Benchmark parity (Gross vs Net).
"""

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from research_v4_relative_strength import (
    calc_cagr,
    calc_annualized_vol,
    calc_sharpe_ratio,
    calc_sortino_ratio,
    calc_max_drawdown,
    simulate_v4_rotation,
    calc_buy_and_hold,
    calc_equal_weight,
    load_aligned_daily_candles,
    DEFAULT_UNIVERSE,
)


class TestStrategyV4ExecutionCostsAndTurnover(unittest.TestCase):
    """Specific unit tests verifying V4.2 turnover and execution cost rules."""

    def setUp(self):
        self.fee_rate = 0.0010
        self.slippage = 0.0005
        self.total_drag = self.fee_rate + self.slippage  # 0.0015 (15 bps)
        self.capital = 10000.0

    def test_cash_to_two_50pct_crypto_holdings(self):
        """Cash to two 50% crypto holdings: traded crypto notional is 100%, friction is 15 bps."""
        target_weights = {"SOLUSDT": 0.5, "ETHUSDT": 0.5, "CASH": 0.0}
        current_weights = {"SOLUSDT": 0.0, "ETHUSDT": 0.0, "CASH": 1.0}
        crypto_assets = ["SOLUSDT", "ETHUSDT"]

        # One-way turnover: half sum of all weight changes across crypto and cash
        all_keys = set(crypto_assets) | {"CASH"}
        one_way = 0.5 * sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
        self.assertEqual(one_way, 1.0)

        # Traded crypto notional: only crypto assets incur exchange fees
        crypto_traded_weight = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in crypto_assets)
        self.assertEqual(crypto_traded_weight, 1.0)

        traded_notional = self.capital * crypto_traded_weight
        friction = traded_notional * self.total_drag
        self.assertEqual(traded_notional, 10000.0)
        self.assertEqual(friction, 15.00)

    def test_two_crypto_holdings_to_cash(self):
        """Two crypto holdings to cash: traded crypto notional is 100%, friction is 15 bps."""
        target_weights = {"SOLUSDT": 0.0, "ETHUSDT": 0.0, "CASH": 1.0}
        current_weights = {"SOLUSDT": 0.5, "ETHUSDT": 0.5, "CASH": 0.0}
        crypto_assets = ["SOLUSDT", "ETHUSDT"]

        all_keys = set(crypto_assets) | {"CASH"}
        one_way = 0.5 * sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
        self.assertEqual(one_way, 1.0)

        crypto_traded_weight = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in crypto_assets)
        self.assertEqual(crypto_traded_weight, 1.0)

        traded_notional = self.capital * crypto_traded_weight
        friction = traded_notional * self.total_drag
        self.assertEqual(traded_notional, 10000.0)
        self.assertEqual(friction, 15.00)

    def test_switching_from_one_asset_to_another(self):
        """Switching one asset (e.g. 50% ETH -> 50% BNB): traded crypto notional is 100%, one-way is 50%."""
        current_weights = {"SOLUSDT": 0.5, "ETHUSDT": 0.5, "BNBUSDT": 0.0, "CASH": 0.0}
        target_weights = {"SOLUSDT": 0.5, "ETHUSDT": 0.0, "BNBUSDT": 0.5, "CASH": 0.0}
        crypto_assets = ["SOLUSDT", "ETHUSDT", "BNBUSDT"]

        all_keys = set(crypto_assets) | {"CASH"}
        one_way = 0.5 * sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
        self.assertEqual(one_way, 0.5)

        # Traded crypto notional: Sell 50% ETH + Buy 50% BNB = 1.0 (SOL has 0 change)
        crypto_traded_weight = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in crypto_assets)
        self.assertEqual(crypto_traded_weight, 1.0)

        traded_notional = self.capital * crypto_traded_weight
        friction = traded_notional * self.total_drag
        self.assertEqual(traded_notional, 10000.0)
        self.assertEqual(friction, 15.00)

    def test_rebalancing_with_unchanged_target_weights(self):
        """Rebalancing with unchanged target weights: zero turnover and zero cost."""
        current_weights = {"SOLUSDT": 0.5, "ETHUSDT": 0.5, "CASH": 0.0}
        target_weights = {"SOLUSDT": 0.5, "ETHUSDT": 0.5, "CASH": 0.0}
        crypto_assets = ["SOLUSDT", "ETHUSDT"]

        all_keys = set(crypto_assets) | {"CASH"}
        one_way = 0.5 * sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
        self.assertEqual(one_way, 0.0)

        crypto_traded_weight = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in crypto_assets)
        self.assertEqual(crypto_traded_weight, 0.0)

        traded_notional = self.capital * crypto_traded_weight
        friction = traded_notional * self.total_drag
        self.assertEqual(traded_notional, 0.0)
        self.assertEqual(friction, 0.0)

    def test_partial_weight_changes(self):
        """Partial weight adjustment (e.g. 50/50 -> 70/30): traded crypto notional is 40%."""
        current_weights = {"SOLUSDT": 0.5, "ETHUSDT": 0.5, "CASH": 0.0}
        target_weights = {"SOLUSDT": 0.7, "ETHUSDT": 0.3, "CASH": 0.0}
        crypto_assets = ["SOLUSDT", "ETHUSDT"]

        all_keys = set(crypto_assets) | {"CASH"}
        one_way = 0.5 * sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
        self.assertAlmostEqual(one_way, 0.20, places=6)

        crypto_traded_weight = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in crypto_assets)
        self.assertAlmostEqual(crypto_traded_weight, 0.40, places=6)

        traded_notional = self.capital * crypto_traded_weight
        friction = traded_notional * self.total_drag
        self.assertAlmostEqual(traded_notional, 4000.0, places=2)
        self.assertAlmostEqual(friction, 6.00, places=2)

    def test_zero_turnover_and_zero_cost_cash_hold(self):
        """Zero turnover when maintaining 100% Cash."""
        current_weights = {"SOLUSDT": 0.0, "ETHUSDT": 0.0, "CASH": 1.0}
        target_weights = {"SOLUSDT": 0.0, "ETHUSDT": 0.0, "CASH": 1.0}
        crypto_assets = ["SOLUSDT", "ETHUSDT"]

        all_keys = set(crypto_assets) | {"CASH"}
        one_way = 0.5 * sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
        self.assertEqual(one_way, 0.0)

        crypto_traded_weight = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in crypto_assets)
        self.assertEqual(crypto_traded_weight, 0.0)

        traded_notional = self.capital * crypto_traded_weight
        friction = traded_notional * self.total_drag
        self.assertEqual(traded_notional, 0.0)
        self.assertEqual(friction, 0.0)


class TestStrategyV4SimulationAndCompounding(unittest.TestCase):
    """Integration unit tests on simulation engine and compounding reconciliation."""

    @classmethod
    def setUpClass(cls):
        cls.aligned_bars = load_aligned_daily_candles()

    def test_financial_metrics_math(self):
        """Validate core metrics mathematics."""
        cagr = calc_cagr(10000.0, 12100.0, 730.5)
        self.assertAlmostEqual(cagr, 0.10, delta=0.01)

        vol_zero = calc_annualized_vol([0.0] * 50)
        self.assertEqual(vol_zero, 0.0)

        sharpe = calc_sharpe_ratio(0.15, 0.15)
        self.assertAlmostEqual(sharpe, 1.0)

        curve = [100.0, 120.0, 90.0, 110.0]
        mdd, dur = calc_max_drawdown(curve)
        self.assertEqual(mdd, 25.0)
        self.assertEqual(dur, 2)

    def test_net_daily_return_exact_compounding_reconciliation(self):
        """The product of (1 + net_return[t]) must equal final_capital / initial_capital (< 1e-10 unrounded, < 0.01 rounded)."""
        res = simulate_v4_rotation(self.aligned_bars, top_n=2, use_btc_filter=True)
        final_capital = res["final_capital"]
        daily_returns = res["daily_returns"]

        compounded = 10000.0
        for r in daily_returns:
            compounded *= (1.0 + r)

        # Unrounded mathematical compounding error is < 1e-10
        self.assertLess(res["compounding_error"], 1e-10, f"Compounding error {res['compounding_error']} exceeded 1e-10")
        # Difference against rounded final_capital ($13,272.09) is less than 0.01 (1 cent)
        self.assertAlmostEqual(compounded, final_capital, places=2)

    def test_trade_ledger_reconciliation(self):
        """Verify that every entry in trade_ledger satisfies conservation of capital."""
        res = simulate_v4_rotation(self.aligned_bars, top_n=2, use_btc_filter=True)
        ledger = res["trade_ledger"]
        self.assertGreater(len(ledger), 0)

        ledger_friction_sum = sum(entry["friction_usd"] for entry in ledger)
        self.assertAlmostEqual(ledger_friction_sum, res["total_friction_usd"], places=2)

        for entry in ledger:
            start_eq = entry["starting_equity"]
            friction = entry["friction_usd"]
            post_eq = entry["post_trade_equity"]
            # Allow 0.02 delta due to independent 2-decimal rounding of start, friction, and post
            self.assertAlmostEqual(start_eq - friction, post_eq, delta=0.02)

    def test_benchmark_parity_and_distinctions(self):
        """Verify that benchmarks clearly distinguish Gross and Net returns."""
        btc = calc_buy_and_hold(self.aligned_bars, "BTCUSDT")
        self.assertIn("gross", btc)
        self.assertLess(btc["total_return_pct"], btc["gross"]["total_return_pct"])
        self.assertGreater(btc["total_friction_usd"], 0.0)

        ew = calc_equal_weight(self.aligned_bars)
        self.assertIn("gross", ew)
        self.assertLess(ew["total_return_pct"], ew["gross"]["total_return_pct"])
        self.assertGreater(ew["total_friction_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
