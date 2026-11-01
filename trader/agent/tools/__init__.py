"""Deterministic quantitative tool bridges for the research framework."""

from trader.agent.tools.market_bridge import (
    MarketDataProvider,
    SealedPartitionAccessViolation,
    execute_strategy_simulation,
)

__all__ = [
    "MarketDataProvider",
    "SealedPartitionAccessViolation",
    "execute_strategy_simulation",
]
