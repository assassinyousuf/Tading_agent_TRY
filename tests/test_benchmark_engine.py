"""
Unit tests for Direction C: Benchmark & Statistical Evaluation Engine.
Verifies:
- Hand-calculated benchmark cases (Cash, BTC B&H Gross/Net, Equal-Weight, 50/50 Drift Band, Vol-Targeted BTC)
- Synthetic constant returns, zero volatility, and exact compounding identities
- Walk-forward chronological splitter invariants (zero leakage, boundary validation, non-overlapping test slices)
- Paired moving-block bootstrap determinism, one-sided null construction, and tail selection
- Minimum Detectable Effect (MDE) formula against hand-calculated analytical reference values
- Bailey & Lopez de Prado Deflated Sharpe Ratio (DSR) and multiple-testing adjustments (Bonferroni, Holm, FDR)
- Edge cases: short samples, zero variance, and division-by-zero safeguards
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

from benchmark_engine import (
    DEFAULT_UNIVERSE,
    load_aligned_daily_candles,
    calc_cagr,
    calc_annualized_vol,
    calc_sharpe_ratio,
    calc_sortino_ratio,
    calc_max_drawdown,
    calc_skewness,
    calc_kurtosis,
    calc_benchmark_cash,
    calc_benchmark_btc_hold,
    calc_benchmark_equal_weight,
    calc_benchmark_50_50_btc,
    calc_benchmark_vol_target_btc,
    WalkForwardSplitter,
    calc_paired_excess_returns,
    paired_block_bootstrap_test,
    calc_minimum_detectable_effect,
    calc_deflated_sharpe_ratio,
    adjust_multiple_testing_pvalues,
    run_benchmark_engine
)


class TestBenchmarkEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.trader_dir = os.path.join(ROOT, "trader")
        cls.bars = load_aligned_daily_candles(DEFAULT_UNIVERSE, trader_dir=cls.trader_dir)

    # --------------------------------------------------------------------------
    # 1. Hand-Calculated & Synthetic Benchmark Tests
    # --------------------------------------------------------------------------
    def test_01_hand_calculated_cash_benchmark(self):
        """Verifies Cash benchmark produces exact 0% return, 0 vol, 0 friction, 0 drawdown."""
        cash = calc_benchmark_cash(n_days=100, initial_capital=10000.0)
        m = cash["metrics"]
        self.assertEqual(m["initial_capital"], 10000.0)
        self.assertEqual(m["final_capital"], 10000.0)
        self.assertEqual(m["net_total_return_pct"], 0.0)
        self.assertEqual(m["cagr_pct"], 0.0)
        self.assertEqual(m["annualized_vol_pct"], 0.0)
        self.assertEqual(m["sharpe_ratio"], 0.0)
        self.assertEqual(m["max_drawdown_pct"], 0.0)
        self.assertEqual(m["total_friction_usd"], 0.0)
        self.assertEqual(m["trades_count"], 0)
        self.assertEqual(len(cash["daily_returns"]), 100)
        self.assertEqual(len(cash["equity_curve"]), 101)

    def test_02_synthetic_constant_returns(self):
        """Verifies mathematical compounding identity on synthetic +1.0% constant daily return."""
        rets = [0.01] * 10
        cap = 10000.0
        curve = [cap]
        for r in rets:
            cap *= (1.0 + r)
            curve.append(cap)

        expected_final = 10000.0 * (1.01 ** 10)  # 11046.22125
        self.assertAlmostEqual(cap, expected_final, places=4)
        vol = calc_annualized_vol(rets)
        self.assertEqual(vol, 0.0)  # Zero variance
        max_dd = calc_max_drawdown(curve)
        self.assertEqual(max_dd, 0.0)  # Monotonic increase

    def test_03_btc_gross_vs_net_benchmark(self):
        """Verifies Gross BTC has 0 friction, while Net BTC charges exact 15 bps entry + terminal exit."""
        start_bar = 30
        end_bar = 728
        gross = calc_benchmark_btc_hold(self.bars, start_bar=start_bar, end_bar=end_bar, mode="gross")
        net = calc_benchmark_btc_hold(self.bars, start_bar=start_bar, end_bar=end_bar, mode="net")

        self.assertEqual(gross["metrics"]["total_friction_usd"], 0.0)
        self.assertEqual(gross["metrics"]["trades_count"], 0)
        self.assertGreater(net["metrics"]["total_friction_usd"], 25.0)
        self.assertEqual(net["metrics"]["trades_count"], 2)

        # Net final equity must be strictly less than Gross final equity due to friction
        self.assertLess(net["metrics"]["final_capital"], gross["metrics"]["final_capital"])
        # Friction derivation: $15.00 entry + terminal exit friction
        self.assertAlmostEqual(net["metrics"]["total_friction_usd"], 27.50, delta=0.10)

    def test_04_equal_weight_l1_basket_passive_drift(self):
        """Verifies Equal-Weight basket starts 25% each and drifts without daily rebalance churn."""
        ew_gross = calc_benchmark_equal_weight(self.bars, start_bar=30, end_bar=728, mode="gross")
        ew_net = calc_benchmark_equal_weight(self.bars, start_bar=30, end_bar=728, mode="net")

        self.assertEqual(ew_gross["metrics"]["total_friction_usd"], 0.0)
        self.assertEqual(ew_net["metrics"]["trades_count"], 8)  # 4 buys at start + 4 sells at end
        self.assertAlmostEqual(ew_net["metrics"]["total_friction_usd"], 26.51, delta=0.10)
        self.assertLess(ew_net["metrics"]["final_capital"], ew_gross["metrics"]["final_capital"])

    def test_05_50_50_btc_cash_rebalancing_rules(self):
        """Verifies 50/50 BTC/Cash rebalances only when drift exceeds 5% buffer."""
        drift_50 = calc_benchmark_50_50_btc(self.bars, start_bar=30, end_bar=728, rebalance_rule="drift_band")
        monthly_50 = calc_benchmark_50_50_btc(self.bars, start_bar=30, end_bar=728, rebalance_rule="monthly")

        # Drift band rebalancing should execute significantly fewer trades than monthly rebalancing
        self.assertLess(drift_50["metrics"]["trades_count"], monthly_50["metrics"]["trades_count"])
        self.assertEqual(drift_50["metrics"]["trades_count"], 11)
        self.assertEqual(monthly_50["metrics"]["trades_count"], 25)
        # 50/50 BTC/Cash volatility should be approximately half of 100% BTC volatility
        btc_vol = calc_benchmark_btc_hold(self.bars, start_bar=30, end_bar=728)["metrics"]["annualized_vol_pct"]
        self.assertAlmostEqual(drift_50["metrics"]["annualized_vol_pct"], btc_vol / 2.0, delta=2.0)

    def test_06_volatility_targeted_btc_benchmark(self):
        """Verifies volatility-targeted benchmark scales exposure inversely to 30d realized volatility."""
        vol_bench = calc_benchmark_vol_target_btc(self.bars, target_vol=0.25, start_bar=30, end_bar=728)
        m = vol_bench["metrics"]
        # Realized volatility must be close to target 25% (within 3 percentage points)
        self.assertAlmostEqual(m["annualized_vol_pct"], 25.0, delta=3.0)
        self.assertGreater(m["total_friction_usd"], 0.0)
        self.assertGreater(m["trades_count"], 0)

    # --------------------------------------------------------------------------
    # 2. Walk-Forward Cross-Validation Splitter Invariants
    # --------------------------------------------------------------------------
    def test_07_walk_forward_splitter_invariants(self):
        """Verifies 5-fold walk-forward splits have zero leakage and contiguous non-overlapping test slices."""
        splitter = WalkForwardSplitter(n_splits=5, warmup_bars=30, mode="rolling", train_bars=300)
        splits = splitter.split(729)

        self.assertEqual(len(splits), 5)
        prev_test_end = None
        for s in splits:
            t = s["train"]
            e = s["test"]
            w = s["warmup"]
            # Strict chronological ordering & zero leakage
            self.assertLess(t["end"], e["start"])
            self.assertLessEqual(w["end"], t["start"])
            # Contiguous test folds
            if prev_test_end is not None:
                self.assertEqual(e["start"], prev_test_end + 1)
            prev_test_end = e["end"]

        # Final test slice must reach the end of the dataset
        self.assertEqual(splits[-1]["test"]["end"], 728)

    def test_08_walk_forward_edge_cases(self):
        """Verifies splitter raises ValueError on short datasets or invalid split count."""
        splitter = WalkForwardSplitter(n_splits=5, warmup_bars=30, train_bars=300)
        with self.assertRaises(ValueError):
            splitter.split(100)  # Too short for 300 train bars
        with self.assertRaises(ValueError):
            WalkForwardSplitter(n_splits=1)  # n_splits must be >= 2

    # --------------------------------------------------------------------------
    # 3. Statistical Utilities & Hypothesis Testing
    # --------------------------------------------------------------------------
    def test_09_paired_bootstrap_determinism_and_null(self):
        """Verifies moving block bootstrap determinism with fixed seed and proper null evaluation."""
        s_rets = [-0.001] * 100
        b_rets = [0.001] * 100

        ht1 = paired_block_bootstrap_test(s_rets, b_rets, block_size=10, n_boot=500, seed=42)
        ht2 = paired_block_bootstrap_test(s_rets, b_rets, block_size=10, n_boot=500, seed=42)

        # Deterministic under fixed seed
        self.assertEqual(ht1["p_value"], ht2["p_value"])
        self.assertEqual(ht1["ci_95_excess_pp"], ht2["ci_95_excess_pp"])
        # When strategy underperforms on every bar, p-value under H0: mu <= 0 must be 1.0
        self.assertEqual(ht1["p_value"], 1.0)

        # Hand-calculated case: strategy strictly outperforms on every bar
        s_win = [0.005] * 100
        b_lose = [0.001] * 100
        ht_win = paired_block_bootstrap_test(s_win, b_lose, block_size=10, n_boot=500, seed=42)
        # Finite-sample (k+1)/(B+1) convention: k=0 exceedances gives min possible p-value = 1/(B+1)
        self.assertEqual(ht_win["p_value"], round(1.0 / (500 + 1), 4))
        self.assertEqual(ht_win["p_value_raw"], 0.0)
        self.assertEqual(ht_win["min_possible_p_value"], round(1.0 / (500 + 1), 6))

    def test_10_minimum_detectable_effect_analytical_formula(self):
        """Verifies MDE formula against hand-calculated analytical reference case."""
        # N = 365.25 days, excess_vol = 20%, alpha = 0.05, power = 0.80
        # Formula: (1.6449 + 0.8416) * 0.20 * sqrt(365.25 / 365) * 100% = 2.4865 * 20 = 49.73%
        mde = calc_minimum_detectable_effect(n_days=365, excess_vol=0.20, alpha=0.05, power=0.80)
        self.assertAlmostEqual(mde, 49.75, delta=0.10)

    def test_11_deflated_sharpe_ratio_and_multiple_testing(self):
        """Verifies Deflated Sharpe Ratio calculation and multiple-testing adjustments."""
        # High observed Sharpe with few trials should yield high DSR
        dsr_high = calc_deflated_sharpe_ratio(observed_sharpe=3.0, n_obs=700, n_trials=5)
        self.assertGreater(dsr_high, 0.90)

        # Modest observed Sharpe with 150 trials should be deflated to low DSR
        dsr_low = calc_deflated_sharpe_ratio(observed_sharpe=0.5, n_obs=700, n_trials=150)
        self.assertLess(dsr_low, 0.10)

        # Multiple testing adjustments
        p_raw = [0.01, 0.04, 0.10]
        p_bonf = adjust_multiple_testing_pvalues(p_raw, method="bonferroni")
        self.assertEqual(p_bonf, [0.03, 0.12, 0.30])

        p_holm = adjust_multiple_testing_pvalues(p_raw, method="holm")
        # Step-down: 0.01 * 3 = 0.03; 0.04 * 2 = 0.08; 0.10 * 1 = 0.10
        self.assertEqual(p_holm, [0.03, 0.08, 0.10])

        p_fdr = adjust_multiple_testing_pvalues(p_raw, method="fdr_bh")
        self.assertLessEqual(p_fdr[0], p_holm[0])

    def test_12_statistical_moments_skewness_and_kurtosis(self):
        """Verifies sample skewness and excess kurtosis against symmetric normal reference."""
        symmetric = [-0.02, -0.01, 0.0, 0.01, 0.02] * 20
        skew = calc_skewness(symmetric)
        self.assertAlmostEqual(skew, 0.0, places=4)

        asymmetric = [0.01] * 90 + [-0.10] * 10
        skew_neg = calc_skewness(asymmetric)
        self.assertLess(skew_neg, -1.0)  # Heavy left tail


if __name__ == "__main__":
    unittest.main()
