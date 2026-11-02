"""Adversarial and empirical verification suite for Phase 2.7.2.

Evaluates statistical validity, multiple-testing control, selection bias,
White's Reality Check, Deflated Sharpe Ratio, serial dependence, overlapping
returns, heavy tails, non-stationarity, and experiment lifecycle accounting.

Typography standard: Strict ASCII hyphen invariant (0 em dashes, 0 en dashes).
"""

import math
import os
import sqlite3
import sys
import unittest
from typing import List

import numpy as np
import scipy.stats as stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from benchmark_engine import calc_deflated_sharpe_ratio, paired_block_bootstrap_test
from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ResearchExecutionEngine,
    SimulationExecutionError,
)
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.tools.market_bridge import MarketDataProvider
from trader.agent.validation.statistical_validity import (
    AccountingDiscrepancyType,
    StatisticalValidationStatus,
    analyze_autocorrelation,
    audit_experiment_lifecycle_accounting,
    calculate_independent_dsr,
    simulate_selection_bias,
    validate_bootstrap_ci_coverage,
    white_reality_check,
)


class CrashingStrategy(SyntheticSMAStrategy):
    """Strategy that raises an exception during simulation to simulate failed experiments."""

    def compute_indicators(self, bars, params):
        raise RuntimeError("Simulated mid-flight strategy execution crash")


class TestPhase272StatisticalValidityAdversarial(unittest.TestCase):
    """Adversarial and validation tests for Phase 2.7.2 statistical integrity."""

    def setUp(self) -> None:
        self.ledger = ResearchLedger(db_path=":memory:")
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)

        # Base hypothesis and preregistration
        self.ledger.record_hypothesis(
            hypothesis_id="H-STAT-01",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Statistical validity testing on TRAIN",
            validation_criteria={"min_sharpe": 0.5},
        )
        self.prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-STAT-01",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [5.0, 10.0, 15.0], "slow_period": [20.0, 30.0, 40.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 30, "end_bar": 430},
            benchmark_ids=["BTC_NET"],
        )

    def tearDown(self) -> None:
        self.ledger.close()

    # -----------------------------------------------------------------------
    # Case 1: Independent Null Strategies
    # -----------------------------------------------------------------------
    def test_case_01_independent_null_strategies(self) -> None:
        """Evaluate M=50 independent zero-alpha strategies against White's Reality Check."""
        rng = np.random.RandomState(20)
        n_obs = 500
        n_strats = 50

        # Generate 50 independent Gaussian null return series (zero mean, 1% daily vol)
        strat_returns = rng.randn(n_obs, n_strats) * 0.01
        bench_returns = np.zeros(n_obs)

        # 1. White's Reality Check should fail to reject H0: max_k E[d_k] <= 0
        wrc_res = white_reality_check(
            strategy_returns_matrix=strat_returns,
            benchmark_returns=bench_returns,
            n_boot=1000,
            block_size=10,
            seed=20,
        )
        self.assertGreaterEqual(wrc_res.p_value_wrc, 0.05, f"WRC falsely rejected null: p={wrc_res.p_value_wrc}")
        self.assertFalse(wrc_res.reject_null_at_05)

        # 2. Check maximum observed Sharpe ratio vs EVT theoretical prediction
        daily_srs = np.mean(strat_returns, axis=0) / np.std(strat_returns, axis=0, ddof=1)
        ann_srs = daily_srs * math.sqrt(365.25)
        max_ann_sr = float(np.max(ann_srs))

        # Expected max of 50 standard normals: approx 2.25 sigma
        euler_gamma = 0.5772156649
        p1 = 1.0 - 1.0 / float(n_strats)
        p2 = 1.0 - 1.0 / (float(n_strats) * math.e)
        z_evt = (1.0 - euler_gamma) * float(stats.norm.ppf(p1)) + euler_gamma * float(stats.norm.ppf(p2))
        expected_ann_sr = (z_evt / math.sqrt(n_obs)) * math.sqrt(365.25)

        # Max observed should be within 2.0 standard errors of EVT prediction
        se_ann = math.sqrt(365.25 / n_obs)
        self.assertAlmostEqual(max_ann_sr, expected_ann_sr, delta=2.0 * se_ann)

        # 3. DSR with M=50 should deflation-correct the cherry-picked best strategy
        dsr_res = calculate_independent_dsr(
            observed_sharpe=max_ann_sr,
            n_obs=n_obs,
            n_trials=n_strats,
            trial_sharpe_std=1.0,
            annual_factor=365.25,
        )
        self.assertLess(dsr_res.dsr, 0.95, f"DSR failed to deflate null cherry-pick: DSR={dsr_res.dsr}")


    # -----------------------------------------------------------------------
    # Case 2: Highly Correlated Null Strategies
    # -----------------------------------------------------------------------
    def test_case_02_highly_correlated_null_strategies(self) -> None:
        """Evaluate M=50 highly correlated null strategies (rho=0.85)."""
        rng = np.random.RandomState(101)
        n_obs = 500
        n_strats = 50
        rho = 0.85

        # Factor structure: r_{t,k} = sqrt(rho) * Common_t + sqrt(1 - rho) * Idio_{t,k}
        common = rng.randn(n_obs, 1) * 0.01
        idio = rng.randn(n_obs, n_strats) * 0.01
        strat_returns = math.sqrt(rho) * common + math.sqrt(1.0 - rho) * idio
        bench_returns = np.zeros(n_obs)

        # Effective trials M_eff via eigenvalue participation ratio
        corr = np.corrcoef(strat_returns.T)
        eigvals = np.maximum(0.0, np.linalg.eigvalsh(corr))
        m_eff = (np.sum(eigvals) ** 2) / np.sum(eigvals ** 2)
        self.assertLess(m_eff, 15.0, f"Expected M_eff << 50 under rho=0.85, got {m_eff}")

        # White's Reality Check should account for cross-correlation and not reject null
        wrc_res = white_reality_check(
            strategy_returns_matrix=strat_returns,
            benchmark_returns=bench_returns,
            n_boot=1000,
            block_size=10,
            seed=101,
        )
        self.assertGreaterEqual(wrc_res.p_value_wrc, 0.05)

        # DSR sensitivity: using raw M=50 produces higher hurdle than M=M_eff
        best_sr = float(np.max(np.mean(strat_returns, axis=0) / np.std(strat_returns, axis=0, ddof=1)) * math.sqrt(365.25))
        dsr_m50 = calculate_independent_dsr(observed_sharpe=max(0.1, best_sr), n_obs=n_obs, n_trials=50)
        dsr_meff = calculate_independent_dsr(observed_sharpe=max(0.1, best_sr), n_obs=n_obs, n_trials=int(round(m_eff)))

        self.assertLessEqual(dsr_m50.dsr, dsr_meff.dsr)
        self.assertGreaterEqual(dsr_m50.expected_max_sr_annual, dsr_meff.expected_max_sr_annual)

    # -----------------------------------------------------------------------
    # Case 3: One Genuinely Profitable Strategy Among Many Null Strategies
    # -----------------------------------------------------------------------
    def test_case_03_genuine_alpha_among_null_strategies(self) -> None:
        """Evaluate detection power when 1 genuine alpha strategy is placed among 29 nulls."""
        rng = np.random.RandomState(202)
        n_obs = 600
        n_strats = 30

        # Strategies 1 to 29 are null noise (mu=0, vol=1% daily)
        strat_returns = rng.randn(n_obs, n_strats) * 0.01
        # Strategy 0 has genuine alpha: +0.15% daily excess return (approx +54% annual excess)
        strat_returns[:, 0] += 0.0015
        bench_returns = np.zeros(n_obs)

        wrc_res = white_reality_check(
            strategy_returns_matrix=strat_returns,
            benchmark_returns=bench_returns,
            n_boot=1000,
            block_size=10,
            seed=202,
        )
        # Strategy 0 must be identified as the best strategy
        self.assertEqual(wrc_res.best_strategy_index, 0)
        # White's Reality Check should reject H0
        self.assertTrue(wrc_res.reject_null_at_05, f"WRC failed to detect true alpha: p={wrc_res.p_value_wrc}")
        self.assertLess(wrc_res.p_value_wrc, 0.05)

        # Hansen's SPA consistent p-value should also reject and be <= WRC p-value
        self.assertLessEqual(wrc_res.p_value_spa_consistent, wrc_res.p_value_wrc + 1e-4)

    # -----------------------------------------------------------------------
    # Case 4: Repeated Parameter Searches
    # -----------------------------------------------------------------------
    def test_case_04_repeated_parameter_searches_selection_bias(self) -> None:
        """Simulate selection bias across grid sizes M=10, 50, 100, 500 under the null."""
        sim_res = simulate_selection_bias(
            n_strategies=100,
            n_obs=400,
            true_alpha=0.0,
            cross_correlation=0.0,
            n_simulations=150,
            seed=303,
        )
        # Naive false positive rate at alpha=0.05 is massive (typically > 80%)
        self.assertGreaterEqual(
            sim_res.nominal_false_positive_rate_at_05,
            0.60,
            f"Expected naive FP rate > 60%, got {sim_res.nominal_false_positive_rate_at_05}",
        )
        # DSR-adjusted false positive rate controls false positives (<= 10%)
        self.assertLessEqual(
            sim_res.dsr_adjusted_false_positive_rate_at_05,
            0.10,
            f"DSR FP rate should be <= 10%, got {sim_res.dsr_adjusted_false_positive_rate_at_05}",
        )
        # Bonferroni also strictly controls FP rate
        self.assertLessEqual(sim_res.bonferroni_false_positive_rate_at_05, 0.10)

    # -----------------------------------------------------------------------
    # Case 5: Small Sample Sizes
    # -----------------------------------------------------------------------
    def test_case_05_small_sample_size_degradation(self) -> None:
        """Verify handling of small sample sizes N=5, 10, 25."""
        # 1. n_obs <= 10 returns sentinel 0.0 per DSR specification
        dsr_n5 = calculate_independent_dsr(observed_sharpe=2.5, n_obs=5, n_trials=5)
        self.assertEqual(dsr_n5.dsr, 0.0)
        self.assertEqual(dsr_n5.status, "NON_POSITIVE_OR_INSUFFICIENT_OBS")
        self.assertTrue(len(dsr_n5.assumptions_violated) > 0)

        # 2. Production calc_deflated_sharpe_ratio matches sentinel
        prod_dsr_n5 = calc_deflated_sharpe_ratio(observed_sharpe=2.5, n_obs=5, n_trials=5)
        self.assertEqual(prod_dsr_n5, 0.0)

        # 3. N=25 produces wide standard errors
        dsr_n25 = calculate_independent_dsr(observed_sharpe=1.5, n_obs=25, n_trials=1)
        dsr_n500 = calculate_independent_dsr(observed_sharpe=1.5, n_obs=500, n_trials=1)
        self.assertGreater(dsr_n25.se_sr_annual, dsr_n500.se_sr_annual * 3.0)

    # -----------------------------------------------------------------------
    # Case 6: Autocorrelated Returns
    # -----------------------------------------------------------------------
    def test_case_06_autocorrelated_returns(self) -> None:
        """Verify AR(1) autocorrelation detection, Ljung-Box test, and sample size inflation."""
        rng = np.random.RandomState(404)
        n = 500
        rho = 0.50

        # Generate AR(1) series: x_t = rho * x_{t-1} + eps_t
        eps = rng.randn(n) * 0.01
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = rho * x[t - 1] + eps[t]

        res = analyze_autocorrelation(returns=x, max_lag=10)
        self.assertTrue(res.has_significant_autocorrelation)
        self.assertLess(res.ljung_box_p_value, 0.001)
        self.assertAlmostEqual(res.ar1_coefficient, rho, delta=0.15)

        # Theoretical N_eff for AR(1) with rho=0.5: N * (1 - 0.5) / (1 + 0.5) = N / 3 approx 167
        expected_n_eff = float(n) * (1.0 - rho) / (1.0 + rho)
        self.assertAlmostEqual(res.effective_sample_size, expected_n_eff, delta=60.0)
        self.assertGreater(res.sample_size_inflation_factor, 2.0)

    # -----------------------------------------------------------------------
    # Case 7: Overlapping Return Observations
    # -----------------------------------------------------------------------
    def test_case_07_overlapping_return_observations(self) -> None:
        """Verify detection of induced MA(h-1) autocorrelation signature in rolling returns."""
        rng = np.random.RandomState(505)
        n_raw = 600
        daily_rets = rng.randn(n_raw) * 0.01

        # Construct 5-day rolling cumulative returns: r_{t}^{(5)} = sum_{i=0}^4 r_{t-i}
        h = 5
        rolling_rets = [sum(daily_rets[t - h:t]) for t in range(h, n_raw)]

        res = analyze_autocorrelation(returns=rolling_rets, max_lag=10)
        self.assertTrue(res.has_significant_autocorrelation)
        # Must detect induced overlapping horizon h=5
        self.assertEqual(res.induced_overlapping_horizon, 5)

        # Moving block bootstrap with block_size < 5 understates dependence
        # Paired test with block_size=1 vs block_size=10
        boot_b1 = paired_block_bootstrap_test(
            strategy_returns=rolling_rets,
            benchmark_returns=[0.0] * len(rolling_rets),
            block_size=1,
            n_boot=300,
            seed=42,
        )
        boot_b10 = paired_block_bootstrap_test(
            strategy_returns=rolling_rets,
            benchmark_returns=[0.0] * len(rolling_rets),
            block_size=10,
            n_boot=300,
            seed=42,
        )
        # Block size 10 CI should be wider than naive block size 1 CI
        ci_w1 = boot_b1["ci_95_daily_mean"][1] - boot_b1["ci_95_daily_mean"][0]
        ci_w10 = boot_b10["ci_95_daily_mean"][1] - boot_b10["ci_95_daily_mean"][0]
        self.assertGreater(ci_w10, ci_w1 * 1.20)

    # -----------------------------------------------------------------------
    # Case 8: Heavy-Tailed Return Distributions
    # -----------------------------------------------------------------------
    def test_case_08_heavy_tailed_return_distributions(self) -> None:
        """Verify Mertens variance adjustment and bootstrap behavior under Student-t (nu=3)."""
        rng = np.random.RandomState(606)
        n = 500
        # Student-t with nu=3 has infinite kurtosis
        heavy_rets = rng.standard_t(df=3, size=n) * 0.01

        kurt_excess = float(stats.kurtosis(heavy_rets))
        skew = float(stats.skew(heavy_rets))
        self.assertGreater(kurt_excess, 2.0, f"Expected heavy tails, got excess kurtosis {kurt_excess}")

        # In DSR, excess kurtosis inflates Var(SR) via Mertens formula
        dsr_normal = calculate_independent_dsr(observed_sharpe=1.5, n_obs=n, n_trials=5, skewness=0.0, kurtosis=0.0)
        dsr_heavy = calculate_independent_dsr(observed_sharpe=1.5, n_obs=n, n_trials=5, skewness=skew, kurtosis=kurt_excess)

        self.assertGreater(dsr_heavy.se_sr_annual, dsr_normal.se_sr_annual)
        self.assertLess(dsr_heavy.z_stat, dsr_normal.z_stat)
        self.assertLess(dsr_heavy.dsr, dsr_normal.dsr)

    # -----------------------------------------------------------------------
    # Case 9: Non-Stationary Returns
    # -----------------------------------------------------------------------
    def test_case_09_non_stationary_regime_shifts(self) -> None:
        """Verify impact of regime shifts (Bull regime vs Bear regime) on stationarity assumptions."""
        rng = np.random.RandomState(707)
        n_regime = 250
        # Regime 1: Bull (+0.1% daily mean, 1% daily vol)
        regime1 = rng.randn(n_regime) * 0.01 + 0.001
        # Regime 2: Bear (-0.2% daily mean, 3% daily vol)
        regime2 = rng.randn(n_regime) * 0.03 - 0.002
        non_stationary = np.concatenate([regime1, regime2])

        # Subsample variance differs drastically
        var1 = np.var(regime1)
        var2 = np.var(regime2)
        self.assertGreater(var2 / var1, 4.0, "Expected variance divergence across regimes")

        # Autocorrelation and regime shift analysis detects severe variance shift
        res = analyze_autocorrelation(returns=non_stationary, max_lag=15)
        self.assertTrue(res.has_regime_shift)
        self.assertGreater(res.subperiod_variance_ratio, 3.0)

    # -----------------------------------------------------------------------
    # Case 10: Failed and Interrupted Experiments Lifecycle Consistency
    # -----------------------------------------------------------------------
    def test_case_10_failed_and_interrupted_experiments_lifecycle(self) -> None:
        """Verify that failed, interrupted, pre-test rejected, and completed trials are audited consistently."""
        # 1. Successful execution
        req_ok = ControlledExperimentRequest(
            hypothesis_id="H-STAT-01",
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-TEST-OK",
        )
        res_ok = self.engine.execute(req_ok)
        self.assertEqual(res_ok.status, "SUCCESS")

        # 2. Crashing execution -> FAILED disposition
        req_fail = ControlledExperimentRequest(
            hypothesis_id="H-STAT-01",
            prereg_id=self.prereg_id,
            strategy=CrashingStrategy(),
            parameters={"fast_period": 15.0, "slow_period": 30.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-TEST-FAIL",
        )
        with self.assertRaises(SimulationExecutionError):
            self.engine.execute(req_fail)

        # 3. Interrupted trial (simulate crash during execution)
        trial_int_id = self.ledger.start_trial(
            hypothesis_id="H-STAT-01",
            prereg_id=self.prereg_id,
            experiment_id="EXP-INTERRUPTED-99",
            parameters_evaluated={"fast_period": 5.0, "slow_period": 20.0},
            disposition="STARTED",
        )
        self.ledger.finalize_trial(trial_int_id, disposition="INTERRUPTED", experiment_id="EXP-INTERRUPTED-99")

        # 4. Pre-test rejection (invalid parameter)
        try:
            req_bad_param = ControlledExperimentRequest(
                hypothesis_id="H-STAT-01",
                prereg_id=self.prereg_id,
                strategy=SyntheticSMAStrategy(),
                parameters={"fast_period": 999.0, "slow_period": 30.0},  # not in grid
                partition="TRAIN",
                symbol="BTCUSDT",
                request_id="REQ-TEST-BAD",
            )
            self.engine.execute(req_bad_param)
        except Exception:
            pass

        # Audit lifecycle accounting
        report = audit_experiment_lifecycle_accounting(self.ledger.conn)
        self.assertEqual(report.total_ledger_rows, 4)
        self.assertEqual(report.completed_count, 1)
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.interrupted_count, 1)
        self.assertEqual(report.pre_test_rejected_count, 1)
        self.assertTrue(report.is_monotonically_ordered)

    # -----------------------------------------------------------------------
    # Case 11: Incorrect Multiple-Testing Denominators
    # -----------------------------------------------------------------------
    def test_case_11_incorrect_multiple_testing_denominators(self) -> None:
        """Demonstrate statistical distortion when cherry-picking under M=1 vs M_total."""
        obs_sharpe = 1.80
        n_obs = 400
        m_true = 50

        # Cherry-picked report with M=1 (ignoring snooping)
        dsr_m1 = calculate_independent_dsr(observed_sharpe=obs_sharpe, n_obs=n_obs, n_trials=1)
        # Proper accounting with M_total = 50
        dsr_m50 = calculate_independent_dsr(observed_sharpe=obs_sharpe, n_obs=n_obs, n_trials=m_true)

        # Reporting M=1 claims high significance (DSR > 0.95)
        self.assertGreater(dsr_m1.dsr, 0.95, f"M=1 gave {dsr_m1.dsr}")
        # Proper M=50 completely deflates significance (DSR < 0.35)
        self.assertLess(dsr_m50.dsr, 0.35, f"M=50 gave {dsr_m50.dsr}")
        # The expected max Sharpe hurdle jumps from 0 to over 2.2 annual Sharpe
        self.assertGreater(dsr_m50.expected_max_sr_annual, 2.0)

    # -----------------------------------------------------------------------
    # Case 12: Deliberately Manipulated Experiment Counts
    # -----------------------------------------------------------------------
    def test_case_12_deliberately_manipulated_ledger_audit(self) -> None:
        """Simulate tampering with cumulative_m_total and verify audit detection."""
        # Insert 3 trials
        t1 = self.ledger.start_trial("H-STAT-01", self.prereg_id, "EXP-1", {}, "COMPLETED")
        t2 = self.ledger.start_trial("H-STAT-01", self.prereg_id, "EXP-2", {}, "COMPLETED")
        t3 = self.ledger.start_trial("H-STAT-01", self.prereg_id, "EXP-3", {}, "COMPLETED")

        # Verify initial clean audit
        clean_rep = audit_experiment_lifecycle_accounting(self.ledger.conn)
        self.assertEqual(clean_rep.overall_status, StatisticalValidationStatus.VALID)

        # Layer 1 defense: SQLite trigger prevent_trial_tampering blocks update directly
        cur = self.ledger.conn.cursor()
        with self.assertRaises(sqlite3.IntegrityError):
            cur.execute("UPDATE trial_ledger SET cumulative_m_total = 1 WHERE trial_id = ?;", (t3,))

        # Layer 2 defense: If an adversary drops the trigger to corrupt the ledger
        cur.execute("DROP TRIGGER IF EXISTS prevent_trial_tampering;")
        cur.execute("UPDATE trial_ledger SET cumulative_m_total = 1 WHERE trial_id = ?;", (t3,))
        self.ledger.conn.commit()

        tampered_rep = audit_experiment_lifecycle_accounting(self.ledger.conn)
        self.assertEqual(tampered_rep.overall_status, StatisticalValidationStatus.INVALID)
        self.assertFalse(tampered_rep.is_monotonically_ordered)
        disc_types = [d.discrepancy_type for d in tampered_rep.discrepancies]
        self.assertIn(AccountingDiscrepancyType.MONOTONICITY_VIOLATION, disc_types)



    # -----------------------------------------------------------------------
    # Independent Numerical Cross-Checks: Production DSR vs Independent DSR
    # -----------------------------------------------------------------------
    def test_independent_dsr_numerical_cross_check(self) -> None:
        """Verify production calc_deflated_sharpe_ratio matches independent implementation."""
        test_cases = [
            (1.20, 698, 67, 0.0, 0.0, 1.0),
            (2.10, 500, 10, -0.5, 3.0, 1.2),
            (0.80, 250, 50, 0.2, 1.5, 0.8),
            (3.00, 700, 1, 0.0, 0.0, 1.0),
            (-0.50, 698, 67, 0.0, 0.0, 1.0),
        ]

        for obs_sr, n_obs, n_trials, skew, kurt, trial_std in test_cases:
            with self.subTest(obs_sr=obs_sr, n_obs=n_obs, n_trials=n_trials):
                prod_val = calc_deflated_sharpe_ratio(
                    observed_sharpe=obs_sr,
                    n_obs=n_obs,
                    n_trials=n_trials,
                    skewness=skew,
                    kurtosis=kurt,
                    trial_sharpe_std=trial_std,
                    is_annualized=True,
                )
                indep_res = calculate_independent_dsr(
                    observed_sharpe=obs_sr,
                    n_obs=n_obs,
                    n_trials=n_trials,
                    skewness=skew,
                    kurtosis=kurt,
                    trial_sharpe_std=trial_std,
                    is_annualized=True,
                )
                self.assertAlmostEqual(prod_val, indep_res.dsr, places=3)

    # -----------------------------------------------------------------------
    # Bootstrap CI Coverage Validation with Known DGP
    # -----------------------------------------------------------------------
    def test_bootstrap_ci_coverage_synthetic_normal(self) -> None:
        """Validate bootstrap 95% CI coverage on synthetic Gaussian DGP with known mean mu=0.0005."""
        mu_true = 0.0005
        cov_res = validate_bootstrap_ci_coverage(
            data_generator=lambda r: r.randn(100) * 0.01 + mu_true,
            ground_truth_mean=mu_true,
            n_samples=50,
            sample_size=100,
            n_boot=300,
            block_size=5,
            nominal_level=0.95,
            seed=808,
            dgp_name="GAUSSIAN_IID",
        )
        # Empirical coverage should be within binomial tolerance of 0.95 (>= 0.88)
        self.assertGreaterEqual(cov_res.empirical_coverage, 0.88)
        self.assertTrue(cov_res.is_well_calibrated)


if __name__ == "__main__":
    unittest.main()
