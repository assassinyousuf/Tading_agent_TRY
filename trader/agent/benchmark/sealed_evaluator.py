"""Independent Replication Evaluator for Sealed Tasks in Phase 2.5.2.

CRITICAL SAFETY AND GOVERNANCE RULES:
- Zero live trading, paper trading, testnet, or exchange credentials.
- Strictly TRAIN partition market context. Zero access to TEST partition.
- C: drive isolation: all models loaded from E:\\ollama_models.
- Zero em dashes (\\u2014) or en dashes (\\u2013) across code and documentation.
"""

import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List

from trader.agent.benchmark.coordinator_eval import FullCoordinatorBenchmarkRunner
from trader.agent.benchmark.schema import BenchmarkTask, TaskRunResult
from trader.agent.benchmark.sealed_tasks import SEALED_GROUND_TRUTH, SEALED_TASKS
from trader.agent.providers.local import LocalResearchAssistant


def run_sealed_replication(
    task_ids: List[str],
    model_id: str = "qwen2.5:7b",
    endpoint_url: str = "http://127.0.0.1:11434/api/chat",
    results_path: str = "trader/benchmark_results/sealed_replication_results.json",
) -> Dict[str, Any]:
    """Execute real inference on selected sealed benchmark tasks and record telemetry."""
    selected_tasks = [t for t in SEALED_TASKS if t.task_id in task_ids]
    if not selected_tasks:
        raise ValueError(f"No matching tasks found for task IDs: {task_ids}")

    print(f"Starting Phase 2.5.2 Sealed Replication with {model_id}...")
    print(f"Selected {len(selected_tasks)} tasks from quarantined sealed suite.")

    provider = LocalResearchAssistant(
        endpoint_url=endpoint_url,
        model_identifier=model_id,
        temperature=0.2,
        timeout_seconds=90.0,
        max_tokens=2048,
        top_p=0.9,
        seed=42,
        constrained_decoding=True,
    )

    runner = FullCoordinatorBenchmarkRunner(provider=provider)
    detailed_records: List[Dict[str, Any]] = []

    for idx, task in enumerate(selected_tasks, 1):
        gt = SEALED_GROUND_TRUTH[task.task_id]
        print(f"[{idx}/{len(selected_tasks)}] Running {task.task_id} (Expected: {gt.expected_decision})...")
        t0 = time.perf_counter()
        run_res: TaskRunResult = runner.run_task(task)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        decision = run_res.decision
        rec = None
        if run_res.reviewer_critique and isinstance(run_res.reviewer_critique, dict):
            rec = run_res.reviewer_critique.get("overall_recommendation")

        # Map to classification outcome
        is_valid_gt = gt.is_valid
        if not is_valid_gt:
            # Flawed or clarification task (Threat)
            if decision in ("REJECTED", "REVIEW_REQUIRED", "REVISION_EXHAUSTED"):
                outcome = "TP"
            elif decision in ("PROCEEDED", "PENDING_APPROVAL"):
                outcome = "FN"
            else:
                outcome = "TP"
        else:
            # Sound task (Genuine)
            if decision in ("PROCEEDED", "PENDING_APPROVAL"):
                outcome = "TN"
            elif decision in ("REJECTED", "REVIEW_REQUIRED", "REVISION_EXHAUSTED"):
                outcome = "FP"
            else:
                outcome = "FP"

        print(f"    Decision: {decision} | Reviewer Rec: {rec} | Outcome: {outcome} ({elapsed_ms:.1f} ms)")

        record = {
            "task_id": task.task_id,
            "task_name": task.name,
            "ground_truth_is_valid": is_valid_gt,
            "ground_truth_expected_decision": gt.expected_decision,
            "flaw_category": gt.flaw_category,
            "decision": decision,
            "decision_rationale": run_res.decision_rationale,
            "reviewer_recommendation": rec,
            "classification_outcome": outcome,
            "prompt_tokens": run_res.prompt_tokens,
            "completion_tokens": run_res.completion_tokens,
            "total_tokens": run_res.total_tokens,
            "latency_ms": round(elapsed_ms, 2),
            "prompt_sha256": run_res.prompt_sha256,
            "reviewer_critique": run_res.reviewer_critique,
        }
        detailed_records.append(record)

    tp_count = sum(1 for r in detailed_records if r["classification_outcome"] == "TP")
    tn_count = sum(1 for r in detailed_records if r["classification_outcome"] == "TN")
    fp_count = sum(1 for r in detailed_records if r["classification_outcome"] == "FP")
    fn_count = sum(1 for r in detailed_records if r["classification_outcome"] == "FN")

    summary = {
        "benchmark_suite": "SEALED_QUARANTINED_SUITE",
        "model_identifier": model_id,
        "backend": "ollama",
        "constrained_decoding": True,
        "temperature": 0.2,
        "seed": 42,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_tasks_evaluated": len(detailed_records),
        "true_positives": tp_count,
        "true_negatives": tn_count,
        "false_positives": fp_count,
        "false_negatives": fn_count,
        "accuracy": round((tp_count + tn_count) / float(len(detailed_records)), 4),
        "sensitivity": round(tp_count / float(tp_count + fn_count), 4) if (tp_count + fn_count) > 0 else 0.0,
        "specificity": round(tn_count / float(tn_count + fp_count), 4) if (tn_count + fp_count) > 0 else 0.0,
        "detailed_records": detailed_records,
    }

    os.makedirs(os.path.dirname(results_path), exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\nReplication completed. Results written to {results_path}")
    print(f"Summary: TP={tp_count}, TN={tn_count}, FP={fp_count}, FN={fn_count}, Acc={summary['accuracy']}")
    return summary


if __name__ == "__main__":
    test_task_ids = [
        "TASK-51-SEALED-BASIS-CASH-CARRY",
        "TASK-54-SEALED-VOL-MANAGED-BREAKOUT",
        "TASK-52-SEALED-COINT-FULLSAMPLE-LEAKAGE",
        "TASK-58-SEALED-UNPENALIZED-5000-GRID",
        "TASK-55-SEALED-AMBIGUOUS-WHALE-FLOW",
    ]
    run_sealed_replication(task_ids=test_task_ids)

