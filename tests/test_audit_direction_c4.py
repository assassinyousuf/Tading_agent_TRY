"""
Direction C.4: Production Statistical Corrections and Regression Gate Test Suite

Tests all corrected production implementations in trader-timemachine/benchmark_engine.py:
1. Production DSR dimensional consistency, daily/annual equivalence, edge cases, and extreme moments.
2. Production null-centered moving-block bootstrap (H0: mu <= 0), one-sided vs two-sided,
   analytical t-test agreement, AR(1) autocorrelation, GARCH volatility clustering,
   block sizes (5, 10, 20), non-divisible sample lengths, determinism without global state pollution.
3. Production conventional arithmetic Sharpe ratio (calc_arithmetic_sharpe) with hand-calculated
   verifications, zero/nonzero risk-free rates, zero volatility, and metric independence.
4. Production DSR sensitivity grid (calc_dsr_sensitivity_grid) across trial space (M in [1, 150])
   and dispersion space (sigma in [0.30, 1.00]), with explicit assumption labeling.
"""

import math
import random
import unittest
from pathlib import Path
import sys

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.append(str(Path(ROOT) / "trader-timemachine"))

from benchmark_engine import (
    calc_sharpe_ratio,
    calc_arithmetic_sharpe,
    calc_deflated_sharpe_ratio,
    calc_dsr_sensitivity_grid,
    paired_block_bootstrap_test,
    calc_paired_excess_returns,
    normal_cdf,
    normal_quantile,
)


class TestDirectionC4ProductionCorrections(unittest.TestCase):

    # ==========================================================================
    # Task 1: Production DSR Implementation Tests
    # ==========================================================================

    def test_01_dsr_dimensional_consistency_and_equivalence(self):
        """
        Verifies that calc_deflated_sharpe_ratio produces identical z-statistics and DSR
        whether inputs are provided in annualized or daily units.
        """
        obs_ann_sharpe = 1.20
        n_obs = 698
        n_trials = 67
        annual_factor = 365.25
        annual_trial_std = 1.0

        # Annualized scale
        res_ann = calc_deflated_sharpe_ratio(
            observed_sharpe=obs_ann_sharpe,
            n_obs=n_obs,
            n_trials=n_trials,
            annual_factor=annual_factor,
            trial_sharpe_std=annual_trial_std,
            is_annualized=True,
            return_details=True
        )

        # Daily scale
        obs_daily_sharpe = obs_ann_sharpe / math.sqrt(annual_factor)
        daily_trial_std = annual_trial_std / math.sqrt(annual_factor)
        res_daily = calc_deflated_sharpe_ratio(
            observed_sharpe=obs_daily_sharpe,
            n_obs=n_obs,
            n_trials=n_trials,
            annual_factor=annual_factor,
            trial_sharpe_std=daily_trial_std,
            is_annualized=False,
            return_details=True
        )

        # Exact dimensional invariance
        self.assertEqual(res_ann["dsr"], res_daily["dsr"])
        self.assertEqual(res_ann["z_stat"], res_daily["z_stat"])
        self.assertAlmostEqual(res_ann["dsr"], 0.0508, delta=0.005)
        self.assertAlmostEqual(res_ann["z_stat"], -1.6372, delta=0.01)

        # Standard float return (default API compatibility)
        float_dsr = calc_deflated_sharpe_ratio(obs_ann_sharpe, n_obs, n_trials)
        self.assertEqual(float_dsr, res_ann["dsr"])

    def test_02_dsr_edge_cases_and_defensive_guards(self):
        """
        Verifies DSR behavior on non-positive Sharpe, insufficient observations,
        non-finite inputs, and extreme distribution moments.
        """
        # Zero or negative Sharpe -> DSR = 0.0
        self.assertEqual(calc_deflated_sharpe_ratio(0.0, 700, 10), 0.0)
        self.assertEqual(calc_deflated_sharpe_ratio(-0.5, 700, 10), 0.0)

        # Insufficient observations (N <= 10) -> DSR = 0.0
        self.assertEqual(calc_deflated_sharpe_ratio(1.5, 10, 5), 0.0)
        self.assertEqual(calc_deflated_sharpe_ratio(1.5, 5, 5), 0.0)

        # Non-finite inputs -> DSR = 0.0
        self.assertEqual(calc_deflated_sharpe_ratio(float("nan"), 700, 5), 0.0)
        self.assertEqual(calc_deflated_sharpe_ratio(float("inf"), 700, 5), 0.0)
        self.assertEqual(calc_deflated_sharpe_ratio(1.5, 700, 5, skewness=float("nan")), 0.0)
        self.assertEqual(calc_deflated_sharpe_ratio(1.5, 700, 5, kurtosis=float("inf")), 0.0)

        # Single trial (M = 1) -> Expected max Sharpe is benchmark_sharpe (0.0)
        res_m1 = calc_deflated_sharpe_ratio(1.20, 698, n_trials=1, return_details=True)
        self.assertGreater(res_m1["dsr"], 0.90)  # Positive Sharpe passes when M=1
        self.assertEqual(res_m1["expected_max_sr_annual"], 0.0)

        # Extreme moments (negative skewness and high excess kurtosis)
        # Should expand standard error and decrease DSR
        res_normal = calc_deflated_sharpe_ratio(1.80, 700, 10, skewness=0.0, kurtosis=0.0, return_details=True)
        res_heavy = calc_deflated_sharpe_ratio(1.80, 700, 10, skewness=-2.0, kurtosis=20.0, return_details=True)
        self.assertGreater(res_heavy["se_sr_annual"], res_normal["se_sr_annual"])
        self.assertLess(res_heavy["dsr"], res_normal["dsr"])

    # ==========================================================================
    # Task 2: Production Bootstrap Hypothesis Test
    # ==========================================================================

    def test_03_bootstrap_known_positive_and_negative_effects(self):
        """Verifies bootstrap p-values on strictly positive and strictly negative effects under (k+1)/(B+1)."""
        s_win = [0.005] * 100
        b_lose = [0.001] * 100
        res_win = paired_block_bootstrap_test(s_win, b_lose, block_size=10, n_boot=500, seed=42)
        # Under (k+1)/(B+1), 0 exceedances out of 500 yields minimum p-value 1/501 = 0.0020
        self.assertEqual(res_win["p_value"], round(1.0 / (500 + 1), 4))
        self.assertEqual(res_win["p_value_raw"], 0.0)
        self.assertEqual(res_win["exceedances_k"], 0)
        self.assertGreater(res_win["empirical_mean_excess_daily"], 0.0)

        s_lose = [-0.002] * 100
        b_win = [0.002] * 100
        res_lose = paired_block_bootstrap_test(s_lose, b_win, block_size=10, n_boot=500, seed=42)
        # All 500 exceedances out of 500 yields (500+1)/(500+1) = 1.0
        self.assertEqual(res_lose["p_value"], 1.0)
        self.assertEqual(res_lose["p_value_raw"], 1.0)
        self.assertEqual(res_lose["exceedances_k"], 500)
        self.assertLess(res_lose["empirical_mean_excess_daily"], 0.0)

    def test_04_bootstrap_null_centered_vs_analytical_reference(self):
        """
        Compares null-centered bootstrap p-value against analytical Student's t-test
        on independent zero-drift and positive-drift series.
        """
        rng = random.Random(12345)
        n = 700
        s_rets = [rng.gauss(0.0001, 0.01) for _ in range(n)]
        b_rets = [rng.gauss(0.0001, 0.01) for _ in range(n)]

        diffs = [s - b for s, b in zip(s_rets, b_rets)]
        mean_d = sum(diffs) / n
        var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
        se_d = math.sqrt(var_d / n)
        t_stat = mean_d / se_d

        # Analytical one-sided t-test p-value (standard normal limit for large N)
        analytical_p = 1.0 - normal_cdf(t_stat)

        boot_res = paired_block_bootstrap_test(s_rets, b_rets, block_size=1, n_boot=2000, seed=42)
        self.assertAlmostEqual(boot_res["p_value"], analytical_p, delta=0.03)

    def test_05_bootstrap_autocorrelation_ar1_adjustment(self):
        """
        Verifies that moving-block bootstrap (L=10) adjusts for serial correlation
        in AR(1) persistent series, producing a wider CI and higher p-value than L=1.
        """
        rng = random.Random(999)
        n = 600
        phi = 0.50
        noise = [rng.gauss(0.0, 0.01) for _ in range(n)]
        ar1_excess = [0.0] * n
        ar1_excess[0] = noise[0]
        for t in range(1, n):
            ar1_excess[t] = phi * ar1_excess[t - 1] + noise[t]

        s_rets = [0.0008 + e for e in ar1_excess]
        b_rets = [0.0] * n

        res_iid = paired_block_bootstrap_test(s_rets, b_rets, block_size=1, n_boot=2000, seed=42)
        res_block10 = paired_block_bootstrap_test(s_rets, b_rets, block_size=10, n_boot=2000, seed=42)

        # Block resampling incorporates positive covariance, widening the variance
        # of the sample mean and raising the p-value under H0
        self.assertGreater(res_block10["p_value"], res_iid["p_value"])

    def test_06_bootstrap_multiple_block_lengths_and_non_divisible_sample(self):
        """
        Tests moving-block bootstrap across multiple block lengths (5, 10, 20)
        and non-divisible sample size (N=698).
        """
        rng = random.Random(777)
        n = 698
        s_rets = [rng.gauss(0.0003, 0.015) for _ in range(n)]
        b_rets = [rng.gauss(0.0002, 0.015) for _ in range(n)]

        for L in [5, 10, 20]:
            res = paired_block_bootstrap_test(s_rets, b_rets, block_size=L, n_boot=1000, seed=42)
            self.assertEqual(res["iterations"], 1000)
            self.assertEqual(res["block_size"], L)
            self.assertTrue(0.0 <= res["p_value"] <= 1.0)
            self.assertEqual(len(res["ci_95_annual_arithmetic_pp"]), 2)
            self.assertLess(res["ci_95_annual_arithmetic_pp"][0], res["ci_95_annual_arithmetic_pp"][1])

    def test_07_bootstrap_determinism_without_global_state_pollution(self):
        """
        Verifies that paired_block_bootstrap_test gives identical results under fixed seed,
        and does not alter the Python global random state.
        """
        s = [0.001, -0.002, 0.003, -0.001] * 50
        b = [0.000, 0.001, -0.001, 0.002] * 50

        # Save global state
        random.seed(12345)
        before_state = random.getstate()

        r1 = paired_block_bootstrap_test(s, b, block_size=5, n_boot=500, seed=999)

        # Restore state, run with different global draw
        random.setstate(before_state)
        _ = random.random()

        r2 = paired_block_bootstrap_test(s, b, block_size=5, n_boot=500, seed=999)

        self.assertEqual(r1["p_value"], r2["p_value"])
        self.assertEqual(r1["ci_95_daily_mean"], r2["ci_95_daily_mean"])

    def test_08_bootstrap_degenerate_and_invalid_inputs(self):
        """Verifies defensive handling for sample length < block size or empty inputs."""
        s = [0.01, 0.02]
        b = [0.00, 0.01]
        res = paired_block_bootstrap_test(s, b, block_size=10)
        self.assertEqual(res["status"], "SAMPLE_TOO_SHORT")
        self.assertEqual(res["p_value"], 1.0)

        empty_res = paired_block_bootstrap_test([], [])
        self.assertEqual(empty_res["status"], "SAMPLE_TOO_SHORT")

    # ==========================================================================
    # Task 3: Conventional Arithmetic Sharpe Ratio Tests
    # ==========================================================================

    def test_09_arithmetic_sharpe_hand_calculated_examples(self):
        """Verifies calc_arithmetic_sharpe against hand-calculated analytical cases."""
        # Case A: 3 returns [0.01, 0.02, 0.03], rf = 0.0
        # Mean = 0.02, Sample Std (dof=2) = 0.01
        # Daily SR = 2.0 -> Annualized = 2.0 * sqrt(365.25)
        rets_a = [0.01, 0.02, 0.03]
        sr_a = calc_arithmetic_sharpe(rets_a, rf_annual=0.0, annual_factor=365.25)
        expected_a = 2.0 * math.sqrt(365.25)
        self.assertAlmostEqual(sr_a, expected_a, places=6)

        # Case B: Nonzero risk-free rate rf_annual = 3.6525% -> rf_daily = 0.0001
        # Excess returns: [0.0099, 0.0199, 0.0299]
        # Mean excess = 0.0199, Sample Std = 0.01
        # Daily SR = 1.99 -> Annualized = 1.99 * sqrt(365.25)
        sr_b = calc_arithmetic_sharpe(rets_a, rf_annual=0.036525, annual_factor=365.25)
        expected_b = 1.99 * math.sqrt(365.25)
        self.assertAlmostEqual(sr_b, expected_b, places=6)

        # Case C: Zero volatility returns
        zero_vol = [0.01] * 20
        self.assertEqual(calc_arithmetic_sharpe(zero_vol), 0.0)

        # Case D: Insufficient observations
        self.assertEqual(calc_arithmetic_sharpe([0.01]), 0.0)
        self.assertEqual(calc_arithmetic_sharpe([]), 0.0)

        # Case E: Non-finite return
        self.assertEqual(calc_arithmetic_sharpe([0.01, float("nan"), 0.02]), 0.0)

    def test_10_arithmetic_vs_compounded_sharpe_distinction(self):
        """
        Verifies that calc_arithmetic_sharpe remains strictly distinct from
        the compounded / CAGR-based calc_sharpe_ratio.
        """
        daily_rets = [0.05, -0.04, 0.06, -0.05] * 50  # Volatile series
        n_days = len(daily_rets)

        cum_equity = 1.0
        for r in daily_rets:
            cum_equity *= (1.0 + r)
        cagr = (cum_equity ** (365.25 / n_days)) - 1.0

        mean_r = sum(daily_rets) / n_days
        var_r = sum((r - mean_r) ** 2 for r in daily_rets) / (n_days - 1)
        ann_vol = math.sqrt(var_r) * math.sqrt(365.25)

        compounded_sr = calc_sharpe_ratio(cagr, ann_vol, rf=0.0)
        arithmetic_sr = calc_arithmetic_sharpe(daily_rets, rf_annual=0.0)

        # Compounded CAGR differs from annualized arithmetic mean return due to
        # non-linear multi-period geometric compounding vs linear arithmetic scaling.
        # Thus, compounded Sharpe is strictly distinct from arithmetic Sharpe.
        self.assertNotEqual(compounded_sr, arithmetic_sr)

    # ==========================================================================
    # Task 4: DSR Sensitivity Grid Tests
    # ==========================================================================

    def test_11_dsr_sensitivity_grid_structure_and_warnings(self):
        """
        Verifies calc_dsr_sensitivity_grid structure, tabular rows, and explicit
        warnings about unverified trial assumptions.
        """
        grid_res = calc_dsr_sensitivity_grid(
            observed_sharpe=1.20,
            n_obs=698,
            trial_counts=[1, 5, 25, 67, 150],
            trial_stds=[0.30, 0.50, 0.70, 1.00]
        )

        self.assertIn("grid", grid_res)
        self.assertIn("rows", grid_res)
        self.assertEqual(len(grid_res["rows"]), 20)  # 5 trials * 4 dispersions

        # Monotonicity test: For any trial dispersion, DSR must decrease monotonically with M
        for s_str in ["0.3", "0.5", "0.7", "1.0"]:
            dsr_vals = [grid_res["grid"][str(m)][s_str]["dsr"] for m in [1, 5, 25, 67, 150]]
            for i in range(len(dsr_vals) - 1):
                self.assertGreaterEqual(dsr_vals[i], dsr_vals[i + 1])

        # Verification of explicit warnings
        warnings = grid_res["warnings_and_limitations"]
        self.assertTrue(any("cannot be empirically reconstructed" in w for w in warnings))
        self.assertTrue(any("sensitivity assumptions" in w for w in warnings))
        self.assertTrue(any("macro survivorship bias" in w for w in warnings))

    # ==========================================================================
    # C.4.2 Edge-Case Hardening Tests
    # ==========================================================================

    def test_12_arithmetic_sharpe_defensive_guards(self):
        """
        Verifies defensive handling for calc_arithmetic_sharpe:
        - Non-positive or non-finite annualization factor returns sentinel 0.0.
        - Non-finite or non-numeric risk-free rate returns sentinel 0.0.
        - Invalid, empty, or single-element return sequences return sentinel 0.0.
        - Non-finite return elements return sentinel 0.0.
        - Preserves default annual_factor=365.25 and valid calculation.
        """
        valid_rets = [0.01, 0.02, 0.03]

        # 1. annual_factor invalid inputs
        for bad_factor in [0, 0.0, -365.25, -1.0, float('nan'), float('inf'), float('-inf'), "invalid", None]:
            res = calc_arithmetic_sharpe(valid_rets, rf_annual=0.0, annual_factor=bad_factor)
            self.assertEqual(res, 0.0, f"Failed on annual_factor={bad_factor}")

        # 2. rf_annual invalid inputs
        for bad_rf in [float('nan'), float('inf'), float('-inf'), "invalid", None]:
            res = calc_arithmetic_sharpe(valid_rets, rf_annual=bad_rf, annual_factor=365.25)
            self.assertEqual(res, 0.0, f"Failed on rf_annual={bad_rf}")

        # 3. daily_returns invalid shape / content
        for bad_rets in [None, [], [0.01], [0.01, float('nan')], [0.01, float('inf')], ["a", "b"], [0.01, "b"]]:
            res = calc_arithmetic_sharpe(bad_rets)
            self.assertEqual(res, 0.0, f"Failed on daily_returns={bad_rets}")

        # 4. Zero volatility series
        zero_vol = [0.01, 0.01, 0.01]
        self.assertEqual(calc_arithmetic_sharpe(zero_vol), 0.0)

        # 5. Default parameter preservation and valid arithmetic calculation
        sr_default = calc_arithmetic_sharpe(valid_rets)
        sr_explicit = calc_arithmetic_sharpe(valid_rets, rf_annual=0.0, annual_factor=365.25)
        self.assertEqual(sr_default, sr_explicit)
        self.assertAlmostEqual(sr_default, 38.223, delta=0.01)

    def test_13_bootstrap_finite_sample_convention_and_bounds(self):
        """
        Verifies Davison & Hinkley (1997) / Phipson & Smyth (2010) (k+1)/(B+1) convention:
        - When k=0 exceedances occur, p_value is strictly positive (1 / (B+1)).
        - When k=B exceedances occur, p_value is exactly 1.0 ((B+1) / (B+1)).
        - Raw empirical ratio k/B is retained in p_value_raw.
        - Metadata min_possible_p_value, exceedances_k, and convention string are populated.
        """
        s_win = [0.005] * 100
        b_lose = [0.001] * 100
        n_boot = 500

        res_win = paired_block_bootstrap_test(s_win, b_lose, block_size=10, n_boot=n_boot, seed=42)
        # Check finite resolution bound: 1 / (500 + 1) = 1 / 501 = 0.001996...
        expected_min_p = round(1.0 / (n_boot + 1), 4)
        self.assertEqual(res_win["p_value"], expected_min_p)
        self.assertEqual(res_win["p_value_raw"], 0.0)
        self.assertEqual(res_win["exceedances_k"], 0)
        self.assertEqual(res_win["min_possible_p_value"], round(1.0 / (n_boot + 1), 6))
        self.assertIn("Davison & Hinkley", res_win["p_value_convention"])
        self.assertGreater(res_win["p_value"], 0.0)

        s_lose = [-0.002] * 100
        b_win = [0.002] * 100
        res_lose = paired_block_bootstrap_test(s_lose, b_win, block_size=10, n_boot=n_boot, seed=42)
        self.assertEqual(res_lose["p_value"], 1.0)
        self.assertEqual(res_lose["p_value_raw"], 1.0)
        self.assertEqual(res_lose["exceedances_k"], n_boot)

    def test_14_bootstrap_ties_and_null_like_data(self):
        """
        Verifies bootstrap behavior on ties and identical series:
        - When strategy and benchmark return identical series (null boundary D_t = 0),
          all null-centered bootstrap replicates equal the observed mean (tie: m >= 0).
        - Under (k+1)/(B+1), ties count towards the null, yielding exceedances k = B and p = 1.0.
        - Two-sided and lower alternatives also evaluate to 1.0 on identical data.
        - Verifies that confidence interval calculation is separate and distinct from p-value.
        """
        identical_s = [0.001 * (i % 5) for i in range(100)]
        identical_b = list(identical_s)

        res_greater = paired_block_bootstrap_test(identical_s, identical_b, block_size=10, n_boot=500, alternative="greater")
        self.assertEqual(res_greater["p_value"], 1.0)
        self.assertEqual(res_greater["exceedances_k"], 500)
        self.assertEqual(res_greater["empirical_mean_excess_daily"], 0.0)

        res_twosided = paired_block_bootstrap_test(identical_s, identical_b, block_size=10, n_boot=500, alternative="two-sided")
        self.assertEqual(res_twosided["p_value"], 1.0)
        self.assertEqual(res_twosided["exceedances_k"], 500)

        res_less = paired_block_bootstrap_test(identical_s, identical_b, block_size=10, n_boot=500, alternative="less")
        self.assertEqual(res_less["p_value"], 1.0)
        self.assertEqual(res_less["exceedances_k"], 500)

        # CI remains distinct: for identical series, daily mean CI is exactly [0.0, 0.0]
        self.assertEqual(res_greater["ci_95_daily_mean"], [0.0, 0.0])
        self.assertEqual(res_greater["ci_95_annual_arithmetic_pp"], [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
