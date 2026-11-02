"""Adversarial and mathematical verification suite for Phase 2.7.3.1.

Audits mathematical correctness, accounting consistency, execution semantics,
liquidity policy enforcement, and provenance for the Phase 2.7.3 economic model.

Typography standard: Strict ASCII hyphen invariant (0 em dashes, 0 en dashes).
"""

import math
import os
import sys
import unittest
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trader.agent.validation.economic_validity import (
    EconomicCostModel,
    EconomicDisposition,
    EconomicExecutionReport,
    ExecutionPricingModel,
    LiquidityPolicy,
    OrderTypeAssumption,
    simulate_economic_execution,
)


class TestPhase2731AdversarialEconomicAudit(unittest.TestCase):
    """Adversarial suite covering the 15 required audit areas for Phase 2.7.3.1."""

    # -----------------------------------------------------------------------
    # 1. Buy Entry and Sell Exit Spread Costs
    # -----------------------------------------------------------------------
    def test_01_buy_entry_and_sell_exit_spread_costs(self) -> None:
        """Verify half-spread is charged on both buy entry and sell exit legs."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-03T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        # Signal: Enter at Bar 1 Open, Exit at Bar 2 Open
        signals = [1.0, 0.0, 0.0]

        # 10 bps half-spread, zero fee, zero slippage, zero impact
        cost_model = EconomicCostModel(
            maker_fee_rate=0.0,
            taker_fee_rate=0.0,
            half_spread_rate=0.0010,
            slippage_rate=0.0,
            market_impact_coefficient=0.0,
            funding_rate_annual=0.0,
        )

        rep = simulate_economic_execution(bars, signals, cost_model, initial_capital=10000.0)

        # Hand calculation:
        # Bar 1 Open: Buy $10,000 -> Spread cost = 10,000 * 0.0010 = $10.00
        # Bar 2 Open: Sell $10,000 -> Spread cost = 10,000 * 0.0010 = $10.00
        # Total spread cost = $20.00
        self.assertEqual(rep.total_trades, 2)
        self.assertAlmostEqual(rep.total_spread_cost_usd, 20.0, places=2)
        self.assertAlmostEqual(rep.final_capital_gross, 10000.0, places=2)
        self.assertAlmostEqual(rep.final_capital_net, 9980.0, places=2)

    # -----------------------------------------------------------------------
    # 2. Short Entry and Buy-to-Cover Spread Costs
    # -----------------------------------------------------------------------
    def test_02_short_entry_and_cover_spread_costs(self) -> None:
        """Verify half-spread is charged on short entry and buy-to-cover exit."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-03T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        # Short signal: -1.0 at Bar 0 -> enters short at Bar 1; 0.0 at Bar 1 -> covers at Bar 2
        signals = [-1.0, 0.0, 0.0]

        cost_model = EconomicCostModel(
            maker_fee_rate=0.0,
            taker_fee_rate=0.0,
            half_spread_rate=0.0010,
            slippage_rate=0.0,
            market_impact_coefficient=0.0,
            funding_rate_annual=0.0,
        )

        rep = simulate_economic_execution(bars, signals, cost_model, initial_capital=10000.0)

        # Short Entry: Sell $10,000 -> Spread cost = $10.00
        # Buy to Cover: Buy $10,000 -> Spread cost = $10.00
        # Total spread cost = $20.00
        self.assertEqual(rep.total_trades, 2)
        self.assertAlmostEqual(rep.total_spread_cost_usd, 20.0, places=2)
        self.assertAlmostEqual(rep.final_capital_net, 9980.0, places=2)

    # -----------------------------------------------------------------------
    # 3. Long and Short Funding Under Positive Funding Rate
    # -----------------------------------------------------------------------
    def test_03_long_and_short_funding_positive_rate(self) -> None:
        """Verify positive funding: longs pay funding (cash decreases), shorts receive funding."""
        # 2 daily bars at flat price $100
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        # Annual funding rate: 10.9575% = 30 bps per day
        cost_model = EconomicCostModel(
            maker_fee_rate=0.0,
            taker_fee_rate=0.0,
            half_spread_rate=0.0,
            slippage_rate=0.0,
            market_impact_coefficient=0.0,
            funding_rate_annual=0.109575,
            funding_settlement_cadence="DAILY",
        )

        # Long position
        rep_long = simulate_economic_execution(bars, [1.0, 1.0], cost_model, initial_capital=10000.0, annual_factor=365.25)
        # Short position
        rep_short = simulate_economic_execution(bars, [-1.0, -1.0], cost_model, initial_capital=10000.0, annual_factor=365.25)

        # Long pays $3.00 (daily rate = 0.0003 * 10,000 = $3.00)
        self.assertAlmostEqual(rep_long.total_funding_cost_usd, 3.0, places=2)
        self.assertAlmostEqual(rep_long.final_capital_net, 9997.0, places=2)

        # Short receives $3.00
        self.assertAlmostEqual(rep_short.total_funding_cost_usd, -3.0, places=2)
        self.assertAlmostEqual(rep_short.final_capital_net, 10003.0, places=2)

    # -----------------------------------------------------------------------
    # 4. Long and Short Funding Under Negative Funding Rate
    # -----------------------------------------------------------------------
    def test_04_long_and_short_funding_negative_rate(self) -> None:
        """Verify negative funding: longs receive funding, shorts pay funding."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        # Annual funding rate: -10.9575%
        cost_model = EconomicCostModel(
            maker_fee_rate=0.0,
            taker_fee_rate=0.0,
            half_spread_rate=0.0,
            slippage_rate=0.0,
            market_impact_coefficient=0.0,
            funding_rate_annual=-0.109575,
            funding_settlement_cadence="DAILY",
        )

        rep_long = simulate_economic_execution(bars, [1.0, 1.0], cost_model, initial_capital=10000.0, annual_factor=365.25)
        rep_short = simulate_economic_execution(bars, [-1.0, -1.0], cost_model, initial_capital=10000.0, annual_factor=365.25)

        # Long receives $3.00
        self.assertAlmostEqual(rep_long.total_funding_cost_usd, -3.0, places=2)
        self.assertAlmostEqual(rep_long.final_capital_net, 10003.0, places=2)

        # Short pays $3.00
        self.assertAlmostEqual(rep_short.total_funding_cost_usd, 3.0, places=2)
        self.assertAlmostEqual(rep_short.final_capital_net, 9997.0, places=2)

    # -----------------------------------------------------------------------
    # 5. Position Opening and Closing Between Funding Settlements
    # -----------------------------------------------------------------------
    def test_05_position_opened_and_closed_between_funding_settlements(self) -> None:
        """Intraday position between funding timestamps (e.g. 09:00 to 15:00 UTC) incurs zero funding."""
        # Binance funding settles at 00:00, 08:00, 16:00 UTC
        bars_intraday = [
            {"open_time": "2026-01-01T09:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-01T10:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-01T15:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        # Open at 10:00, Close at 15:00 (never touches 00:00, 08:00, or 16:00 UTC)
        signals_intraday = [1.0, 0.0, 0.0]

        cost_model = EconomicCostModel(
            funding_rate_annual=0.109575,
            funding_settlement_cadence="DISCRETE_8H",
        )

        rep = simulate_economic_execution(bars_intraday, signals_intraday, cost_model, initial_capital=10000.0)
        self.assertEqual(rep.total_funding_cost_usd, 0.0, "Intraday trade between settlement hours must pay 0 funding")

    # -----------------------------------------------------------------------
    # 6. Partial Maker Fills (Hybrid Execution)
    # -----------------------------------------------------------------------
    def test_06_partial_maker_fills_hybrid_model(self) -> None:
        """Verify hybrid execution blends maker and taker rates according to fill probability."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        signals = [1.0, 1.0]

        cost_model = EconomicCostModel(
            order_type=OrderTypeAssumption.HYBRID_REALISTIC,
            maker_fee_rate=0.0002,  # 2 bps
            taker_fee_rate=0.0005,  # 5 bps
            maker_fill_probability=0.60,
            maker_adverse_selection_bps=3.0,
            half_spread_rate=0.00025,
            slippage_rate=0.0,
            market_impact_coefficient=0.0,
        )

        rep = simulate_economic_execution(bars, signals, cost_model, initial_capital=10000.0)

        # Expected blended fee rate: 0.60 * 0.0002 + 0.40 * 0.0005 = 0.00032 (3.2 bps)
        # Expected fee on $10,000 = $3.20
        self.assertAlmostEqual(rep.total_fee_cost_usd, 3.20, places=2)

    # -----------------------------------------------------------------------
    # 7. Zero Maker Fills and 100% Taker Fallback
    # -----------------------------------------------------------------------
    def test_07_zero_maker_fills_taker_fallback(self) -> None:
        """With maker_fill_probability=0.0, hybrid execution must fall back 100% to taker."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        signals = [1.0, 1.0]

        model_hybrid_zero = EconomicCostModel(
            order_type=OrderTypeAssumption.HYBRID_REALISTIC,
            maker_fee_rate=0.0002,
            taker_fee_rate=0.0005,
            maker_fill_probability=0.0,  # 0% maker fills
            half_spread_rate=0.0002,
        )
        model_pure_taker = EconomicCostModel(
            order_type=OrderTypeAssumption.TAKER_ONLY,
            taker_fee_rate=0.0005,
            half_spread_rate=0.0002,
        )

        rep_hybrid = simulate_economic_execution(bars, signals, model_hybrid_zero, initial_capital=10000.0)
        rep_taker = simulate_economic_execution(bars, signals, model_pure_taker, initial_capital=10000.0)

        # Fee must equal pure taker fee exactly
        self.assertAlmostEqual(rep_hybrid.total_fee_cost_usd, rep_taker.total_fee_cost_usd, places=4)
        self.assertAlmostEqual(rep_hybrid.total_fee_cost_usd, 5.0, places=2)

    # -----------------------------------------------------------------------
    # 8. Participation Above the Cap (Liquidity Policy Enforcement)
    # -----------------------------------------------------------------------
    def test_08_participation_above_cap_liquidity_enforcement(self) -> None:
        """Verify CAP_EXECUTABLE restricts executed order quantity to available volume allowance."""
        # Bar volume is $10,000. 5% cap allows max $500 trade.
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0, "quote_volume": 10000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0, "quote_volume": 10000.0},
        ]
        signals = [1.0, 1.0]

        # Policy: CAP_EXECUTABLE
        cost_model_cap = EconomicCostModel(
            max_volume_participation=0.05,  # 5% of $10,000 = $500 max
            liquidity_policy=LiquidityPolicy.CAP_EXECUTABLE,
            taker_fee_rate=0.0,
            half_spread_rate=0.0,
            slippage_rate=0.0,
        )

        rep = simulate_economic_execution(bars, signals, cost_model_cap, initial_capital=10000.0)

        # Proposed trade was $10,000. Capped trade is $500.
        # Unfilled notional should be $9,500.
        self.assertEqual(len(rep.liquidity_violations), 1)
        self.assertAlmostEqual(rep.total_turnover_usd, 500.0, places=2)
        self.assertAlmostEqual(rep.total_unfilled_notional_usd, 9500.0, places=2)
        self.assertAlmostEqual(rep.total_unfilled_qty, 95.0, places=2)

    # -----------------------------------------------------------------------
    # 9. Insufficient Cash and Exposure Limits
    # -----------------------------------------------------------------------
    def test_09_insufficient_cash_prevents_negative_cash_balance(self) -> None:
        """Verify spot cash constraint prevents cash from going negative."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        signals = [1.0, 1.0]

        cost_model = EconomicCostModel(
            allow_negative_cash=False,  # Strict spot account
            taker_fee_rate=0.010,  # 1% high fee
            half_spread_rate=0.005,  # 0.5% spread
            slippage_rate=0.005,  # 0.5% slippage
        )

        rep = simulate_economic_execution(bars, signals, cost_model, initial_capital=1000.0)

        # Cash cannot be negative under spot constraints
        self.assertGreaterEqual(rep.final_capital_net, 0.0)

    # -----------------------------------------------------------------------
    # 10. Price Gaps Between Signal and Next Open
    # -----------------------------------------------------------------------
    def test_10_overnight_price_gap_executes_at_next_open_not_signal_close(self) -> None:
        """Verify trade executes strictly at Next Open price, not the prior bar's Close."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            # Gap up: Open at 110 (+10% gap). Close flat at 110.
            {"open_time": "2026-01-02T00:00:00Z", "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        # Signal generated at Bar 0 Close
        signals = [1.0, 1.0]

        cost_model = EconomicCostModel(
            pricing_model=ExecutionPricingModel.NEXT_OPEN,
            taker_fee_rate=0.0,
            half_spread_rate=0.0,
            slippage_rate=0.0,
        )

        rep = simulate_economic_execution(bars, signals, cost_model, initial_capital=10000.0)

        # If executed at Bar 0 Close ($100), gross return would be +10%.
        # Executed at Bar 1 Open ($110), intraday gain is $110 to $110 = 0.0%.
        self.assertAlmostEqual(rep.gross_total_return_pct, 0.0, places=2)

    # -----------------------------------------------------------------------
    # 11. Missing or Stale Volume Microstructure Halt
    # -----------------------------------------------------------------------
    def test_11_missing_or_stale_volume_halts_pipeline(self) -> None:
        """Bars with missing/negative volume must deterministically halt with UNCERTAINTY."""
        corrupt_bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": -100.0, "quote_volume": -10000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0, "quote_volume": 10000.0},
        ]
        rep = simulate_economic_execution(corrupt_bars, [1.0, 1.0])
        self.assertEqual(rep.disposition, EconomicDisposition.UNCERTAINTY_REVIEW_REQUIRED)

    # -----------------------------------------------------------------------
    # 12. Zero-Volume Bars
    # -----------------------------------------------------------------------
    def test_12_zero_volume_bars_liquidity_handling(self) -> None:
        """Zero volume bars (market freeze/halt) trigger liquidity violation."""
        halt_bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0, "quote_volume": 0.0},
        ]
        # Attempt to trade into zero volume bar (signal generated at bar 0)
        signals = [1.0, 1.0]

        cost_model = EconomicCostModel(
            max_volume_participation=0.05,
            liquidity_policy=LiquidityPolicy.CAP_EXECUTABLE,
        )

        rep = simulate_economic_execution(halt_bars, signals, cost_model)
        # Because quote volume is 0, executable volume is 0, so order is 100% unfilled
        self.assertGreater(rep.total_unfilled_notional_usd, 0.0)

    # -----------------------------------------------------------------------
    # 13. Impact Dimensional Consistency (Almgren-Chriss Scaling)
    # -----------------------------------------------------------------------
    def test_13_almgren_chriss_impact_scaling_consistency(self) -> None:
        """Verify market impact scales with sqrt(Q/V) dimensionally."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 10000.0, "quote_volume": 1000000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 10000.0, "quote_volume": 1000000.0},
        ]
        signals = [1.0, 1.0]

        # Model with pure impact (zero fee, spread, slip)
        model = EconomicCostModel(
            taker_fee_rate=0.0,
            half_spread_rate=0.0,
            slippage_rate=0.0,
            market_impact_coefficient=0.20,
        )

        # Capital $10,000 (Q1) vs Capital $40,000 (Q2 = 4 * Q1)
        rep1 = simulate_economic_execution(bars, signals, model, initial_capital=10000.0)
        rep2 = simulate_economic_execution(bars, signals, model, initial_capital=40000.0)

        # Impact rate = eta * sigma * sqrt(Q/V).
        # For Q2 = 4*Q1, sqrt(4) = 2x impact rate.
        # Total impact dollar cost = Q * impact rate = 4 * 2 = 8x total dollar cost.
        ratio = rep2.total_market_impact_cost_usd / max(0.01, rep1.total_market_impact_cost_usd)
        self.assertAlmostEqual(ratio, 8.0, delta=0.2)

    # -----------------------------------------------------------------------
    # 14. Exact Gross-to-Net Multi-Step Reconciliation
    # -----------------------------------------------------------------------
    def test_14_exact_gross_to_net_multi_step_reconciliation(self) -> None:
        """Verify exact reconciliation between gross equity, net equity, and friction."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 105.0, "low": 100.0, "close": 105.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-03T00:00:00Z", "open": 105.0, "high": 105.0, "low": 105.0, "close": 105.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        # Enter at Bar 1, Exit at Bar 2
        signals = [1.0, 0.0, 0.0]

        cost_model = EconomicCostModel(
            taker_fee_rate=0.0010,  # 10 bps
            half_spread_rate=0.0,
            slippage_rate=0.0,
            market_impact_coefficient=0.0,
            funding_rate_annual=0.0,
        )

        rep = simulate_economic_execution(bars, signals, cost_model, initial_capital=10000.0)

        # Bar 1 Buy: $10,000 notional -> $10.00 fee
        # Bar 2 Sell: 100 tokens at $105 = $10,500 notional -> $10.50 fee
        # Total fee = $20.50
        self.assertAlmostEqual(rep.total_fee_cost_usd, 20.50, places=2)
        # Gross equity at end = $10,500 (bought at 100, sold at 105)
        self.assertAlmostEqual(rep.final_capital_gross, 10500.0, places=2)
        # Net equity at end = $10,500 - $20.50 = $10,479.50
        self.assertAlmostEqual(rep.final_capital_net, 10479.50, places=2)

    # -----------------------------------------------------------------------
    # 15. No Double Counting of Execution Costs
    # -----------------------------------------------------------------------
    def test_15_no_double_counting_of_execution_costs(self) -> None:
        """Verify total friction strictly equals the sum of its discrete components."""
        bars = [
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
            {"open_time": "2026-01-02T00:00:00Z", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "quote_volume": 100000.0},
        ]
        signals = [1.0, 1.0]

        cost_model = EconomicCostModel(
            taker_fee_rate=0.0010,
            half_spread_rate=0.00025,
            slippage_rate=0.0005,
            market_impact_coefficient=0.10,
            funding_rate_annual=0.05,
        )

        rep = simulate_economic_execution(bars, signals, cost_model, initial_capital=10000.0)

        sum_components = (
            rep.total_fee_cost_usd
            + rep.total_spread_cost_usd
            + rep.total_slippage_cost_usd
            + rep.total_market_impact_cost_usd
            + rep.total_funding_cost_usd
        )

        self.assertAlmostEqual(rep.total_friction_usd, sum_components, places=4)


if __name__ == "__main__":
    unittest.main()
