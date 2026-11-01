"""
Adversarial statistical validation and production regression tests for Direction C.3.

Directly tests and challenges:
1. Production benchmark_engine.py defects (scaling defect in DSR, uncentered bootstrap defect).
2. Adversarial bootstrap behavior on AR(1) autocorrelated returns and GARCH volatility clustering.
3. Independent analytical t-test comparison against moving-block bootstrap.
4. Non-divisible sample size truncation (N=698, L=10).
5. DSR extreme moment bounds and M=1 edge cases.
6. Walk-forward fold reconstruction from raw data.
7. Verification that V1-V3 aligned return series are missing, rendering M_eff an unverified assumption.
"""

import math
import os
import random
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.append(str(Path(ROOT) / "trader-timemachine"))

from benchmark_engine import (
    load_aligned_daily_candles,
    DEFAULT_UNIVERSE,
    WalkForwardSplitter,
    calc_deflated_sharpe_ratio as prod_calc_dsr,
    paired_block_bootstrap_test as prod_paired_bootstrap_test,
)

from audit_direction_c1 import (
    normal_cdf,
    normal_quantile,
)

from audit_direction_c2 import (
    calc_dsr_consistent,
    paired_null_centered_bootstrap,
)

from audit_direction_c3 import (
    audit_production_benchmark_engine,
    analytical_t_test_p_value,
    generate_ar1_series,
    generate_vol_clustered_series,
    audit_trial_data_availability,
)


class TestDirectionC3AdversarialAudit(unittest.TestCase):

    def test_01_production_dsr_scaling_defect_exposed(self):
        """
        Adversarial test: Proves the legacy production DSR contained a scaling bug
        (z=-31.32, DSR=0.0000) and verifies that corrected production code produces DSR=0.0508.
        """
        # Legacy formula (pre-correction defect)
        euler_mascheroni = 0.5772156649
        term1 = (1.0 - euler_mascheroni) * math.sqrt(2.0 * math.log(67))
        term2 = euler_mascheroni * math.log(4.0 * math.log(67)) / math.sqrt(2.0 * math.log(67))
        exp_max_legacy = term1 + term2
        se_legacy = math.sqrt((1.0 + 0.5 * (1.20 ** 2)) / (698 - 1))
        z_legacy = (1.20 - exp_max_legacy) / se_legacy
        legacy_dsr = 0.5 * (1.0 + math.erf(z_legacy / math.sqrt(2.0)))
        self.assertEqual(round(legacy_dsr, 4), 0.0)

        # Production engine (post-correction)
        prod_dsr = prod_calc_dsr(observed_sharpe=1.20, n_obs=698, n_trials=67)
        corr_dsr_res = calc_dsr_consistent(observed_annual_sharpe=1.20, n_obs=698, n_trials=67, annual_trial_std=1.0)

        # Confirm production engine matches corrected derivation (~0.0508)
        self.assertAlmostEqual(prod_dsr, 0.0508, delta=0.01)
        self.assertAlmostEqual(corr_dsr_res["dsr"], 0.0508, delta=0.01)
        self.assertEqual(prod_dsr, corr_dsr_res["dsr"])

    def test_02_production_bootstrap_uncentered_defect_exposed(self):
        """
        Adversarial test: Proves legacy uncentered bootstrap returned 0.1220 and verifies
        that corrected production paired_block_bootstrap_test returns centered p-value 0.1800.
        """
        random.seed(42)
        s_noisy = [random.gauss(0.0005, 0.01) for _ in range(500)]
        b_noisy = [random.gauss(0.0010, 0.01) for _ in range(500)]

        prod_boot = prod_paired_bootstrap_test(s_noisy, b_noisy, block_size=10, n_boot=2000, seed=42)
        corr_boot = paired_null_centered_bootstrap(s_noisy, b_noisy, block_size=10, n_boot=2000, seed=42)

        # Production engine returns centered null p-value in p_value field (finite convention (k+1)/(B+1))
        # and retains empirical k/B in p_value_raw
        self.assertAlmostEqual(prod_boot["p_value"], 0.1800, delta=0.03)
        self.assertAlmostEqual(corr_boot["p_value_centered"], 0.1800, delta=0.03)
        self.assertEqual(prod_boot["p_value_raw"], round(corr_boot["p_value_centered"], 4))
        self.assertAlmostEqual(prod_boot["p_value"], corr_boot["p_value_centered"], delta=0.005)

        # Legacy uncentered percentile retained in p_value_uncentered_legacy field
        self.assertAlmostEqual(prod_boot["p_value_uncentered_legacy"], 0.1220, delta=0.03)

    def test_03_adversarial_bootstrap_ar1_autocorrelation(self):
        """
        Adversarial test: Tests moving-block bootstrap on persistent AR(1) return series (phi = +0.50).
        Block resampling (L=10) must adjust variance upward and produce a more conservative p-value
        than naive i.i.d. resampling (L=1).
        """
        d_ar = generate_ar1_series(n=500, phi=0.50, mu=0.0005, sigma=0.01, seed=103)
        boot_iid = paired_null_centered_bootstrap(d_ar, [0.0]*500, block_size=1, n_boot=1000, seed=42)
        boot_block = paired_null_centered_bootstrap(d_ar, [0.0]*500, block_size=10, n_boot=1000, seed=42)

        # Naive i.i.d. bootstrap ignores positive persistence and overstates significance (lower p-value)
        # Block bootstrap accounts for autocovariances and increases p-value
        self.assertLessEqual(boot_iid["p_value_centered"], boot_block["p_value_centered"])
        self.assertGreater(boot_block["p_value_centered"], 0.01)

    def test_04_adversarial_bootstrap_against_analytical_ttest(self):
        """
        Adversarial test: Validates moving-block bootstrap against analytical Student's t-test
        on independent Gaussian series with known parameters.
        """
        random.seed(102)
        n = 500
        d_zero = [random.gauss(0.0, 0.01) for _ in range(n)]
        p_t, t_stat = analytical_t_test_p_value(d_zero)
        boot_res = paired_null_centered_bootstrap(d_zero, [0.0]*n, block_size=5, n_boot=1000, seed=42)

        # For zero drift, analytical p-value and centered bootstrap p-value must both be near 0.50
        self.assertAlmostEqual(p_t, 0.50, delta=0.10)
        self.assertAlmostEqual(boot_res["p_value_centered"], 0.50, delta=0.10)
        self.assertAlmostEqual(p_t, boot_res["p_value_centered"], delta=0.08)

    def test_05_adversarial_bootstrap_non_divisible_sample_size(self):
        """
        Adversarial test: Verifies moving block bootstrap handles non-divisible sample lengths
        (N = 698, Block Size = 10 -> 70 blocks drawn, truncated to 698).
        """
        d_698 = [0.001] * 698
        boot_res = paired_null_centered_bootstrap(d_698, [0.0]*698, block_size=10, n_boot=100, seed=42)
        self.assertEqual(boot_res["n_obs"], 698)
        self.assertEqual(len(boot_res["ci_95_daily_mean"]), 2)

    def test_06_dsr_extreme_moments_and_boundaries(self):
        """
        Adversarial test: Validates DSR behavior on extreme skewness, excess kurtosis, and boundary inputs.
        """
        # M=1 trial: DSR must equal standard normal CDF of Sharpe / SE
        res_m1 = calc_dsr_consistent(observed_annual_sharpe=1.20, n_obs=698, n_trials=1, annual_trial_std=1.0)
        self.assertEqual(res_m1["expected_max_annual_sharpe"], 0.0)
        self.assertAlmostEqual(res_m1["dsr"], normal_cdf(1.20 / res_m1["se_annual_sharpe"]), delta=1e-4)

        # Heavy negative skewness (-2.0) and high kurtosis (10.0) increases standard error
        res_norm = calc_dsr_consistent(1.20, 698, n_trials=1, skewness=0.0, kurtosis=0.0)
        res_heavy = calc_dsr_consistent(1.20, 698, n_trials=1, skewness=-2.0, kurtosis=10.0)
        self.assertGreater(res_heavy["se_annual_sharpe"], res_norm["se_annual_sharpe"])

        # Invalid inputs (N <= 10 or Sharpe <= 0) must return 0.0
        res_invalid = calc_dsr_consistent(observed_annual_sharpe=-0.5, n_obs=698, n_trials=5)
        self.assertEqual(res_invalid["dsr"], 0.0)
        self.assertEqual(res_invalid["status"], "INVALID_INPUT")

    def test_07_walk_forward_fold_reconstruction(self):
        """
        Adversarial test: Reconstructs all 5 folds from raw candle data and verifies
        that bar counts, train/test boundaries, and dates are completely non-overlapping.
        """
        bars = load_aligned_daily_candles(DEFAULT_UNIVERSE, trader_dir=os.path.join(ROOT, "trader"))
        splitter = WalkForwardSplitter(n_splits=5, warmup_bars=30, mode="rolling", train_bars=300)
        splits = splitter.split(len(bars))

        self.assertEqual(len(splits), 5)
        for i, s in enumerate(splits):
            self.assertEqual(s["train"]["count"], 300)
            self.assertLess(s["train"]["end"], s["test"]["start"])
            self.assertEqual(s["train"]["end"] + 1, s["test"]["start"])
            if i > 0:
                self.assertEqual(splits[i-1]["test"]["end"] + 1, s["test"]["start"])

    def test_08_trial_data_missingness_audit(self):
        """
        Adversarial test: Proves that historical daily return vectors for V1, V2, and V3
        do not exist in the repository, making M_eff = 15-25 an unverified sensitivity assumption.
        """
        avail = audit_trial_data_availability()
        self.assertFalse(avail["v1_v2_v3_daily_aligned_returns_exist"])
        self.assertFalse(avail["empirical_67x67_correlation_matrix_computable"])
        self.assertEqual(avail["m_eff_classification"], "UNVERIFIED_SENSITIVITY_ASSUMPTION")


if __name__ == "__main__":
    unittest.main()
