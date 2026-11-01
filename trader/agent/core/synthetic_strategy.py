"""Synthetic SMA Crossover Strategy for Pipeline Demonstration.

Demonstrates adherence to StrategyInterface without modifying legacy strategy code.
- Fast SMA / Slow SMA trend following.
- Strictly offline, deterministic, zero lookahead.
- Evaluates signals at Day t Close; executions occur at Day t+1 Open.
"""

from typing import Any, Dict, List
from trader.agent.core.strategy_base import StrategyInterface


class SyntheticSMAStrategy(StrategyInterface):
    """Demonstration SMA trend following strategy."""

    def __init__(self, name: str = "SyntheticSMAStrategy", version: str = "1.0.0") -> None:
        super().__init__(name=name, version=version)

    @property
    def warmup_bars_required(self) -> int:
        """Conservative default warmup requirement (30 bars)."""
        return 30

    def compute_indicators(
        self, bars: List[Dict[str, Any]], params: Dict[str, float]
    ) -> Dict[str, List[float]]:
        """Compute rolling Simple Moving Averages strictly over historical closes."""
        fast_period = int(params.get("fast_period", 10))
        slow_period = int(params.get("slow_period", 30))

        if fast_period <= 0 or slow_period <= 0:
            raise ValueError(f"Periods must be positive: fast={fast_period}, slow={slow_period}")
        if fast_period >= slow_period:
            raise ValueError(f"fast_period ({fast_period}) must be strictly less than slow_period ({slow_period})")

        closes = [float(b["close"]) for b in bars]
        n = len(closes)

        sma_fast = [float("nan")] * n
        sma_slow = [float("nan")] * n

        # Point-in-time rolling SMA calculation
        for i in range(n):
            if i >= fast_period - 1:
                sma_fast[i] = sum(closes[i - fast_period + 1 : i + 1]) / fast_period
            if i >= slow_period - 1:
                sma_slow[i] = sum(closes[i - slow_period + 1 : i + 1]) / slow_period

        return {"sma_fast": sma_fast, "sma_slow": sma_slow}

    def generate_signals(
        self,
        bars: List[Dict[str, Any]],
        indicators: Dict[str, List[float]],
        params: Dict[str, float],
    ) -> List[float]:
        """Generate long or cash position signals at bar Close.

        Signal rule:
            signal = 1.0 if sma_fast > sma_slow else 0.0
        """
        sma_fast = indicators.get("sma_fast", [])
        sma_slow = indicators.get("sma_slow", [])
        n = len(bars)

        signals = [0.0] * n
        for i in range(n):
            fast_val = sma_fast[i]
            slow_val = sma_slow[i]

            # In warmup period or invalid indicator value
            if fast_val != fast_val or slow_val != slow_val:
                signals[i] = 0.0
                continue

            if fast_val > slow_val:
                signals[i] = 1.0
            else:
                signals[i] = 0.0

        return signals
