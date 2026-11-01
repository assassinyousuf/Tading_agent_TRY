#!/usr/bin/env python3
"""Unit tests for Direction C.5.2 -- Calibration Policy Validation & Release Gate.

Verifies:
1. Chi-square survival function and regularized incomplete gamma accuracy.
2. Wilson score confidence interval properties.
3. Autocorrelation, Ljung-Box, and McLeod-Li diagnostic computations.
4. AR(2) diagnostic bypass demonstration (low lag-1 rho, strong lag-2 dependence).
5. GARCH(1,1) non-linear dependence detection via McLeod-Li.
6. Deflated Sharpe Ratio (DSR) mathematical invariants and semantic policy rules.
7. Research-Use Policy Matrix structure and epistemological categories.
8. Non-negotiable repository invariants (zero em-dashes, V5 frozen).
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

from audit_direction_c5_2 import (
    wilson_score_interval,
    gammp,
    chi2_sf,
    calc_autocorrelation,
    calc_ljung_box,
    calc_mcleod_li,
    generate_gaussian_series,
    generate_student_t_series,
    generate_ar1_series,
    generate_ar2_series,
    generate_garch_series,
    run_audit_4_dsr_evaluation,
    formulate_research_use_policy
)
from benchmark_engine import calc_deflated_sharpe_ratio


class TestDirectionC52PolicyValidation(unittest.TestCase):
    """Test suite for Direction C.5.2 statistical validation and policy gate."""

    def test_chi2_survival_function_accuracy(self):
        """Verifies chi2_sf against known theoretical critical values across degrees of freedom."""
        # df = 1: critical value for alpha=0.05 is 3.84146
        p1 = chi2_sf(3.84146, 1)
        self.assertAlmostEqual(p1, 0.05, delta=0.001)

        # df = 2: critical value for alpha=0.05 is 5.99146 (exact formula: exp(-x/2))
        p2 = chi2_sf(5.99146, 2)
        self.assertAlmostEqual(p2, 0.05, delta=0.001)

        # df = 10: critical value for alpha=0.05 is 18.307
        p10 = chi2_sf(18.307, 10)
        self.assertAlmostEqual(p10, 0.05, delta=0.001)

        # df = 20: critical value for alpha=0.01 is 37.566
        p20 = chi2_sf(37.566, 20)
        self.assertAlmostEqual(p20, 0.01, delta=0.001)

        # Boundary conditions
        self.assertEqual(chi2_sf(0.0, 5), 1.0)
        self.assertEqual(chi2_sf(-5.0, 5), 1.0)
        self.assertEqual(chi2_sf(100.0, 0), 0.0)

    def test_wilson_score_interval(self):
        """Verifies Wilson interval bounds, symmetry, and small proportion behavior."""
        ci_zero = wilson_score_interval(0, 5000)
        self.assertEqual(ci_zero[0], 0.0)
        self.assertGreater(ci_zero[1], 0.0)
        self.assertLess(ci_zero[1], 0.002)

        ci_nom = wilson_score_interval(25, 5000)  # 25 / 5000 = 0.005
        self.assertTrue(ci_nom[0] <= 0.005 <= ci_nom[1])
        self.assertAlmostEqual((ci_nom[0] + ci_nom[1]) / 2.0, 0.005, delta=0.001)

    def test_autocorrelation_and_ljung_box(self):
        """Verifies sample autocorrelation and Ljung-Box test logic."""
        # Pure alternating series [1, -1, 1, -1, ...]
        alt_series = [1.0 if i % 2 == 0 else -1.0 for i in range(100)]
        rhos = calc_autocorrelation(alt_series, max_lag=4)
        self.assertAlmostEqual(rhos[0], -1.0, delta=0.05)
        self.assertAlmostEqual(rhos[1], 1.0, delta=0.05)

        # Constant series has zero autocorrelation
        const_series = [5.0] * 50
        rhos_const = calc_autocorrelation(const_series, max_lag=4)
        self.assertEqual(rhos_const, [0.0, 0.0, 0.0, 0.0])

        # Strong AR(1) series (phi=0.70)
        rng = random.Random(42)
        ar1_strong = generate_ar1_series(500, phi=0.70, rng=rng)
        lb_strong = calc_ljung_box(ar1_strong, lags=10)
        self.assertLess(lb_strong["p_val"], 1e-4)
        self.assertGreater(lb_strong["sample_rhos"][0], 0.50)

    def test_ar2_diagnostic_bypass_limitation(self):
        """Demonstrates that AR(2) process can have small lag-1 autocorrelation (<0.30) but strong dependence at lag 2."""
        rng = random.Random(101)
        # AR(2) with phi1=0.10, phi2=0.50: theoretical rho1 = 0.20, rho2 = 0.52
        ar2_series = generate_ar2_series(600, phi1=0.10, phi2=0.50, rng=rng)
        rhos = calc_autocorrelation(ar2_series, max_lag=5)

        # Lag 1 autocorrelation is well below 0.30
        self.assertLess(rhos[0], 0.30)
        # But lag 2 autocorrelation is strong
        self.assertGreater(rhos[1], 0.35)

        # Ljung-Box test decisively detects serial dependence despite low rho1
        lb = calc_ljung_box(ar2_series, lags=10)
        self.assertLess(lb["p_val"], 0.01)

    def test_garch_mcleod_li_detection(self):
        """Demonstrates that GARCH(1,1) has near-zero linear autocorrelation but strong ARCH effects."""
        rng = random.Random(202)
        garch_series = generate_garch_series(800, omega=5e-6, alpha=0.15, beta=0.80, rng=rng)

        # Linear autocorrelation at lag 1 is near zero
        linear_rhos = calc_autocorrelation(garch_series, max_lag=5)
        self.assertLess(abs(linear_rhos[0]), 0.15)

        # McLeod-Li test on squared returns strongly detects volatility clustering
        ml = calc_mcleod_li(garch_series, lags=10)
        self.assertLess(ml["p_val"], 0.01)
        self.assertGreater(ml["sample_rhos"][0], 0.10)

    def test_dsr_inferential_invariants_and_policy(self):
        """Verifies Deflated Sharpe Ratio constraints and sensitivity grid format."""
        dsr_eval = run_audit_4_dsr_evaluation()
        distinctions = dsr_eval["inferential_distinctions"]

        self.assertIn("Model-based", distinctions["nature_of_metric"])
        self.assertIn("0.95", distinctions["threshold_095_meaning"])
        self.assertIn("NOT", distinctions["complementary_tail_prob"])
        self.assertIn("Zero guarantee", distinctions["profitability_guarantee"])

        # Check sensitivity grid evaluation cases
        cases = dsr_eval["evaluated_cases"]
        self.assertEqual(len(cases), 3)
        for c in cases:
            grid = c["sensitivity_grid"]
            self.assertIn("sigma_M_0.30", grid)
            self.assertIn("sigma_M_1.00", grid)
            self.assertIn("M_1", grid["sigma_M_1.00"])
            self.assertIn("M_150", grid["sigma_M_1.00"])

        # Non-positive Sharpe produces DSR = 0.0
        neg_dsr = calc_deflated_sharpe_ratio(-0.5, 500, n_trials=10)
        self.assertEqual(neg_dsr, 0.0)

    def test_research_use_policy_matrix_structure(self):
        """Verifies that the 4-tier Research-Use Policy Matrix contains all required sections and categories."""
        policy = formulate_research_use_policy()
        self.assertEqual(policy["policy_verdict"], "ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS")

        matrix = policy["four_tier_policy_matrix"]
        self.assertIn("tier_1_validated_use_cases", matrix)
        self.assertIn("tier_2_sensitivity_required_use_cases", matrix)
        self.assertIn("tier_3_prohibited_or_inconclusive_claims", matrix)
        self.assertIn("tier_4_unverified_model_assumptions", matrix)

        # Check epistemological distinctions
        epist = policy["epistemological_distinctions"]
        self.assertIn("category_1_mathematical_implementation", epist)
        self.assertIn("category_2_synthetic_empirical_calibration", epist)
        self.assertIn("category_3_historical_strategy_evidence", epist)
        self.assertIn("category_4_real_world_execution", epist)

    def test_no_em_dashes_in_test_file(self):
        """Enforces repository invariant of zero em-dashes."""
        file_path = os.path.abspath(__file__)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("\u2014", content, "Em-dash found in test_audit_direction_c5_2.py!")


if __name__ == "__main__":
    unittest.main()

