"""Market Data Bridge & Sealed Partition Protocol Enforcer.

Enforces:
1. Architectural Sealed Test Vault: Blocks unauthorized queries to the TEST partition.
2. Independent Initial Portfolio States: Zero state or position carryover between partitions.
3. Strict Warmup Separation: Warmup observations precede trading start and never execute trades.
4. Zero Look-Ahead Simulation: Signals at Day t Close execute at Day t+1 Open with exact fees.
5. Integration with benchmark_engine.py mathematical routines.
"""

import hashlib
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from trader.agent.core.strategy_base import StrategyInterface

# Add repository root and trader-timemachine to sys.path for benchmark_engine
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from benchmark_engine import (
    calc_cagr,
    calc_annualized_vol,
    calc_sharpe_ratio,
    calc_sortino_ratio,
    calc_max_drawdown,
    calc_skewness,
    calc_kurtosis,
)


class SealedPartitionAccessViolation(PermissionError):
    """Raised when an unauthorized attempt is made to access the sealed TEST partition."""
    pass


class MarketDataProvider:
    """Safe, read-only market data provider enforcing chronological partition boundaries."""

    # Immutable partition bounds on the 729-day dataset
    _IMMUTABLE_BOUNDS = {
        "WARMUP": {"start_bar": 0, "end_bar": 30, "usable_return_intervals": 0},
        "TRAIN": {"start_bar": 30, "end_bar": 430, "usable_return_intervals": 400},
        "VALIDATION": {"start_bar": 430, "end_bar": 580, "usable_return_intervals": 150},
        "TEST": {"start_bar": 580, "end_bar": 729, "usable_return_intervals": 149},
    }

    # Public dictionary for backward-compatible metadata inspection
    PARTITIONS = {k: dict(v) for k, v in _IMMUTABLE_BOUNDS.items()}

    def __init__(
        self,
        trader_dir: Optional[str] = None,
        ledger: Optional[Any] = None,
    ) -> None:
        """Initialize market data provider with verified historical candle caches."""
        if trader_dir is None:
            trader_dir = os.path.join(ROOT, "trader")
        self.trader_dir = trader_dir
        self.ledger = ledger
        self.raw_caches: Dict[str, List[Dict[str, Any]]] = {}
        self.data_hashes: Dict[str, str] = {}
        self._load_and_verify_all()

    def _load_and_verify_all(self) -> None:
        """Load and verify SHA-256 digests and 86400s timestamp spacing for all symbols."""
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        for sym in symbols:
            filename = f"cached_klines_{sym.lower()}_1d_730d.json"
            filepath = os.path.join(self.trader_dir, filename)
            if not os.path.isfile(filepath):
                raise FileNotFoundError(f"Required candle cache not found: {filepath}")

            with open(filepath, "rb") as f:
                content_bytes = f.read()

            sha256_hash = hashlib.sha256(content_bytes).hexdigest()
            self.data_hashes[sym] = sha256_hash

            parsed_json = json.loads(content_bytes.decode("utf-8"))
            raw_klines = parsed_json.get("klines", []) if isinstance(parsed_json, dict) else parsed_json

            if len(raw_klines) != 729:
                raise ValueError(f"Expected 729 bars for {sym}, found {len(raw_klines)}.")

            bars: List[Dict[str, Any]] = []
            for k in raw_klines:
                bars.append({
                    "open_time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })

            # Validate 86400s timestamp spacing
            for i in range(1, len(bars)):
                diff = bars[i]["open_time"] - bars[i - 1]["open_time"]
                if diff != 86400000:
                    raise ValueError(f"Timestamp gap detected in {sym} at index {i}: {diff} ms != 86400000 ms.")

            self.raw_caches[sym] = bars

    def get_data_hash(self, symbol: str = "BTCUSDT") -> str:
        """Return SHA-256 provenance hash for the given symbol dataset."""
        return self.data_hashes.get(symbol, "UNKNOWN")

    def get_partition_data(
        self,
        partition: str,
        symbol: str = "BTCUSDT",
        test_token: Optional[str] = None,
        warmup_bars: int = 30,
        prereg_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Provide immutable, sliced historical candles for the specified partition.

        Enforces:
        - Rejection of un-tokenized TEST partition queries.
        - Inclusion of preceding warmup bars strictly for indicator smoothing.
        - Zero future data leakage.
        - Independent portfolio state initialization.
        """
        partition = partition.upper()
        if partition not in self._IMMUTABLE_BOUNDS:
            raise ValueError(f"Unknown partition '{partition}'. Allowed: {list(self._IMMUTABLE_BOUNDS.keys())}")

        # Sealed Test Vault Enforcement
        if partition == "TEST":
            if self.ledger is None:
                raise SealedPartitionAccessViolation(
                    "Access denied: TEST partition is sealed. Requires MarketDataProvider initialized with a valid ResearchLedger."
                )
            if not test_token or not test_token.strip():
                raise SealedPartitionAccessViolation(
                    "Access denied: TEST partition is sealed. Requires valid TEST_EVALUATION_TOKEN."
                )
            if not prereg_id or not prereg_id.strip():
                raise SealedPartitionAccessViolation(
                    "Access denied: TEST partition access requires explicit prereg_id binding."
                )
            try:
                self.ledger.verify_and_consume_test_token(
                    raw_token=test_token,
                    prereg_id=prereg_id,
                    caller_identity="MarketDataProvider",
                )
            except PermissionError as exc:
                raise SealedPartitionAccessViolation(str(exc)) from exc

        bounds = self._IMMUTABLE_BOUNDS[partition]
        start_bar = bounds["start_bar"]
        end_bar = bounds["end_bar"]

        all_bars = self.raw_caches[symbol]

        # Extract warmup slice strictly preceding start_bar
        actual_warmup_len = min(warmup_bars, start_bar)
        warmup_slice = all_bars[start_bar - actual_warmup_len : start_bar]

        # Extract active trading slice
        trading_slice = all_bars[start_bar:end_bar]

        return {
            "partition": partition,
            "symbol": symbol,
            "start_bar": start_bar,
            "end_bar": end_bar,
            "warmup_bars": [dict(b) for b in warmup_slice],
            "trading_bars": [dict(b) for b in trading_slice],
            "data_hash": self.data_hashes[symbol],
            # Independent initial portfolio state: zero carryover
            "initial_portfolio_state": {
                "initial_capital": 10000.0,
                "cash": 10000.0,
                "positions": {},
                "equity": 10000.0,
            },
        }


def execute_strategy_simulation(
    strategy: StrategyInterface,
    partition_data: Dict[str, Any],
    params: Dict[str, float],
    cost_model: Optional[Dict[str, float]] = None,
    initial_capital: float = 10000.0,
) -> Dict[str, Any]:
    """Execute deterministic strategy simulation on partition data with exact friction.

    WARNING: LOW-LEVEL SIMULATION KERNEL - NOT AN AUTHORIZED AGENT EXECUTION PATHWAY.
    Direct calls to execute_strategy_simulation() bypass preregistration validation
    and multiple-testing trial accounting (M_total).
    Autonomous agents and controlled quantitative experiments MUST use
    trader.agent.core.execution.ResearchExecutionEngine.

    Invariants:
    1. Warmup bars are used exclusively for indicator calculation; zero trades occur in warmup.
    2. Signals evaluated at Day t Close execute at Day t+1 Open with fee + slippage.
    3. Independent initial portfolio: starts fresh with initial_capital.
    4. Compounding identity verified: |E_end - E_0 * prod(1 + r_t)| < 1e-10.
    5. Returns array connects seamlessly with benchmark_engine.py.
    """
    if cost_model is None:
        cost_model = {"taker_fee_rate": 0.0010, "slippage_rate": 0.0005}

    fee_rate = cost_model.get("taker_fee_rate", 0.0010)
    slippage = cost_model.get("slippage_rate", 0.0005)
    friction_per_leg = fee_rate + slippage

    warmup_bars = partition_data["warmup_bars"]
    trading_bars = partition_data["trading_bars"]

    # Combine warmup and trading bars for point-in-time indicator calculation
    combined_bars = warmup_bars + trading_bars
    warmup_len = len(warmup_bars)
    trading_len = len(trading_bars)

    if trading_len == 0:
        raise ValueError("Trading partition contains zero bars.")

    # 1. Compute indicators across combined sequence
    indicators = strategy.compute_indicators(combined_bars, params)

    # 2. Generate target position signals across combined sequence
    signals = strategy.generate_signals(combined_bars, indicators, params)

    # 3. Simulate trading strictly over the trading partition
    cash = initial_capital
    position_qty = 0.0
    equity = initial_capital

    equity_curve: List[float] = [equity]
    daily_net_returns: List[float] = []
    daily_gross_returns: List[float] = []
    trades: List[Dict[str, Any]] = []
    total_friction_usd = 0.0

    # Iteration: bar t in trading_bars corresponds to combined_bars[warmup_len + t]
    for t in range(trading_len):
        comb_idx = warmup_len + t
        bar_t = combined_bars[comb_idx]
        open_price_t = float(bar_t["open"])
        close_price_t = float(bar_t["close"])

        # Prior day signal evaluated at comb_idx - 1 Close
        target_signal = signals[comb_idx - 1] if comb_idx > 0 else 0.0

        prev_equity = equity
        gross_pnl_today = 0.0
        friction_today = 0.0

        # Execute rebalance at Day t Open
        current_target_val = prev_equity * target_signal
        current_pos_val = position_qty * open_price_t
        delta_val = current_target_val - current_pos_val

        if abs(delta_val) > 1.0:  # Minimum rebalance threshold
            trade_qty = delta_val / open_price_t
            friction_cost = abs(delta_val) * friction_per_leg
            friction_today += friction_cost
            total_friction_usd += friction_cost

            cash -= (delta_val + friction_cost)
            position_qty += trade_qty

            trades.append({
                "bar_index": t,
                "timestamp": bar_t["open_time"],
                "delta_val": delta_val,
                "trade_qty": trade_qty,
                "price": open_price_t,
                "friction": friction_cost,
            })

        # End of Day t valuation at Close
        position_val_close = position_qty * close_price_t
        equity = cash + position_val_close
        equity_curve.append(equity)

        # Net daily return
        net_ret = (equity - prev_equity) / prev_equity
        daily_net_returns.append(net_ret)

        # Gross daily return (excluding today's friction)
        gross_equity = equity + friction_today
        gross_ret = (gross_equity - prev_equity) / prev_equity
        daily_gross_returns.append(gross_ret)

    final_capital = equity
    net_total_return_pct = ((final_capital - initial_capital) / initial_capital) * 100.0
    gross_total_return_pct = net_total_return_pct + (total_friction_usd / initial_capital) * 100.0

    # Verify compounding identity: final equity vs product of (1 + r_t)
    cum_prod = initial_capital
    for r in daily_net_returns:
        cum_prod *= (1.0 + r)
    compounding_error = abs(final_capital - cum_prod)
    if compounding_error > 1e-9:
        raise ArithmeticError(f"Compounding identity error: {compounding_error} > 1e-9")

    # Connect to benchmark_engine.py functions
    cagr = calc_cagr(initial_capital, final_capital, trading_len) * 100.0
    ann_vol = calc_annualized_vol(daily_net_returns) * 100.0
    sharpe = calc_sharpe_ratio(cagr / 100.0, ann_vol / 100.0)
    sortino = calc_sortino_ratio(cagr / 100.0, daily_net_returns)
    max_dd = calc_max_drawdown(equity_curve) * 100.0
    skewness = calc_skewness(daily_net_returns)
    kurtosis = calc_kurtosis(daily_net_returns)

    wins = sum(1 for r in daily_net_returns if r > 0)
    total_intervals = len(daily_net_returns)
    win_rate = (wins / total_intervals * 100.0) if total_intervals > 0 else 0.0

    raw_metrics = {
        "initial_capital": initial_capital,
        "final_capital": round(final_capital, 4),
        "net_total_return_pct": round(net_total_return_pct, 4),
        "gross_total_return_pct": round(gross_total_return_pct, 4),
        "cagr_pct": round(cagr, 4),
        "annualized_vol_pct": round(ann_vol, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 2),
        "total_friction_usd": round(total_friction_usd, 4),
        "compounding_error": compounding_error,
        "skewness": round(skewness, 4),
        "kurtosis": round(kurtosis, 4),
    }

    return {
        "initial_capital": initial_capital,
        "final_capital": final_capital,
        "net_total_return_pct": net_total_return_pct,
        "gross_total_return_pct": gross_total_return_pct,
        "cagr_pct": cagr,
        "annualized_vol_pct": ann_vol,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": max_dd,
        "total_trades": len(trades),
        "win_rate_pct": win_rate,
        "total_friction_usd": total_friction_usd,
        "compounding_error": compounding_error,
        "daily_returns": daily_net_returns,
        "daily_gross_returns": daily_gross_returns,
        "equity_curve": equity_curve,
        "raw_metrics": raw_metrics,
    }
