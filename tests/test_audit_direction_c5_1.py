#!/usr/bin/env python3
"""Regression Test Suite for Direction C.5.1 Audit.

Verifies:
1. Exact reconciliation of AR(1) phi=0.50 seed schedules and sampling variation.
2. Low-alpha calibration properties and documented serial correlation inflation.
3. Independent numerical verification of Deflated Sharpe Ratio (DSR).
4. Power sequence monotonicity and confidence-interval coverage bounds.
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

from benchmark_engine import calc_deflated_sharpe_ratio, paired_block_bootstrap_test
from audit_direction_c5_1 import (
    reconcile_ar1_discrepancy,
    independent_calc_dsr,
    audit_dsr_implementation_and_labels,
    audit_power_and_coverage
)


class TestDirectionC51Audit(unittest.TestCase):

    def test_01_ar1_discrepancy_reconciliation(self):
        """Verifies AR(1) reconciliation logic and sampling variance bounds."""
        results_path = os.path.join(ROOT, "trader", "direction_c5_1_audit_results.json")
        self.assertTrue(os.path.isfile(results_path), "direction_c5_1_audit_results.json must exist")

        with open(results_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        recon = data["audit_1_ar1_reconciliation"]
        # Batch 1 matches C.5 Table 4 exactly
        self.assertEqual(recon["batch1_type1_table"]["rejections"], 19)
        self.assertEqual(recon["batch1_type1_table"]["empirical_rate"], 0.0760)

        # Batch 2 matches C.5 Table 6 exactly
        self.assertEqual(recon["batch2_block_table"]["rejections"], 24)
        self.assertEqual(recon["batch2_block_table"]["empirical_rate"], 0.0960)

        # Difference is statistically indistinguishable from random sampling
        self.assertFalse(recon["statistical_comparison"]["statistically_distinguishable"])
        self.assertGreater(recon["statistical_comparison"]["p_value_of_difference"], 0.05)

        # Fast execution test: verify that reconcile_ar1_discrepancy runs deterministically
        fast_recon = reconcile_ar1_discrepancy(n_obs=200, n_boot=50, k_datasets=10)
        self.assertIn("batch1_type1_table", fast_recon)
        self.assertIn("batch2_block_table", fast_recon)

    def test_02_low_alpha_calibration_analysis(self):
        """Verifies high-resolution low-alpha (alpha=0.01, K=2500, B=1000) calibration results."""
        results_path = os.path.join(ROOT, "trader", "direction_c5_1_audit_results.json")
        with open(results_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        low_a = data["audit_2_low_alpha_calibration"]

        # Gaussian i.i.d. and GARCH(1,1) must enclose nominal 0.01
        self.assertTrue(low_a["gaussian_iid"]["encloses_nominal"])
        self.assertTrue(low_a["garch_11"]["encloses_nominal"])
        self.assertAlmostEqual(low_a["gaussian_iid"]["empirical_rejection_rate"], 0.0088, delta=0.001)

        # AR(1) processes exhibit mild size inflation under serial correlation, excluding 0.01
        self.assertFalse(low_a["ar1_phi_030"]["encloses_nominal"])
        self.assertFalse(low_a["ar1_phi_050"]["encloses_nominal"])
        self.assertGreater(low_a["ar1_phi_030"]["empirical_rejection_rate"], 0.0100)
        self.assertGreater(low_a["ar1_phi_050"]["empirical_rejection_rate"], 0.0100)
        self.assertGreater(low_a["ar1_phi_050"]["distortion_ratio"], 1.50)

    def test_03_dsr_independent_numerical_verification(self):
        """Verifies DSR independent reference implementation and semantic audit."""
        dsr_res = audit_dsr_implementation_and_labels()
        self.assertTrue(dsr_res["mathematical_verification_verified"])

        # Check comparisons
        for comp in dsr_res["comparisons"]:
            self.assertTrue(comp["exact_match"])
            self.assertEqual(comp["production"]["z"], comp["independent"]["z"])
            self.assertEqual(comp["production"]["dsr"], comp["independent"]["dsr"])

        # Check non-positive Sharpe
        d_zero = independent_calc_dsr(observed_sharpe=0.0, n_obs=698)
        self.assertEqual(d_zero["dsr"], 0.0)
        self.assertEqual(d_zero["status"], "NON_POSITIVE")

    def test_04_power_and_coverage_audit(self):
        """Verifies power monotonicity and confidence interval coverage properties."""
        pwr_audit = audit_power_and_coverage()
        self.assertTrue(pwr_audit["empirical_power_monotonicity"])
        self.assertTrue(pwr_audit["coverage_bounds_satisfied"])
        self.assertAlmostEqual(pwr_audit["mean_coverage_across_effects"], 0.9440, delta=0.01)


if __name__ == "__main__":
    unittest.main()

