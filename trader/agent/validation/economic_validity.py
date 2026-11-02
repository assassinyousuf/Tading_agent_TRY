"""Economic validity, transaction cost modeling, and execution realism package.

Phase 2.7.3: Determines whether historical strategy performance remains economically
viable after realistic transaction costs, liquidity constraints, execution assumptions,
and capital limitations.

Typography standard: Strict ASCII hyphen invariant (0 em dashes, 0 en dashes).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np


class ExecutionPricingModel(str, Enum):
    NEXT_OPEN = "NEXT_OPEN"
    PREV_CLOSE = "PREV_CLOSE"
    VWAP = "VWAP"
    MID_PRICE = "MID_PRICE"
    ADVERSE_TOUCH = "ADVERSE_TOUCH"


class OrderTypeAssumption(str, Enum):
    TAKER_ONLY = "TAKER_ONLY"
    MAKER_ONLY = "MAKER_ONLY"
    HYBRID_REALISTIC = "HYBRID_REALISTIC"


class LiquidityPolicy(str, Enum):
    WARN_PENALIZE = "WARN_PENALIZE"
    CAP_EXECUTABLE = "CAP_EXECUTABLE"
    REJECT_ORDER = "REJECT_ORDER"


class EconomicDisposition(str, Enum):
    ECONOMICALLY_VIABLE = "ECONOMICALLY_VIABLE"
    MARGINAL = "MARGINAL"
    ECONOMICALLY_UNVIABLE = "ECONOMICALLY_UNVIABLE"
    UNCERTAINTY_REVIEW_REQUIRED = "UNCERTAINTY_REVIEW_REQUIRED"


class EconomicStressScenario(str, Enum):
    BASELINE = "BASELINE"
    ELEVATED_FRICTION = "ELEVATED_FRICTION"
    ADVERSE_STRESS = "ADVERSE_STRESS"
    EXTREME_ILLIQUIDITY = "EXTREME_ILLIQUIDITY"


@dataclass
class EconomicCostModel:
    """Detailed transaction cost and market microstructure specification."""

    maker_fee_rate: float = 0.0010  # 10 bps default
    taker_fee_rate: float = 0.0010  # 10 bps default
    slippage_rate: float = 0.0005  # 5 bps baseline
    half_spread_rate: float = 0.00025  # 2.5 bps half spread (5 bps full spread)
    funding_rate_annual: float = 0.0  # Annualized funding rate (e.g. 0.1095 = 10.95% annual)
    market_impact_coefficient: float = 0.10  # Almgren-Chriss eta parameter
    max_volume_participation: float = 0.05  # Max 5% of bar volume
    fee_tier: str = "VIP0"
    order_type: OrderTypeAssumption = OrderTypeAssumption.TAKER_ONLY
    pricing_model: ExecutionPricingModel = ExecutionPricingModel.NEXT_OPEN
    maker_fill_probability: float = 0.60  # Only 60% of passive limit orders fill at touch
    maker_adverse_selection_bps: float = 3.0  # Adverse selection cost on passive fills
    liquidity_policy: LiquidityPolicy = LiquidityPolicy.WARN_PENALIZE
    allow_negative_cash: bool = True  # Set False for spot cash-constrained accounts
    discrete_funding_hours: Tuple[int, ...] = (0, 8, 16)  # Binance 8-hour settlements
    funding_settlement_cadence: str = "DAILY"  # "DAILY" (default) or "DISCRETE_8H"

    def validate(self) -> List[str]:
        """Validate parameter ranges and completeness."""
        issues: List[str] = []
        if self.maker_fee_rate < -0.0010 or self.maker_fee_rate > 0.05:
            issues.append(f"Invalid maker_fee_rate: {self.maker_fee_rate}")
        if self.taker_fee_rate < 0.0 or self.taker_fee_rate > 0.05:
            issues.append(f"Invalid taker_fee_rate: {self.taker_fee_rate}")
        if self.slippage_rate < 0.0 or self.slippage_rate > 0.10:
            issues.append(f"Invalid slippage_rate: {self.slippage_rate}")
        if self.half_spread_rate < 0.0:
            issues.append(f"Invalid half_spread_rate: {self.half_spread_rate}")
        if not (0.0 < self.max_volume_participation <= 1.0):
            issues.append(f"Invalid max_volume_participation: {self.max_volume_participation}")
        if not (0.0 <= self.maker_fill_probability <= 1.0):
            issues.append(f"Invalid maker_fill_probability: {self.maker_fill_probability}")
        return issues


@dataclass
class LiquidityViolation:
    bar_index: int
    timestamp: str
    order_qty: float
    order_value_usd: float
    bar_volume_usd: float
    participation_rate: float
    max_allowed_participation: float
    excess_qty: float


@dataclass
class EconomicExecutionReport:
    """Comprehensive economic validity, gross vs net performance, and execution audit."""

    initial_capital: float
    final_capital_gross: float
    final_capital_net: float
    gross_total_return_pct: float
    net_total_return_pct: float
    gross_cagr_pct: float
    net_cagr_pct: float
    gross_sharpe: float
    net_sharpe: float
    gross_sortino: float
    net_sortino: float
    gross_max_dd_pct: float
    net_max_dd_pct: float
    total_trades: int
    total_turnover_usd: float
    annualized_turnover_ratio: float
    total_fee_cost_usd: float
    total_spread_cost_usd: float
    total_slippage_cost_usd: float
    total_funding_cost_usd: float
    total_market_impact_cost_usd: float
    total_friction_usd: float
    friction_drag_pct: float  # Percentage of initial capital consumed by total friction
    net_expectancy_per_trade_bps: float
    breakeven_cost_per_trade_bps: float
    is_gross_profitable: bool
    is_net_profitable: bool
    is_reversal_by_fees: bool
    is_reversal_by_slippage: bool
    is_reversal_by_funding: bool
    liquidity_violations: List[LiquidityViolation]
    stress_test_matrix: Dict[str, Dict[str, float]]
    disposition: EconomicDisposition
    disposition_rationale: str
    breakeven_cost_per_side_bps: float = 0.0
    breakeven_cost_round_trip_bps: float = 0.0
    total_unfilled_qty: float = 0.0
    total_unfilled_notional_usd: float = 0.0
    manifest_id: Optional[str] = None
    manifest_hash: Optional[str] = None
    manifest_linked: bool = False
    audit_notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Deterministic Economic Simulation Kernel
# ---------------------------------------------------------------------------

def simulate_economic_execution(
    bars: Sequence[Dict[str, Any]],
    signals: Sequence[float],
    cost_model: Optional[EconomicCostModel] = None,
    initial_capital: float = 10000.0,
    annual_factor: float = 365.25,
    manifest_id: Optional[str] = None,
    manifest_hash: Optional[str] = None,
) -> EconomicExecutionReport:
    """Simulate strategy execution with realistic market microstructure frictions.

    Distinctly tracks:
      - Gross compounding: Capital compounding strictly from market price changes without friction.
      - Net compounding: Capital compounding after deducting fees, spread, slippage, funding, and impact.
      - Path-dependent cash and position accounting with exact compounding identities.
    """
    if cost_model is None:
        cost_model = EconomicCostModel()

    model_issues = cost_model.validate()
    if model_issues:
        return EconomicExecutionReport(
            initial_capital=initial_capital,
            final_capital_gross=initial_capital,
            final_capital_net=initial_capital,
            gross_total_return_pct=0.0,
            net_total_return_pct=0.0,
            gross_cagr_pct=0.0,
            net_cagr_pct=0.0,
            gross_sharpe=0.0,
            net_sharpe=0.0,
            gross_sortino=0.0,
            net_sortino=0.0,
            gross_max_dd_pct=0.0,
            net_max_dd_pct=0.0,
            total_trades=0,
            total_turnover_usd=0.0,
            annualized_turnover_ratio=0.0,
            total_fee_cost_usd=0.0,
            total_spread_cost_usd=0.0,
            total_slippage_cost_usd=0.0,
            total_funding_cost_usd=0.0,
            total_market_impact_cost_usd=0.0,
            total_friction_usd=0.0,
            friction_drag_pct=0.0,
            net_expectancy_per_trade_bps=0.0,
            breakeven_cost_per_trade_bps=0.0,
            is_gross_profitable=False,
            is_net_profitable=False,
            is_reversal_by_fees=False,
            is_reversal_by_slippage=False,
            is_reversal_by_funding=False,
            liquidity_violations=[],
            stress_test_matrix={},
            disposition=EconomicDisposition.UNCERTAINTY_REVIEW_REQUIRED,
            disposition_rationale=f"Invalid cost model specification: {'; '.join(model_issues)}",
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            manifest_linked=bool(manifest_id and manifest_hash),
        )

    n_bars = len(bars)
    if n_bars < 2 or len(signals) < n_bars:
        return EconomicExecutionReport(
            initial_capital=initial_capital,
            final_capital_gross=initial_capital,
            final_capital_net=initial_capital,
            gross_total_return_pct=0.0,
            net_total_return_pct=0.0,
            gross_cagr_pct=0.0,
            net_cagr_pct=0.0,
            gross_sharpe=0.0,
            net_sharpe=0.0,
            gross_sortino=0.0,
            net_sortino=0.0,
            gross_max_dd_pct=0.0,
            net_max_dd_pct=0.0,
            total_trades=0,
            total_turnover_usd=0.0,
            annualized_turnover_ratio=0.0,
            total_fee_cost_usd=0.0,
            total_spread_cost_usd=0.0,
            total_slippage_cost_usd=0.0,
            total_funding_cost_usd=0.0,
            total_market_impact_cost_usd=0.0,
            total_friction_usd=0.0,
            friction_drag_pct=0.0,
            net_expectancy_per_trade_bps=0.0,
            breakeven_cost_per_trade_bps=0.0,
            is_gross_profitable=False,
            is_net_profitable=False,
            is_reversal_by_fees=False,
            is_reversal_by_slippage=False,
            is_reversal_by_funding=False,
            liquidity_violations=[],
            stress_test_matrix={},
            disposition=EconomicDisposition.UNCERTAINTY_REVIEW_REQUIRED,
            disposition_rationale="Insufficient bar or signal observations.",
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            manifest_linked=bool(manifest_id and manifest_hash),
        )

    # Check for non-finite or corrupt market data
    for idx, b in enumerate(bars):
        for key in ["open", "close", "high", "low"]:
            val = float(b.get(key, float("nan")))
            if not math.isfinite(val) or val <= 0:
                return EconomicExecutionReport(
                    initial_capital=initial_capital,
                    final_capital_gross=initial_capital,
                    final_capital_net=initial_capital,
                    gross_total_return_pct=0.0,
                    net_total_return_pct=0.0,
                    gross_cagr_pct=0.0,
                    net_cagr_pct=0.0,
                    gross_sharpe=0.0,
                    net_sharpe=0.0,
                    gross_sortino=0.0,
                    net_sortino=0.0,
                    gross_max_dd_pct=0.0,
                    net_max_dd_pct=0.0,
                    total_trades=0,
                    total_turnover_usd=0.0,
                    annualized_turnover_ratio=0.0,
                    total_fee_cost_usd=0.0,
                    total_spread_cost_usd=0.0,
                    total_slippage_cost_usd=0.0,
                    total_funding_cost_usd=0.0,
                    total_market_impact_cost_usd=0.0,
                    total_friction_usd=0.0,
                    friction_drag_pct=0.0,
                    net_expectancy_per_trade_bps=0.0,
                    breakeven_cost_per_trade_bps=0.0,
                    is_gross_profitable=False,
                    is_net_profitable=False,
                    is_reversal_by_fees=False,
                    is_reversal_by_slippage=False,
                    is_reversal_by_funding=False,
                    liquidity_violations=[],
                    stress_test_matrix={},
                    disposition=EconomicDisposition.UNCERTAINTY_REVIEW_REQUIRED,
                    disposition_rationale=f"Non-finite or missing market microstructure data at bar {idx} ({key}={val}).",
                    manifest_id=manifest_id,
                    manifest_hash=manifest_hash,
                    manifest_linked=bool(manifest_id and manifest_hash),
                )
        for key in ["volume", "quote_volume"]:
            if key in b:
                val = float(b.get(key, float("nan")))
                if not math.isfinite(val) or val < 0:
                    return EconomicExecutionReport(
                        initial_capital=initial_capital,
                        final_capital_gross=initial_capital,
                        final_capital_net=initial_capital,
                        gross_total_return_pct=0.0,
                        net_total_return_pct=0.0,
                        gross_cagr_pct=0.0,
                        net_cagr_pct=0.0,
                        gross_sharpe=0.0,
                        net_sharpe=0.0,
                        gross_sortino=0.0,
                        net_sortino=0.0,
                        gross_max_dd_pct=0.0,
                        net_max_dd_pct=0.0,
                        total_trades=0,
                        total_turnover_usd=0.0,
                        annualized_turnover_ratio=0.0,
                        total_fee_cost_usd=0.0,
                        total_spread_cost_usd=0.0,
                        total_slippage_cost_usd=0.0,
                        total_funding_cost_usd=0.0,
                        total_market_impact_cost_usd=0.0,
                        total_friction_usd=0.0,
                        friction_drag_pct=0.0,
                        net_expectancy_per_trade_bps=0.0,
                        breakeven_cost_per_trade_bps=0.0,
                        is_gross_profitable=False,
                        is_net_profitable=False,
                        is_reversal_by_fees=False,
                        is_reversal_by_slippage=False,
                        is_reversal_by_funding=False,
                        liquidity_violations=[],
                        stress_test_matrix={},
                        disposition=EconomicDisposition.UNCERTAINTY_REVIEW_REQUIRED,
                        disposition_rationale=f"Non-finite or missing market microstructure data at bar {idx} ({key}={val}).",
                        manifest_id=manifest_id,
                        manifest_hash=manifest_hash,
                        manifest_linked=bool(manifest_id and manifest_hash),
                    )


    # State variables for Gross vs Net simulation
    gross_equity = initial_capital
    gross_pos_qty = 0.0
    gross_cash = initial_capital

    net_equity = initial_capital
    net_pos_qty = 0.0
    net_cash = initial_capital

    gross_equity_curve: List[float] = [gross_equity]
    net_equity_curve: List[float] = [net_equity]
    gross_daily_returns: List[float] = []
    net_daily_returns: List[float] = []

    total_fees = 0.0
    total_spread = 0.0
    total_slippage = 0.0
    total_funding = 0.0
    total_impact = 0.0
    total_turnover = 0.0
    total_trades = 0
    total_unfilled_notional = 0.0
    total_unfilled_qty = 0.0

    liquidity_violations: List[LiquidityViolation] = []
    daily_funding_rate = cost_model.funding_rate_annual / annual_factor

    # Rolling volatility estimate for market impact calculation
    closes = [float(b["close"]) for b in bars]
    ret_series = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    daily_vol = float(np.std(ret_series, ddof=1)) if len(ret_series) > 1 else 0.02

    for t in range(n_bars):
        bar_t = bars[t]
        open_t = float(bar_t["open"])
        close_t = float(bar_t["close"])
        vol_usd = float(bar_t.get("quote_volume", bar_t.get("volume", 1e6) * open_t))
        vol_usd = max(1000.0, vol_usd)

        # Signal from previous bar close: signals[t - 1]
        target_signal = signals[t - 1] if t > 0 else 0.0

        prev_gross_eq = gross_equity
        prev_net_eq = net_equity

        # Determine execution price based on pricing model
        if cost_model.pricing_model == ExecutionPricingModel.NEXT_OPEN:
            exec_price = open_t
        elif cost_model.pricing_model == ExecutionPricingModel.VWAP:
            high_t = float(bar_t["high"])
            low_t = float(bar_t["low"])
            exec_price = (open_t + high_t + low_t + close_t) / 4.0
        elif cost_model.pricing_model == ExecutionPricingModel.ADVERSE_TOUCH:
            half_spr = cost_model.half_spread_rate
            if target_signal > 0:
                exec_price = open_t * (1.0 + half_spr)
            elif target_signal < 0:
                exec_price = open_t * (1.0 - half_spr)
            elif net_pos_qty > 1e-8:
                exec_price = open_t * (1.0 - half_spr)
            elif net_pos_qty < -1e-8:
                exec_price = open_t * (1.0 + half_spr)
            else:
                exec_price = open_t
        else:
            exec_price = open_t

        # 1. Gross Execution (zero friction)
        target_gross_val = prev_gross_eq * target_signal
        current_gross_pos_val = gross_pos_qty * exec_price
        delta_gross_val = target_gross_val - current_gross_pos_val

        if abs(delta_gross_val) > 1.0:
            gross_trade_qty = delta_gross_val / exec_price
            gross_cash -= delta_gross_val
            gross_pos_qty += gross_trade_qty

        # 2. Net Execution (with all modeled frictions)
        target_net_val = prev_net_eq * target_signal
        current_net_pos_val = net_pos_qty * exec_price
        delta_net_val = target_net_val - current_net_pos_val
        trade_abs_val = abs(delta_net_val)

        # Cash constraint check if negative cash is disallowed (Spot mode)
        if not cost_model.allow_negative_cash and delta_net_val > 0:
            fee_est = cost_model.taker_fee_rate if cost_model.order_type == OrderTypeAssumption.TAKER_ONLY else cost_model.maker_fee_rate
            frict_est = fee_est + cost_model.half_spread_rate + cost_model.slippage_rate
            max_cash_allowed = max(0.0, net_cash / (1.0 + frict_est))
            if delta_net_val > max_cash_allowed:
                unfilled_cash = delta_net_val - max_cash_allowed
                total_unfilled_notional += unfilled_cash
                total_unfilled_qty += (unfilled_cash / exec_price)
                delta_net_val = max_cash_allowed
                trade_abs_val = abs(delta_net_val)

        if trade_abs_val > 1.0:
            # Liquidity constraint check
            participation = trade_abs_val / vol_usd if vol_usd > 0 else float("inf")
            if participation > cost_model.max_volume_participation:
                max_allowed_val = vol_usd * cost_model.max_volume_participation if vol_usd > 0 else 0.0
                excess_val = trade_abs_val - max_allowed_val
                liquidity_violations.append(
                    LiquidityViolation(
                        bar_index=t,
                        timestamp=str(bar_t.get("open_time", t)),
                        order_qty=trade_abs_val / exec_price,
                        order_value_usd=round(trade_abs_val, 2),
                        bar_volume_usd=round(vol_usd, 2),
                        participation_rate=round(participation, 4) if math.isfinite(participation) else 999.0,
                        max_allowed_participation=cost_model.max_volume_participation,
                        excess_qty=round(excess_val / exec_price, 4),
                    )
                )

                if cost_model.liquidity_policy == LiquidityPolicy.CAP_EXECUTABLE:
                    # Strictly cap executed quantity to available participation limit
                    executed_val = max_allowed_val
                    unfilled_val = trade_abs_val - executed_val
                    total_unfilled_notional += unfilled_val
                    total_unfilled_qty += (unfilled_val / exec_price)
                    delta_net_val = executed_val if delta_net_val > 0 else -executed_val
                    trade_abs_val = executed_val
                elif cost_model.liquidity_policy == LiquidityPolicy.REJECT_ORDER:
                    # Reject order entirely
                    total_unfilled_notional += trade_abs_val
                    total_unfilled_qty += (trade_abs_val / exec_price)
                    delta_net_val = 0.0
                    trade_abs_val = 0.0

        if trade_abs_val > 1.0:
            total_trades += 1
            total_turnover += trade_abs_val

            # A. Fee Cost
            if cost_model.order_type == OrderTypeAssumption.TAKER_ONLY:
                fee_rate = cost_model.taker_fee_rate
            elif cost_model.order_type == OrderTypeAssumption.MAKER_ONLY:
                fee_rate = cost_model.maker_fee_rate
            else:  # HYBRID_REALISTIC
                fee_rate = (cost_model.maker_fill_probability * cost_model.maker_fee_rate +
                            (1.0 - cost_model.maker_fill_probability) * cost_model.taker_fee_rate)
            fee_cost = trade_abs_val * fee_rate
            total_fees += fee_cost

            # B. Bid-Ask Spread Cost (half spread paid per crossing leg)
            if cost_model.order_type == OrderTypeAssumption.TAKER_ONLY:
                spread_cost = trade_abs_val * cost_model.half_spread_rate
            elif cost_model.order_type == OrderTypeAssumption.MAKER_ONLY:
                adverse_bps = cost_model.maker_adverse_selection_bps / 10000.0
                spread_cost = trade_abs_val * (adverse_bps - cost_model.half_spread_rate)
            else:
                spread_cost = trade_abs_val * (cost_model.half_spread_rate * (1.0 - cost_model.maker_fill_probability))
            spread_cost = max(0.0, spread_cost)
            total_spread += spread_cost

            # C. Slippage Cost
            slip_cost = trade_abs_val * cost_model.slippage_rate
            total_slippage += slip_cost

            # D. Almgren-Chriss Market Impact: I = eta * sigma * sqrt(Q / V)
            part_calc = (trade_abs_val / vol_usd) if vol_usd > 0 else 1.0
            impact_rate = cost_model.market_impact_coefficient * daily_vol * math.sqrt(min(1.0, part_calc))
            impact_cost = trade_abs_val * impact_rate
            total_impact += impact_cost

            total_friction_trade = fee_cost + spread_cost + slip_cost + impact_cost
            trade_qty = delta_net_val / exec_price

            net_cash -= (delta_net_val + total_friction_trade)
            net_pos_qty += trade_qty

        # 3. Futures Funding Cost (holding cost applied at funding settlement timestamps or daily)
        funding_cost_today = 0.0
        time_str = str(bar_t.get("open_time", ""))
        has_iso_time = "T" in time_str or " " in time_str
        is_funding_bar = False
        fund_rate_bar = 0.0

        if cost_model.funding_rate_annual != 0.0 and abs(net_pos_qty) > 1e-8:
            if cost_model.funding_settlement_cadence == "DISCRETE_8H":
                # Settle strictly on discrete funding hours (00:00, 08:00, 16:00 UTC)
                if any(f"T{h:02d}:" in time_str or f" {h:02d}:" in time_str for h in cost_model.discrete_funding_hours):
                    is_funding_bar = True
                    fund_rate_bar = cost_model.funding_rate_annual / (annual_factor * 3.0)
            else:
                # Standard daily settlement
                is_funding_bar = True
                fund_rate_bar = daily_funding_rate

        if is_funding_bar and abs(net_pos_qty) > 1e-8:
            # Longs pay positive funding; shorts receive positive funding
            pos_val_hold = net_pos_qty * close_t
            funding_cost_today = pos_val_hold * fund_rate_bar
            total_funding += funding_cost_today
            net_cash -= funding_cost_today

        # End of bar valuation at Close price
        gross_equity = gross_cash + (gross_pos_qty * close_t)
        net_equity = net_cash + (net_pos_qty * close_t)

        gross_equity_curve.append(gross_equity)
        net_equity_curve.append(net_equity)

        r_gross = (gross_equity - prev_gross_eq) / prev_gross_eq
        r_net = (net_equity - prev_net_eq) / prev_net_eq

        gross_daily_returns.append(r_gross)
        net_daily_returns.append(r_net)

    # Metrics computation
    gross_total_ret = ((gross_equity - initial_capital) / initial_capital) * 100.0
    net_total_ret = ((net_equity - initial_capital) / initial_capital) * 100.0

    gross_cagr = (((gross_equity / initial_capital) ** (annual_factor / float(n_bars))) - 1.0) * 100.0 if gross_equity > 0 else -100.0
    net_cagr = (((net_equity / initial_capital) ** (annual_factor / float(n_bars))) - 1.0) * 100.0 if net_equity > 0 else -100.0

    gross_vol = float(np.std(gross_daily_returns, ddof=1) * math.sqrt(annual_factor)) * 100.0 if len(gross_daily_returns) > 1 else 0.0
    net_vol = float(np.std(net_daily_returns, ddof=1) * math.sqrt(annual_factor)) * 100.0 if len(net_daily_returns) > 1 else 0.0

    gross_sharpe = (gross_cagr / gross_vol) if gross_vol > 1e-4 else 0.0
    net_sharpe = (net_cagr / net_vol) if net_vol > 1e-4 else 0.0

    # Downside deviation for Sortino
    gross_downside = [min(0.0, r) for r in gross_daily_returns]
    net_downside = [min(0.0, r) for r in net_daily_returns]
    gross_down_vol = float(np.std(gross_downside, ddof=1) * math.sqrt(annual_factor)) * 100.0 if len(gross_downside) > 1 else 1e-4
    net_down_vol = float(np.std(net_downside, ddof=1) * math.sqrt(annual_factor)) * 100.0 if len(net_downside) > 1 else 1e-4

    gross_sortino = (gross_cagr / gross_down_vol) if gross_down_vol > 1e-4 else 0.0
    net_sortino = (net_cagr / net_down_vol) if net_down_vol > 1e-4 else 0.0

    # Max Drawdowns
    def _calc_max_dd(eq_curve: List[float]) -> float:
        peak = eq_curve[0]
        max_dd = 0.0
        for val in eq_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd * 100.0

    gross_max_dd = _calc_max_dd(gross_equity_curve)
    net_max_dd = _calc_max_dd(net_equity_curve)

    total_friction = total_fees + total_spread + total_slippage + total_funding + total_impact
    friction_drag_pct = (total_friction / initial_capital) * 100.0

    years = float(n_bars) / annual_factor
    ann_turnover_ratio = (total_turnover / (2.0 * initial_capital)) / max(0.01, years)

    # Net expectancy per trade in basis points: Net PnL / Turnover
    net_expectancy_bps = ((net_equity - initial_capital) / max(1.0, total_turnover)) * 10000.0
    # Breakeven friction rate per trade in basis points: Gross PnL / Turnover
    breakeven_bps = ((gross_equity - initial_capital) / max(1.0, total_turnover)) * 10000.0

    is_gross_prof = gross_total_ret > 0.0
    is_net_prof = net_total_ret > 0.0

    # Reversal causes
    rev_fees = is_gross_prof and not is_net_prof and (total_fees >= (gross_equity - initial_capital))
    rev_slip = is_gross_prof and not is_net_prof and ((total_fees + total_slippage + total_spread) >= (gross_equity - initial_capital))
    rev_fund = is_gross_prof and not is_net_prof and (total_funding >= (gross_equity - initial_capital))

    # Evaluate Stress Test Grid across cost multipliers
    stress_matrix = evaluate_execution_stress_grid(
        bars=bars,
        signals=signals,
        base_cost_model=cost_model,
        initial_capital=initial_capital,
    )

    # Disposition resolution
    if not is_gross_prof:
        disposition = EconomicDisposition.ECONOMICALLY_UNVIABLE
        rationale = "Strategy fails to generate gross positive return even under zero transaction costs."
    elif not is_net_prof:
        disposition = EconomicDisposition.ECONOMICALLY_UNVIABLE
        if rev_fund:
            rationale = f"Gross alpha (+{gross_total_ret:.2f}%) is completely erased by futures funding costs (${total_funding:.2f})."
        elif rev_fees:
            rationale = f"Gross alpha (+{gross_total_ret:.2f}%) is completely erased by trading fees (${total_fees:.2f})."
        else:
            rationale = f"Gross alpha (+{gross_total_ret:.2f}%) is completely erased by combined transaction frictions (${total_friction:.2f})."
    elif len(liquidity_violations) > 0:
        disposition = EconomicDisposition.MARGINAL
        rationale = f"Strategy generates positive net return (+{net_total_ret:.2f}%), but encounters {len(liquidity_violations)} liquidity limit violations."
    elif net_sharpe < 0.5:
        disposition = EconomicDisposition.MARGINAL
        rationale = f"Strategy is net profitable (+{net_total_ret:.2f}%), but net Sharpe ({net_sharpe:.2f}) falls below institutional hurdle 0.5."
    else:
        disposition = EconomicDisposition.ECONOMICALLY_VIABLE
        rationale = f"Strategy is economically viable: net return +{net_total_ret:.2f}%, net Sharpe {net_sharpe:.2f}, breakeven hurdle {breakeven_bps:.1f} bps."

    manifest_linked = bool(manifest_id and manifest_hash)

    return EconomicExecutionReport(
        initial_capital=initial_capital,
        final_capital_gross=round(gross_equity, 4),
        final_capital_net=round(net_equity, 4),
        gross_total_return_pct=round(gross_total_ret, 4),
        net_total_return_pct=round(net_total_ret, 4),
        gross_cagr_pct=round(gross_cagr, 4),
        net_cagr_pct=round(net_cagr, 4),
        gross_sharpe=round(gross_sharpe, 4),
        net_sharpe=round(net_sharpe, 4),
        gross_sortino=round(gross_sortino, 4),
        net_sortino=round(net_sortino, 4),
        gross_max_dd_pct=round(gross_max_dd, 4),
        net_max_dd_pct=round(net_max_dd, 4),
        total_trades=total_trades,
        total_turnover_usd=round(total_turnover, 2),
        annualized_turnover_ratio=round(ann_turnover_ratio, 2),
        total_fee_cost_usd=round(total_fees, 4),
        total_spread_cost_usd=round(total_spread, 4),
        total_slippage_cost_usd=round(total_slippage, 4),
        total_funding_cost_usd=round(total_funding, 4),
        total_market_impact_cost_usd=round(total_impact, 4),
        total_friction_usd=round(total_friction, 4),
        friction_drag_pct=round(friction_drag_pct, 4),
        net_expectancy_per_trade_bps=round(net_expectancy_bps, 2),
        breakeven_cost_per_trade_bps=round(breakeven_bps, 2),
        is_gross_profitable=is_gross_prof,
        is_net_profitable=is_net_prof,
        is_reversal_by_fees=rev_fees,
        is_reversal_by_slippage=rev_slip,
        is_reversal_by_funding=rev_fund,
        liquidity_violations=liquidity_violations,
        stress_test_matrix=stress_matrix,
        disposition=disposition,
        disposition_rationale=rationale,
        breakeven_cost_per_side_bps=round(breakeven_bps, 2),
        breakeven_cost_round_trip_bps=round(breakeven_bps * 2.0, 2),
        total_unfilled_qty=round(total_unfilled_qty, 4),
        total_unfilled_notional_usd=round(total_unfilled_notional, 2),
        manifest_id=manifest_id,
        manifest_hash=manifest_hash,
        manifest_linked=manifest_linked,
        audit_notes=[
            f"Simulated {n_bars} bars with pricing model '{cost_model.pricing_model.value}'.",
            f"Gross vs Net divergence: Gross return={gross_total_ret:.2f}%, Net return={net_total_ret:.2f}%.",
            f"Friction breakdown: Fees=${total_fees:.2f}, Spread=${total_spread:.2f}, Slippage=${total_slippage:.2f}, Funding=${total_funding:.2f}, Impact=${total_impact:.2f}.",
        ],
    )


# ---------------------------------------------------------------------------
# 2. Execution Stress Test Grid
# ---------------------------------------------------------------------------

def evaluate_execution_stress_grid(
    bars: Sequence[Dict[str, Any]],
    signals: Sequence[float],
    base_cost_model: EconomicCostModel,
    initial_capital: float = 10000.0,
) -> Dict[str, Dict[str, float]]:
    """Evaluate strategy net performance under multi-scenario execution stress."""
    scenarios = {
        EconomicStressScenario.BASELINE.value: (1.0, 1.0, 1.0),
        EconomicStressScenario.ELEVATED_FRICTION.value: (1.5, 2.0, 1.5),
        EconomicStressScenario.ADVERSE_STRESS.value: (2.0, 3.0, 2.0),
        EconomicStressScenario.EXTREME_ILLIQUIDITY.value: (2.5, 5.0, 3.0),
    }

    results: Dict[str, Dict[str, float]] = {}

    for name, (fee_mult, slip_mult, impact_mult) in scenarios.items():
        stressed_model = EconomicCostModel(
            maker_fee_rate=base_cost_model.maker_fee_rate * fee_mult,
            taker_fee_rate=base_cost_model.taker_fee_rate * fee_mult,
            slippage_rate=base_cost_model.slippage_rate * slip_mult,
            half_spread_rate=base_cost_model.half_spread_rate * slip_mult,
            funding_rate_annual=base_cost_model.funding_rate_annual,
            market_impact_coefficient=base_cost_model.market_impact_coefficient * impact_mult,
            max_volume_participation=base_cost_model.max_volume_participation,
            fee_tier=base_cost_model.fee_tier,
            order_type=base_cost_model.order_type,
            pricing_model=base_cost_model.pricing_model,
        )

        n_bars = len(bars)
        net_cash = initial_capital
        net_pos = 0.0
        prev_eq = initial_capital
        daily_rets = []

        daily_fund = stressed_model.funding_rate_annual / 365.25

        for t in range(n_bars):
            open_p = float(bars[t]["open"])
            close_p = float(bars[t]["close"])
            vol_usd = max(1000.0, float(bars[t].get("quote_volume", bars[t].get("volume", 1e6) * open_p)))

            target_sig = signals[t - 1] if t > 0 else 0.0
            target_val = prev_eq * target_sig
            pos_val = net_pos * open_p
            delta_val = target_val - pos_val
            trade_abs = abs(delta_val)

            if trade_abs > 1.0:
                fee = trade_abs * stressed_model.taker_fee_rate
                slip = trade_abs * stressed_model.slippage_rate
                part = trade_abs / vol_usd
                impact = trade_abs * (stressed_model.market_impact_coefficient * 0.02 * math.sqrt(part))
                net_cash -= (delta_val + fee + slip + impact)
                net_pos += (delta_val / open_p)

            if stressed_model.funding_rate_annual != 0.0:
                fund = (net_pos * close_p) * daily_fund
                net_cash -= fund

            eq = net_cash + (net_pos * close_p)
            daily_rets.append((eq - prev_eq) / prev_eq)
            prev_eq = eq

        net_ret = ((prev_eq - initial_capital) / initial_capital) * 100.0
        ann_vol = float(np.std(daily_rets, ddof=1) * math.sqrt(365.25)) * 100.0 if len(daily_rets) > 1 else 1e-4
        cagr = (((prev_eq / initial_capital) ** (365.25 / float(n_bars))) - 1.0) * 100.0 if prev_eq > 0 else -100.0
        sr = (cagr / ann_vol) if ann_vol > 1e-4 else 0.0

        results[name] = {
            "net_total_return_pct": round(net_ret, 2),
            "net_sharpe": round(sr, 2),
            "final_capital": round(prev_eq, 2),
        }

    return results


# ---------------------------------------------------------------------------
# 3. Evidence Manifest Linkage & Verification
# ---------------------------------------------------------------------------

def verify_manifest_economic_link(
    economic_report: EconomicExecutionReport,
    evidence_manifest_dict: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Cryptographically verify that an economic audit links to its evidence manifest."""
    discrepancies: List[str] = []

    m_id = evidence_manifest_dict.get("manifest_id")
    if not m_id:
        discrepancies.append("Evidence manifest dictionary has missing 'manifest_id'.")
    elif economic_report.manifest_id and economic_report.manifest_id != m_id:
        discrepancies.append(f"Manifest ID mismatch: report has '{economic_report.manifest_id}', manifest has '{m_id}'.")

    # Verify partition boundary: manifest strictly requires TRAIN
    part = evidence_manifest_dict.get("partition_name")
    if part != "TRAIN":
        discrepancies.append(f"Partition violation: manifest partition '{part}' is not TRAIN.")

    # Verify manifest hash integrity
    m_hash = evidence_manifest_dict.get("manifest_hash")
    if not m_hash:
        discrepancies.append("Manifest dictionary is missing 'manifest_hash'.")
    elif economic_report.manifest_hash and economic_report.manifest_hash != m_hash:
        discrepancies.append(f"Manifest hash mismatch: report has '{economic_report.manifest_hash}', manifest has '{m_hash}'.")

    is_valid = len(discrepancies) == 0
    return is_valid, discrepancies
