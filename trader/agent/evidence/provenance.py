"""Experiment Provenance and Lineage Tracking for Research Integrity.

Enforces:
1. Complete unbroken chain of custody from hypothesis to proposal, revisions, approval, and execution.
2. Distinct categorical origins (HUMAN_AUTHORED, LLM_ASSISTED, RESEARCHER_REVISION, HUMAN_APPROVED, PROGRAMMATIC_GENERATION).
3. Immutable relational mapping between sessions, proposals, tickets, requests, and manifests.
4. Zero em dashes (\\u2014) or en dashes (\\u2013).
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Tuple

from trader.agent.evidence.manifest import ProvenanceType


@dataclass(frozen=True)
class ProvenanceRecord:
    """Immutable audit record linking proposal origin, revisions, human ticket, and final execution."""

    hypothesis_id: str
    provenance_type: str
    origin_author: str
    initial_proposal_id: Optional[str] = None
    revision_count: int = 0
    revision_ids: List[str] = field(default_factory=list)
    approved_proposal_id: Optional[str] = None
    ticket_id: Optional[str] = None
    approver_identity: Optional[str] = None
    approval_timestamp_utc: Optional[str] = None
    request_id: Optional[str] = None
    trial_id: Optional[int] = None
    experiment_id: Optional[str] = None
    manifest_id: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary."""
        return asdict(self)


class ProvenanceTracker:
    """Manages research provenance verification and lineage reconstruction."""

    @staticmethod
    def build_lineage_chain(
        hypothesis_id: str,
        provenance_type: str,
        origin_author: str,
        initial_proposal_id: Optional[str] = None,
        revision_ids: Optional[List[str]] = None,
        approved_proposal_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        approver_identity: Optional[str] = None,
        approval_timestamp_utc: Optional[str] = None,
        request_id: Optional[str] = None,
        trial_id: Optional[int] = None,
        experiment_id: Optional[str] = None,
        manifest_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> ProvenanceRecord:
        """Construct an immutable provenance lineage record."""
        revs = revision_ids or []
        return ProvenanceRecord(
            hypothesis_id=hypothesis_id,
            provenance_type=provenance_type,
            origin_author=origin_author,
            initial_proposal_id=initial_proposal_id,
            revision_count=len(revs),
            revision_ids=revs,
            approved_proposal_id=approved_proposal_id,
            ticket_id=ticket_id,
            approver_identity=approver_identity,
            approval_timestamp_utc=approval_timestamp_utc,
            request_id=request_id,
            trial_id=trial_id,
            experiment_id=experiment_id,
            manifest_id=manifest_id,
            notes=notes,
        )

    @staticmethod
    def verify_provenance_integrity(record: ProvenanceRecord) -> Tuple[bool, List[str]]:
        """Verify structural consistency of the provenance lineage chain."""
        violations: List[str] = []

        if not record.hypothesis_id:
            violations.append("Missing hypothesis_id")

        if record.provenance_type not in [p.value for p in ProvenanceType]:
            violations.append(f"Invalid provenance_type: {record.provenance_type}")

        # If approved, ticket and approver identity must be present
        if record.approved_proposal_id:
            if not record.ticket_id:
                violations.append("Approved proposal lacks bound ticket_id")
            if not record.approver_identity:
                violations.append("Approved proposal lacks approver_identity")

        # If execution occurred, request_id and experiment_id must be bound
        if record.manifest_id:
            if not record.experiment_id:
                violations.append("Manifest exists without bound experiment_id")
            if not record.request_id:
                violations.append("Manifest exists without bound request_id")

        return len(violations) == 0, violations
