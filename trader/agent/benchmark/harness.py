"""Unified Benchmark Harness for Phase 2.3 Controlled Research Agent Benchmark.

Enforces:
1. Benchmark fairness: identical TRAIN data, prompts, model versions, and evaluation rules across configurations.
2. Randomized task presentation order using deterministic random seed.
3. Multi-configuration evaluation across:
   - Config A: Single Researcher
   - Config B: Researcher + Independent Reviewer
   - Config C: Full Research Coordinator
4. Non-destructive persistence of execution results, digests, and summary metrics.
"""

from datetime import datetime, timezone
import json
import os
import random
import time
from typing import Any, Dict, List, Optional

from trader.agent.benchmark.baseline import SingleResearcherRunner
from trader.agent.benchmark.coordinator_eval import FullCoordinatorBenchmarkRunner
from trader.agent.benchmark.mock_provider import BenchmarkMockLLMProvider
from trader.agent.benchmark.reviewer import ResearcherReviewerRunner
from trader.agent.benchmark.schema import (
    BenchmarkSuiteSummary,
    BenchmarkTask,
    TaskRunResult,
)
from trader.agent.benchmark.tasks import get_benchmark_tasks
from trader.agent.providers.base import LLMProvider


class BenchmarkHarness:
    """Unified benchmark execution, scoring, and persistence harness."""

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        results_dir: Optional[str] = None,
        random_seed: int = 42,
    ) -> None:
        self.provider = provider or BenchmarkMockLLMProvider()
        self.random_seed = random_seed

        if results_dir is None:
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            results_dir = os.path.join(root_dir, "trader", "benchmark_results")
        self.results_dir = results_dir
        os.makedirs(self.results_dir, exist_ok=True)

    def run_configuration(
        self,
        config_id: str,
        tasks: Optional[List[BenchmarkTask]] = None,
        randomize_order: bool = True,
        expanded: bool = False,
    ) -> BenchmarkSuiteSummary:
        """Run all benchmark tasks for a designated configuration with fairness controls."""
        task_list = list(tasks) if tasks is not None else get_benchmark_tasks(expanded=expanded)

        if randomize_order:
            rng = random.Random(self.random_seed)
            rng.shuffle(task_list)

        # Initialize runner based on config_id
        if config_id == "SINGLE_RESEARCHER":
            runner = SingleResearcherRunner(provider=self.provider)
        elif config_id == "RESEARCHER_REVIEWER":
            runner = ResearcherReviewerRunner(provider=self.provider)
        elif config_id == "FULL_COORDINATOR":
            runner = FullCoordinatorBenchmarkRunner(provider=self.provider)
        else:
            raise ValueError(f"Unknown configuration ID: '{config_id}'")

        results: List[TaskRunResult] = []
        for task in task_list:
            res = runner.run_task(task)
            results.append(res)

        summary = self._compute_summary(config_id=config_id, results=results)
        self._persist_summary(summary)
        return summary

    def run_comparative_benchmark(
        self,
        configs: Optional[List[str]] = None,
        tasks: Optional[List[BenchmarkTask]] = None,
        expanded: bool = False,
    ) -> Dict[str, BenchmarkSuiteSummary]:
        """Execute all designated configurations on identical tasks and produce comparative results."""
        target_configs = configs or ["SINGLE_RESEARCHER", "RESEARCHER_REVIEWER", "FULL_COORDINATOR"]
        summaries: Dict[str, BenchmarkSuiteSummary] = {}

        for cfg in target_configs:
            summary = self.run_configuration(config_id=cfg, tasks=tasks, expanded=expanded)
            summaries[cfg] = summary

        return summaries

    def _compute_summary(
        self,
        config_id: str,
        results: List[TaskRunResult],
    ) -> BenchmarkSuiteSummary:
        """Compute aggregate summary metrics, precision, recall, and dimension averages."""
        total_tasks = len(results)
        if total_tasks == 0:
            raise ValueError("Cannot summarize empty benchmark results list.")

        overall_scores = [r.scores.overall_score for r in results]
        mean_overall = round(sum(overall_scores) / total_tasks, 2)

        dimensions = [
            "hypothesis_quality",
            "experimental_reproducibility",
            "leakage_detection",
            "statistical_reasoning",
            "cost_model_validity",
            "appropriate_rejection",
            "evidence_grounded_conclusions",
        ]
        dim_means = {}
        for dim in dimensions:
            dim_vals = [getattr(r.scores, dim) for r in results]
            dim_means[dim] = round(sum(dim_vals) / total_tasks, 2)

        # Identify sound (valid) tasks vs flawed tasks based on task_id tag
        sound_task_ids = {r.task_id for r in results if "VALID" in r.task_id}
        flawed_task_ids = {r.task_id for r in results if "VALID" not in r.task_id}

        is_rejected = lambda r: r.decision in {"REJECTED", "FAILED", "REVIEW_REQUIRED"}
        true_positives = sum(1 for r in results if r.task_id in flawed_task_ids and is_rejected(r))
        false_positives = sum(1 for r in results if r.task_id in sound_task_ids and is_rejected(r))
        false_negatives = sum(1 for r in results if r.task_id in flawed_task_ids and not is_rejected(r))

        precision = (
            round((true_positives / (true_positives + false_positives)) * 100.0, 2)
            if (true_positives + false_positives) > 0
            else 0.0
        )
        recall = (
            round((true_positives / (true_positives + false_negatives)) * 100.0, 2)
            if (true_positives + false_negatives) > 0
            else 0.0
        )

        total_toks = sum(r.total_tokens for r in results)
        mean_lat = round(sum(r.latency_ms for r in results) / total_tasks, 2)

        return BenchmarkSuiteSummary(
            config_id=config_id,
            total_tasks=total_tasks,
            mean_overall_score=mean_overall,
            dimension_means=dim_means,
            rejection_precision=precision,
            rejection_recall=recall,
            total_tokens=total_toks,
            mean_latency_ms=mean_lat,
            task_results=results,
        )

    def _persist_summary(self, summary: BenchmarkSuiteSummary) -> str:
        """Persist benchmark execution results to non-destructive timestamped JSON."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"benchmark_{summary.config_id}_{timestamp}.json"
        path = os.path.join(self.results_dir, filename)

        with open(path, "w", encoding="utf-8") as f:
            f.write(summary.model_dump_json(indent=2))

        return path

