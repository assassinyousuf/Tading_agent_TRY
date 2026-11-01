#!/usr/bin/env python3
"""Unit tests for Strategy V4.3 Independent Reproducibility and Benchmark Audit.

Validates:
- Raw candle file integrity, common alignment, and 0-gap continuity across BTC, ETH, SOL, BNB.
- Mathematical partition slicing (Train 403, Val 134, Test 134, Seams 2, Overall 673).
- Independent reproduction of V4-B_TOP2 equity curve, final capital ($13,272.09), friction ($1,663.13),
  and compounding error (< 1e-10).
- Independent reproduction of benchmark friction:
  * BTC Overall $27.35 ($15.00 entry + $12.35 exit).
  * BTC Test $33.55 ($15.00 entry + $18.55 exit).
  * EW Overall $26.65 ($15.00 entry + $11.65 exit).
  * EW Test $34.49 ($15.00 entry + $19.49 exit).
- Paired moving block bootstrap hypothesis test (confirming p > 0.05 and failure to reject H0).
- Consistency between v4_corrected_results.json and v4_3_audit_reproduction.json.
- Final NO-GO decision verification.
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

from audit_v4_3_reproducibility import (
    verify_raw_candles,
    independently_reproduce_v4b,
    independently_rebuild_benchmarks,
    perform_hypothesis_tests,
    run_v4_3_audit,
    DEFAULT_UNIVERSE,
)


class TestStrategyV43Reproducibility(unittest.TestCase):
    """Test suite for V4.3 independent reproducibility audit."""

    @classmethod
    def setUpClass(cls):
        cls.candle_verif = verify_raw_candles()
        cls.bars = cls.candle_verif["aligned_bars"]
        cls.warmup = 55
        usable = cls.bars[cls.warmup:]
        val_end = int(len(usable) * 0.80)
        cls.bars_test = cls.bars[cls.warmup + val_end - cls.warmup:]

    def test_raw_candle_integrity_and_alignment(self):
        """All 4 Layer-1 symbols must have exactly 729 daily candles with zero irregular gaps."""
        self.assertEqual(self.candle_verif["common_bars_count"], 729)
        self.assertEqual(self.candle_verif["irregular_gaps_count"], 0)
        for s in DEFAULT_UNIVERSE:
            stats = self.candle_verif["candle_stats"][s]
            self.assertEqual(stats["candle_count"], 729)

    def test_partition_boundary_intervals(self):
        """Verify partition slices and accounting of seam intervals."""
        n_usable = len(self.bars) - self.warmup  # 674 bars
        train_end = int(n_usable * 0.60)        # 404
        val_end = int(n_usable * 0.80)          # 539

        bars_train = self.bars[:self.warmup + train_end]
        bars_val = self.bars[self.warmup + train_end - self.warmup:self.warmup + val_end]
        bars_test = self.bars[self.warmup + val_end - self.warmup:]

        train_res = independently_reproduce_v4b(bars_train)
        val_res = independently_reproduce_v4b(bars_val)
        test_res = independently_reproduce_v4b(bars_test)
        all_res = independently_reproduce_v4b(self.bars)

        # Check return intervals
        self.assertEqual(len(train_res["daily_returns"]), 403)
        self.assertEqual(len(val_res["daily_returns"]), 134)
        self.assertEqual(len(test_res["daily_returns"]), 134)
        self.assertEqual(len(all_res["daily_returns"]), 673)

        # 403 (train) + 1 (seam 1) + 134 (val) + 1 (seam 2) + 134 (test) == 673
        self.assertEqual(403 + 1 + 134 + 1 + 134, 673)

    def test_reproduced_strategy_metrics(self):
        """Verify independent reproduction of V4-B_TOP2 final equity, friction, and compounding error."""
        res_ov = independently_reproduce_v4b(self.bars)
        self.assertAlmostEqual(res_ov["final_capital"], 13272.09, places=2)
        self.assertAlmostEqual(res_ov["total_return_pct"], 32.72, places=2)
        self.assertAlmostEqual(res_ov["total_friction_usd"], 1663.13, places=2)
        self.assertLess(res_ov["compounding_error"], 1e-10)

        res_te = independently_reproduce_v4b(self.bars_test)
        self.assertAlmostEqual(res_te["final_capital"], 12307.40, places=2)
        self.assertAlmostEqual(res_te["total_return_pct"], 23.07, places=2)
        self.assertAlmostEqual(res_te["total_friction_usd"], 372.86, places=2)

    def test_independent_benchmark_friction_reproduction(self):
        """Reproduce benchmark entry/exit friction down to the exact cent."""
        bench_ov = independently_rebuild_benchmarks(self.bars)
        btc_ov = bench_ov["btc_buy_and_hold"]
        ew_ov = bench_ov["equal_weight_l1s"]

        # BTC Overall: $15.00 entry + $12.35 terminal exit = $27.35
        self.assertAlmostEqual(btc_ov["entry_friction_usd"], 15.00, places=2)
        self.assertAlmostEqual(btc_ov["exit_friction_usd"], 12.35, places=2)
        self.assertAlmostEqual(btc_ov["total_friction_usd"], 27.35, places=2)
        self.assertAlmostEqual(btc_ov["net_total_return_pct"], -17.80, places=2)

        # EW Overall: $15.00 entry + $11.65 terminal exit = $26.65
        self.assertAlmostEqual(ew_ov["entry_friction_usd"], 15.00, places=2)
        self.assertAlmostEqual(ew_ov["exit_friction_usd"], 11.65, places=2)
        self.assertAlmostEqual(ew_ov["total_friction_usd"], 26.65, places=2)
        self.assertAlmostEqual(ew_ov["net_total_return_pct"], -22.48, places=2)

        # Test Benchmarks
        bench_te = independently_rebuild_benchmarks(self.bars_test)
        btc_te = bench_te["btc_buy_and_hold"]
        ew_te = bench_te["equal_weight_l1s"]

        # BTC Test: $15.00 entry + $18.55 exit = $33.55
        self.assertAlmostEqual(btc_te["entry_friction_usd"], 15.00, places=2)
        self.assertAlmostEqual(btc_te["exit_friction_usd"], 18.55, places=2)
        self.assertAlmostEqual(btc_te["total_friction_usd"], 33.55, places=2)
        self.assertAlmostEqual(btc_te["net_total_return_pct"], 23.49, places=2)

        # EW Test: $15.00 entry + $19.49 exit = $34.49
        self.assertAlmostEqual(ew_te["entry_friction_usd"], 15.00, places=2)
        self.assertAlmostEqual(ew_te["exit_friction_usd"], 19.49, places=2)
        self.assertAlmostEqual(ew_te["total_friction_usd"], 34.49, places=2)
        self.assertAlmostEqual(ew_te["net_total_return_pct"], 29.71, places=2)

    def test_paired_bootstrap_hypothesis_test(self):
        """Paired moving block bootstrap must demonstrate p > 0.05 (failure to reject H0)."""
        test_strat = independently_reproduce_v4b(self.bars_test)
        test_btc_returns = [ (self.bars_test[self.warmup + i + 1]["BTCUSDT"]["c"] - self.bars_test[self.warmup + i]["BTCUSDT"]["c"]) / self.bars_test[self.warmup + i]["BTCUSDT"]["c"] for i in range(len(test_strat["daily_returns"])) ]
        test_ew_returns = [ sum(0.25 * (self.bars_test[self.warmup + i + 1][s]["c"] - self.bars_test[self.warmup + i][s]["c"]) / self.bars_test[self.warmup + i][s]["c"] for s in DEFAULT_UNIVERSE) for i in range(len(test_strat["daily_returns"])) ]

        tests = perform_hypothesis_tests(test_strat["daily_returns"], test_btc_returns, test_ew_returns, n_boot=1000, seed=42)
        self.assertGreater(tests["vs_btc_buy_and_hold"]["empirical_p_value"], 0.05)
        self.assertGreater(tests["vs_equal_weight_l1s"]["empirical_p_value"], 0.05)
        self.assertIn("FAIL TO REJECT H0", tests["vs_btc_buy_and_hold"]["hypothesis_verdict"])

    def test_report_to_ledger_consistency(self):
        """Ensure v4_corrected_results.json and v4_3_audit_reproduction.json match down to the cent."""
        corrected_p = os.path.join(ROOT, "trader", "v4_corrected_results.json")
        audit_p = os.path.join(ROOT, "trader", "v4_3_audit_reproduction.json")
        self.assertTrue(os.path.exists(corrected_p))
        self.assertTrue(os.path.exists(audit_p))

        with open(corrected_p, "r", encoding="utf-8") as f:
            d_corr = json.load(f)
        with open(audit_p, "r", encoding="utf-8") as f:
            d_aud = json.load(f)

        v4b_corr = [s for s in d_corr["summary"]["strategies"] if s["id"] == "V4_B_TOP2"][0]
        v4b_aud = d_aud["reproduced_strategy"]

        self.assertEqual(v4b_corr["overall"]["final_capital"], v4b_aud["overall"]["final_capital"])
        self.assertEqual(v4b_corr["overall"]["total_friction_usd"], v4b_aud["overall"]["total_friction_usd"])
        self.assertEqual(v4b_corr["test"]["final_capital"], v4b_aud["test"]["final_capital"])
        self.assertEqual(v4b_corr["test"]["total_friction_usd"], v4b_aud["test"]["total_friction_usd"])

    def test_audit_verdict_no_go(self):
        """Final audit decision must be NO-GO for live or paper deployment."""
        res = run_v4_3_audit()
        self.assertIn("NO-GO", res["final_decision"])
        self.assertIn("INCONCLUSIVE", res["classification"])


if __name__ == "__main__":
    unittest.main()

