"""Phase 2.3 & 2.4 Controlled Research Agent Benchmark package."""

from trader.agent.benchmark.repeated_runner import RepeatedBenchmarkRunner
from trader.agent.benchmark.schema import (
    BenchmarkCategory,
    BenchmarkSuiteSummary,
    BenchmarkTask,
    DimensionScores,
    TaskGroundTruth,
    TaskRunResult,
)
from trader.agent.benchmark.tasks import (
    get_benchmark_tasks,
    get_canonical_tasks,
    get_expanded_tasks,
)

__all__ = [
    "BenchmarkCategory",
    "BenchmarkSuiteSummary",
    "BenchmarkTask",
    "DimensionScores",
    "TaskGroundTruth",
    "TaskRunResult",
    "RepeatedBenchmarkRunner",
    "get_benchmark_tasks",
    "get_canonical_tasks",
    "get_expanded_tasks",
]
