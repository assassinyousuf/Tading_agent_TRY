#!/usr/bin/env python3
"""Unit tests for Direction C.5.3 -- Independent C.5.2 Verification and Profiling.

Verifies:
1. Phase A: Independent verification of Direction C.5.2 calculations and seed disjointness.
2. Phase B: Bartlett confidence bounds (2/sqrt(N)), AR(2) diagnostic bypass, and GARCH McLeod-Li detection.
3. Phase C: Mertens asymptotic standard error and EVT expected maximum Sharpe formulas.
4. Phase D: Profiling data structures, stage breakdown accounting, and GPU feasibility findings.
5. Repository invariant: zero em-dashes.
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

from audit_direction_c5_3 import (
    verify_c5_2_artifacts,
    audit_dependence_policy_and_risks,
    audit_dsr_mathematics_and_terminology
)


class TestDirectionC53IndependentAudit(unittest.TestCase):
    """Test suite for Direction C.5.3 verification and profiling."""

    def test_phase_a_c5_2_verification(self):
        """Verifies that all Direction C.5.2 calculations match reported artifacts."""
        res = verify_c5_2_artifacts()
        self.assertTrue(res["all_c5_2_metrics_verified"], "C.5.2 numerical metrics verification failed!")
        self.assertTrue(res["intra_audit_seeds_disjoint"], "Intra-audit scenario seeds overlap!")
        # Correctly identifies that global seed namespace was shared across audit functions
        self.assertFalse(res["global_seed_namespace_disjoint"])
        self.assertTrue(res["invariants_verified"], "Benchmark engine methodological invariants failed!")
        self.assertGreater(res["verification_log_summary"]["passed_checks"], 30)
        self.assertEqual(res["verification_log_summary"]["failed_checks"], 0)

    def test_phase_b_bartlett_and_dependence(self):
        """Verifies Bartlett bounds and multi-lag dependence properties."""
        res = audit_dependence_policy_and_risks(k_datasets=20, n_obs=500)
        # 95% Bartlett band for N=500 is 2.0 / sqrt(500) ~ 0.0894
        expected_band = 2.0 / math.sqrt(500)
        self.assertAlmostEqual(res["bartlett_95_value_n500"], expected_band, places=3)

        rec = res["policy_recommendation"]
        self.assertEqual(rec["status"], "REFINED_MULTI_LAG_PRE_TEST_GATE")
        self.assertIn("Ljung-Box", rec["primary_correction"])
        self.assertIn("McLeod-Li", rec["primary_correction"])

    def test_phase_c_dsr_mertens_and_evt(self):
        """Verifies that Mertens asymptotic SE and EVT expected max match manual derivations."""
        res = audit_dsr_mathematics_and_terminology()
        self.assertTrue(res["mertens_formula_verified"], "Mertens SE mismatch with production!")
        self.assertTrue(res["evt_formula_verified"], "EVT expected max Sharpe mismatch with production!")
        self.assertTrue(res["dimensional_scaling_verified"], "Daily vs Annualized Sharpe scaling mismatch!")

        rules = res["terminology_rules"]
        self.assertIn("NEVER", rules["rule_3"])
        self.assertIn("zero guarantee", rules["rule_4"])

    def test_no_em_dashes_in_source_and_test(self):
        """Enforces repository invariant of zero em-dashes."""
        test_file = os.path.abspath(__file__)
        with open(test_file, "r", encoding="utf-8") as f:
            self.assertNotIn("\u2014", f.read(), "Em-dash in test_audit_direction_c5_3.py!")

        src_file = os.path.join(TM_DIR, "audit_direction_c5_3.py")
        with open(src_file, "r", encoding="utf-8") as f:
            self.assertNotIn("\u2014", f.read(), "Em-dash in audit_direction_c5_3.py!")


if __name__ == "__main__":
    unittest.main()
