"""Pydantic v2 schemas and validation models for Phase 2 Autonomous Quant Research Agent.

All schemas enforce strict validation on agent reasoning outputs, tool contracts,
human approval tickets, audit events, and research session states.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class HypothesisPayload(BaseModel):
    """Structured payload for a falsifiable quantitative trading hypothesis."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(
        ...,
        pattern=r"^H-[A-Z0-9_-]+$",
        description="Unique hypothesis identifier, e.g. H-MOM-01",
    )
    strategy_name: str = Field(
        ...,
        description="Name of compiled Python strategy primitive, e.g. SyntheticSMAStrategy",
    )
    economic_rationale: str = Field(
        ...,
        min_length=20,
        description="Economic rationale explaining why the strategy should produce alpha",
    )
    validation_criteria: Dict[str, float] = Field(
        ...,
        description="Preregistered performance hurdles, e.g. min_sharpe, max_drawdown_pct",
    )
    max_evaluation_budget: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum lifetime number of parameter trials permitted for this hypothesis",
    )


class SkepticalReviewerOutput(BaseModel):
    """Structured pre-flight adversarial review of proposed hypothesis and parameter grid."""

    model_config = ConfigDict(extra="forbid")

    lookahead_risk_score: int = Field(
        ...,
        ge=1,
        le=5,
        description="1 = No future data leakage risk; 5 = High lookahead bias risk",
    )
    snooping_risk_score: int = Field(
        ...,
        ge=1,
        le=5,
        description="1 = Strong economic premise; 5 = Arbitrary data snooping / curve fitting",
    )
    granularity_risk_score: int = Field(
        ...,
        ge=1,
        le=5,
        description="1 = Sparse bounded grid; 5 = Overly dense continuous parameter space",
    )
    turnover_realism_score: int = Field(
        ...,
        ge=1,
        le=5,
        description="1 = Realistic friction and rebalancing; 5 = Hyperactive friction blindness",
    )
    overall_recommendation: Literal[
        "RECOMMEND_APPROVAL",
        "RECOMMEND_REVISION",
        "RECOMMEND_REJECTION",
        "REVIEW_REQUIRED",
        "INSUFFICIENT_EVIDENCE",
    ] = Field(
        ...,
        description="Adversarial critique recommendation",
    )
    critique_rationale: str = Field(
        ...,
        min_length=50,
        description="Detailed justification for scores and recommendation",
    )
    defect_type: Optional[str] = Field(
        default=None,
        description="Exact category of methodological defect, if present",
    )
    affected_field: Optional[str] = Field(
        default=None,
        description="Relevant proposal field or assumption exhibiting the defect",
    )
    invalidation_reason: Optional[str] = Field(
        default=None,
        description="Detailed explanation of why the defect invalidates the proposed experiment",
    )
    corrective_action: Optional[str] = Field(
        default=None,
        description="What specific correction would make the proposal acceptable",
    )
    required_revisions: List[str] = Field(
        default_factory=list,
        description="Specific changes required before approval recommendation can be granted",
    )


class PreregistrationProposalPayload(BaseModel):
    """Structured proposal for an experimental grid before human freezing."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: Optional[str] = Field(
        default=None,
        description="Unique proposal identifier assigned by queue, e.g. PROP-8F2A10",
    )
    hypothesis_id: str = Field(
        ...,
        description="Parent hypothesis identifier",
    )
    universe: List[str] = Field(
        ...,
        min_length=1,
        description="List of trading asset symbols, e.g. ['BTCUSDT']",
    )
    timeframe: str = Field(
        default="1d",
        description="Bar timeframe, strictly '1d' for Phase 2",
    )
    parameter_grid: Dict[str, List[float]] = Field(
        ...,
        description="Explicit bounded parameter values, max 5 values per parameter",
    )
    partition_protocol_id: str = Field(
        default="CHRONOLOGICAL_V1",
        description="Fixed partition protocol identifier",
    )


class ApprovalTicket(BaseModel):
    """Human approval review ticket for a preregistration proposal."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(
        ...,
        description="Unique ticket identifier, e.g. TICK-9281-A",
    )
    proposal_id: str = Field(
        ...,
        description="Associated preregistration proposal identifier",
    )
    approval_scope: Literal["PREREGISTRATION_APPROVAL", "TEST_EVALUATION"] = Field(
        default="PREREGISTRATION_APPROVAL",
        description="Scope of requested authorization",
    )
    status: Literal["PENDING", "APPROVED", "REJECTED"] = Field(
        default="PENDING",
        description="Human review status",
    )
    submission_notes: Optional[str] = Field(
        default=None,
        description="Contextual notes provided by the agent coordinator",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Ticket creation timestamp (UTC ISO format)",
    )
    decision_rationale: Optional[str] = Field(
        default=None,
        description="Human director rationale for approval or rejection",
    )
    decided_at: Optional[str] = Field(
        default=None,
        description="Timestamp when human decision was recorded",
    )
    decided_by: Optional[str] = Field(
        default=None,
        description="Identifier of the Human Research Director",
    )


class ExecutionRequestPayload(BaseModel):
    """Request specification for executing a backtest via ResearchExecutionEngine."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        ...,
        description="Client idempotency token, e.g. REQ-20261102-01",
    )
    prereg_id: str = Field(
        ...,
        description="Approved preregistration identifier",
    )
    candidate_parameters: Dict[str, float] = Field(
        ...,
        description="Candidate configuration matching approved frozen grid",
    )
    partition: Literal["TRAIN", "VALIDATION", "TEST"] = Field(
        default="TRAIN",
        description="Target execution partition",
    )


class AuditEvent(BaseModel):
    """Immutable audit trail event for model reasoning and tool actions."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(
        ...,
        description="Unique audit event identifier",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Event timestamp in UTC ISO format",
    )
    stage: str = Field(
        ...,
        description="Research lifecycle stage name",
    )
    role: str = Field(
        ...,
        description="Agent role mode, e.g. Coordinator, Skeptical Reviewer",
    )
    provider: str = Field(
        ...,
        description="LLM provider name, e.g. mock, local, anthropic",
    )
    model_identifier: str = Field(
        ...,
        description="Model identifier version string",
    )
    prompt_sha256: str = Field(
        ...,
        description="SHA-256 digest of input prompt (excluding chain-of-thought)",
    )
    response_sha256: str = Field(
        ...,
        description="SHA-256 digest of structured response",
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Tool invoked during this event, if applicable",
    )
    tool_input_sha256: Optional[str] = Field(
        default=None,
        description="SHA-256 digest of tool input parameters",
    )
    tool_output_sha256: Optional[str] = Field(
        default=None,
        description="SHA-256 digest of tool output",
    )
    structured_payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Sanitized structured payload data",
    )


class ResearchSessionState(BaseModel):
    """Persistent state of an autonomous research session."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        ...,
        description="Unique session identifier, e.g. SESS-20261102-01",
    )
    objective: str = Field(
        ...,
        description="Human-defined quantitative research objective",
    )
    current_stage: str = Field(
        default="OBJECTIVE_INGESTION",
        description="Current stage in the 12-stage research state machine",
    )
    hypothesis: Optional[HypothesisPayload] = Field(
        default=None,
        description="Formulated research hypothesis",
    )
    critique: Optional[SkepticalReviewerOutput] = Field(
        default=None,
        description="Skeptical reviewer audit report",
    )
    proposal: Optional[PreregistrationProposalPayload] = Field(
        default=None,
        description="Submitted preregistration proposal",
    )
    ticket: Optional[ApprovalTicket] = Field(
        default=None,
        description="Human approval ticket",
    )
    prereg_id: Optional[str] = Field(
        default=None,
        description="Approved and frozen preregistration ID",
    )
    experiment_ids: List[str] = Field(
        default_factory=list,
        description="List of executed experiment identifiers",
    )
    report_path: Optional[str] = Field(
        default=None,
        description="Filesystem path of compiled research report",
    )
    is_completed: bool = Field(
        default=False,
        description="True if session reached terminal stage successfully",
    )
    is_failed: bool = Field(
        default=False,
        description="True if session aborted due to unrecoverable error",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Failure details if session failed",
    )
    audit_events: List[AuditEvent] = Field(
        default_factory=list,
        description="In-memory trace of audit events during this session",
    )

