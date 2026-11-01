"""Abstract Strategy Interface for Offline Research.

Defines the minimal standard protocol for research strategies.
Enforces:
1. Strict separation of indicator warmup from active trading bars.
2. Zero look-ahead signal generation evaluated at Day t Close for Day t+1 Open execution.
3. Stateless execution across evaluation partitions.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class StrategyInterface(ABC):
    """Minimal abstract interface for quantitative research strategies."""

    def __init__(self, name: str, version: str = "1.0.0") -> None:
        self.name = name
        self.version = version

    @property
    @abstractmethod
    def warmup_bars_required(self) -> int:
        """Minimum historical bars required to prime rolling indicators."""
        pass

    @abstractmethod
    def compute_indicators(
        self, bars: List[Dict[str, Any]], params: Dict[str, float]
    ) -> Dict[str, List[float]]:
        """Compute point-in-time indicators across continuous historical bars.

        Args:
            bars: List of chronological candle dictionaries with keys:
                  'open_time', 'open', 'high', 'low', 'close', 'volume'.
            params: Validated parameter dictionary.

        Returns:
            Dictionary mapping indicator names to parallel float arrays.
        """
        pass

    @abstractmethod
    def generate_signals(
        self,
        bars: List[Dict[str, Any]],
        indicators: Dict[str, List[float]],
        params: Dict[str, float],
    ) -> List[float]:
        """Generate target position signals at bar Close.

        Signal convention:
            +1.0 = Long target position
             0.0 = Neutral / Cash
            -1.0 = Short target position (if supported by market)

        Timing invariant:
            Signal generated at bar t Close is filled at bar t+1 Open.
        """
        pass
