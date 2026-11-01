#!/usr/bin/env python3
"""Regression and Statistical Validation Suite for Direction C.5 Calibration Gate.

Verifies:
1. Synthetic return generators (Gaussian, Student t, AR(1), GARCH(1,1)).
2. Wilson score interval calculations.
3. Bootstrap Type I error calibration under independent null distributions.
4. Monotonic statistical power and 95% confidence-interval coverage.
5. Block-length sensitivity under serial dependence.
6. Deflated Sharpe Ratio (DSR) dimensional invariance and parameter sensitivities.
"""

import math
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from benchmark_engine import (
    calc_arithmetic_sharpe,
    calc_deflated_sharpe_ratio,
    paired_block_bootstrap_test,
)
from calibration_direction_c5 import (
    wilson_score_interval,
    generate_gaussian_series,
    generate_student_t_series,
    generate_ar1_series,
    generate_garch_series,
    run_dsr_verification_suite,
)


class TestDirectionC5Calibration(unittest.TestCase):

    def test_01_wilson_score_interval_properties(self):
        """Verifies Wilson score confidence interval boundary and coverage properties."""
        # Case A: 0 successes out of 100 trials -> lower bound must be exactly 0.0
        ci_zero = wilson_score_interval(0, 100, confidence=0.95)
        self.assertEqual(ci_zero[0], 0.0)
        self.assertGreater(ci_zero[1], 0.0)
        self.assertLess(ci_zero[1], 0.05)

        # Case B: 100 successes out of 100 trials -> upper bound must be exactly 1.0
        ci_full = wilson_score_interval(100, 100, confidence=0.95)
        self.assertGreater(ci_full[0], 0.95)
        self.assertEqual(ci_full[1], 1.0)

        # Case C: 50 successes out of 100 trials -> symmetric around 0.50
        ci_half = wilson_score_interval(50, 100, confidence=0.95)
        self.assertAlmostEqual(ci_half[0], 0.4038, delta=0.01)
        self.assertAlmostEqual(ci_half[1], 0.5962, delta=0.01)

        # Case D: Empty trials
        self.assertEqual(wilson_score_interval(0, 0), [0.0, 0.0])

    def test_02_synthetic_generators_empirical_moments(self):
        """Verifies synthetic generator empirical moments against theoretical parameters."""
        rng = random.Random(42)
        n = 50000

        # Gaussian i.i.d.: mu=0, sigma=0.01
        g_series = generate_gaussian_series(n, mu=0.0, sigma=0.01, rng=rng)
        mean_g = sum(g_series) / n
        std_g = math.sqrt(sum((x - mean_g) ** 2 for x in g_series) / (n - 1))
        self.assertAlmostEqual(mean_g, 0.0, delta=0.0005)
        self.assertAlmostEqual(std_g, 0.01, delta=0.0005)

        # Student t (df=5): Var(T) = df / (df-2) = 5/3, excess kurtosis = 6 / (df-4) = 6.0
        t_series = generate_student_t_series(n, df=5, mu=0.0, sigma=0.01, rng=rng)
        mean_t = sum(t_series) / n
        std_t = math.sqrt(sum((x - mean_t) ** 2 for x in t_series) / (n - 1))
        self.assertAlmostEqual(mean_t, 0.0, delta=0.0005)
        self.assertAlmostEqual(std_t, 0.01, delta=0.0008)

        # AR(1) phi=0.50: autocorrelation at lag 1 should be approx 0.50
        ar_series = generate_ar1_series(n, phi=0.50, mu=0.0, sigma=0.01, rng=rng)
        mean_ar = sum(ar_series) / n
        std_ar = math.sqrt(sum((x - mean_ar) ** 2 for x in ar_series) / (n - 1))
        cov_1 = sum((ar_series[t] - mean_ar) * (ar_series[t - 1] - mean_ar) for t in range(1, n)) / (n - 1)
        rho_1 = cov_1 / (std_ar ** 2)
        self.assertAlmostEqual(mean_ar, 0.0, delta=0.0005)
        self.assertAlmostEqual(std_ar, 0.01, delta=0.0008)
        self.assertAlmostEqual(rho_1, 0.50, delta=0.03)

        # GARCH(1,1): unconditional std should be approx 0.01
        garch_series = generate_garch_series(n, omega=5e-6, alpha=0.15, beta=0.80, mu=0.0, rng=rng)
        mean_garch = sum(garch_series) / n
        std_garch = math.sqrt(sum((x - mean_garch) ** 2 for x in garch_series) / (n - 1))
        self.assertAlmostEqual(mean_garch, 0.0, delta=0.0005)
        self.assertAlmostEqual(std_garch, 0.01, delta=0.0015)

    def test_03_type_1_error_calibration_gaussian_and_fat_tails(self):
        """
        Fast regression check verifying Type I error calibration of moving-block bootstrap
        under Gaussian i.i.d. and Student's t (df=4) null distributions.
        """
        k_datasets = 50
        n_obs = 300
        n_boot = 200
        block_size = 10

        # Gaussian i.i.d.
        rej_gauss_05 = 0
        for i in range(k_datasets):
            rng = random.Random(1000 + i)
            d = generate_gaussian_series(n_obs, mu=0.0, sigma=0.01, rng=rng)
            res = paired_block_bootstrap_test(d, [0.0] * n_obs, block_size=block_size, n_boot=n_boot, seed=2000 + i)
            if res["p_value"] <= 0.05:
                rej_gauss_05 += 1

        rate_gauss = rej_gauss_05 / float(k_datasets)
        # Empirical rate at alpha=0.05 should be reasonably close to 0.05 (e.g. in [0.0, 0.12])
        self.assertLessEqual(rate_gauss, 0.12)

        # Student's t (df=5, kurtosis=6.0)
        rej_t5_05 = 0
        for i in range(k_datasets):
            rng = random.Random(3000 + i)
            d = generate_student_t_series(n_obs, df=5, mu=0.0, sigma=0.01, rng=rng)
            res = paired_block_bootstrap_test(d, [0.0] * n_obs, block_size=block_size, n_boot=n_boot, seed=4000 + i)
            if res["p_value"] <= 0.05:
                rej_t5_05 += 1

        rate_t5 = rej_t5_05 / float(k_datasets)
        self.assertLessEqual(rate_t5, 0.12)

    def test_04_block_length_sensitivity_ar1_demonstration(self):
        """
        Demonstrates that naive i.i.d. bootstrap (L=1) over-rejects on AR(1) series (phi=0.50),
        while block bootstrap (L=10) reduces the false-positive rate.
        """
        k_datasets = 50
        n_obs = 300
        n_boot = 200

        rej_L1 = 0
        rej_L10 = 0

        for i in range(k_datasets):
            rng = random.Random(5000 + i)
            d = generate_ar1_series(n_obs, phi=0.50, mu=0.0, sigma=0.01, rng=rng)
            zero_bench = [0.0] * n_obs

            res_1 = paired_block_bootstrap_test(d, zero_bench, block_size=1, n_boot=n_boot, seed=6000 + i)
            if res_1["p_value"] <= 0.05:
                rej_L1 += 1

            res_10 = paired_block_bootstrap_test(d, zero_bench, block_size=10, n_boot=n_boot, seed=7000 + i)
            if res_10["p_value"] <= 0.05:
                rej_L10 += 1

        # L=1 neglects positive covariance and must exhibit higher rejection than L=10
        self.assertGreaterEqual(rej_L1, rej_L10)

    def test_05_power_monotonicity_and_coverage(self):
        """Verifies that upper-tail power increases with effect size and CI covers true parameter."""
        n_obs = 300
        n_boot = 200
        k_datasets = 30

        # Small effect vs large effect
        mu_small = 0.0002
        mu_large = 0.0010

        pwr_small = 0
        pwr_large = 0
        ci_covered_large = 0

        for i in range(k_datasets):
            rng_s = random.Random(8000 + i)
            d_small = generate_gaussian_series(n_obs, mu=mu_small, sigma=0.01, rng=rng_s)
            res_s = paired_block_bootstrap_test(d_small, [0.0] * n_obs, block_size=10, n_boot=n_boot, seed=9000 + i)
            if res_s["p_value"] <= 0.05:
                pwr_small += 1

            rng_l = random.Random(10000 + i)
            d_large = generate_gaussian_series(n_obs, mu=mu_large, sigma=0.01, rng=rng_l)
            res_l = paired_block_bootstrap_test(d_large, [0.0] * n_obs, block_size=10, n_boot=n_boot, seed=11000 + i)
            if res_l["p_value"] <= 0.05:
                pwr_large += 1

            ci = res_l["ci_95_daily_mean"]
            if ci[0] <= mu_large <= ci[1]:
                ci_covered_large += 1

        # Power must be higher for large effect
        self.assertGreater(pwr_large, pwr_small)
        # CI coverage should be high (e.g. >= 85% on 30 runs)
        self.assertGreaterEqual(ci_covered_large / float(k_datasets), 0.85)

    def test_06_dsr_verification_suite(self):
        """Verifies full DSR verification suite against synthetic distribution parameters."""
        dsr_res = run_dsr_verification_suite()

        # 1. Scaling invariance
        self.assertTrue(dsr_res["scaling_invariance"]["verified"])
        self.assertEqual(dsr_res["scaling_invariance"]["annualized_z"], dsr_res["scaling_invariance"]["daily_z"])

        # 2. Non-positive Sharpe
        self.assertTrue(dsr_res["non_positive_sharpe"]["verified"])

        # 3. Monotonic decrease of DSR with M
        trials = dsr_res["trials_progression"]
        for idx in range(len(trials) - 1):
            self.assertGreaterEqual(trials[idx]["dsr"], trials[idx + 1]["dsr"])
            self.assertLessEqual(trials[idx]["expected_max_sr_annual"], trials[idx + 1]["expected_max_sr_annual"])

        # 4. Monotonic decrease of DSR with trial dispersion sigma_M
        disps = dsr_res["trial_dispersion_sensitivity"]
        for idx in range(len(disps) - 1):
            self.assertGreaterEqual(disps[idx]["dsr"], disps[idx + 1]["dsr"])


if __name__ == "__main__":
    unittest.main()
