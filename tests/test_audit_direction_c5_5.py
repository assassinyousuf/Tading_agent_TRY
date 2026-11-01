#!/usr/bin/env python3
"""Unit tests for Direction C.5.5 -- Final Release Corrections and Independent Verification.

Verifies:
1. Phase A: Joint screening calibration rates, Wilson 95% CIs, and union (OR) rule semantics.
2. Phase B: Correct 63-bit hierarchical seed derivation, range [1, 2^63 - 1], and zero collisions.
3. Phase C: Strict Numba algebraic equivalence on common pre-generated block-index matrix (< 1e-15).
4. Phase D: Corrected Four-Tier policy matrix, L=10 non-optimality, and fragility sweep rules.
5. Repository invariant: zero em-dashes exist across C.5.5 code and tests.
"""

import math
import os
import sys
import unittest
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from audit_direction_c5_5 import (
    derive_audit_seed_63bit,
    audit_hierarchical_seed_derivation_63bit,
    audit_screening_calibration,
    boot_py_with_indices_c55,
    boot_numpy_with_indices_c55,
    boot_numba_with_indices_c55,
    audit_strict_numba_equivalence,
    audit_four_tier_policy_corrections
)


class TestDirectionC55ReleaseCorrections(unittest.TestCase):
    """Test suite for Direction C.5.5 final release corrections."""

    def test_phase_a_screening_calibration_rates(self):
        """Verifies joint screening false-positive calibration and union rule properties."""
        # Fast calibration verification on K=200 datasets
        res = audit_screening_calibration(k_datasets=200, n_obs=500, master_seed=55100)
        indiv = res["individual_bartlett"]
        bonf = res["bonferroni_bartlett"]
        lb = res["ljung_box_q10"]
        ml = res["mcleod_li_q10"]
        joint_flag = res["joint_screening_flagged"]
        joint_pass = res["joint_screening_pass"]

        # Individual Bartlett rate should be elevated (~30-50%)
        self.assertGreater(indiv["rate"], 0.25)
        # Bonferroni Bartlett, Ljung-Box, McLeod-Li should be controlled (~2-9% in K=200)
        self.assertLess(bonf["rate"], 0.10)
        self.assertLess(lb["rate"], 0.10)
        self.assertLess(ml["rate"], 0.10)

        # Joint screening flagged rate is union of tests (approx 8-18% in K=200, NOT 3-5%)
        self.assertGreater(joint_flag["rate"], bonf["rate"])
        self.assertEqual(joint_flag["count"] + joint_pass["count"], 200)

        # Wilson CI bounds must enclose empirical rate
        self.assertLessEqual(joint_flag["ci_95_percent"][0], joint_flag["rate_percent"])
        self.assertGreaterEqual(joint_flag["ci_95_percent"][1], joint_flag["rate_percent"])

    def test_phase_b_63bit_hierarchical_seeds(self):
        """Verifies 63-bit derivation range [1, 2^63 - 1], determinism, and zero collisions."""
        # 1. Determinism
        s1 = derive_audit_seed_63bit(12345, "phase_a", "sc_test", 0)
        s2 = derive_audit_seed_63bit(12345, "phase_a", "sc_test", 0)
        self.assertEqual(s1, s2)

        # 2. Namespace separation
        s_diff_phase = derive_audit_seed_63bit(12345, "phase_b", "sc_test", 0)
        s_diff_sc = derive_audit_seed_63bit(12345, "phase_a", "sc_test_2", 0)
        self.assertNotEqual(s1, s_diff_phase)
        self.assertNotEqual(s1, s_diff_sc)

        # 3. 63-bit range
        max_63bit = (1 << 63) - 1
        for i in range(100):
            s = derive_audit_seed_63bit(999, "test", "sc", i)
            self.assertGreater(s, 0)
            self.assertLessEqual(s, max_63bit)

        # 4. Collision check on 5,000 seeds
        seeds = set()
        for i in range(5000):
            s = derive_audit_seed_63bit(42, "bulk_test", "sc", i)
            self.assertNotIn(s, seeds)
            seeds.add(s)

    def test_phase_c_strict_numba_equivalence_common_indices(self):
        """Verifies strict numerical equivalence (< 1e-15) when using common index matrix."""
        n_obs = 200
        block_size = 10
        n_boot = 500
        n_blocks = (n_obs + block_size - 1) // block_size

        np.random.seed(42)
        excess = np.random.normal(0.0005, 0.015, n_obs).tolist()
        indices_matrix = np.random.randint(0, n_obs - block_size + 1, size=(n_boot, n_blocks))

        r_py = boot_py_with_indices_c55(excess, block_size, indices_matrix)
        r_np = boot_numpy_with_indices_c55(excess, block_size, indices_matrix)
        r_nb = boot_numba_with_indices_c55(excess, block_size, indices_matrix)

        # 1. Exact integer match in exceedances k
        self.assertEqual(r_py["k"], r_nb["k"])
        self.assertEqual(r_py["k"], r_np["k"])

        # 2. Exact match in finite Monte Carlo p-value
        self.assertEqual(r_py["p_val"], r_nb["p_val"])
        self.assertEqual(r_py["p_val"], r_np["p_val"])

        # 3. Replicate null means match within floating-point epsilon (< 1e-15)
        for b in range(n_boot):
            m_py = r_py["null_means"][b]
            m_nb = r_nb["null_means"][b]
            m_np = r_np["null_means"][b]
            self.assertLess(abs(m_py - m_nb), 1e-15)
            self.assertLess(abs(m_py - m_np), 1e-15)

        # 4. Confidence intervals match within machine epsilon (< 1e-12)
        self.assertLess(abs(r_py["ci_low"] - r_nb["ci_low"]), 1e-12)
        self.assertLess(abs(r_py["ci_high"] - r_nb["ci_high"]), 1e-12)

    def test_phase_d_policy_asymptotics_and_fragility(self):
        """Verifies policy corrections regarding L=10 non-optimality and sensitivity sweep."""
        policy = audit_four_tier_policy_corrections()
        cat_a = policy["Category_A_SCREENING_PASS"]
        ref_spec = cat_a["block_length_specification"]["reference_specification"]
        sens_req = cat_a["block_length_specification"]["mandatory_sensitivity_requirement"]

        # 1. Must NOT claim L=10 is universally valid or optimal
        self.assertIn("NOT as a universally valid or optimal block length", ref_spec)
        self.assertIn("scales asymptotically as O(N^(1/3))", ref_spec)

        # 2. Must mandate L in {5, 10, 20} as fragility diagnostic
        self.assertIn("strictly a FRAGILITY DIAGNOSTIC", sens_req)
        self.assertIn("demoted to Category C", sens_req)

        # 3. Category C must include threshold instability
        cat_c = policy["Category_C_INCONCLUSIVE"]
        self.assertIn("cross significance thresholds", cat_c["condition"])

    def test_zero_em_dashes_in_c5_5_files(self):
        """Verifies repository standard: zero em-dashes exist across C.5.5 code and tests."""
        c5_5_files = [
            os.path.join(ROOT, "trader-timemachine", "audit_direction_c5_5.py"),
            os.path.join(ROOT, "tests", "test_audit_direction_c5_5.py")
        ]
        for filepath in c5_5_files:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertNotIn("\u2014", content, f"Em-dash detected in {filepath}")


if __name__ == "__main__":
    unittest.main()

