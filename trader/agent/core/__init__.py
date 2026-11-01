"""Core quantitative research interfaces, strategy base classes, and execution governance."""

from trader.agent.core.strategy_base import StrategyInterface
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ControlledExperimentResult,
    ResearchExecutionEngine,
    ExecutionGovernanceError,
    PreregistrationMismatchError,
    DuplicateExperimentError,
    SimulationExecutionError,
    canonical_parameter_json,
)

__all__ = [
    "StrategyInterface",
    "SyntheticSMAStrategy",
    "ControlledExperimentRequest",
    "ControlledExperimentResult",
    "ResearchExecutionEngine",
    "ExecutionGovernanceError",
    "PreregistrationMismatchError",
    "DuplicateExperimentError",
    "SimulationExecutionError",
    "canonical_parameter_json",
]
