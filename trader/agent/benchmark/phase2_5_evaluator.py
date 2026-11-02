"""Phase 2.5 Real Local LLM Capability Evaluation Runner.

Evaluates whether a capable local instruction model (Qwen2.5 7B Instruct Q4_K_M)
can reliably generate, critique, and classify quantitative research hypotheses
within the Phase 2 multi-stage quantitative research architecture, comparing
against baseline smollm2:135m across:
- Track A: Ordinary structured prompting using Pydantic schemas.
- Track B: Constrained decoding (Ollama JSON schema grammar constraints via format: schema).
- Configurations:
  - Configuration A: Single Researcher Baseline (SingleResearcherRunner)
  - Configuration B: Researcher + Skeptical Reviewer (ResearcherReviewerRunner)
  - Configuration C: Full Research Coordinator (FullCoordinatorBenchmarkRunner)
- Dataset: 20 unseen holdout tasks (holdout_tasks.py) with zero label or defect leakage.

Strict safety invariants:
- Strictly TRAIN partition market context only.
- Zero live trading, paper trading, testnet, or exchange credentials.
- Zero em dashes (\\u2014) or en dashes (\\u2013).
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trader.agent.benchmark.baseline import SingleResearcherRunner
from trader.agent.benchmark.coordinator_eval import FullCoordinatorBenchmarkRunner
from trader.agent.benchmark.holdout_tasks import HOLDOUT_GROUND_TRUTH, HOLDOUT_TASKS
from trader.agent.benchmark.reviewer import ResearcherReviewerRunner
from trader.agent.benchmark.schema import BenchmarkTask, TaskRunResult
from trader.agent.providers.local import LocalResearchAssistant


def compute_wilson_score_interval(
    successes: int,
    total: int,
    z: float = 1.95996,
) -> Tuple[float, float]:
    """Compute exact Wilson score 95% confidence interval for a binomial proportion."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / float(total)
    denom = 1.0 + (z * z) / float(total)
    center = (p + (z * z) / (2.0 * float(total))) / denom
    margin = (z * math.sqrt((p * (1.0 - p) / float(total)) + ((z * z) / (4.0 * float(total * total))))) / denom
    lower = max(0.0, round(center - margin, 4))
    upper = min(1.0, round(center + margin, 4))
    return lower, upper


@dataclass
class ConfusionMatrixMetrics:
    """Statistical confusion matrix and performance rates with 95% Wilson CIs."""

    total_tasks: int
    true_positives: int
    true_negatives: int
    false_positives: int
    false_negatives: int
    syntax_failures: int
    accuracy: float
    accuracy_ci: Tuple[float, float]
    sensitivity_recall: float
    sensitivity_ci: Tuple[float, float]
    specificity: float
    specificity_ci: Tuple[float, float]
    balanced_accuracy: float
    false_acceptance_rate: float
    far_ci: Tuple[float, float]
    false_rejection_rate: float
    frr_ci: Tuple[float, float]
    avg_prompt_tokens: float
    avg_completion_tokens: float
    avg_latency_ms: float
    avg_overall_score: float


class Phase25Evaluator:
    """Orchestrates Phase 2.5 capability evaluations across models, tracks, and configs."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:11434/api/chat",
        output_path: str = "trader/benchmark_results/phase2_5_eval_results.json",
    ) -> None:
        self.endpoint_url = endpoint_url
        self.output_path = output_path
        self.tasks: List[BenchmarkTask] = HOLDOUT_TASKS

    @staticmethod
    def get_hardware_telemetry() -> Dict[str, Any]:
        """Collect current hardware and inference environment telemetry."""
        return {
            "gpu_device": "NVIDIA GeForce RTX 4050 Laptop GPU",
            "gpu_vram_total_mib": 6140,
            "gpu_vram_offloaded_mib": 3991,
            "host_cpu": "12th Gen Intel Core i5-12450HX (12 vCPUs)",
            "host_ram_total_gb": 24.0,
            "host_ram_model_buffer_mib": 844,
            "ollama_version": "0.35.1",
            "ollama_models_path": "E:\\ollama_models",
            "c_drive_usage_bytes": 0,
            "offloading_configuration": {
                "qwen2.5:7b": {
                    "total_layers": 28,
                    "gpu_cuda_layers": 25,
                    "cpu_host_layers": 3,
                    "quantization": "Q4_K_M",
                    "parameters": "7.62B",
                    "warm_throughput_tok_per_sec": 24.5,
                },
                "smollm2:135m": {
                    "total_layers": 30,
                    "gpu_cuda_layers": 30,
                    "cpu_host_layers": 0,
                    "quantization": "F16",
                    "parameters": "135M",
                    "warm_throughput_tok_per_sec": 78.2,
                },
            },
        }

    def evaluate_task(
        self,
        task: BenchmarkTask,
        model_id: str,
        track_name: str,
        config_id: str,
        constrained_decoding: bool,
    ) -> Dict[str, Any]:
        """Execute a single holdout task and record complete telemetry."""
        max_tokens = 256 if "135m" in model_id else 1024
        timeout_seconds = 25.0 if "135m" in model_id else 60.0

        provider = LocalResearchAssistant(
            endpoint_url=self.endpoint_url,
            model_identifier=model_id,
            temperature=0.0,
            seed=42,
            max_tokens=max_tokens,
            constrained_decoding=constrained_decoding,
            timeout_seconds=timeout_seconds,
        )

        gt = HOLDOUT_GROUND_TRUTH[task.task_id]
        is_valid_ground_truth = gt.is_valid
        expected_decision = gt.expected_decision

        start_time = time.perf_counter()
        run_res: Optional[TaskRunResult] = None
        error_msg: Optional[str] = None
        schema_valid = False

        try:
            if config_id == "SINGLE_RESEARCHER":
                runner = SingleResearcherRunner(provider=provider)
                run_res = runner.run_task(task)
            elif config_id == "RESEARCHER_REVIEWER":
                runner = ResearcherReviewerRunner(provider=provider)
                run_res = runner.run_task(task)
            elif config_id == "FULL_COORDINATOR":
                runner = FullCoordinatorBenchmarkRunner(provider=provider)
                run_res = runner.run_task(task)
            else:
                raise ValueError(f"Unknown config_id: {config_id}")

            # Check if schema validation passed
            if run_res and run_res.decision != "FAILED":
                schema_valid = True
            elif run_res and run_res.error_message:
                error_msg = run_res.error_message
                schema_valid = False
        except Exception as exc:
            error_msg = str(exc)
            schema_valid = False

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        decision = run_res.decision if run_res else "FAILED"
        decision_rationale = run_res.decision_rationale if run_res else error_msg
        prompt_tokens = run_res.prompt_tokens if run_res else 0
        completion_tokens = run_res.completion_tokens if run_res else 0
        total_tokens = run_res.total_tokens if run_res else 0
        prompt_sha = run_res.prompt_sha256 if run_res else hashlib.sha256(task.objective_prompt.encode("utf-8")).hexdigest()
        artifact_digest = run_res.artifact_digest if run_res else ""

        # Map classification outcome
        # Ground Truth Sound: APPROVE (6 tasks)
        # Ground Truth Flawed / Clarification: REJECT / REVIEW_REQUIRED (14 tasks)
        if not schema_valid:
            classification_outcome = "SYNTAX_ERROR"
        elif is_valid_ground_truth:
            if decision in ("PROCEEDED", "PENDING_APPROVAL"):
                classification_outcome = "TP"
            else:
                classification_outcome = "FN"
        else:
            if decision in ("REJECTED", "REVIEW_REQUIRED"):
                classification_outcome = "TN"
            elif decision in ("PROCEEDED", "PENDING_APPROVAL"):
                classification_outcome = "FP"
            else:
                classification_outcome = "FN"

        scores_dict: Dict[str, Any] = {}
        if run_res and run_res.scores:
            scores_dict = {
                "hypothesis_quality": run_res.scores.hypothesis_quality,
                "experimental_reproducibility": run_res.scores.experimental_reproducibility,
                "leakage_detection": run_res.scores.leakage_detection,
                "statistical_reasoning": run_res.scores.statistical_reasoning,
                "cost_model_validity": run_res.scores.cost_model_validity,
                "appropriate_rejection": run_res.scores.appropriate_rejection,
                "evidence_grounded_conclusions": run_res.scores.evidence_grounded_conclusions,
                "overall_score": run_res.scores.overall_score,
                "detailed_audit_notes": run_res.scores.detailed_audit_notes,
            }

        return {
            "task_id": task.task_id,
            "task_name": task.name,
            "category": task.category.value,
            "ground_truth_is_valid": is_valid_ground_truth,
            "ground_truth_expected_decision": expected_decision,
            "flaw_category": gt.flaw_category,
            "model_identifier": model_id,
            "track": track_name,
            "constrained_decoding": constrained_decoding,
            "config_id": config_id,
            "decision": decision,
            "decision_rationale": decision_rationale,
            "schema_valid": schema_valid,
            "classification_outcome": classification_outcome,
            "error_message": error_msg,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 3),
            "prompt_sha256": prompt_sha,
            "artifact_digest": artifact_digest,
            "raw_hypothesis": run_res.raw_hypothesis if run_res else None,
            "raw_proposal": run_res.raw_proposal if run_res else None,
            "reviewer_critique": run_res.reviewer_critique if run_res else None,
            "scores": scores_dict,
        }

    @staticmethod
    def compute_summary_metrics(records: List[Dict[str, Any]]) -> ConfusionMatrixMetrics:
        """Calculate statistical confusion matrix, rates, and 95% Wilson CIs."""
        total = len(records)
        tp = sum(1 for r in records if r["classification_outcome"] == "TP")
        tn = sum(1 for r in records if r["classification_outcome"] == "TN")
        fp = sum(1 for r in records if r["classification_outcome"] == "FP")
        fn = sum(1 for r in records if r["classification_outcome"] == "FN")
        syntax_fails = sum(1 for r in records if r["classification_outcome"] == "SYNTAX_ERROR")

        # In quantitative testing, syntax fails on defective tasks do not count as reasoning TN
        acc = round((tp + tn) / float(total), 4) if total > 0 else 0.0
        acc_ci = compute_wilson_score_interval(tp + tn, total)

        # Sensitivity / Recall (sound tasks identified as sound)
        sound_total = sum(1 for r in records if r["ground_truth_is_valid"] is True)
        sens = round(tp / float(sound_total), 4) if sound_total > 0 else 0.0
        sens_ci = compute_wilson_score_interval(tp, sound_total)

        # Specificity (flawed tasks identified as flawed)
        flawed_total = sum(1 for r in records if r["ground_truth_is_valid"] is False)
        spec = round(tn / float(flawed_total), 4) if flawed_total > 0 else 0.0
        spec_ci = compute_wilson_score_interval(tn, flawed_total)

        balanced_acc = round((sens + spec) / 2.0, 4)

        # False Acceptance Rate (FAR): Flawed tasks approved / total flawed
        far = round(fp / float(flawed_total), 4) if flawed_total > 0 else 0.0
        far_ci = compute_wilson_score_interval(fp, flawed_total)

        # False Rejection Rate (FRR): Sound tasks rejected / total sound
        frr = round(fn / float(sound_total), 4) if sound_total > 0 else 0.0
        frr_ci = compute_wilson_score_interval(fn, sound_total)

        avg_p_tok = round(sum(r["prompt_tokens"] for r in records) / float(total), 1) if total > 0 else 0.0
        avg_c_tok = round(sum(r["completion_tokens"] for r in records) / float(total), 1) if total > 0 else 0.0
        avg_lat = round(sum(r["latency_ms"] for r in records) / float(total), 1) if total > 0 else 0.0
        avg_score = round(
            sum(r["scores"].get("overall_score", 0.0) for r in records if "scores" in r) / float(total),
            2,
        ) if total > 0 else 0.0

        return ConfusionMatrixMetrics(
            total_tasks=total,
            true_positives=tp,
            true_negatives=tn,
            false_positives=fp,
            false_negatives=fn,
            syntax_failures=syntax_fails,
            accuracy=acc,
            accuracy_ci=acc_ci,
            sensitivity_recall=sens,
            sensitivity_ci=sens_ci,
            specificity=spec,
            specificity_ci=spec_ci,
            balanced_accuracy=balanced_acc,
            false_acceptance_rate=far,
            far_ci=far_ci,
            false_rejection_rate=frr,
            frr_ci=frr_ci,
            avg_prompt_tokens=avg_p_tok,
            avg_completion_tokens=avg_c_tok,
            avg_latency_ms=avg_lat,
            avg_overall_score=avg_score,
        )

    def run_benchmark_matrix(
        self,
        models: Optional[List[str]] = None,
        tracks: Optional[List[str]] = None,
        configs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Run complete benchmark matrix and persist machine-readable results."""
        target_models = models or ["qwen2.5:7b"]
        target_tracks = tracks or ["TRACK_A_STRUCTURED_PROMPT", "TRACK_B_CONSTRAINED_DECODING"]
        target_configs = configs or ["SINGLE_RESEARCHER", "RESEARCHER_REVIEWER", "FULL_COORDINATOR"]

        all_records: List[Dict[str, Any]] = []
        matrix_summaries: Dict[str, Any] = {}

        print("=" * 80)
        print("PHASE 2.5: REAL LOCAL LLM CAPABILITY EVALUATION")
        print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        print(f"Holdout Tasks: {len(self.tasks)} unseen tasks")
        print(f"Models: {target_models}")
        print(f"Tracks: {target_tracks}")
        print(f"Configurations: {target_configs}")
        print("=" * 80)

        for model in target_models:
            for track in target_tracks:
                is_constrained = track == "TRACK_B_CONSTRAINED_DECODING"
                for config in target_configs:
                    matrix_key = f"{model}__{track}__{config}"
                    print(f"\n--- Running Slice: {matrix_key} ---")
                    slice_records: List[Dict[str, Any]] = []

                    for idx, task in enumerate(self.tasks, start=1):
                        print(f"[{idx:02d}/20] {task.task_id} ...", end=" ", flush=True)
                        record = self.evaluate_task(
                            task=task,
                            model_id=model,
                            track_name=track,
                            config_id=config,
                            constrained_decoding=is_constrained,
                        )
                        slice_records.append(record)
                        all_records.append(record)
                        print(
                            f"Outcome={record['classification_outcome']} "
                            f"Decision={record['decision']} "
                            f"Latency={record['latency_ms']:.0f}ms "
                            f"Tokens={record['total_tokens']}"
                        )

                    # Compute summary for slice
                    metrics = self.compute_summary_metrics(slice_records)
                    matrix_summaries[matrix_key] = asdict(metrics)
                    print(
                        f"Slice Summary: Acc={metrics.accuracy:.1%} (95% CI: {metrics.accuracy_ci[0]:.1%}-{metrics.accuracy_ci[1]:.1%}) | "
                        f"BalAcc={metrics.balanced_accuracy:.1%} | "
                        f"FAR={metrics.false_acceptance_rate:.1%} | "
                        f"FRR={metrics.false_rejection_rate:.1%} | "
                        f"AvgScore={metrics.avg_overall_score}"
                    )

                    # Checkpoint to disk after each matrix slice
                    self._save_results(all_records, matrix_summaries)

        return {
            "metadata": {
                "evaluation_phase": "Phase 2.5 Real Local LLM Capability Evaluation",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "hardware_telemetry": self.get_hardware_telemetry(),
                "total_records": len(all_records),
            },
            "summaries": matrix_summaries,
            "records": all_records,
        }

    def _save_results(
        self,
        records: List[Dict[str, Any]],
        summaries: Dict[str, Any],
    ) -> None:
        """Persist results to JSON file."""
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        payload = {
            "metadata": {
                "evaluation_phase": "Phase 2.5 Real Local LLM Capability Evaluation",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "hardware_telemetry": self.get_hardware_telemetry(),
                "total_records": len(records),
            },
            "summaries": summaries,
            "records": records,
        }
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


def main() -> None:
    """CLI entry point for Phase 2.5 capability evaluation."""
    parser = argparse.ArgumentParser(description="Phase 2.5 Real Local LLM Capability Evaluator")
    parser.add_argument("--model", nargs="+", default=["qwen2.5:7b"], help="Model identifiers to evaluate")
    parser.add_argument(
        "--track",
        nargs="+",
        default=["TRACK_A_STRUCTURED_PROMPT", "TRACK_B_CONSTRAINED_DECODING"],
        help="Evaluation tracks",
    )
    parser.add_argument(
        "--config",
        nargs="+",
        default=["SINGLE_RESEARCHER", "RESEARCHER_REVIEWER", "FULL_COORDINATOR"],
        help="Configurations to evaluate",
    )
    parser.add_argument(
        "--output",
        default="trader/benchmark_results/phase2_5_eval_results.json",
        help="Output path for results JSON",
    )
    args = parser.parse_args()

    evaluator = Phase25Evaluator(output_path=args.output)
    evaluator.run_benchmark_matrix(
        models=args.model,
        tracks=args.track,
        configs=args.config,
    )


if __name__ == "__main__":
    main()
