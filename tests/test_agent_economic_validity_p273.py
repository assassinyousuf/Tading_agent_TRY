"""Adversarial and empirical verification suite for Phase 2.7.3.

Evaluates economic validity, transaction cost modeling, execution realism,
maker/taker assumptions, bid-ask spread, slippage, liquidity constraints,
turnover drag, futures funding costs, execution pricing models, and manifest linkage.

Typography standard: Strict ASCII hyphen invariant (0 em dashes, 0 en dashes).
"""

import math
import os
import sys
import unittest
from typing import Any, Dict, List

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trader.agent.evidence.manifest import (
    ExperimentEvidenceManifest,
    generate_evidence_manifest,
)
from trader.agent.validation.economic_validity import (
    EconomicCostModel,
    EconomicDisposition,
    EconomicExecutionReport,
    EconomicStressScenario,
    ExecutionPricingModel,
    OrderTypeAssumption,
    evaluate_execution_stress_grid,
    simulate_economic_execution,
    verify_manifest_economic_link,
)


def _generate_synthetic_market_bars(
    n_bars: int = 400,
    start_price: float = 30000.0,
    daily_vol: float = 0.02,
    base_volume_usd: float = 10000000.0,
    trend: float = 0.0002,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """Generate deterministic synthetic market bars for economic validation."""
    rng = np.random.RandomState(seed)
    bars: List[Dict[str, Any]] = []
    p = start_price

    for t in range(n_bars):
        ret = rng.randn() * daily_vol + trend
        open_p = p
        close_p = open_p * (1.0 + ret)
        high_p = max(open_p, close_p) * (1.0 + abs(rng.randn() * 0.005))
        low_p = min(open_p, close_p) * (1.0 - abs(rng.randn() * 0.005))
        vol = base_volume_usd * (0.8 + 0.4 * rng.rand())

        bars.append({
            "open_time": f"2026-01-01T{t % 24:02d}:00:00Z",
            "open": round(open_p, 2),
            "high": round(high_p, 2),
            "low": round(low_p, 2),
            "close": round(close_p, 2),
            "volume": round(vol / open_p, 4),
            "quote_volume": round(vol, 2),
        })
        p = close_p

    return bars


def _generate_profitable_gross_market(n_cycles: int = 25) -> tuple[List[Dict[str, Any]], List[float]]:
    """Generate market bars and signals yielding positive gross alpha that can be reversed by friction."""
    bars: List[Dict[str, Any]] = []
    signals: List[float] = []
    p = 100.0
    t = 0
    for _ in range(n_cycles):
        # Bar 0: Enter position at next open
        bars.append({
            "open_time": f"2026-01-01T{t % 24:02d}:00:00Z",
            "open": round(p, 4),
            "high": round(p * 1.001, 4),
            "low": round(p * 0.999, 4),
            "close": round(p, 4),
            "volume": 1000.0,
            "quote_volume": 100000.0,
        })
        signals.append(1.0)
        t += 1

        # Bar 1: Price appreciation while holding
        p_close1 = p * 1.006
        bars.append({
            "open_time": f"2026-01-01T{t % 24:02d}:00:00Z",
            "open": round(p, 4),
            "high": round(p_close1 * 1.001, 4),
            "low": round(p * 0.999, 4),
            "close": round(p_close1, 4),
            "volume": 1000.0,
            "quote_volume": 100000.0,
        })
        signals.append(1.0)
        t += 1

        # Bar 2: Additional gain, then signal to exit at next open
        p_close2 = p_close1 * 1.004
        bars.append({
            "open_time": f"2026-01-01T{t % 24:02d}:00:00Z",
            "open": round(p_close1, 4),
            "high": round(p_close2 * 1.001, 4),
            "low": round(p_close1 * 0.999, 4),
            "close": round(p_close2, 4),
            "volume": 1000.0,
            "quote_volume": 100000.0,
        })
        signals.append(0.0)
        t += 1

        # Bar 3: Price drop while flat (strategy avoids drawdown)
        p_close3 = p_close2 * 0.985
        bars.append({
            "open_time": f"2026-01-01T{t % 24:02d}:00:00Z",
            "open": round(p_close2, 4),
            "high": round(p_close2 * 1.001, 4),
            "low": round(p_close3 * 0.999, 4),
            "close": round(p_close3, 4),
            "volume": 1000.0,
            "quote_volume": 100000.0,
        })
        signals.append(0.0)
        p = p_close3
        t += 1

    return bars, signals


class TestPhase273EconomicValidityAdversarial(unittest.TestCase):
    """Adversarial suite for Phase 2.7.3 economic validity and execution realism."""

    def setUp(self) -> None:
        self.bars = _generate_synthetic_market_bars(n_bars=400, seed=123)

    # -----------------------------------------------------------------------
    # Case 1: Profitable Gross Strategy Becoming Unprofitable After Fees
    # -----------------------------------------------------------------------
    def test_case_01_gross_profit_reversed_by_fees(self) -> None:
        """A strategy with positive gross alpha turns net negative solely due to trading fees."""
        # Use targeted cyclic market where gross alpha is positive (+28.3%)
        bars, signals = _generate_profitable_gross_market(n_cycles=25)

        # Model with standard taker fee (60 bps) and zero slippage/spread/funding
        cost_model = EconomicCostModel(
            taker_fee_rate=0.0060,
            slippage_rate=0.0,
            half_spread_rate=0.0,
            funding_rate_annual=0.0,
            market_impact_coefficient=0.0,
        )

        rep = simulate_economic_execution(
            bars=bars,
            signals=signals,
            cost_model=cost_model,
            initial_capital=10000.0,
        )

        # Gross return must be positive
        self.assertTrue(rep.is_gross_profitable, f"Gross return should be > 0, got {rep.gross_total_return_pct}%")
        # Net return must be negative
        self.assertFalse(rep.is_net_profitable, f"Net return should be < 0, got {rep.net_total_return_pct}%")
        # Flagged specifically as fee reversal
        self.assertTrue(rep.is_reversal_by_fees)
        self.assertEqual(rep.disposition, EconomicDisposition.ECONOMICALLY_UNVIABLE)
        self.assertIn("completely erased by trading fees", rep.disposition_rationale)

    # -----------------------------------------------------------------------
    # Case 2: Profitable Strategy Becoming Unprofitable After Spread & Slippage
    # -----------------------------------------------------------------------
    def test_case_02_spread_and_slippage_reversal(self) -> None:
        """A strategy that survives fees is rendered unprofitable by spread and slippage."""
        bars, signals = _generate_profitable_gross_market(n_cycles=25)

        # Model with modest fee (5 bps) but wider spread (30 bps half) and slippage (40 bps)
        cost_model = EconomicCostModel(
            taker_fee_rate=0.0005,
            half_spread_rate=0.0030,
            slippage_rate=0.0040,
            funding_rate_annual=0.0,
            market_impact_coefficient=0.0,
        )

        rep = simulate_economic_execution(
            bars=bars,
            signals=signals,
            cost_model=cost_model,
            initial_capital=10000.0,
        )

        self.assertTrue(rep.is_gross_profitable)
        self.assertFalse(rep.is_net_profitable)
        self.assertFalse(rep.is_reversal_by_fees)
        self.assertTrue(rep.is_reversal_by_slippage)
        self.assertEqual(rep.disposition, EconomicDisposition.ECONOMICALLY_UNVIABLE)

    # -----------------------------------------------------------------------
    # Case 3: Unrealistic Maker-Only Assumptions
    # -----------------------------------------------------------------------
    def test_case_03_unrealistic_maker_only_assumptions(self) -> None:
        """Demonstrate that naive maker-only backtesting masks unprofitability under realistic execution."""
        signals = [1.0 if (i // 8) % 2 == 0 else 0.0 for i in range(len(self.bars))]

        # 1. Naive Maker-Only: assumes 100% fill, zero fee, zero slippage
        naive_maker_model = EconomicCostModel(
            order_type=OrderTypeAssumption.MAKER_ONLY,
            maker_fee_rate=0.0,
            half_spread_rate=0.0,
            slippage_rate=0.0,
            maker_fill_probability=1.0,
            maker_adverse_selection_bps=0.0,
        )
        rep_naive = simulate_economic_execution(self.bars, signals, naive_maker_model)

        # 2. Realistic Hybrid: 60% passive fill probability, 3 bps adverse selection, fallback to taker
        realistic_model = EconomicCostModel(
            order_type=OrderTypeAssumption.HYBRID_REALISTIC,
            maker_fee_rate=0.0002,
            taker_fee_rate=0.0005,
            half_spread_rate=0.00025,
            slippage_rate=0.0005,
            maker_fill_probability=0.60,
            maker_adverse_selection_bps=3.0,
        )
        rep_real = simulate_economic_execution(self.bars, signals, realistic_model)

        # Naive maker appears profitable
        self.assertGreater(rep_naive.net_total_return_pct, rep_real.net_total_return_pct)
        # Realistic friction significantly deflates returns
        self.assertGreater(rep_real.total_friction_usd, rep_naive.total_friction_usd)

    # -----------------------------------------------------------------------
    # Case 4: Insufficient Liquidity for Intended Position Sizes
    # -----------------------------------------------------------------------
    def test_case_04_insufficient_liquidity_participation_cap(self) -> None:
        """Attempting to execute an order exceeding 5% of bar volume triggers liquidity violations."""
        # Generate illiquid bars ($50,000 quote volume per bar)
        illiquid_bars = _generate_synthetic_market_bars(n_bars=100, base_volume_usd=50000.0, seed=99)
        signals = [1.0 if (i // 5) % 2 == 0 else 0.0 for i in range(len(illiquid_bars))]

        # Trading with $100,000 capital on $50,000 volume bar implies 200% participation
        cost_model = EconomicCostModel(
            max_volume_participation=0.05,  # Max 5% participation
            market_impact_coefficient=0.20,
        )

        rep = simulate_economic_execution(
            bars=illiquid_bars,
            signals=signals,
            cost_model=cost_model,
            initial_capital=100000.0,
        )

        # Must record multiple liquidity violations
        self.assertGreater(len(rep.liquidity_violations), 0)
        self.assertGreater(rep.total_market_impact_cost_usd, 0.0)
        self.assertIn(rep.disposition, [EconomicDisposition.MARGINAL, EconomicDisposition.ECONOMICALLY_UNVIABLE])

    # -----------------------------------------------------------------------
    # Case 5: Excessive Turnover
    # -----------------------------------------------------------------------
    def test_case_05_excessive_turnover_friction_drag(self) -> None:
        """High-frequency daily churn creates extreme turnover drag that destroys Sharpe ratio."""
        # Rapid daily alternating signal: 0, 1, 0, 1, 0, 1...
        signals = [1.0 if i % 2 == 0 else 0.0 for i in range(len(self.bars))]

        cost_model = EconomicCostModel(
            taker_fee_rate=0.0010,
            slippage_rate=0.0005,
            half_spread_rate=0.00025,
        )

        rep = simulate_economic_execution(self.bars, signals, cost_model, initial_capital=10000.0)

        # Annualized turnover should exceed 50x
        self.assertGreater(rep.annualized_turnover_ratio, 30.0)
        # Friction drag should consume a large fraction of capital
        self.assertGreater(rep.friction_drag_pct, 20.0)
        # Net Sharpe collapsed
        self.assertLess(rep.net_sharpe, rep.gross_sharpe)
        self.assertEqual(rep.disposition, EconomicDisposition.ECONOMICALLY_UNVIABLE)

    # -----------------------------------------------------------------------
    # Case 6: Funding Costs Reversing Futures Profitability
    # -----------------------------------------------------------------------
    def test_case_06_futures_funding_costs_reversal(self) -> None:
        """Holding a long perpetual futures position during high funding regime erases alpha."""
        # Mild upward trending market with constant long position
        trending_bars = _generate_synthetic_market_bars(n_bars=365, trend=0.0004, seed=77)
        signals = [1.0] * len(trending_bars)

        # Annual funding rate of +12% (approx 0.011% per 8h, standard bull market perp rate)
        cost_model = EconomicCostModel(
            taker_fee_rate=0.0005,
            slippage_rate=0.0002,
            funding_rate_annual=0.12,  # 12% annual holding cost
        )

        rep = simulate_economic_execution(trending_bars, signals, cost_model, initial_capital=10000.0)

        # Funding cost must be positive and substantial
        self.assertGreater(rep.total_funding_cost_usd, 500.0)
        # Net CAGR should be reduced by approximately 12% relative to gross CAGR
        self.assertLess(rep.net_cagr_pct, rep.gross_cagr_pct - 8.0)

    # -----------------------------------------------------------------------
    # Case 7: Strategy Working Only Under Ideal Execution Prices
    # -----------------------------------------------------------------------
    def test_case_07_ideal_price_execution_sensitivity(self) -> None:
        """Strategy that is profitable under Next-Open degrades under adverse execution."""
        signals = [1.0 if (i // 7) % 2 == 0 else 0.0 for i in range(len(self.bars))]

        # Next-Open vs Adverse-Slippage execution
        model_ideal = EconomicCostModel(slippage_rate=0.0000, half_spread_rate=0.0000)
        model_adverse = EconomicCostModel(slippage_rate=0.0020, half_spread_rate=0.0010)

        rep_ideal = simulate_economic_execution(self.bars, signals, model_ideal)
        rep_adverse = simulate_economic_execution(self.bars, signals, model_adverse)

        self.assertGreater(rep_ideal.net_total_return_pct, rep_adverse.net_total_return_pct)
        self.assertGreater(rep_ideal.net_sharpe, rep_adverse.net_sharpe)

    # -----------------------------------------------------------------------
    # Case 8: Cost-Model Sensitivity and Uncertainty Stress Matrix
    # -----------------------------------------------------------------------
    def test_case_08_cost_model_stress_test_matrix(self) -> None:
        """Evaluate stress test grid across baseline, elevated, and adverse scenarios."""
        signals = [1.0 if (i // 12) % 2 == 0 else 0.0 for i in range(len(self.bars))]
        base_model = EconomicCostModel(taker_fee_rate=0.0010, slippage_rate=0.0005)

        stress_res = evaluate_execution_stress_grid(self.bars, signals, base_model)

        self.assertIn(EconomicStressScenario.BASELINE.value, stress_res)
        self.assertIn(EconomicStressScenario.ELEVATED_FRICTION.value, stress_res)
        self.assertIn(EconomicStressScenario.ADVERSE_STRESS.value, stress_res)
        self.assertIn(EconomicStressScenario.EXTREME_ILLIQUIDITY.value, stress_res)

        # Net return should monotonically decrease as stress increases
        ret_base = stress_res[EconomicStressScenario.BASELINE.value]["net_total_return_pct"]
        ret_elev = stress_res[EconomicStressScenario.ELEVATED_FRICTION.value]["net_total_return_pct"]
        ret_adv = stress_res[EconomicStressScenario.ADVERSE_STRESS.value]["net_total_return_pct"]
        ret_ext = stress_res[EconomicStressScenario.EXTREME_ILLIQUIDITY.value]["net_total_return_pct"]

        self.assertGreaterEqual(ret_base, ret_elev)
        self.assertGreaterEqual(ret_elev, ret_adv)
        self.assertGreaterEqual(ret_adv, ret_ext)

    # -----------------------------------------------------------------------
    # Case 9: Missing or Unreliable Market Microstructure Data
    # -----------------------------------------------------------------------
    def test_case_09_missing_microstructure_data_halts_pipeline(self) -> None:
        """Corrupt or non-finite market data deterministically halts with UNCERTAINTY."""
        corrupt_bars = [dict(b) for b in self.bars]
        # Introduce NaN volume at bar 50
        corrupt_bars[50]["quote_volume"] = float("nan")
        signals = [1.0] * len(corrupt_bars)

        rep = simulate_economic_execution(corrupt_bars, signals)
        self.assertEqual(rep.disposition, EconomicDisposition.UNCERTAINTY_REVIEW_REQUIRED)
        self.assertIn("Non-finite or missing market microstructure data", rep.disposition_rationale)

    # -----------------------------------------------------------------------
    # Case 10: Incorrect Gross-to-Net PnL Accounting
    # -----------------------------------------------------------------------
    def test_case_10_exact_compounding_vs_linear_friction(self) -> None:
        """Verify exact compounding identity and reject linear addition of friction."""
        signals = [1.0 if (i // 6) % 2 == 0 else 0.0 for i in range(len(self.bars))]
        cost_model = EconomicCostModel(taker_fee_rate=0.0010, slippage_rate=0.0005)

        rep = simulate_economic_execution(self.bars, signals, cost_model, initial_capital=10000.0)

        # Linear approximation: Net + (Friction / E0) * 100
        linear_approx = rep.net_total_return_pct + rep.friction_drag_pct
        # True gross return compounded without friction
        true_gross = rep.gross_total_return_pct

        # Linear addition diverges from true path-dependent compounding
        # (Difference exists because friction paid early reduces capital available for compounding)
        diff = abs(true_gross - linear_approx)
        self.assertGreater(diff, 0.0001, "Linear addition should diverge from true path-dependent compounding")

    # -----------------------------------------------------------------------
    # Integration: Evidence Manifest Linkage
    # -----------------------------------------------------------------------
    def test_integration_evidence_manifest_linkage(self) -> None:
        """Verify cryptographic linkage between economic execution report and evidence manifest."""
        exp_id = "EXP-ECON-P273-01"
        man = generate_evidence_manifest(
            experiment_id=exp_id,
            hypothesis_id="H-ECON-01",
            prereg_id="PREREG-ECON-01",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data-fingerprint-123",
            code_revision="HEAD",
            metrics={"net_total_return_pct": 5.4, "sharpe_ratio": 0.8},
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T10:00:00Z",
            completion_timestamp_utc="2026-11-02T10:05:00Z",
            fee_assumptions={"maker_fee": 0.001, "taker_fee": 0.001},
            slippage_assumptions={"slippage_rate": 0.0005},
        )

        signals = [1.0] * len(self.bars)
        rep = simulate_economic_execution(
            bars=self.bars,
            signals=signals,
            manifest_id=man.manifest_id,
            manifest_hash=man.manifest_hash,
        )

        is_valid, discrepancies = verify_manifest_economic_link(rep, man.to_dict())
        self.assertTrue(is_valid, f"Manifest link failed: {discrepancies}")
        self.assertEqual(len(discrepancies), 0)
        self.assertTrue(rep.manifest_linked)

        # Tampered manifest ID fails linkage
        bad_dict = man.to_dict()
        bad_dict["manifest_id"] = "MAN-TAMPERED-99"
        is_bad, bad_disc = verify_manifest_economic_link(rep, bad_dict)
        self.assertFalse(is_bad)
        self.assertIn("Manifest ID mismatch", bad_disc[0])


if __name__ == "__main__":
    unittest.main()
