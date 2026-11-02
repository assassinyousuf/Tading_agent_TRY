"""Canonical Benchmark Task Registry for Governed Quantitative Research.

Authoritative source of truth for benchmark task identities, prompt digests,
ground-truth classifications, expected decisions, and exposure history.

Zero em dashes (\\u2014) and zero en dashes (\\u2013) in this file.
"""

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional

from trader.agent.benchmark.holdout_tasks import HOLDOUT_TASKS
from trader.agent.benchmark.sealed_tasks import SEALED_TASKS
from trader.agent.benchmark.tasks import get_expanded_tasks


class ExposureCategory(str, Enum):
    """Categorical classification of task exposure across development phases."""

    CALIBRATION_EXPOSED = "CALIBRATION_EXPOSED"
    INFERENCE_EXPOSED = "INFERENCE_EXPOSED"
    REPORT_EXPOSED = "REPORT_EXPOSED"
    QUARANTINED_UNEXPOSED = "QUARANTINED_UNEXPOSED"


@dataclass(frozen=True)
class CanonicalTaskRecord:
    """Immutable authoritative record for a benchmark task."""

    task_id: str
    name: str
    category: str
    objective_prompt: str
    prompt_sha256: str
    is_valid: bool
    expected_decision: str
    flaw_category: Optional[str]
    exposure_category: ExposureCategory
    calibration_exposure: bool
    inference_exposure: bool
    report_exposure: bool
    quarantined_status: bool
    evaluation_history: str
    holdout_eligible: bool
    provenance_module: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary."""
        d = asdict(self)
        d["exposure_category"] = self.exposure_category.value
        return d


def _compute_sha256(text: str) -> str:
    """Compute standard SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Known task IDs evaluated with live inference in Phase 2.5.2 sealed replication
_EVALUATED_SEALED_TASK_IDS = {
    "TASK-51-SEALED-BASIS-CASH-CARRY",
    "TASK-52-SEALED-COINT-FULLSAMPLE-LEAKAGE",
    "TASK-54-SEALED-VOL-MANAGED-BREAKOUT",
    "TASK-55-SEALED-AMBIGUOUS-WHALE-FLOW",
    "TASK-58-SEALED-UNPENALIZED-5000-GRID",
}


def build_canonical_registry() -> Dict[str, CanonicalTaskRecord]:
    """Build the complete, authoritative 70-task canonical registry."""
    registry: Dict[str, CanonicalTaskRecord] = {}

    # 1. Canonical Tasks (TASK-01 through TASK-20)
    for task in get_expanded_tasks():
        prompt = task.objective_prompt
        digest = _compute_sha256(prompt)
        gt = getattr(task, "ground_truth", None)
        is_valid = gt.is_valid if gt else True
        exp_dec = gt.expected_decision if gt else "APPROVE"
        flaw_cat = gt.flaw_category if gt else None

        registry[task.task_id] = CanonicalTaskRecord(
            task_id=task.task_id,
            name=task.name,
            category=str(task.category.name if hasattr(task.category, "name") else task.category),
            objective_prompt=prompt,
            prompt_sha256=digest,
            is_valid=is_valid,
            expected_decision=exp_dec,
            flaw_category=flaw_cat,
            exposure_category=ExposureCategory.CALIBRATION_EXPOSED,
            calibration_exposure=True,
            inference_exposure=True,
            report_exposure=True,
            quarantined_status=False,
            evaluation_history="Evaluated extensively in Phase 2.3, 2.4, and 2.4.1 (504+ runs)",
            holdout_eligible=False,
            provenance_module="trader.agent.benchmark.tasks",
        )

    # 2. Holdout Suite (TASK-21 through TASK-50)
    for task in HOLDOUT_TASKS:
        prompt = task.objective_prompt
        digest = _compute_sha256(prompt)
        gt = task.ground_truth
        tid_num = int(task.task_id.split("-")[1])

        if tid_num <= 40:
            exp_cat = ExposureCategory.CALIBRATION_EXPOSED
            cal_exp = True
            inf_exp = True
            rep_exp = True
            quar = False
            eval_hist = "Phase 2.5 baseline (40 runs), Phase 2.5.1 prompt calibration (120 runs)"
            eligible = False
        else:
            exp_cat = ExposureCategory.INFERENCE_EXPOSED
            cal_exp = False
            inf_exp = True
            rep_exp = True
            quar = False
            eval_hist = "Introduced in Phase 2.5.1 holdout (60 runs); failure modes analyzed in Phase 2.5.2"
            eligible = False

        registry[task.task_id] = CanonicalTaskRecord(
            task_id=task.task_id,
            name=task.name,
            category=str(task.category.name if hasattr(task.category, "name") else task.category),
            objective_prompt=prompt,
            prompt_sha256=digest,
            is_valid=gt.is_valid,
            expected_decision=gt.expected_decision,
            flaw_category=gt.flaw_category,
            exposure_category=exp_cat,
            calibration_exposure=cal_exp,
            inference_exposure=inf_exp,
            report_exposure=rep_exp,
            quarantined_status=quar,
            evaluation_history=eval_hist,
            holdout_eligible=eligible,
            provenance_module="trader.agent.benchmark.holdout_tasks",
        )

    # 3. Sealed Quarantined Suite (TASK-51 through TASK-70)
    for task in SEALED_TASKS:
        prompt = task.objective_prompt
        digest = _compute_sha256(prompt)
        gt = task.ground_truth

        if task.task_id in _EVALUATED_SEALED_TASK_IDS:
            exp_cat = ExposureCategory.INFERENCE_EXPOSED
            cal_exp = False
            inf_exp = True
            rep_exp = True
            quar = False
            eval_hist = "Evaluated in Phase 2.5.2 sealed replication sample (1 live run)"
            eligible = False
        else:
            exp_cat = ExposureCategory.QUARANTINED_UNEXPOSED
            cal_exp = False
            inf_exp = False
            rep_exp = False
            quar = True
            eval_hist = "Quarantined: zero evaluation runs, zero prompt exposure"
            eligible = True

        registry[task.task_id] = CanonicalTaskRecord(
            task_id=task.task_id,
            name=task.name,
            category=str(task.category.name if hasattr(task.category, "name") else task.category),
            objective_prompt=prompt,
            prompt_sha256=digest,
            is_valid=gt.is_valid,
            expected_decision=gt.expected_decision,
            flaw_category=gt.flaw_category,
            exposure_category=exp_cat,
            calibration_exposure=cal_exp,
            inference_exposure=inf_exp,
            report_exposure=rep_exp,
            quarantined_status=quar,
            evaluation_history=eval_hist,
            holdout_eligible=eligible,
            provenance_module="trader.agent.benchmark.sealed_tasks",
        )

    return registry


# Singleton cache of the registry
_CANONICAL_REGISTRY: Optional[Dict[str, CanonicalTaskRecord]] = None


def get_canonical_task_registry() -> Dict[str, CanonicalTaskRecord]:
    """Retrieve the authoritative canonical benchmark task registry."""
    global _CANONICAL_REGISTRY
    if _CANONICAL_REGISTRY is None:
        _CANONICAL_REGISTRY = build_canonical_registry()
    return _CANONICAL_REGISTRY


def lookup_canonical_task(task_id: str) -> Optional[CanonicalTaskRecord]:
    """Look up a task record by canonical task ID or prefix."""
    registry = get_canonical_task_registry()
    if task_id in registry:
        return registry[task_id]
    for key, task in registry.items():
        if key == task_id or key.startswith(f"{task_id}-"):
            return task
    return None


def get_holdout_eligible_tasks() -> List[CanonicalTaskRecord]:
    """Return all benchmark tasks strictly eligible for independent holdout evaluation."""
    registry = get_canonical_task_registry()
    return [task for task in registry.values() if task.holdout_eligible]


def verify_registry_consistency(registry: Optional[Dict[str, CanonicalTaskRecord]] = None) -> Dict[str, Any]:
    """Verify consistency between the canonical registry and raw task modules.

    Returns diagnostic summary and raises ValueError if any contradiction is detected.
    """
    if registry is None:
        registry = get_canonical_task_registry()

    if len(registry) != 70:
        raise ValueError(f"Registry count mismatch: expected 70 tasks, got {len(registry)}")

    eligible = [t for t in registry.values() if t.holdout_eligible]
    if len(eligible) != 15:
        raise ValueError(f"Eligible holdout count mismatch: expected 15, got {len(eligible)}")

    # Verify that prompt digests are strictly 64 hex characters and match content
    for tid, rec in registry.items():
        if len(rec.prompt_sha256) != 64:
            raise ValueError(f"Invalid SHA-256 digest length for {tid}: {len(rec.prompt_sha256)}")

        computed_sha = _compute_sha256(rec.objective_prompt)
        if rec.prompt_sha256 != computed_sha:
            raise ValueError(f"Prompt SHA-256 mismatch for {tid}: expected {computed_sha}, got {rec.prompt_sha256}")

        # Verify ground-truth validity matches expected decision
        if rec.is_valid and rec.expected_decision != "APPROVE":
            raise ValueError(f"Sound task {tid} has conflicting expected_decision: {rec.expected_decision}")

        if not rec.is_valid and rec.expected_decision not in ("REJECT", "REVIEW_REQUIRED", "REVISE"):
            raise ValueError(f"Flawed task {tid} has conflicting expected_decision: {rec.expected_decision}")

    return {
        "status": "CONSISTENT",
        "is_consistent": True,
        "total_tasks": len(registry),
        "holdout_eligible_count": len(eligible),
        "holdout_eligible": len(eligible),
        "quarantined_unexposed": len([t for t in registry.values() if t.exposure_category == ExposureCategory.QUARANTINED_UNEXPOSED]),
        "calibration_exposed": len([t for t in registry.values() if t.exposure_category == ExposureCategory.CALIBRATION_EXPOSED]),
        "inference_exposed": len([t for t in registry.values() if t.exposure_category == ExposureCategory.INFERENCE_EXPOSED]),
        "report_exposed": len([t for t in registry.values() if t.exposure_category == ExposureCategory.REPORT_EXPOSED]),
        "quarantined_count": len([t for t in registry.values() if t.quarantined_status]),
        "calibration_exposed_count": len([t for t in registry.values() if t.calibration_exposure]),
    }
