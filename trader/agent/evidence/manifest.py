"""Experiment Evidence Manifests for Governed Quantitative Research.

Enforces:
1. Immutable evidence record for every executed, failed, or interrupted experiment.
2. Authoritative derivation from Python and SQLite ledger data (zero LLM hallucination).
3. Strictly TRAIN partition data (rejection of TEST or VALIDATION fingerprints).
4. Deterministic canonical JSON serialization and SHA-256 manifest hashing.
5. Zero em dashes (\\u2014) or en dashes (\\u2013) across code and docstrings.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional


class EvidenceManifestError(Exception):
    """Base exception for evidence manifest operations."""


class EvidenceManifestContradictionError(EvidenceManifestError):
    """Raised when an attempt is made to persist contradictory manifest data for an existing manifest ID."""


class EvidenceManifestPersistenceError(EvidenceManifestError):
    """Raised when mandatory evidence manifest generation or persistence fails."""


class ProvenanceType(str, Enum):
    """Categorical origin of research hypotheses and proposals."""

    HUMAN_AUTHORED = "HUMAN_AUTHORED"
    LLM_ASSISTED = "LLM_ASSISTED"
    RESEARCHER_REVISION = "RESEARCHER_REVISION"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    PROGRAMMATIC_GENERATION = "PROGRAMMATIC_GENERATION"


@dataclass(frozen=True)
class ExperimentEvidenceManifest:
    """Immutable, auditable manifest documenting the exact environment, parameters, and evidence of an experiment."""

    manifest_id: str
    experiment_id: str
    hypothesis_id: str
    prereg_id: str
    strategy_name: str
    strategy_version: str
    strategy_digest: str
    provenance_type: str
    dataset_id: str
    partition_name: str
    dataset_fingerprint: str
    code_revision: str
    feature_definitions: Dict[str, str]
    parameter_definitions: Dict[str, Any]
    execution_engine_version: str
    fee_assumptions: Dict[str, float]
    slippage_assumptions: Dict[str, float]
    benchmark_id: str
    statistical_test_identifiers: List[str]
    random_seeds: Dict[str, int]
    start_timestamp_utc: str
    completion_timestamp_utc: str
    input_artifact_hash: str
    output_artifact_hash: str
    result_status: str  # COMPLETED, FAILED, INTERRUPTED
    metrics_summary: Dict[str, Any]
    manifest_hash: str
    created_at: str
    session_id: Optional[str] = None
    ticket_id: Optional[str] = None
    request_id: Optional[str] = None
    trial_id: Optional[int] = None
    funding_assumptions: Optional[Dict[str, float]] = None
    failure_or_interruption_status: Optional[str] = None

    def __post_init__(self) -> None:
        """Enforce partition bounds and safety invariants."""
        if self.partition_name != "TRAIN":
            raise ValueError(
                f"Security violation: Evidence manifests are strictly restricted to TRAIN partition. Attempted: {self.partition_name}"
            )
        if not self.manifest_id or not self.experiment_id:
            raise ValueError("Manifest ID and Experiment ID must not be empty.")

    def to_dict(self) -> Dict[str, Any]:
        """Convert manifest to standard serializable dictionary."""
        return asdict(self)


def compute_manifest_hash(manifest_payload: Dict[str, Any]) -> str:
    """Calculate SHA-256 digest of a manifest payload using canonical deterministic JSON.

    Excludes volatile or recursive keys ('manifest_hash', 'created_at') prior to hashing.
    """
    clean_dict = {k: v for k, v in manifest_payload.items() if k not in ("manifest_hash", "created_at")}
    canonical_bytes = json.dumps(clean_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def generate_evidence_manifest(
    experiment_id: str,
    hypothesis_id: str,
    prereg_id: str,
    strategy_name: str,
    strategy_version: str,
    parameters: Dict[str, Any],
    dataset_id: str,
    partition_name: str,
    dataset_fingerprint: str,
    code_revision: str,
    metrics: Dict[str, Any],
    result_status: str,
    start_timestamp_utc: str,
    completion_timestamp_utc: str,
    provenance_type: str = ProvenanceType.PROGRAMMATIC_GENERATION.value,
    session_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    request_id: Optional[str] = None,
    trial_id: Optional[int] = None,
    fee_assumptions: Optional[Dict[str, float]] = None,
    slippage_assumptions: Optional[Dict[str, float]] = None,
    funding_assumptions: Optional[Dict[str, float]] = None,
    benchmark_id: str = "BTC_NET",
    random_seeds: Optional[Dict[str, int]] = None,
    feature_definitions: Optional[Dict[str, str]] = None,
    statistical_test_identifiers: Optional[List[str]] = None,
    execution_engine_version: str = "2.6.0",
    failure_or_interruption_status: Optional[str] = None,
    created_at: Optional[str] = None,
) -> ExperimentEvidenceManifest:
    """Produce an authoritative evidence manifest from verified execution data."""
    if partition_name != "TRAIN":
        raise ValueError(
            f"Partition access violation: Evidence manifests strictly require TRAIN data. Partition '{partition_name}' is forbidden."
        )

    # Deterministic strategy digest
    canonical_param_str = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    strategy_digest_payload = f"{strategy_name}:{strategy_version}:{canonical_param_str}"
    strategy_digest = hashlib.sha256(strategy_digest_payload.encode("utf-8")).hexdigest()

    # Input artifact hash: binds preregistration, parameters, and dataset fingerprint
    input_str = f"{prereg_id}:{canonical_param_str}:{dataset_fingerprint}:{dataset_id}"
    input_artifact_hash = hashlib.sha256(input_str.encode("utf-8")).hexdigest()

    # Output artifact hash: binds metrics and execution result
    canonical_metrics_str = json.dumps(metrics, sort_keys=True, separators=(",", ":"))
    output_str = f"{result_status}:{canonical_metrics_str}"
    output_artifact_hash = hashlib.sha256(output_str.encode("utf-8")).hexdigest()

    # Default assumptions if unspecified
    fees = fee_assumptions or {"maker_fee": 0.001, "taker_fee": 0.001}
    slippage = slippage_assumptions or {"slippage_rate": 0.0005}
    seeds = random_seeds or {"simulation_seed": 42}
    features = feature_definitions or {"close": "v1", "high": "v1", "low": "v1", "volume": "v1"}
    tests = statistical_test_identifiers or [
        "annualized_sharpe",
        "t_test_p_value",
        "max_drawdown_pct",
        "deflated_sharpe_ratio",
    ]

    manifest_id = f"MAN-{experiment_id}"
    ts_created = created_at or datetime.now(timezone.utc).isoformat()

    manifest_dict = {
        "manifest_id": manifest_id,
        "experiment_id": experiment_id,
        "hypothesis_id": hypothesis_id,
        "prereg_id": prereg_id,
        "session_id": session_id,
        "ticket_id": ticket_id,
        "request_id": request_id,
        "trial_id": trial_id,
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "strategy_digest": strategy_digest,
        "provenance_type": provenance_type,
        "dataset_id": dataset_id,
        "partition_name": partition_name,
        "dataset_fingerprint": dataset_fingerprint,
        "code_revision": code_revision,
        "feature_definitions": features,
        "parameter_definitions": parameters,
        "execution_engine_version": execution_engine_version,
        "fee_assumptions": fees,
        "slippage_assumptions": slippage,
        "funding_assumptions": funding_assumptions,
        "benchmark_id": benchmark_id,
        "statistical_test_identifiers": tests,
        "random_seeds": seeds,
        "start_timestamp_utc": start_timestamp_utc,
        "completion_timestamp_utc": completion_timestamp_utc,
        "input_artifact_hash": input_artifact_hash,
        "output_artifact_hash": output_artifact_hash,
        "result_status": result_status,
        "failure_or_interruption_status": failure_or_interruption_status,
        "metrics_summary": metrics,
        "created_at": ts_created,
    }

    manifest_hash = compute_manifest_hash(manifest_dict)

    return ExperimentEvidenceManifest(
        manifest_id=manifest_id,
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        prereg_id=prereg_id,
        session_id=session_id,
        ticket_id=ticket_id,
        request_id=request_id,
        trial_id=trial_id,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        strategy_digest=strategy_digest,
        provenance_type=provenance_type,
        dataset_id=dataset_id,
        partition_name=partition_name,
        dataset_fingerprint=dataset_fingerprint,
        code_revision=code_revision,
        feature_definitions=features,
        parameter_definitions=parameters,
        execution_engine_version=execution_engine_version,
        fee_assumptions=fees,
        slippage_assumptions=slippage,
        funding_assumptions=funding_assumptions,
        benchmark_id=benchmark_id,
        statistical_test_identifiers=tests,
        random_seeds=seeds,
        start_timestamp_utc=start_timestamp_utc,
        completion_timestamp_utc=completion_timestamp_utc,
        input_artifact_hash=input_artifact_hash,
        output_artifact_hash=output_artifact_hash,
        result_status=result_status,
        failure_or_interruption_status=failure_or_interruption_status,
        metrics_summary=metrics,
        manifest_hash=manifest_hash,
        created_at=ts_created,
    )
