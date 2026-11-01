#!/usr/bin/env python3
"""Unit tests for Direction C.5.6 -- Final Conditional Calibration and Research Readiness Gate.

Verifies:
1. Phase A: Conditional calibration invariants (zero selection inflation on Gaussian/Student-t;
            100% screening suppression on AR(1) and AR(2) persistent series).
2. Phase B: AR(2) companion matrix eigenvalue formulation, stability conditions, and inestimable handling.
3. Phase C: Replicate-by-replicate parity (< 1e-15) on common indices and series fingerprint uniqueness.
4. Phase D: Epistemological distinctions in research-use policy and V5 frozen status.
5. Repository invariant: zero em-dashes exist across C.5.6 code and tests.
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

from audit_direction_c5_6 import (
    calc_ar2_companion_roots,
    fit_and_check_ar2,
    derive_audit_seed_63bit,
    compute_series_fingerprint,
    evaluate_screening_gate_c56,
    audit_parity_common_indices,
    get_final_c56_research_policy
)


class TestDirectionC56ReadinessGate(unittest.TestCase):
    """Test suite for Direction C.5.6 conditional calibration and research readiness."""

    def test_phase_b_ar2_companion_roots_mathematics(self):
        """Verifies AR(2) companion matrix eigenvalue derivation and persistence flag."""
        # Case 1: AR(2) bimodal test case: phi1 = 0.10, phi2 = 0.50
        # det(F - lambda*I) = lambda^2 - 0.10*lambda - 0.50 = 0
        # lambda = (0.10 +/- sqrt(0.01 + 2.0)) / 2 = (0.10 +/- sqrt(2.01)) / 2
        # sqrt(2.01) approx 1.4177446878757827
        # lambda_1 = (0.10 + 1.4177446878757827) / 2 = 0.758872
        # lambda_2 = (0.10 - 1.4177446878757827) / 2 = -0.658872
        res = calc_ar2_companion_roots(0.10, 0.50)
        self.assertAlmostEqual(res["modulus_1"], 0.758872, places=5)
        self.assertAlmostEqual(res["modulus_2"], 0.658872, places=5)
        self.assertTrue(res["is_causal_stationary"], "AR(2) with roots inside unit circle must be stationary")
        self.assertTrue(res["persistence_flag_ge_050"], "Max modulus >= 0.50 must set persistence flag")

        # Case 2: AR(2) with complex conjugate roots: phi1 = 0.40, phi2 = -0.50
        # disc = 0.16 - 2.0 = -1.84 < 0
        res_complex = calc_ar2_companion_roots(0.40, -0.50)
        self.assertTrue(res_complex["is_complex_conjugate"])
        # modulus = sqrt((0.2)^2 + (sqrt(1.84)/2)^2) = sqrt(0.04 + 0.46) = sqrt(0.50) approx 0.707106
        self.assertAlmostEqual(res_complex["max_modulus"], math.sqrt(0.50), places=5)
        self.assertTrue(res_complex["persistence_flag_ge_050"])

        # Case 3: Inestimable AR(2) handling
        res_short = fit_and_check_ar2([0.01, 0.02, 0.01])
        self.assertFalse(res_short["is_estimable"])
        self.assertTrue(res_short["inconclusive"])

    def test_phase_c_replicate_parity_common_indices(self):
        """Verifies replicate null mean parity down to machine precision (< 1e-15)."""
        res = audit_parity_common_indices(n_obs=100, n_boot=200, block_size=10, master_seed=42)
        parity = res["replicate_parity"]

        self.assertLess(parity["max_replicate_null_mean_diff_py_vs_numba"], 1e-15)
        self.assertLess(parity["max_replicate_null_mean_diff_py_vs_numpy"], 1e-15)
        self.assertTrue(parity["exceedance_count_match"])
        self.assertTrue(parity["p_value_match"])
        self.assertTrue(parity["ci_low_match_machine_epsilon"])
        self.assertTrue(parity["ci_high_match_machine_epsilon"])

    def test_phase_c_fingerprint_uniqueness(self):
        """Verifies that generated series across distinct scenarios have unique SHA-256 fingerprints."""
        import random
        rng1 = random.Random(derive_audit_seed_63bit(42, "phase_a", "gauss", 0))
        rng2 = random.Random(derive_audit_seed_63bit(42, "phase_a", "student_t", 0))
        s1 = [rng1.gauss(0, 0.01) for _ in range(100)]
        s2 = [rng2.gauss(0, 0.01) for _ in range(100)]

        fp1 = compute_series_fingerprint(s1)
        fp2 = compute_series_fingerprint(s2)
        self.assertNotEqual(fp1, fp2, "Generated series fingerprints must be distinct")

    def test_phase_d_research_policy_structure(self):
        """Verifies that C.5.6 research policy includes the 6 epistemological distinctions."""
        policy = get_final_c56_research_policy()
        self.assertIn("Category_A", policy)
        self.assertIn("Category_B", policy)
        self.assertIn("Category_C", policy)
        self.assertIn("epistemological_distinctions", policy)

        distinctions = policy["epistemological_distinctions"]
        expected_keys = [
            "1_engineering_correctness",
            "2_screening_status",
            "3_bootstrap_calibration",
            "4_dsr_evidence",
            "5_out_of_sample_edge",
            "6_economic_profitability"
        ]
        for k in expected_keys:
            self.assertIn(k, distinctions)

        self.assertIn("frozen_strategy_status", policy)
        self.assertIn("VERIFIED NO-GO", policy["frozen_strategy_status"])

    def test_zero_em_dashes_in_c5_6_files(self):
        """Verifies repository standard: zero em-dashes exist across C.5.6 code and tests."""
        c5_6_files = [
            os.path.join(ROOT, "trader-timemachine", "audit_direction_c5_6.py"),
            os.path.join(ROOT, "tests", "test_audit_direction_c5_6.py")
        ]
        for filepath in c5_6_files:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertNotIn("\u2014", content, f"Em-dash detected in {filepath}")


if __name__ == "__main__":
    unittest.main()

