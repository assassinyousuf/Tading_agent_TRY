"""Audit Metric Recalculator across Calibration (TASK 21-40) and Holdout (TASK 41-50).

Calculates exact confusion matrices, rates, and 95% Wilson score confidence intervals
under both Ground Truth A (TASK-22 = FLAWED) and Ground Truth B (TASK-22 = CLARIFICATION).

Zero em dashes (\\u2014) or en dashes (\\u2013) in this file.
"""

import json
import math
from typing import Any, Dict, List, Tuple


def wilson_ci(successes: int, total: int, z: float = 1.95996) -> Tuple[float, float]:
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


def evaluate_records(
    records: List[Dict[str, Any]],
    task_22_as_clarification: bool = False,
) -> Dict[str, Any]:
    total = len(records)
    tp = 0
    tn = 0
    fp = 0
    fn = 0
    syntax_errors = 0

    for r in records:
        task_id = r["task_id"]
        decision = r["decision"]
        is_valid = r["ground_truth_is_valid"]
        schema_valid = r.get("schema_valid", True)

        if not schema_valid:
            syntax_errors += 1
            continue

        # In both Ground Truth A and Ground Truth B, TASK-22 has is_valid=False.
        # It is never a sound task that should be approved.
        if not is_valid:
            # Positive condition (flawed / clarification)
            if decision in ("REJECTED", "REVIEW_REQUIRED", "REVISION_EXHAUSTED"):
                tp += 1
            elif decision in ("PROCEEDED", "PENDING_APPROVAL"):
                fn += 1
            else:
                tp += 1
        else:
            # Negative condition (sound)
            if decision in ("PROCEEDED", "PENDING_APPROVAL"):
                tn += 1
            elif decision in ("REJECTED", "REVIEW_REQUIRED", "REVISION_EXHAUSTED"):
                fp += 1
            else:
                fp += 1

    pos_total = tp + fn
    neg_total = tn + fp

    acc = round((tp + tn) / float(total), 4) if total > 0 else 0.0
    acc_ci = wilson_ci(tp + tn, total)

    sens = round(tp / float(pos_total), 4) if pos_total > 0 else 0.0
    sens_ci = wilson_ci(tp, pos_total)

    spec = round(tn / float(neg_total), 4) if neg_total > 0 else 0.0
    spec_ci = wilson_ci(tn, neg_total)

    balanced_acc = round((sens + spec) / 2.0, 4)

    far = round(fn / float(pos_total), 4) if pos_total > 0 else 0.0
    far_ci = wilson_ci(fn, pos_total)

    frr = round(fp / float(neg_total), 4) if neg_total > 0 else 0.0
    frr_ci = wilson_ci(fp, neg_total)

    return {
        "total": total,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "syntax_errors": syntax_errors,
        "pos_total": pos_total,
        "neg_total": neg_total,
        "accuracy": acc,
        "accuracy_ci": acc_ci,
        "sensitivity": sens,
        "sensitivity_ci": sens_ci,
        "specificity": spec,
        "specificity_ci": spec_ci,
        "balanced_accuracy": balanced_acc,
        "far": far,
        "far_ci": far_ci,
        "frr": frr,
        "frr_ci": frr_ci,
    }


def main() -> None:
    with open("trader/benchmark_results/phase2_5_1_eval_results.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data["detailed_records"]

    configs = [
        ("Track A", "SINGLE_RESEARCHER"),
        ("Track A", "RESEARCHER_REVIEWER"),
        ("Track A", "FULL_COORDINATOR"),
        ("Track B", "SINGLE_RESEARCHER"),
        ("Track B", "RESEARCHER_REVIEWER"),
        ("Track B", "FULL_COORDINATOR"),
    ]

    print("================================================================================")
    print("PHASE 2.5.2 BENCHMARK RE-EVALUATION AND CONTAMINATION AUDIT")
    print("================================================================================")

    for track, cfg in configs:
        cfg_records = [r for r in records if r["track"] == track and r["config_id"] == cfg]
        r_seen = [r for r in cfg_records if int(r["task_id"].split("-")[1]) <= 40]
        r_new = [r for r in cfg_records if int(r["task_id"].split("-")[1]) > 40]

        res_seen = evaluate_records(r_seen)
        res_new = evaluate_records(r_new)
        res_all_a = evaluate_records(cfg_records, task_22_as_clarification=False)
        res_all_b = evaluate_records(cfg_records, task_22_as_clarification=True)

        print(f"\n### Configuration: {track} | {cfg}")
        print("--------------------------------------------------------------------------------")
        print(f"1. CALIBRATION SUBSET (TASK 21-40, n={len(r_seen)}):")
        print(f"   TP: {res_seen['tp']}, TN: {res_seen['tn']}, FP: {res_seen['fp']}, FN: {res_seen['fn']}, Syntax: {res_seen['syntax_errors']}")
        print(f"   Sensitivity (TPR):    {res_seen['sensitivity']:.4f} [95% CI: {res_seen['sensitivity_ci'][0]:.4f}, {res_seen['sensitivity_ci'][1]:.4f}]")
        print(f"   Specificity (TNR):    {res_seen['specificity']:.4f} [95% CI: {res_seen['specificity_ci'][0]:.4f}, {res_seen['specificity_ci'][1]:.4f}]")
        print(f"   False Acceptance:     {res_seen['far']:.4f} [95% CI: {res_seen['far_ci'][0]:.4f}, {res_seen['far_ci'][1]:.4f}]")
        print(f"   False Rejection:      {res_seen['frr']:.4f} [95% CI: {res_seen['frr_ci'][0]:.4f}, {res_seen['frr_ci'][1]:.4f}]")
        print(f"   Accuracy:             {res_seen['accuracy']:.4f} [95% CI: {res_seen['accuracy_ci'][0]:.4f}, {res_seen['accuracy_ci'][1]:.4f}]")
        print(f"   Balanced Accuracy:    {res_seen['balanced_accuracy']:.4f}")

        print(f"\n2. NEW HOLDOUT SUBSET (TASK 41-50, n={len(r_new)}):")
        print(f"   TP: {res_new['tp']}, TN: {res_new['tn']}, FP: {res_new['fp']}, FN: {res_new['fn']}, Syntax: {res_new['syntax_errors']}")
        print(f"   Sensitivity (TPR):    {res_new['sensitivity']:.4f} [95% CI: {res_new['sensitivity_ci'][0]:.4f}, {res_new['sensitivity_ci'][1]:.4f}]")
        print(f"   Specificity (TNR):    {res_new['specificity']:.4f} [95% CI: {res_new['specificity_ci'][0]:.4f}, {res_new['specificity_ci'][1]:.4f}]")
        print(f"   False Acceptance:     {res_new['far']:.4f} [95% CI: {res_new['far_ci'][0]:.4f}, {res_new['far_ci'][1]:.4f}]")
        print(f"   False Rejection:      {res_new['frr']:.4f} [95% CI: {res_new['frr_ci'][0]:.4f}, {res_new['frr_ci'][1]:.4f}]")
        print(f"   Accuracy:             {res_new['accuracy']:.4f} [95% CI: {res_new['accuracy_ci'][0]:.4f}, {res_new['accuracy_ci'][1]:.4f}]")
        print(f"   Balanced Accuracy:    {res_new['balanced_accuracy']:.4f}")

        print(f"\n3. FULL DATASET (TASK 21-50, n={len(cfg_records)}):")
        print(f"   TP: {res_all_a['tp']}, TN: {res_all_a['tn']}, FP: {res_all_a['fp']}, FN: {res_all_a['fn']}, Syntax: {res_all_a['syntax_errors']}")
        print(f"   Sensitivity (TPR):    {res_all_a['sensitivity']:.4f} [95% CI: {res_all_a['sensitivity_ci'][0]:.4f}, {res_all_a['sensitivity_ci'][1]:.4f}]")
        print(f"   Specificity (TNR):    {res_all_a['specificity']:.4f} [95% CI: {res_all_a['specificity_ci'][0]:.4f}, {res_all_a['specificity_ci'][1]:.4f}]")
        print(f"   False Acceptance:     {res_all_a['far']:.4f} [95% CI: {res_all_a['far_ci'][0]:.4f}, {res_all_a['far_ci'][1]:.4f}]")
        print(f"   False Rejection:      {res_all_a['frr']:.4f} [95% CI: {res_all_a['frr_ci'][0]:.4f}, {res_all_a['frr_ci'][1]:.4f}]")
        print(f"   Accuracy:             {res_all_a['accuracy']:.4f} [95% CI: {res_all_a['accuracy_ci'][0]:.4f}, {res_all_a['accuracy_ci'][1]:.4f}]")
        print(f"   Balanced Accuracy:    {res_all_a['balanced_accuracy']:.4f}")


if __name__ == "__main__":
    main()

