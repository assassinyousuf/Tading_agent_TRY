"""Phase 2.3 & 2.4 Controlled Research Agent Benchmark package.

Zero em dashes (\\u2014) and zero en dashes (\\u2013) in this file.
"""

from trader.agent.benchmark.canonical_registry import (
    CanonicalTaskRecord,
    ExposureCategory,
    get_canonical_task_registry,
    get_holdout_eligible_tasks,
    lookup_canonical_task,
    verify_registry_consistency,
)
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
    "CanonicalTaskRecord",
    "ExposureCategory",
    "get_canonical_task_registry",
    "get_holdout_eligible_tasks",
    "lookup_canonical_task",
    "verify_registry_consistency",
]
