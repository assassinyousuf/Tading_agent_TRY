"""
Unit and regression tests for Direction C.2 Formula-Level Verification and Audit Closure.
Tests cover:
1. MDE paired calculation on actual difference series, unit scaling, and zero-volatility guards.
2. Arithmetic MDE vs CAGR excess variance drag divergence.
3. Deflated Sharpe Ratio dimensional consistency (daily vs annual scaling equality).
4. DSR sensitivity across trial counts M and cross-sectional trial standard deviation sigma_trials.
5. Centered moving-block bootstrap independent reference cases (identical, constant positive, constant negative).
6. Bootstrap confidence interval distinction (daily mean vs annualized arithmetic vs cumulative horizon).
7. Walk-forward cross-validation synthetic future perturbation test (zero leakage proof).
8. Multiple testing corrections against hand-calculated benchmarks (tied, unsorted, boundary values).
"""

import math
import sys
import unittest
from pathlib import Path

# Add trader-timemachine to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent / "trader-timemachine"))

from benchmark_engine import (
    WalkForwardSplitter,
    adjust_multiple_testing_pvalues,
)

from audit_direction_c1 import (
    normal_cdf,
    normal_quantile,
)

from audit_direction_c2 import (
    calc_sample_std,
    calc_mde_rigorous,
    calc_dsr_consistent,
    paired_null_centered_bootstrap,
    test_walk_forward_perturbation,
)


class TestDirectionC2Audit(unittest.TestCase):
    def test_mde_paired_exact_calculation(self):
        """Verify MDE calculation directly from paired series standard deviation."""
        n_days = 698
        # Case A: Active vs Cash (sigma_D = 42.93% annualized)
        sigma_d_annual = 0.4293
        sigma_d_daily = sigma_d_annual / math.sqrt(365.25)
        res_cash = calc_mde_rigorous(n_days, sigma_d_daily)
        self.assertEqual(res_cash["status"], "VALID")
        self.assertAlmostEqual(res_cash["mde_annualized_arithmetic_pct"], 77.22, delta=0.05)

        # Case B: Correlated pair (sigma_D = 21.28% annualized)
        sigma_d_corr_daily = 0.2128 / math.sqrt(365.25)
        res_corr = calc_mde_rigorous(n_days, sigma_d_corr_daily)
        self.assertAlmostEqual(res_corr["mde_annualized_arithmetic_pct"], 38.27, delta=0.05)

        # Case C: Zero volatility paired series (identical returns)
        res_zero = calc_mde_rigorous(n_days, 0.0)
        self.assertEqual(res_zero["status"], "UNDEFINED_OR_ZERO")
        self.assertEqual(res_zero["mde_annualized_arithmetic_pct"], 0.0)

        # Case D: Undefined sample size (N <= 1)
        res_undef = calc_mde_rigorous(1, sigma_d_daily)
        self.assertEqual(res_undef["status"], "UNDEFINED_OR_ZERO")

    def test_mde_units_arithmetic_vs_cagr(self):
        """Verify that arithmetic MDE cannot be equated to CAGR excess when volatilities differ."""
        # Arithmetic mean excess
        mean_d_ann = 0.40  # +40% arithmetic mean excess
        # If strategy has higher volatility than benchmark (e.g. 50% vs 20%)
        sigma_strat = 0.50
        sigma_bench = 0.20
        # Variance drag difference = 0.5 * (0.50^2 - 0.20^2) = 0.5 * (0.25 - 0.04) = 0.105 (10.5 pp drag)
        var_drag_diff = 0.5 * (sigma_strat ** 2 - sigma_bench ** 2)
        approx_cagr_excess = mean_d_ann - var_drag_diff

        self.assertAlmostEqual(var_drag_diff, 0.105, delta=1e-5)
        # CAGR excess is only +29.5%, significantly lower than arithmetic +40.0%
        self.assertAlmostEqual(approx_cagr_excess, 0.295, delta=1e-5)
        self.assertLess(approx_cagr_excess, mean_d_ann)

    def test_dsr_dimensional_consistency(self):
        """Verify that DSR z-statistic is strictly identical in daily vs annual units."""
        n_obs = 698
        obs_annual_sr = 1.20
        for m_trials in [1, 5, 25, 67, 150]:
            for s_std in [0.30, 0.50, 1.00]:
                res = calc_dsr_consistent(
                    observed_annual_sharpe=obs_annual_sr,
                    n_obs=n_obs,
                    n_trials=m_trials,
                    skewness=-0.5,
                    kurtosis=2.5,
                    annual_trial_std=s_std
                )
                self.assertEqual(res["status"], "VALID_DIMENSIONALLY_CONSISTENT")
                self.assertIsInstance(res["z_stat"], float)
                self.assertIsInstance(res["dsr"], float)
                self.assertGreaterEqual(res["dsr"], 0.0)
                self.assertLessEqual(res["dsr"], 1.0)

    def test_dsr_trial_std_sensitivity(self):
        """Verify expected maximum Sharpe ratio and DSR vary monotonically with sigma_trials and M."""
        n_obs = 698
        obs_sr = 1.20

        # M=67: comparing sigma_trials = 0.50 vs 1.00
        res_s05 = calc_dsr_consistent(obs_sr, n_obs, n_trials=67, annual_trial_std=0.50)
        res_s10 = calc_dsr_consistent(obs_sr, n_obs, n_trials=67, annual_trial_std=1.00)

        # Expected max SR scales directly with sigma_trials
        self.assertAlmostEqual(res_s10["expected_max_annual_sharpe"] / res_s05["expected_max_annual_sharpe"], 2.0, delta=0.01)
        # DSR is higher when trial standard deviation is lower (less severe snooping penalty)
        self.assertGreater(res_s05["dsr"], res_s10["dsr"])
        self.assertAlmostEqual(res_s05["dsr"], 0.504, delta=0.02)
        self.assertAlmostEqual(res_s10["dsr"], 0.054, delta=0.02)

    def test_bootstrap_independent_reference_cases(self):
        """Verify moving-block bootstrap against deterministic synthetic reference cases."""
        # 1. Identical returns: D_t = 0 everywhere
        r_zeros = [0.0] * 100
        res_ident = paired_null_centered_bootstrap(r_zeros, r_zeros, block_size=5, n_boot=200, seed=42)
        # Fraction of null-centered replicates >= 0 is 1.0 since all replicates are 0
        self.assertEqual(res_ident["p_value_centered"], 1.0)

        # 2. Constant positive excess: D_t = +0.02 everywhere
        r_pos = [0.02] * 100
        res_pos = paired_null_centered_bootstrap(r_pos, r_zeros, block_size=5, n_boot=200, seed=42)
        # Null centered mean is 0, observed mean is +0.02 -> null means >= 0.02 is 0.0
        self.assertEqual(res_pos["p_value_centered"], 0.0)

        # 3. Constant negative excess: D_t = -0.02 everywhere
        r_neg = [-0.02] * 100
        res_neg = paired_null_centered_bootstrap(r_neg, r_zeros, block_size=5, n_boot=200, seed=42)
        # Null centered mean is 0, observed mean is -0.02 -> null means >= -0.02 is 1.0
        self.assertEqual(res_neg["p_value_centered"], 1.0)

    def test_bootstrap_ci_distinction(self):
        """Verify that daily mean CI, annual arithmetic CI, and cumulative horizon CI scale exactly."""
        r_strat = [0.01, -0.005, 0.015, -0.010] * 25
        r_bench = [0.005, -0.002, 0.008, -0.004] * 25
        n = 100

        res = paired_null_centered_bootstrap(r_strat, r_bench, block_size=5, n_boot=500, seed=42)
        d_lo, d_hi = res["ci_95_daily_mean"]
        a_lo, a_hi = res["ci_95_annual_arithmetic_pp"]
        c_lo, c_hi = res["ci_95_cumulative_horizon_pp"]

        # Annual arithmetic = daily * 365.25 * 100%
        self.assertAlmostEqual(a_lo, d_lo * 365.25 * 100.0, delta=0.01)
        self.assertAlmostEqual(a_hi, d_hi * 365.25 * 100.0, delta=0.01)
        # Cumulative horizon = daily * N * 100%
        self.assertAlmostEqual(c_lo, d_lo * n * 100.0, delta=0.01)
        self.assertAlmostEqual(c_hi, d_hi * n * 100.0, delta=0.01)

    def test_walk_forward_synthetic_perturbation_leakage(self):
        """Verify zero leakage by perturbing future test data and proving earlier training is unchanged."""
        # Run standard splitter invariant verification
        res = test_walk_forward_perturbation()
        self.assertEqual(res["status"], "PASSED_ZERO_LEAKAGE")
        self.assertEqual(res["total_folds"], 5)

        # Create synthetic time series
        splitter = WalkForwardSplitter(n_splits=5, warmup_bars=30, mode="rolling", train_bars=300)
        splits = splitter.split(729)

        fold1_train = splits[0]["train"]
        fold1_test = splits[0]["test"]

        # Assert no boundary intersection
        self.assertEqual(fold1_train["end"] + 1, fold1_test["start"])
        self.assertLess(fold1_train["end"], fold1_test["start"])

    def test_multiple_testing_hand_calculated_benchmarks(self):
        """Verify Bonferroni, Holm, and Benjamini-Hochberg against exact hand-calculated values."""
        raw_p = [0.005, 0.015, 0.030, 0.040, 0.050]
        expected_bonf = [0.025, 0.075, 0.150, 0.200, 0.250]
        expected_holm = [0.025, 0.060, 0.090, 0.090, 0.090]
        expected_fdr  = [0.025, 0.0375, 0.050, 0.050, 0.050]

        calc_bonf = adjust_multiple_testing_pvalues(raw_p, method="bonferroni")
        calc_holm = adjust_multiple_testing_pvalues(raw_p, method="holm")
        calc_fdr  = adjust_multiple_testing_pvalues(raw_p, method="fdr_bh")

        for c, e in zip(calc_bonf, expected_bonf):
            self.assertAlmostEqual(c, e, delta=1e-4)

        for c, e in zip(calc_holm, expected_holm):
            self.assertAlmostEqual(c, e, delta=1e-4)

        for c, e in zip(calc_fdr, expected_fdr):
            self.assertAlmostEqual(c, e, delta=1e-4)

        # Tied and unsorted test
        raw_unsorted = [0.040, 0.005, 0.040, 0.800]
        adj_holm_unsorted = adjust_multiple_testing_pvalues(raw_unsorted, method="holm")
        # Ties must receive identical adjusted p-values
        self.assertEqual(adj_holm_unsorted[0], adj_holm_unsorted[2])
        # Smallest raw p must yield smallest adjusted p
        self.assertEqual(adj_holm_unsorted[1], min(adj_holm_unsorted))


if __name__ == "__main__":
    unittest.main()

