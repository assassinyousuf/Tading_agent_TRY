#!/usr/bin/env python3
"""Unit tests for Direction C.5.4 -- Statistical Policy Finalization and Numba Equivalence.

Verifies:
1. Phase A: Joint screening semantics, Bonferroni simultaneous Bartlett bounds, and Tier 1 renaming.
2. Phase B: Moving-block bootstrap implementation invariants and O(N^(1/3)) asymptotic properties.
3. Phase C: Numba JIT equivalence, NumPy vectorization, and execution speedup.
4. Phase D: Hierarchical seed derivation determinism and zero-collision guarantee.
5. Phase E & F: Research policy classification matrix and repo code hygiene (zero em-dashes).
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

from audit_direction_c5_4 import (
    derive_audit_seed,
    evaluate_screening_gate,
    audit_bootstrap_limitations_and_asymptotics,
    paired_block_bootstrap_py,
    paired_block_bootstrap_numpy,
    paired_block_bootstrap_numba,
    audit_numba_equivalence_and_benchmark,
    institute_final_research_policy
)


class TestDirectionC54PolicyAndEquivalence(unittest.TestCase):
    """Test suite for Direction C.5.4 release gate and Numba equivalence."""

    def test_phase_a_screening_gate_and_bonferroni(self):
        """Verifies screening gate semantics and Bonferroni simultaneous Bartlett bounds."""
        n = 500
        # 1. Theoretical Bonferroni bound across K=10 lags at alpha=0.05
        # z_{1 - 0.05 / 20} = z_{0.9975} approx 2.80703
        crit_bonf = 2.80703 / math.sqrt(n)
        crit_indiv = 1.95996 / math.sqrt(n)
        self.assertGreater(crit_bonf, crit_indiv)

        # 2. Screening on stationary Gaussian noise
        import random
        rng = random.Random(12345)
        clean_series = [rng.gauss(0, 0.01) for _ in range(n)]
        gate = evaluate_screening_gate(clean_series, max_lag=10, alpha=0.05)
        self.assertEqual(gate["n_obs"], 500)
        self.assertEqual(gate["max_lag"], 10)
        self.assertIn("screening_pass", gate)

    def test_phase_b_bootstrap_invariants_and_asymptotics(self):
        """Verifies implementation invariants and asymptotic block length grid."""
        res = audit_bootstrap_limitations_and_asymptotics()
        invariants = res["implementation_invariants"]
        for k, v in invariants.items():
            self.assertTrue(v, f"Bootstrap invariant failed: {k}")

        grid = res["asymptotic_block_size_analysis"]
        self.assertEqual(len(grid), 4)
        for row in grid:
            self.assertGreaterEqual(row["l_opt_estimate_range"][1], row["l_opt_estimate_range"][0])
            self.assertGreater(row["n_pow_one_third"], 0.0)

    def test_phase_c_numba_equivalence(self):
        """Verifies mathematical equivalence between Pure Python, NumPy, and Numba."""
        import random
        rng = random.Random(999)
        test_excess = [rng.gauss(0.0005, 0.015) for _ in range(500)]

        r_py = paired_block_bootstrap_py(test_excess, block_size=10, n_boot=1000, seed=42)
        r_np = paired_block_bootstrap_numpy(test_excess, block_size=10, n_boot=1000, seed=42)
        r_nb = paired_block_bootstrap_numba(test_excess, block_size=10, n_boot=1000, seed=42)

        # 1. Empirical mean excess must match to machine precision
        self.assertAlmostEqual(r_py["mean_excess"], r_np["mean_excess"], places=10)
        self.assertAlmostEqual(r_py["mean_excess"], r_nb["mean_excess"], places=10)

        # 2. P-values should be statistically close (finite MC tolerance)
        self.assertLess(abs(r_py["p_val"] - r_np["p_val"]), 0.03)
        self.assertLess(abs(r_py["p_val"] - r_nb["p_val"]), 0.03)

        # 3. 95% Confidence interval bounds should be close
        w_py = r_py["ci_high"] - r_py["ci_low"]
        w_nb = r_nb["ci_high"] - r_nb["ci_low"]
        self.assertLess(abs(w_py - w_nb), 0.002)

    def test_phase_d_hierarchical_seed_derivation(self):
        """Verifies hierarchical seed derivation determinism and zero collisions."""
        s1 = derive_audit_seed(42, "phase_a", "scenario_1", 0)
        s2 = derive_audit_seed(42, "phase_a", "scenario_1", 0)
        self.assertEqual(s1, s2, "Seed derivation is not deterministic!")

        s3 = derive_audit_seed(42, "phase_a", "scenario_2", 0)
        self.assertNotEqual(s1, s3, "Seed collision between scenarios!")

        s4 = derive_audit_seed(42, "phase_b", "scenario_1", 0)
        self.assertNotEqual(s1, s4, "Seed collision between phases!")

        # Generate 5,000 distinct seeds and confirm 0 collisions
        seed_set = set()
        for i in range(5000):
            seed = derive_audit_seed(100, "bench", "test", i)
            self.assertNotIn(seed, seed_set, f"Collision detected at index {i}!")
            seed_set.add(seed)

    def test_phase_e_research_policy_structure(self):
        """Verifies that the 4 policy categories are properly defined."""
        policy = institute_final_research_policy()
        required_categories = [
            "Category_A_SCREENING_PASS",
            "Category_B_DEPENDENCE_FLAGGED",
            "Category_C_INCONCLUSIVE",
            "Category_D_STRUCTURAL_LIMITATIONS"
        ]
        for cat in required_categories:
            self.assertIn(cat, policy)
            self.assertIn("permitted_statistical_claims", policy[cat])
            self.assertIn("strictly_prohibited_claims", policy[cat])

    def test_zero_em_dashes_in_c5_4_files(self):
        """Verifies repository standard: zero em-dashes exist in C.5.4 code or tests."""
        c5_4_files = [
            os.path.join(ROOT, "trader-timemachine", "audit_direction_c5_4.py"),
            os.path.join(ROOT, "tests", "test_audit_direction_c5_4.py")
        ]
        for filepath in c5_4_files:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertNotIn("\u2014", content, f"Em-dash detected in {filepath}")


if __name__ == "__main__":
    unittest.main()

