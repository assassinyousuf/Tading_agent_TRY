"""Reproducibility Verification Engine for Quantitative Research Experiments.

Enforces:
1. Multi-attribute comparison across digests, seeds, parameters, metrics, and artifact hashes.
2. Four-way discrete classification:
   - EXACT_REPRODUCIBILITY
   - NUMERICALLY_EQUIVALENT
   - NON_REPRODUCIBLE
   - INSUFFICIENT_INFORMATION
3. Explicit numerical tolerance bounds (default: absolute 1e-6, relative 1e-5).
4. No silent approximation: approximate matches are never classified as exact matches.
5. Zero em dashes (\\u2014) or en dashes (\\u2013).
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import math
from typing import Any, Dict, List, Optional, Tuple

from trader.agent.evidence.manifest import ExperimentEvidenceManifest


class ReproducibilityVerdict(str, Enum):
    """Rigorous classification of experimental replication outcomes."""

    EXACT_REPRODUCIBILITY = "EXACT_REPRODUCIBILITY"
    NUMERICALLY_EQUIVALENT = "NUMERICALLY_EQUIVALENT"
    NON_REPRODUCIBLE = "NON_REPRODUCIBLE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


@dataclass(frozen=True)
class ReproducibilityTolerances:
    """Documented tolerance bounds for numerical equivalence."""

    metric_abs_tol: float = 1e-6
    metric_rel_tol: float = 1e-5
    integer_exact: bool = True


@dataclass(frozen=True)
class MetricDiscrepancy:
    """Detailed record of a metric difference observed during replication."""

    metric_name: str
    original_value: Any
    reproduced_value: Any
    difference: float
    exceeds_tolerance: bool


@dataclass(frozen=True)
class ReproducibilityResult:
    """Complete audit verdict and evidence from a reproducibility check."""

    verification_id: str
    manifest_id: str
    experiment_id: str
    verdict: ReproducibilityVerdict
    is_exact_match: bool
    is_numerically_equivalent: bool
    tolerances: ReproducibilityTolerances
    metric_discrepancies: List[MetricDiscrepancy]
    digest_matches: Dict[str, bool]
    reproduced_metrics: Dict[str, Any]
    reproduced_output_hash: str
    audit_notes: str
    verification_timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "verification_id": self.verification_id,
            "manifest_id": self.manifest_id,
            "experiment_id": self.experiment_id,
            "verdict": self.verdict.value,
            "is_exact_match": self.is_exact_match,
            "is_numerically_equivalent": self.is_numerically_equivalent,
            "tolerances": asdict(self.tolerances),
            "metric_discrepancies": [asdict(d) for d in self.metric_discrepancies],
            "digest_matches": self.digest_matches,
            "reproduced_metrics": self.reproduced_metrics,
            "reproduced_output_hash": self.reproduced_output_hash,
            "audit_notes": self.audit_notes,
            "verification_timestamp_utc": self.verification_timestamp_utc,
        }


class ReproducibilityEngine:
    """Authoritative engine verifying independent reproducibility of quantitative experiments."""

    def __init__(self, tolerances: Optional[ReproducibilityTolerances] = None) -> None:
        self.tolerances = tolerances or ReproducibilityTolerances()

    def verify_reproducibility(
        self,
        reference_manifest: ExperimentEvidenceManifest,
        reproduced_metrics: Dict[str, Any],
        reproduced_output_hash: str,
        reproduced_parameters: Optional[Dict[str, Any]] = None,
        reproduced_dataset_fingerprint: Optional[str] = None,
        reproduced_seeds: Optional[Dict[str, int]] = None,
        reproduced_code_revision: Optional[str] = None,
        verification_id: Optional[str] = None,
    ) -> ReproducibilityResult:
        """Evaluate replication results against the authoritative baseline manifest."""
        v_id = verification_id or f"VERIF-{reference_manifest.experiment_id[:16]}"
        now_utc = datetime.now(timezone.utc).isoformat()

        # Step 1: Check for Insufficient Information
        missing_fields: List[str] = []
        if not reference_manifest.parameter_definitions or reproduced_parameters is None:
            missing_fields.append("parameter_definitions")
        if not reference_manifest.dataset_fingerprint or reproduced_dataset_fingerprint is None:
            missing_fields.append("dataset_fingerprint")
        if not reference_manifest.random_seeds or reproduced_seeds is None:
            missing_fields.append("random_seeds")

        if missing_fields:
            return ReproducibilityResult(
                verification_id=v_id,
                manifest_id=reference_manifest.manifest_id,
                experiment_id=reference_manifest.experiment_id,
                verdict=ReproducibilityVerdict.INSUFFICIENT_INFORMATION,
                is_exact_match=False,
                is_numerically_equivalent=False,
                tolerances=self.tolerances,
                metric_discrepancies=[],
                digest_matches={},
                reproduced_metrics=reproduced_metrics,
                reproduced_output_hash=reproduced_output_hash,
                audit_notes=f"Insufficient information to verify reproducibility: missing fields {missing_fields}",
                verification_timestamp_utc=now_utc,
            )

        # Step 2: Digest and Prerequisite Matching
        param_match = reference_manifest.parameter_definitions == reproduced_parameters
        data_match = reference_manifest.dataset_fingerprint == reproduced_dataset_fingerprint
        seed_match = reference_manifest.random_seeds == reproduced_seeds
        code_match = reference_manifest.code_revision == reproduced_code_revision if reproduced_code_revision else True

        digest_matches = {
            "parameter_definitions": param_match,
            "dataset_fingerprint": data_match,
            "random_seeds": seed_match,
            "code_revision": code_match,
        }

        # If fundamental prerequisites diverge, the result is immediately non-reproducible
        if not (param_match and data_match and seed_match):
            reasons: List[str] = []
            if not param_match:
                reasons.append("Parameters modified")
            if not data_match:
                reasons.append("Dataset fingerprint modified")
            if not seed_match:
                reasons.append("Random seeds modified")

            return ReproducibilityResult(
                verification_id=v_id,
                manifest_id=reference_manifest.manifest_id,
                experiment_id=reference_manifest.experiment_id,
                verdict=ReproducibilityVerdict.NON_REPRODUCIBLE,
                is_exact_match=False,
                is_numerically_equivalent=False,
                tolerances=self.tolerances,
                metric_discrepancies=[],
                digest_matches=digest_matches,
                reproduced_metrics=reproduced_metrics,
                reproduced_output_hash=reproduced_output_hash,
                audit_notes=f"Replication failed: prerequisite inputs diverged ({'; '.join(reasons)})",
                verification_timestamp_utc=now_utc,
            )

        # Step 3: Exact Bit-for-Bit Hash Check
        exact_hash_match = reference_manifest.output_artifact_hash == reproduced_output_hash

        # Step 4: Metric-by-Metric Tolerance Comparison
        discrepancies: List[MetricDiscrepancy] = []
        any_metric_exceeds_tol = False

        orig_metrics = reference_manifest.metrics_summary or {}
        for m_key, orig_val in orig_metrics.items():
            if m_key not in reproduced_metrics:
                discrepancies.append(
                    MetricDiscrepancy(
                        metric_name=m_key,
                        original_value=orig_val,
                        reproduced_value=None,
                        difference=float("inf"),
                        exceeds_tolerance=True,
                    )
                )
                any_metric_exceeds_tol = True
                continue

            repro_val = reproduced_metrics[m_key]

            # Integer metrics require exact match
            if isinstance(orig_val, int) and isinstance(repro_val, int):
                diff = abs(orig_val - repro_val)
                exceeds = diff != 0 if self.tolerances.integer_exact else diff > self.tolerances.metric_abs_tol
                if diff != 0:
                    discrepancies.append(
                        MetricDiscrepancy(
                            metric_name=m_key,
                            original_value=orig_val,
                            reproduced_value=repro_val,
                            difference=float(diff),
                            exceeds_tolerance=exceeds,
                        )
                    )
                    if exceeds:
                        any_metric_exceeds_tol = True
            elif isinstance(orig_val, (int, float)) and isinstance(repro_val, (int, float)):
                diff = abs(float(orig_val) - float(repro_val))
                scale = max(abs(float(orig_val)), abs(float(repro_val)), 1.0)
                rel_diff = diff / scale
                exceeds = diff > self.tolerances.metric_abs_tol and rel_diff > self.tolerances.metric_rel_tol
                if diff > 0.0:
                    discrepancies.append(
                        MetricDiscrepancy(
                            metric_name=m_key,
                            original_value=orig_val,
                            reproduced_value=repro_val,
                            difference=round(diff, 9),
                            exceeds_tolerance=exceeds,
                        )
                    )
                    if exceeds:
                        any_metric_exceeds_tol = True
            else:
                # String or non-numeric metric
                if orig_val != repro_val:
                    discrepancies.append(
                        MetricDiscrepancy(
                            metric_name=m_key,
                            original_value=orig_val,
                            reproduced_value=repro_val,
                            difference=1.0,
                            exceeds_tolerance=True,
                        )
                    )
                    any_metric_exceeds_tol = True

        # Step 5: Assign Final Verdict
        if exact_hash_match and not discrepancies:
            verdict = ReproducibilityVerdict.EXACT_REPRODUCIBILITY
            notes = "Exact bit-for-bit reproducibility verified across all artifacts, seeds, and metrics."
            is_exact = True
            is_equiv = True
        elif not any_metric_exceeds_tol:
            verdict = ReproducibilityVerdict.NUMERICALLY_EQUIVALENT
            notes = (
                f"Numerically equivalent reproduction verified. All metrics match within documented tolerances "
                f"(abs_tol={self.tolerances.metric_abs_tol}, rel_tol={self.tolerances.metric_rel_tol}). "
                f"Minor discrepancies observed in {len(discrepancies)} metric(s)."
            )
            is_exact = False
            is_equiv = True
        else:
            verdict = ReproducibilityVerdict.NON_REPRODUCIBLE
            bad_metrics = [d.metric_name for d in discrepancies if d.exceeds_tolerance]
            notes = f"Replication failed: {len(bad_metrics)} metric(s) exceeded tolerance bounds: {bad_metrics}."
            is_exact = False
            is_equiv = False

        return ReproducibilityResult(
            verification_id=v_id,
            manifest_id=reference_manifest.manifest_id,
            experiment_id=reference_manifest.experiment_id,
            verdict=verdict,
            is_exact_match=is_exact,
            is_numerically_equivalent=is_equiv,
            tolerances=self.tolerances,
            metric_discrepancies=discrepancies,
            digest_matches=digest_matches,
            reproduced_metrics=reproduced_metrics,
            reproduced_output_hash=reproduced_output_hash,
            audit_notes=notes,
            verification_timestamp_utc=now_utc,
        )

