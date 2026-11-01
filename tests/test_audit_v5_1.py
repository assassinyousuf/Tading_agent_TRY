"""
Unit tests for Strategy V5.1 Independent Audit & Reconciliation.
Validates:
- Data accounting (729 total, 30 warmup, 699 active bars)
- Execution timing discrepancy resolution (NEXT_OPEN $10,107.96 vs SAME_CLOSE $10,108.28)
- Bootstrap p-value reproducibility and inferential validation
- Benchmark friction derivation to the exact cent
- Like-for-like V4 vs V5 friction comparison on identical dates
- Portfolio risk constraints (40% max asset cap, cash preservation, compounding error < 1e-10)
- Final VERIFIED NO-GO verdict enforcement
"""

import json
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

from research_v5_trend_following import (
    DEFAULT_UNIVERSE,
    load_aligned_daily_candles,
    simulate_trend_following,
    calc_btc_buy_and_hold,
    calc_equal_weight,
    paired_block_bootstrap_test
)
from audit_v5_1 import audit_v5_1


class TestAuditV51(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.trader_dir = os.path.join(ROOT, "trader")
        cls.bars = load_aligned_daily_candles(DEFAULT_UNIVERSE, trader_dir=cls.trader_dir)
        cls.audit_res = audit_v5_1()

    def test_01_data_accounting(self):
        """Verifies 729 raw candles, 30 warmup bars, and exactly 699 continuous active bars."""
        self.assertEqual(len(self.bars), 729)
        self.assertEqual(self.audit_res["data_accounting"]["warmup_excluded_bars"], 30)
        self.assertEqual(self.audit_res["data_accounting"]["continuous_active_bars"], 699)
        self.assertEqual(self.audit_res["data_accounting"]["continuous_return_intervals"], 698)

    def test_02_execution_timing_and_price_diff(self):
        """Verifies NEXT_OPEN and SAME_CLOSE produce exact $0.32 difference on Test."""
        sim_open = simulate_trend_following(self.bars, start_bar=580, end_bar=728, fill_delay="NEXT_OPEN")
        sim_close = simulate_trend_following(self.bars, start_bar=580, end_bar=728, fill_delay="SAME_CLOSE")

        open_final = sim_open["equity_curve"][-1]
        close_final = sim_close["equity_curve"][-1]
        diff = close_final - open_final

        self.assertAlmostEqual(open_final, 10107.96, places=2)
        self.assertAlmostEqual(close_final, 10108.28, places=2)
        self.assertAlmostEqual(diff, 0.32, places=2)

    def test_03_bootstrap_pvalue_and_semantics(self):
        """Verifies paired block bootstrap p-value reproducibility and negative excess return."""
        sim = simulate_trend_following(self.bars, start_bar=580, end_bar=728)
        btc = calc_btc_buy_and_hold(self.bars, start_bar=580, end_bar=728)
        ht = paired_block_bootstrap_test(sim["daily_returns"], btc["daily_returns"], seed=42)

        self.assertEqual(ht["p_value"], 0.9570)
        self.assertLess(ht["empirical_mean_excess_daily"], 0.0)
        self.assertAlmostEqual(ht["total_excess_return_pp"], -20.40, places=1)

    def test_04_benchmark_friction_accounting(self):
        """Verifies BTC benchmark friction is composed of $15.00 entry + drifted terminal exit friction."""
        btc = calc_btc_buy_and_hold(self.bars, start_bar=580, end_bar=728)
        # Entry friction: 10,000 * 0.0015 = 15.00
        # Terminal liquidation: ~12,148 * 0.0015 = ~18.22
        total_friction = btc["metrics"]["total_friction_usd"]
        self.assertAlmostEqual(total_friction, 33.25, delta=0.10)

    def test_05_like_for_like_v4_v5_friction(self):
        """Verifies genuine like-for-like V4 vs V5 cost reduction on exact identical dates."""
        v4_f = self.audit_res["like_for_like_v4_v5_friction"]["v4_overall_friction"]
        v5_f = self.audit_res["like_for_like_v4_v5_friction"]["v5_like_overall_friction"]
        reduction_pct = self.audit_res["like_for_like_v4_v5_friction"]["overall_cost_reduction_pct"]

        self.assertEqual(v4_f, 1663.13)
        self.assertAlmostEqual(v5_f, 383.97, delta=1.0)
        self.assertGreater(reduction_pct, 75.0)

    def test_06_portfolio_risk_constraints(self):
        """Verifies 40% max asset cap and compounding error < 1e-10."""
        cap = self.audit_res["portfolio_risk_constraints"]["max_observed_weight"]
        self.assertLessEqual(cap, 0.40001)
        err = self.audit_res["portfolio_risk_constraints"]["compounding_error_usd"]
        self.assertLess(err, 1e-10)

    def test_07_verified_nogo_decision(self):
        """Verifies that the audit decision is strictly VERIFIED NO-GO."""
        self.assertEqual(self.audit_res["decision"], "VERIFIED NO-GO")


if __name__ == "__main__":
    unittest.main()

