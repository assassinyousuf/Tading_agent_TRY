"""Phase 2.5.1 Reviewer Calibration and Coordinator Gate Evaluation Runner.

Evaluates:
1. Reviewer calibration: Distinguishing genuine methodological defects from preferences and standard literature designs.
2. Coordinator gating: Enforcement of decision semantics across RECOMMEND_APPROVAL, RECOMMEND_REVISION, RECOMMEND_REJECTION, REVIEW_REQUIRED, INSUFFICIENT_EVIDENCE.
3. Expanded 30-task holdout dataset (TASK-21 through TASK-50) with 10 sound tasks, 17 flawed tasks, and 3 clarification tasks.
4. Real revision workflow: Researcher correction attempts and reviewer re-evaluation across up to max_revisions=2.
5. Exact confusion matrix metrics under user-specified formulas:
   - Sensitivity: TP / (TP + FN)
   - Specificity: TN / (TN + FP)
   - False Acceptance Rate (FAR): FN / (TP + FN)
   - False Rejection Rate (FRR): FP / (FP + TN)
   - Balanced Accuracy: (Sensitivity + Specificity) / 2
   - Raw TP, FP, TN, and FN counts beside every metric.
6. Separation of outcomes: review passed, review failed, revision requested, ticket created, approval granted, execution authorized.

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
class Phase251Metrics:
    """Statistical confusion matrix and performance rates with exact raw counts and 95% Wilson CIs."""

    total_tasks: int
    true_positives: int  # Flawed/clarification tasks correctly rejected/halted
    true_negatives: int  # Sound tasks correctly approved/proceeded
    false_positives: int  # Sound tasks falsely rejected/halted (False Rejection)
    false_negatives: int  # Flawed tasks falsely approved/proceeded (False Acceptance)
    syntax_failures: int
    accuracy: float
    accuracy_ci: Tuple[float, float]
    sensitivity: float  # TP / (TP + FN)
    sensitivity_ci: Tuple[float, float]
    specificity: float  # TN / (TN + FP)
    specificity_ci: Tuple[float, float]
    balanced_accuracy: float  # (Sensitivity + Specificity) / 2
    false_acceptance_rate: float  # FN / (TP + FN)
    far_ci: Tuple[float, float]
    false_rejection_rate: float  # FP / (FP + TN)
    frr_ci: Tuple[float, float]
    review_passed_count: int
    review_failed_count: int
    revision_requested_count: int
    investigation_halted_count: int
    approval_tickets_created: int
    approval_granted_count: int
    execution_authorized_count: int
    avg_prompt_tokens: float
    avg_completion_tokens: float
    avg_latency_ms: float
    avg_overall_score: float


class Phase251Evaluator:
    """Orchestrates Phase 2.5.1 reviewer calibration and gate evaluation."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:11434/api/chat",
        results_dir: str = "trader/benchmark_results",
    ) -> None:
        self.endpoint_url = endpoint_url
        self.results_dir = results_dir
        os.makedirs(results_dir, exist_ok=True)

    def evaluate_task(
        self,
        task: BenchmarkTask,
        model_id: str,
        track_name: str,
        config_id: str,
        constrained_decoding: bool = False,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Execute a single holdout task and record rigorous telemetry and outcome separation."""
        gt = HOLDOUT_GROUND_TRUTH[task.task_id]
        is_valid_ground_truth = gt.is_valid
        expected_decision = gt.expected_decision

        provider = LocalResearchAssistant(
            endpoint_url=self.endpoint_url,
            model_identifier=model_id,
            temperature=temperature,
            timeout_seconds=90.0,
            max_tokens=2048,
            top_p=0.9,
            seed=42,
            constrained_decoding=constrained_decoding,
        )

        start_time = time.perf_counter()
        run_res: Optional[TaskRunResult] = None
        error_msg: Optional[str] = None
        schema_valid = False

        try:
            if config_id == "SINGLE_RESEARCHER":
                runner = SingleResearcherRunner(provider=provider)
                run_res = runner.run_task(task)
            elif config_id == "RESEARCHER_REVIEWER":
                runner = ResearcherReviewerRunner(provider=provider, max_revisions=2)
                run_res = runner.run_task(task)
            elif config_id == "FULL_COORDINATOR":
                runner = FullCoordinatorBenchmarkRunner(provider=provider)
                run_res = runner.run_task(task)
            else:
                raise ValueError(f"Unknown config_id: {config_id}")

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

        # Outcome Separation Analysis
        raw_critique = run_res.reviewer_critique if run_res else None
        overall_rec = raw_critique.get("overall_recommendation") if raw_critique else None

        review_passed = overall_rec == "RECOMMEND_APPROVAL"
        review_failed = overall_rec == "RECOMMEND_REJECTION"
        revision_requested = overall_rec == "RECOMMEND_REVISION" or decision == "REVISION_EXHAUSTED"
        investigation_halted = overall_rec in ("REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE") or decision == "REVIEW_REQUIRED"

        # Ticket creation occurs strictly if decision reached PENDING_APPROVAL or PROCEEDED
        approval_ticket_created = decision in ("PENDING_APPROVAL", "PROCEEDED")
        # In automated benchmarking, no live human has granted approval or execution
        approval_granted = False
        execution_authorized = False

        # Confusion Matrix Classification Mapping:
        # Threat / Positive Condition = Flawed / Clarification task (must be caught/rejected)
        # Genuine / Negative Condition = Sound / Valid task (should be approved)
        if not schema_valid:
            classification_outcome = "SYNTAX_ERROR"
        elif not is_valid_ground_truth:
            # Flawed / Clarification task
            if decision in ("REJECTED", "REVIEW_REQUIRED", "REVISION_EXHAUSTED"):
                classification_outcome = "TP"  # Correctly rejected or halted
            elif decision in ("PROCEEDED", "PENDING_APPROVAL"):
                classification_outcome = "FN"  # Falsely accepted (False Acceptance)
            else:
                classification_outcome = "TP"  # Halted safely
        else:
            # Sound task
            if decision in ("PROCEEDED", "PENDING_APPROVAL"):
                classification_outcome = "TN"  # Correctly approved (True Negative)
            elif decision in ("REJECTED", "REVIEW_REQUIRED", "REVISION_EXHAUSTED"):
                classification_outcome = "FP"  # Falsely rejected (False Rejection)
            else:
                classification_outcome = "FP"

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
            "category": task.category.value if hasattr(task.category, "value") else str(task.category),
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
            "review_passed": review_passed,
            "review_failed": review_failed,
            "revision_requested": revision_requested,
            "investigation_halted": investigation_halted,
            "approval_ticket_created": approval_ticket_created,
            "approval_granted": approval_granted,
            "execution_authorized": execution_authorized,
            "error_message": error_msg,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 3),
            "prompt_sha256": prompt_sha,
            "artifact_digest": artifact_digest,
            "raw_hypothesis": run_res.raw_hypothesis if run_res else None,
            "raw_proposal": run_res.raw_proposal if run_res else None,
            "reviewer_critique": raw_critique,
            "scores": scores_dict,
        }

    @staticmethod
    def compute_summary_metrics(records: List[Dict[str, Any]]) -> Phase251Metrics:
        """Calculate statistical confusion matrix, rates, and 95% Wilson CIs under exact formulas."""
        total = len(records)
        tp = sum(1 for r in records if r["classification_outcome"] == "TP")
        tn = sum(1 for r in records if r["classification_outcome"] == "TN")
        fp = sum(1 for r in records if r["classification_outcome"] == "FP")
        fn = sum(1 for r in records if r["classification_outcome"] == "FN")
        syntax_fails = sum(1 for r in records if r["classification_outcome"] == "SYNTAX_ERROR")

        acc = round((tp + tn) / float(total), 4) if total > 0 else 0.0
        acc_ci = compute_wilson_score_interval(tp + tn, total)

        # Sensitivity (True Positive Rate): TP / (TP + FN)
        pos_total = tp + fn
        sens = round(tp / float(pos_total), 4) if pos_total > 0 else 0.0
        sens_ci = compute_wilson_score_interval(tp, pos_total)

        # Specificity (True Negative Rate): TN / (TN + FP)
        neg_total = tn + fp
        spec = round(tn / float(neg_total), 4) if neg_total > 0 else 0.0
        spec_ci = compute_wilson_score_interval(tn, neg_total)

        # Balanced Accuracy: (Sensitivity + Specificity) / 2
        balanced_acc = round((sens + spec) / 2.0, 4)

        # False Acceptance Rate (FAR): FN / (TP + FN)
        far = round(fn / float(pos_total), 4) if pos_total > 0 else 0.0
        far_ci = compute_wilson_score_interval(fn, pos_total)

        # False Rejection Rate (FRR): FP / (FP + TN)
        frr = round(fp / float(neg_total), 4) if neg_total > 0 else 0.0
        frr_ci = compute_wilson_score_interval(fp, neg_total)

        # Outcome Counts
        rev_pass = sum(1 for r in records if r.get("review_passed"))
        rev_fail = sum(1 for r in records if r.get("review_failed"))
        rev_req = sum(1 for r in records if r.get("revision_requested"))
        inv_halt = sum(1 for r in records if r.get("investigation_halted"))
        tix_created = sum(1 for r in records if r.get("approval_ticket_created"))
        app_granted = sum(1 for r in records if r.get("approval_granted"))
        exec_auth = sum(1 for r in records if r.get("execution_authorized"))

        avg_p_tok = round(sum(r["prompt_tokens"] for r in records) / float(total), 1) if total > 0 else 0.0
        avg_c_tok = round(sum(r["completion_tokens"] for r in records) / float(total), 1) if total > 0 else 0.0
        avg_lat = round(sum(r["latency_ms"] for r in records) / float(total), 1) if total > 0 else 0.0
        avg_score = round(
            sum(r["scores"].get("overall_score", 0.0) for r in records if "scores" in r) / float(total),
            2,
        ) if total > 0 else 0.0

        return Phase251Metrics(
            total_tasks=total,
            true_positives=tp,
            true_negatives=tn,
            false_positives=fp,
            false_negatives=fn,
            syntax_failures=syntax_fails,
            accuracy=acc,
            accuracy_ci=acc_ci,
            sensitivity=sens,
            sensitivity_ci=sens_ci,
            specificity=spec,
            specificity_ci=spec_ci,
            balanced_accuracy=balanced_acc,
            false_acceptance_rate=far,
            far_ci=far_ci,
            false_rejection_rate=frr,
            frr_ci=frr_ci,
            review_passed_count=rev_pass,
            review_failed_count=rev_fail,
            revision_requested_count=rev_req,
            investigation_halted_count=inv_halt,
            approval_tickets_created=tix_created,
            approval_granted_count=app_granted,
            execution_authorized_count=exec_auth,
            avg_prompt_tokens=avg_p_tok,
            avg_completion_tokens=avg_c_tok,
            avg_latency_ms=avg_lat,
            avg_overall_score=avg_score,
        )

    def run_calibration_evaluation(
        self,
        model_id: str = "qwen2.5:7b",
        limit_tasks: Optional[int] = None,
        track_filter: str = "all",
        config_filter: str = "all",
    ) -> Dict[str, Any]:
        """Execute full calibration evaluation for model across tracks and configurations."""
        tasks = HOLDOUT_TASKS[:limit_tasks] if limit_tasks else HOLDOUT_TASKS
        print(f"=== Starting Phase 2.5.1 Calibration Evaluation for {model_id} on {len(tasks)} Holdout Tasks ===")

        all_matrix_configs = [
            ("Track A", False, "SINGLE_RESEARCHER", "A"),
            ("Track A", False, "RESEARCHER_REVIEWER", "B"),
            ("Track A", False, "FULL_COORDINATOR", "C"),
            ("Track B", True, "SINGLE_RESEARCHER", "A"),
            ("Track B", True, "RESEARCHER_REVIEWER", "B"),
            ("Track B", True, "FULL_COORDINATOR", "C"),
        ]

        matrix_configs = []
        for track_name, constrained_dec, config_id, cfg_letter in all_matrix_configs:
            track_letter = "A" if "Track A" in track_name else "B"
            if track_filter != "all" and track_filter != track_letter:
                continue
            if config_filter != "all" and config_filter != cfg_letter:
                continue
            matrix_configs.append((track_name, constrained_dec, config_id))

        output_path = os.path.join(self.results_dir, f"phase2_5_1_{model_id.replace(':', '_')}_results.json")
        all_records_map: Dict[str, Dict[str, Any]] = {}
        matrix_summaries: Dict[str, Any] = {}

        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    for r in existing_data.get("detailed_records", []):
                        key = f"{r['task_id']}_{r['track']}_{r['config_id']}"
                        all_records_map[key] = r
                    matrix_summaries = existing_data.get("matrix_summaries", {})
            except Exception:
                pass

        for track_name, constrained_dec, config_id in matrix_configs:
            label = f"{model_id} | {track_name} | {config_id}"
            print(f"\n--- Running Suite: {label} ({len(tasks)} tasks) ---")
            suite_records: List[Dict[str, Any]] = []

            for i, task in enumerate(tasks, 1):
                t0 = time.perf_counter()
                rec = self.evaluate_task(
                    task=task,
                    model_id=model_id,
                    track_name=track_name,
                    config_id=config_id,
                    constrained_decoding=constrained_dec,
                )
                dt = time.perf_counter() - t0
                suite_records.append(rec)
                key = f"{rec['task_id']}_{rec['track']}_{rec['config_id']}"
                all_records_map[key] = rec
                print(
                    f"[{i:02d}/{len(tasks):02d}] {task.task_id} "
                    f"decision={rec['decision']} outcome={rec['classification_outcome']} "
                    f"time={dt:.2f}s",
                    flush=True,
                )

            metrics = self.compute_summary_metrics(suite_records)
            matrix_summaries[f"{track_name}_{config_id}"] = asdict(metrics)
            print(f"Suite Summary for {label}:", flush=True)
            print(
                f"  Accuracy: {metrics.accuracy*100:.1f}% [{metrics.true_positives + metrics.true_negatives}/{metrics.total_tasks}] "
                f"(95% CI: {metrics.accuracy_ci[0]*100:.1f}% - {metrics.accuracy_ci[1]*100:.1f}%)"
            )
            print(
                f"  Sensitivity (TPR): {metrics.sensitivity*100:.1f}% [{metrics.true_positives}/{metrics.true_positives + metrics.false_negatives}] "
                f"(95% CI: {metrics.sensitivity_ci[0]*100:.1f}% - {metrics.sensitivity_ci[1]*100:.1f}%)"
            )
            print(
                f"  Specificity (TNR): {metrics.specificity*100:.1f}% [{metrics.true_negatives}/{metrics.true_negatives + metrics.false_positives}] "
                f"(95% CI: {metrics.specificity_ci[0]*100:.1f}% - {metrics.specificity_ci[1]*100:.1f}%)"
            )
            print(
                f"  FAR: {metrics.false_acceptance_rate*100:.1f}% [{metrics.false_negatives}/{metrics.true_positives + metrics.false_negatives}]"
            )
            print(
                f"  FRR: {metrics.false_rejection_rate*100:.1f}% [{metrics.false_positives}/{metrics.true_negatives + metrics.false_positives}]"
            )
            print(
                f"  Balanced Accuracy: {metrics.balanced_accuracy*100:.1f}%"
            )
            print(
                f"  Outcomes: Review Passed={metrics.review_passed_count}, Failed={metrics.review_failed_count}, "
                f"Revision Requested={metrics.revision_requested_count}, Tickets Created={metrics.approval_tickets_created}",
                flush=True,
            )

        final_records = list(all_records_map.values())
        payload = {
            "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
            "model_identifier": model_id,
            "total_evaluations": len(final_records),
            "holdout_tasks_count": len(tasks),
            "matrix_summaries": matrix_summaries,
            "detailed_records": final_records,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\nPersisted {len(final_records)} evaluation records to {output_path}", flush=True)

        return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2.5.1 Reviewer Calibration Evaluator")
    parser.add_argument("--model", type=str, default="qwen2.5:7b", help="Model identifier")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of holdout tasks")
    parser.add_argument("--track", type=str, default="all", choices=["all", "A", "B"], help="Track filter")
    parser.add_argument("--config", type=str, default="all", choices=["all", "A", "B", "C"], help="Config filter")
    args = parser.parse_args()

    evaluator = Phase251Evaluator()
    evaluator.run_calibration_evaluation(
        model_id=args.model,
        limit_tasks=args.limit,
        track_filter=args.track,
        config_filter=args.config,
    )


if __name__ == "__main__":
    main()
