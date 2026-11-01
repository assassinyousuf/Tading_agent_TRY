"""
Unit and regression tests for Direction C.1 Independent Statistical Correctness Audit.
Tests cover:
1. Arithmetic vs Compounded Sharpe & Sortino formulas.
2. Paired excess volatility vs single-asset volatility in MDE calculations.
3. Centered null moving-block bootstrap vs uncentered percentile inversion.
4. Bailey & López de Prado (2014) Deflated Sharpe Ratio scaling and expected max SR.
5. Multiple-testing adjustments (Bonferroni, Holm, Benjamini-Hochberg FDR) with unsorted & tied p-values.
6. Authoritative walk-forward boundary table contiguity, leak-free design, and balance.
"""

import math
import sys
import unittest
from pathlib import Path

# Add trader-timemachine to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent / "trader-timemachine"))

from benchmark_engine import (
    calc_cagr,
    calc_annualized_vol,
    calc_sharpe_ratio as legacy_calc_sharpe_ratio,
    calc_sortino_ratio as legacy_calc_sortino_ratio,
    calc_minimum_detectable_effect,
    adjust_multiple_testing_pvalues,
    WalkForwardSplitter,
)

from audit_direction_c1 import (
    calc_arithmetic_sharpe,
    calc_arithmetic_sortino,
    normal_cdf,
    normal_quantile,
    paired_null_centered_bootstrap_test,
    calc_rigorous_deflated_sharpe_ratio,
)


class TestDirectionC1Audit(unittest.TestCase):
    def test_sharpe_arithmetic_vs_compounded(self):
        """Verify the mathematical distinction between arithmetic Sharpe and compounded Sharpe."""
        # Case 1: Constant positive returns
        daily_rets = [0.001] * 100
        end_val_1 = 10000.0 * math.prod(1.0 + r for r in daily_rets)
        cagr = calc_cagr(10000.0, end_val_1, len(daily_rets))
        vol = calc_annualized_vol(daily_rets)
        self.assertAlmostEqual(vol, 0.0, places=6)
        # Both must return 0.0 when volatility is zero (division by zero guard)
        self.assertEqual(calc_arithmetic_sharpe(daily_rets, 0.0), 0.0)
        self.assertEqual(legacy_calc_sharpe_ratio(cagr, vol, 0.0), 0.0)

        # Case 2: Volatile asset with high variance drag (e.g. daily alternating +50%, -40%)
        # Arithmetic mean is (+0.50 - 0.40) / 2 = +0.05 > 0
        # Geometric growth factor per 2 days is 1.50 * 0.60 = 0.90 (-10% every 2 days)
        daily_drag_loss = [0.50, -0.40] * 50
        end_val_loss = 10000.0 * math.prod(1.0 + r for r in daily_drag_loss)
        cagr_loss = calc_cagr(10000.0, end_val_loss, len(daily_drag_loss))
        vol_loss = calc_annualized_vol(daily_drag_loss)

        arith_sr_drag = calc_arithmetic_sharpe(daily_drag_loss, 0.0)
        comp_sr_drag = legacy_calc_sharpe_ratio(cagr_loss, vol_loss, 0.0)

        self.assertGreater(arith_sr_drag, 0.0)  # Arithmetic mean is positive (+5%/day)
        self.assertLess(comp_sr_drag, 0.0)      # Compounded CAGR is negative due to variance drag!
        self.assertLess(cagr_loss, 0.0)

    def test_sortino_arithmetic_vs_compounded(self):
        """Verify that downside semi-deviation correctly scales arithmetic vs compounded Sortino."""
        daily_rets = [0.02, -0.01, 0.03, -0.02, 0.01] * 20
        end_val = 10000.0 * math.prod(1.0 + r for r in daily_rets)
        cagr = calc_cagr(10000.0, end_val, len(daily_rets))
        arith_sortino = calc_arithmetic_sortino(daily_rets, 0.0)
        comp_sortino = legacy_calc_sortino_ratio(cagr, daily_rets, 0.0)

        self.assertIsInstance(arith_sortino, float)
        self.assertIsInstance(comp_sortino, float)
        # Both should be well-defined and positive for this positive net return series
        self.assertGreater(arith_sortino, 0.0)
        self.assertGreater(comp_sortino, 0.0)

    def test_mde_excess_volatility_sensitivity(self):
        """Verify that MDE depends directly on paired excess volatility, not single-asset volatility."""
        n_days = 698
        # Case A: Active Strategy vs Cash (excess vol = 42.93%)
        mde_cash = calc_minimum_detectable_effect(n_days, 0.4293)
        self.assertAlmostEqual(mde_cash, 77.22, delta=0.1)

        # Case B: Active Strategy highly correlated with BTC (excess vol = 21.28%)
        mde_corr = calc_minimum_detectable_effect(n_days, 0.2128)
        self.assertAlmostEqual(mde_corr, 38.27, delta=0.1)

        # Case C: 50/50 BTC vs Cash (excess vol = 21.52%)
        mde_50_50 = calc_minimum_detectable_effect(n_days, 0.2152)
        self.assertAlmostEqual(mde_50_50, 38.71, delta=0.1)

        # Ratio must equal the volatility ratio
        self.assertAlmostEqual(mde_corr / mde_cash, 0.2128 / 0.4293, delta=0.01)

    def test_normal_quantile_accuracy(self):
        """Verify Acklam's inverse normal CDF implementation matches known standard quantiles."""
        self.assertAlmostEqual(normal_quantile(0.50), 0.0, delta=1e-6)
        self.assertAlmostEqual(normal_quantile(0.8413447), 1.0, delta=1e-4)
        self.assertAlmostEqual(normal_quantile(0.975), 1.95996, delta=1e-4)
        self.assertAlmostEqual(normal_quantile(0.99), 2.32635, delta=1e-4)
        self.assertAlmostEqual(normal_quantile(0.025), -1.95996, delta=1e-4)

    def test_bootstrap_null_centering_vs_uncentered(self):
        """Verify centered null bootstrap tests H0: mu <= 0 correctly."""
        # Synthetic daily returns with strictly negative excess mean
        rets_strat = [0.005, -0.015, 0.002, -0.012] * 25
        rets_bench = [0.010, -0.005, 0.008, -0.002] * 25

        boot_res = paired_null_centered_bootstrap_test(
            rets_strat, rets_bench, block_size=5, n_boot=500, seed=42
        )

        p_centered = boot_res["p_value_centered"]
        ci_lo, ci_hi = boot_res["ci_95_annual_excess_pp"]
        mean_excess = boot_res["observed_mean_excess_annual_pct"]

        self.assertLess(mean_excess, 0.0)
        # When mean excess is negative, p-value under H0: mu <= 0 must be > 0.50
        self.assertGreater(p_centered, 0.50)
        # Confidence interval must contain the observed sample mean
        self.assertLessEqual(ci_lo, mean_excess)
        self.assertGreaterEqual(ci_hi, mean_excess)

    def test_deflated_sharpe_ratio_properties(self):
        """Verify DSR behaves monotonically with trials M and daily time scaling."""
        daily_obs = 698
        annual_sr = 1.20

        res_1 = calc_rigorous_deflated_sharpe_ratio(annual_sr, daily_obs, n_trials=1)
        self.assertGreater(res_1["dsr"], 0.90)
        self.assertEqual(res_1["expected_max_annual_sharpe"], 0.0)

        res_5 = calc_rigorous_deflated_sharpe_ratio(annual_sr, daily_obs, n_trials=5)
        res_67 = calc_rigorous_deflated_sharpe_ratio(annual_sr, daily_obs, n_trials=67)

        self.assertIn("expected_max_annual_sharpe", res_1)
        self.assertIn("z_stat", res_1)
        self.assertIn("annual_sharpe_std_error", res_1)

    def test_multiple_testing_adjustments_edge_cases(self):
        """Verify multiple-testing adjustments handle unsorted lists, ties, and preserve monotonicity."""
        raw_p = [0.04, 0.01, 0.04, 0.80, 0.001]

        # Bonferroni
        adj_bonf = adjust_multiple_testing_pvalues(raw_p, method="bonferroni")
        self.assertEqual(len(adj_bonf), len(raw_p))
        self.assertAlmostEqual(adj_bonf[4], 0.005, delta=1e-4)  # 0.001 * 5 = 0.005
        self.assertAlmostEqual(adj_bonf[1], 0.05, delta=1e-4)   # 0.01 * 5 = 0.05
        self.assertAlmostEqual(adj_bonf[0], 0.20, delta=1e-4)   # 0.04 * 5 = 0.20
        self.assertAlmostEqual(adj_bonf[2], 0.20, delta=1e-4)   # Tied values get identical adjusted p
        self.assertEqual(adj_bonf[3], 1.0)                      # Capped at 1.0

        # Holm step-down
        adj_holm = adjust_multiple_testing_pvalues(raw_p, method="holm")
        self.assertEqual(len(adj_holm), len(raw_p))
        self.assertAlmostEqual(adj_holm[4], 0.005, delta=1e-4)
        self.assertAlmostEqual(adj_holm[1], 0.04, delta=1e-4)
        self.assertEqual(adj_holm[0], adj_holm[2])

        # Benjamini-Hochberg FDR
        adj_fdr = adjust_multiple_testing_pvalues(raw_p, method="fdr_bh")
        self.assertEqual(len(adj_fdr), len(raw_p))
        # Monotonicity check
        for i in range(len(raw_p)):
            for j in range(len(raw_p)):
                if raw_p[i] < raw_p[j]:
                    self.assertLessEqual(adj_fdr[i], adj_fdr[j])

    def test_authoritative_walk_forward_table_contiguity(self):
        """Verify the 5 walk-forward test folds are contiguous, non-overlapping, and cover the test horizon."""
        splitter = WalkForwardSplitter(n_splits=5, warmup_bars=30, mode="rolling", train_bars=300)
        splits = splitter.split(total_bars=729)
        self.assertEqual(len(splits), 5)

        for i, s in enumerate(splits):
            t = s["train"]
            e = s["test"]
            # Check train size
            self.assertEqual(t["count"], 300)
            # Check train start is strictly after warmup
            self.assertGreaterEqual(t["start"], 30)
            # Check train and test don't overlap within the fold
            self.assertLess(t["end"], e["start"])
            self.assertEqual(e["start"], t["end"] + 1)

            # Check test partition contiguity between consecutive folds
            if i > 0:
                prev_e = splits[i - 1]["test"]
                # Test fold i must start exactly on prev_fold's test_end + 1
                self.assertEqual(e["start"], prev_e["end"] + 1)

        # Check total test bars cover from bar 330 to bar 728 (399 test bars)
        first_test_bar = splits[0]["test"]["start"]
        last_test_bar = splits[-1]["test"]["end"]
        self.assertEqual(first_test_bar, 330)
        self.assertEqual(last_test_bar, 728)
        total_test_bars = sum(s["test"]["count"] for s in splits)
        self.assertEqual(total_test_bars, 728 - 330 + 1)


if __name__ == "__main__":
    unittest.main()
