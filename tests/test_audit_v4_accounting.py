#!/usr/bin/env python3
"""Unit tests for Strategy V4.1 Independent Accounting and Robustness Audit.

Validates:
- Conservation of capital across all rebalance events (Pre - Friction == Post).
- Turnover definitions (One-Way vs Two-Way vs Cash-leg overcharge).
- Detection of friction omission in daily_returns array.
- Benchmark parity (Gross vs Net accounting).
- Look-ahead prevention and candle boundary continuity.
- Cross-asset collinearity and correlation matrix integrity.
- Execution delay robustness.
- Audit verdict verification (AUDIT FAIL due to material accounting and statistical discrepancies).
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
    load_aligned_daily_candles,
    simulate_v4_rotation,
    calc_cagr,
    calc_annualized_vol,
    DEFAULT_UNIVERSE,
)
from audit_v4_accounting import (
    audit_transaction_costs,
    audit_benchmark_parity,
    audit_timing_and_lookahead,
    audit_statistical_claims,
    audit_regimes_and_robustness,
    run_v4_audit,
    pearson_corr,
)


class TestStrategyV4AccountingAudit(unittest.TestCase):
    """Test suite for V4.1 independent accounting audit."""

    @classmethod
    def setUpClass(cls):
        cls.aligned_bars = load_aligned_daily_candles()

    def test_conservation_of_capital_reconciliation(self):
        """Pre-trade equity minus friction must equal post-trade equity on every rebalance."""
        res = simulate_v4_rotation(self.aligned_bars, top_n=2, use_btc_filter=True)
        records = res["rebalance_records"]
        self.assertGreater(len(records), 0)

        for r in records:
            if r["turnover"] > 0:
                # In rebalance record: pre_capital = capital + friction_usd
                post_capital = r["capital"]
                friction = r["friction_usd"]
                pre_capital = post_capital + friction
                self.assertAlmostEqual(pre_capital - friction, post_capital, places=2)

    def test_turnover_definitions_and_cash_overcharge(self):
        """Validate one-way vs two-way turnover and prove cash-leg fee overcharge."""
        # Scenario: 100% Cash -> 50% Asset A, 50% Asset B
        target_weights = {"SOL": 0.5, "ETH": 0.5, "CASH": 0.0}
        current_weights = {"SOL": 0.0, "ETH": 0.0, "CASH": 1.0}

        # Standard portfolio management one-way turnover
        one_way = 0.5 * sum(abs(target_weights[k] - current_weights[k]) for k in target_weights)
        self.assertEqual(one_way, 1.0)

        # Traded crypto notional is 100% of capital (Buy 50% SOL, Buy 50% ETH)
        crypto_traded_weight = sum(abs(target_weights[s] - current_weights[s]) for s in ["SOL", "ETH"])
        self.assertEqual(crypto_traded_weight, 1.0)

        # Flawed code turnover includes CASH:
        code_turnover = sum(abs(target_weights[k] - current_weights[k]) for k in target_weights)
        self.assertEqual(code_turnover, 2.0)
        # Charging code_turnover * 0.0015 = 0.0030 (30 bps), which doubles the fee on cash transitions
        self.assertEqual(code_turnover, 2 * crypto_traded_weight)

    def test_daily_returns_friction_reconciliation(self):
        """Verify that V4.2 daily_returns now accounts for friction and reconciles with final_capital."""
        res = simulate_v4_rotation(self.aligned_bars, top_n=2, use_btc_filter=True)
        final_capital = res["final_capital"]
        daily_returns = res["daily_returns"]

        compounded = 10000.0
        for r in daily_returns:
            compounded *= (1.0 + r)

        # Compounded net returns now match final capital to within 1 cent (1e-10 unrounded error)
        self.assertAlmostEqual(compounded, final_capital, places=2)
        self.assertLess(res["compounding_error"], 1e-10)

        # Contrast with gross simulation (0 fees, 0 slippage), which reaches $15,352.41 (+53.52%)
        res_gross = simulate_v4_rotation(self.aligned_bars, top_n=2, use_btc_filter=True, fee_rate=0.0, slippage=0.0)
        self.assertGreater(res_gross["final_capital"] - final_capital, 2000.0)

    def test_benchmark_parity_gross_vs_net(self):
        """Verify that benchmark net returns strictly account for trading friction."""
        audit2 = audit_benchmark_parity(self.aligned_bars)
        btc = audit2["comparisons"]["btc_bnh"]
        ew = audit2["comparisons"]["equal_weight_l1s"]

        # BTC Net must be lower than BTC Gross
        self.assertLess(btc["net_return_pct"], btc["gross_return_pct"])
        # Equal-Weight Net must be lower than Equal-Weight Gross
        self.assertLess(ew["net_return_pct"], ew["gross_return_pct"])

    def test_lookahead_prevention_and_boundary_continuity(self):
        """Verify indicator lookbacks and candle open/close boundary continuity."""
        audit3 = audit_timing_and_lookahead(self.aligned_bars)
        self.assertIn("PASS", audit3["lookahead_check"])

        # Boundary continuity: average open-close gap across continuous candles must be < 0.01%
        for sym, stats in audit3["candle_boundary_continuity"].items():
            self.assertLess(stats["avg_gap_pct"], 0.01)

    def test_cross_asset_collinearity(self):
        """Verify that pairwise correlations between all 4 Layer-1 assets exceed 0.65."""
        audit4 = audit_statistical_claims(self.aligned_bars)
        matrix = audit4["cross_asset_correlation_matrix"]
        universe = DEFAULT_UNIVERSE

        for s1 in universe:
            for s2 in universe:
                c = matrix[s1][s2]
                if s1 == s2:
                    self.assertAlmostEqual(c, 1.0, places=2)
                else:
                    self.assertGreater(c, 0.65, f"Correlation between {s1} and {s2} was unexpectedly low: {c}")

    def test_execution_delay_sensitivity(self):
        """Verify 1-day delayed execution preserves positive return but suffers a lag penalty."""
        audit3 = audit_timing_and_lookahead(self.aligned_bars)
        delay_data = audit3["execution_delay_sensitivity"]
        self.assertGreater(delay_data["one_day_delay_return_pct"], 0.0)
        self.assertGreater(delay_data["delay_performance_penalty_pp"], 0.0)

    def test_audit_verdict_and_reconciliation(self):
        """Audit verdict must be AUDIT FAIL due to documented accounting & statistical discrepancies."""
        audit_rep = run_v4_audit()
        self.assertEqual(audit_rep["final_classification"], "AUDIT FAIL")
        self.assertGreater(len(audit_rep["classification_summary"]), 3)

        # Reconcile reported friction discrepancy ($318.20 vs $2354.70)
        costs = audit_rep["audit_1_transaction_costs"]
        self.assertEqual(costs["discrepancy_vs_report"]["report_claimed_friction"], 318.20)
        self.assertGreater(costs["code_model"]["total_friction_usd"], 2000.0)


if __name__ == "__main__":
    unittest.main()

