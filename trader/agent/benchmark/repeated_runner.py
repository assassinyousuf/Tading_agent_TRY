"""Repeated evaluation runner across temperatures and model stochasticity.

Evaluates configurations (Single Researcher, Researcher + Reviewer, Full Coordinator)
across multiple temperatures (0.0, 0.2, 0.5, 0.8) and repetitions.
Records dimensional scores, confusion matrices (TP, FP, TN, FN), rejection precision/recall,
false rejection rate, false acceptance rate, overall accuracy, balanced accuracy,
token consumption, latency, and cost-effectiveness without relying on LLM self-grading.
"""

from datetime import datetime, timezone
import json
import math
import os
import random
import time
from typing import Any, Callable, Dict, List, Optional

from trader.agent.benchmark.baseline import SingleResearcherRunner
from trader.agent.benchmark.coordinator_eval import FullCoordinatorBenchmarkRunner
from trader.agent.benchmark.reviewer import ResearcherReviewerRunner
from trader.agent.benchmark.schema import BenchmarkTask, TaskRunResult
from trader.agent.benchmark.tasks import get_benchmark_tasks
from trader.agent.providers.base import LLMProvider


class RepeatedBenchmarkRunner:
    """Orchestrates multi-temperature, multi-repetition benchmark runs."""

    def __init__(
        self,
        provider_factory: Callable[[float], LLMProvider],
        temperatures: Optional[List[float]] = None,
        repetitions_per_temp: int = 3,
        configurations: Optional[List[str]] = None,
        results_dir: Optional[str] = None,
        random_seed: int = 42,
    ) -> None:
        self.provider_factory = provider_factory
        self.temperatures = temperatures or [0.0, 0.2, 0.5, 0.8]
        self.repetitions_per_temp = repetitions_per_temp
        self.configurations = configurations or [
            "SINGLE_RESEARCHER",
            "RESEARCHER_REVIEWER",
            "FULL_COORDINATOR",
        ]
        self.random_seed = random_seed

        if results_dir is None:
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            results_dir = os.path.join(root_dir, "trader", "benchmark_results")
        self.results_dir = results_dir
        os.makedirs(self.results_dir, exist_ok=True)

    def run_repeated_evaluation(
        self,
        tasks: Optional[List[BenchmarkTask]] = None,
        expanded: bool = False,
    ) -> Dict[str, Any]:
        """Execute full matrix of temperatures, repetitions, and configurations."""
        task_list = list(tasks) if tasks is not None else get_benchmark_tasks(expanded=expanded)
        run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        raw_records: List[Dict[str, Any]] = []

        # Ground truth maps
        sound_task_ids = {t.task_id for t in task_list if t.ground_truth.is_valid}
        flawed_task_ids = {t.task_id for t in task_list if not t.ground_truth.is_valid}

        for temp in self.temperatures:
            for rep in range(1, self.repetitions_per_temp + 1):
                # Instantiate provider calibrated for this temperature
                provider = self.provider_factory(temp)

                for config_id in self.configurations:
                    # Deterministic seeded shuffle per repetition
                    rep_seed = self.random_seed + int(temp * 1000) + rep * 100
                    shuffled_tasks = list(task_list)
                    rng = random.Random(rep_seed)
                    rng.shuffle(shuffled_tasks)

                    if config_id == "SINGLE_RESEARCHER":
                        runner = SingleResearcherRunner(provider=provider)
                    elif config_id == "RESEARCHER_REVIEWER":
                        runner = ResearcherReviewerRunner(provider=provider)
                    elif config_id == "FULL_COORDINATOR":
                        runner = FullCoordinatorBenchmarkRunner(provider=provider)
                    else:
                        raise ValueError(f"Unknown config: {config_id}")

                    start_time = time.perf_counter()
                    task_results: List[TaskRunResult] = []
                    failed_requests = 0

                    for task in shuffled_tasks:
                        res = runner.run_task(task)
                        task_results.append(res)
                        if res.decision == "FAILED" or res.error_message:
                            failed_requests += 1

                    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                    # Compute repetition metrics
                    metrics = self._calculate_run_metrics(
                        config_id=config_id,
                        temperature=temp,
                        repetition=rep,
                        results=task_results,
                        sound_task_ids=sound_task_ids,
                        flawed_task_ids=flawed_task_ids,
                        elapsed_ms=elapsed_ms,
                        failed_requests=failed_requests,
                    )
                    raw_records.append(metrics)

        # Compute summary aggregations across repetitions
        aggregated = self._aggregate_results(raw_records)

        output_payload = {
            "timestamp": run_timestamp,
            "tasks_count": len(task_list),
            "sound_tasks_count": len(sound_task_ids),
            "flawed_tasks_count": len(flawed_task_ids),
            "is_expanded": expanded,
            "temperatures_tested": self.temperatures,
            "repetitions_per_temp": self.repetitions_per_temp,
            "configurations": self.configurations,
            "aggregated_by_temp_and_config": aggregated,
            "raw_run_records": raw_records,
        }

        # Persist summary
        out_filename = f"repeated_benchmark_{run_timestamp}.json"
        out_path = os.path.join(self.results_dir, out_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output_payload, f, indent=2)

        output_payload["persisted_path"] = out_path
        return output_payload

    @staticmethod
    def _calculate_run_metrics(
        config_id: str,
        temperature: float,
        repetition: int,
        results: List[TaskRunResult],
        sound_task_ids: set,
        flawed_task_ids: set,
        elapsed_ms: float,
        failed_requests: int,
    ) -> Dict[str, Any]:
        """Compute comprehensive metrics for a single repetition run using explicit formulas."""
        total_tasks = len(results)

        # Rejection decision defines halting or flagging before unmonitored backtesting
        is_rejected = lambda r: r.decision in {"REJECTED", "FAILED", "REVIEW_REQUIRED"}

        tp = sum(1 for r in results if r.task_id in flawed_task_ids and is_rejected(r))
        fp = sum(1 for r in results if r.task_id in sound_task_ids and is_rejected(r))
        fn = sum(1 for r in results if r.task_id in flawed_task_ids and not is_rejected(r))
        tn = sum(1 for r in results if r.task_id in sound_task_ids and not is_rejected(r))

        recall = (tp / (tp + fn) * 100.0) if (tp + fn) > 0 else 0.0
        precision = (tp / (tp + fp) * 100.0) if (tp + fp) > 0 else 0.0
        false_rejection_rate = (fp / (fp + tn) * 100.0) if (fp + tn) > 0 else 0.0
        false_acceptance_rate = (fn / (tp + fn) * 100.0) if (tp + fn) > 0 else 0.0
        overall_accuracy = ((tp + tn) / total_tasks * 100.0) if total_tasks > 0 else 0.0
        balanced_accuracy = 0.5 * (recall + (100.0 - false_rejection_rate))

        overall_scores = [r.scores.overall_score for r in results]
        mean_score = sum(overall_scores) / total_tasks if total_tasks > 0 else 0.0

        dim_scores = {
            "hypothesis_quality": sum(r.scores.hypothesis_quality for r in results) / total_tasks,
            "experimental_reproducibility": sum(r.scores.experimental_reproducibility for r in results) / total_tasks,
            "leakage_detection": sum(r.scores.leakage_detection for r in results) / total_tasks,
            "statistical_reasoning": sum(r.scores.statistical_reasoning for r in results) / total_tasks,
            "cost_model_validity": sum(r.scores.cost_model_validity for r in results) / total_tasks,
            "appropriate_rejection": sum(r.scores.appropriate_rejection for r in results) / total_tasks,
            "evidence_grounded_conclusions": sum(r.scores.evidence_grounded_conclusions for r in results) / total_tasks,
        }

        total_prompt_tok = sum(r.prompt_tokens for r in results)
        total_comp_tok = sum(r.completion_tokens for r in results)
        total_tokens = total_prompt_tok + total_comp_tok

        # Track per-task decision correctness
        task_outcomes = {}
        for r in results:
            correct = (r.task_id in flawed_task_ids and is_rejected(r)) or (r.task_id in sound_task_ids and not is_rejected(r))
            task_outcomes[r.task_id] = {
                "decision": r.decision,
                "is_correct": correct,
                "overall_score": r.scores.overall_score,
            }

        return {
            "config_id": config_id,
            "temperature": temperature,
            "repetition": repetition,
            "confusion_matrix": {
                "true_positives": tp,
                "false_positives": fp,
                "true_negatives": tn,
                "false_negatives": fn,
            },
            "metrics": {
                "mean_overall_score": round(mean_score, 2),
                "rejection_recall": round(recall, 2),
                "rejection_precision": round(precision, 2),
                "false_rejection_rate": round(false_rejection_rate, 2),
                "false_acceptance_rate": round(false_acceptance_rate, 2),
                "overall_accuracy": round(overall_accuracy, 2),
                "balanced_accuracy": round(balanced_accuracy, 2),
            },
            "total_tokens": total_tokens,
            "prompt_tokens": total_prompt_tok,
            "completion_tokens": total_comp_tok,
            "elapsed_ms": round(elapsed_ms, 2),
            "failed_requests": failed_requests,
            "dimension_scores": {k: round(v, 2) for k, v in dim_scores.items()},
            "task_outcomes": task_outcomes,
        }

    @staticmethod
    def _aggregate_results(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate repeated runs by (config_id, temperature) and compute means, standard deviations, and 95% CIs."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for rec in records:
            key = f"{rec['config_id']}__T_{rec['temperature']}"
            groups.setdefault(key, []).append(rec)

        aggregated: Dict[str, Any] = {}
        for key, rec_list in groups.items():
            n = len(rec_list)
            scores = [r["metrics"]["mean_overall_score"] for r in rec_list]
            recalls = [r["metrics"]["rejection_recall"] for r in rec_list]
            precisions = [r["metrics"]["rejection_precision"] for r in rec_list]
            frrs = [r["metrics"]["false_rejection_rate"] for r in rec_list]
            fars = [r["metrics"]["false_acceptance_rate"] for r in rec_list]
            accs = [r["metrics"]["overall_accuracy"] for r in rec_list]
            bal_accs = [r["metrics"]["balanced_accuracy"] for r in rec_list]
            tokens = [r["total_tokens"] for r in rec_list]
            latencies = [r["elapsed_ms"] for r in rec_list]

            def stats_bundle(vals: List[float]) -> Dict[str, float]:
                m = sum(vals) / len(vals)
                var = sum((x - m) ** 2 for x in vals) / len(vals) if len(vals) > 1 else 0.0
                std = math.sqrt(var)
                ci95 = (1.96 * std / math.sqrt(len(vals))) if len(vals) > 1 else 0.0
                return {
                    "mean": round(m, 2),
                    "std": round(std, 2),
                    "ci95_low": round(m - ci95, 2),
                    "ci95_high": round(m + ci95, 2),
                }

            # Sum total confusion matrix counts across repetitions
            total_tp = sum(r["confusion_matrix"]["true_positives"] for r in rec_list)
            total_fp = sum(r["confusion_matrix"]["false_positives"] for r in rec_list)
            total_tn = sum(r["confusion_matrix"]["true_negatives"] for r in rec_list)
            total_fn = sum(r["confusion_matrix"]["false_negatives"] for r in rec_list)

            # Per-task consistency (frequency of correct decision across reps)
            task_ids = list(rec_list[0]["task_outcomes"].keys())
            task_consistency = {}
            for tid in task_ids:
                correct_count = sum(1 for r in rec_list if r["task_outcomes"].get(tid, {}).get("is_correct", False))
                task_consistency[tid] = {
                    "correct_count": correct_count,
                    "accuracy_pct": round(correct_count / n * 100.0, 1),
                }

            mean_tok = sum(tokens) / n
            mean_sc = sum(scores) / n
            quality_per_1k_tokens = round((mean_sc / (mean_tok / 1000.0)), 3) if mean_tok > 0 else 0.0

            mean_lat_sec = (sum(latencies) / n) / 1000.0
            quality_per_sec = round(mean_sc / mean_lat_sec, 2) if mean_lat_sec > 0 else 0.0

            cfg_id, temp_str = key.split("__T_")
            aggregated[key] = {
                "config_id": cfg_id,
                "temperature": float(temp_str),
                "repetitions": n,
                "aggregate_confusion_matrix": {
                    "true_positives": total_tp,
                    "false_positives": total_fp,
                    "true_negatives": total_tn,
                    "false_negatives": total_fn,
                },
                "overall_score": stats_bundle(scores),
                "rejection_recall": stats_bundle(recalls),
                "rejection_precision": stats_bundle(precisions),
                "false_rejection_rate": stats_bundle(frrs),
                "false_acceptance_rate": stats_bundle(fars),
                "overall_accuracy": stats_bundle(accs),
                "balanced_accuracy": stats_bundle(bal_accs),
                "tokens": stats_bundle([float(t) for t in tokens]),
                "latency_ms": stats_bundle(latencies),
                "cost_effectiveness": {
                    "quality_score_per_1k_tokens": quality_per_1k_tokens,
                    "quality_score_per_second": quality_per_sec,
                },
                "task_consistency": task_consistency,
            }

        return aggregated
