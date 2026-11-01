"""Pydantic v2 schemas and data models for Phase 2.3 Research Agent Benchmark.

Enforces:
1. Strict separation of public agent task prompts from private ground-truth evaluation labels.
2. Independent dimensional scoring containers (0.0 to 100.0 scale).
3. Cryptographic integrity digests (SHA-256) of input data, prompts, and output artifacts.
4. Comprehensive resource tracking (tokens, latency, stage transitions, failure states).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class BenchmarkCategory(str, Enum):
    """Methodological task categories for quantitative research benchmarking."""

    VALID_HYPOTHESIS = "VALID_HYPOTHESIS"
    UNDERSPECIFIED_HYPOTHESIS = "UNDERSPECIFIED_HYPOTHESIS"
    DATA_LEAKAGE = "DATA_LEAKAGE"
    LOOKAHEAD_BIAS = "LOOKAHEAD_BIAS"
    MULTIPLE_TESTING = "MULTIPLE_TESTING"
    OVERFITTING = "OVERFITTING"
    STATISTICAL_INTERPRETATION = "STATISTICAL_INTERPRETATION"
    UNREALISTIC_FRICTION = "UNREALISTIC_FRICTION"
    NON_REPRODUCIBLE = "NON_REPRODUCIBLE"
    SURVIVORSHIP_BIAS = "SURVIVORSHIP_BIAS"
    OVER_OPTIMIZATION = "OVER_OPTIMIZATION"
    PARTITION_CONTAMINATION = "PARTITION_CONTAMINATION"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    SAMPLE_SIZE_DEFICIT = "SAMPLE_SIZE_DEFICIT"
    SUBTLE_LOOKAHEAD = "SUBTLE_LOOKAHEAD"


class TaskGroundTruth(BaseModel):
    """Private ground truth evaluation criteria strictly separated from agent prompt inputs."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(
        ...,
        description="Whether the research task is methodologically sound and free of traps",
    )
    expected_decision: Literal[
        "APPROVE",
        "REVISE",
        "REJECT",
        "REVIEW_REQUIRED",
        "INSUFFICIENT_EVIDENCE",
    ] = Field(
        ...,
        description="Authoritative expected action on proposal",
    )
    flaw_category: Optional[str] = Field(
        default=None,
        description="Primary methodological defect if is_valid is False",
    )
    expected_flaw_keywords: List[str] = Field(
        default_factory=list,
        description="Key terms that must be flagged if critique or rejection is required",
    )
    max_allowed_grid_size: int = Field(
        default=5,
        description="Maximum permitted parameter combinations (multiple-testing bound)",
    )
    min_taker_fee_rate: float = Field(
        default=0.0005,
        description="Minimum realistic taker fee threshold (0.05%)",
    )
    requires_lookahead_warning: bool = Field(
        default=False,
        description="Whether lookahead bias must be identified and scored high risk",
    )
    requires_snooping_warning: bool = Field(
        default=False,
        description="Whether data snooping must be identified and scored high risk",
    )


class BenchmarkTask(BaseModel):
    """Container for a governed quantitative research benchmark evaluation task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(
        ...,
        pattern=r"^TASK-[0-9]{2}-[A-Z0-9_-]+$",
        description="Unique task identifier, e.g. TASK-01-VALID-SMA",
    )
    name: str = Field(
        ...,
        description="Descriptive task title",
    )
    category: BenchmarkCategory = Field(
        ...,
        description="Methodological category of the task",
    )
    objective_prompt: str = Field(
        ...,
        description="The research mandate and context presented to the agent",
    )
    market_context: Dict[str, Any] = Field(
        ...,
        description="Summary statistics strictly from approved TRAIN partition",
    )
    ground_truth: TaskGroundTruth = Field(
        ...,
        description="Private evaluation rubric and expected actions (never sent to LLM)",
    )

    def get_agent_input_payload(self) -> Dict[str, Any]:
        """Return the sanitized public payload exposed to the LLM agent without ground truth."""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "category": self.category.value,
            "objective": self.objective_prompt,
            "market_context": self.market_context,
        }


class DimensionScores(BaseModel):
    """Deterministic scores across eight independent evaluation dimensions (0.0 to 100.0)."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_quality: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Falsifiability, economic rationale, and hurdle boundedness",
    )
    experimental_reproducibility: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Deterministic specification, seed presence, and parameter boundedness",
    )
    leakage_detection: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Identification and avoidance of lookahead bias or future data leakage",
    )
    statistical_reasoning: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Multiple-testing awareness, sample size discipline, and bias mitigation",
    )
    cost_model_validity: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Realistic friction, taker fees, and slippage representation",
    )
    appropriate_rejection: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Accurately approving sound setups and rejecting flawed setups",
    )
    evidence_grounded_conclusions: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Claims strictly grounded in TRAIN data without unwarranted extrapolation",
    )
    overall_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Equally weighted composite score across all seven qualitative dimensions",
    )
    detailed_audit_notes: List[str] = Field(
        default_factory=list,
        description="Step-by-step scoring rationale and rule evaluations",
    )


class TaskRunResult(BaseModel):
    """Immutable execution record for a benchmark task evaluation run."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description="Benchmark task ID")
    config_id: str = Field(..., description="Configuration ID: SINGLE_RESEARCHER, RESEARCHER_REVIEWER, FULL_COORDINATOR")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Execution timestamp in UTC ISO format",
    )
    model_identifier: str = Field(..., description="LLM model version/identifier")
    prompt_sha256: str = Field(..., description="SHA-256 hash of task objective prompt")
    dataset_digest: str = Field(..., description="SHA-256 hash of input market context")
    artifact_digest: str = Field(..., description="SHA-256 hash of serialized output artifact")
    decision: str = Field(..., description="Final configuration disposition: PROCEEDED, REJECTED, PENDING_APPROVAL, or FAILED")
    decision_rationale: Optional[str] = Field(default=None, description="Rationale for disposition")
    raw_hypothesis: Optional[Dict[str, Any]] = Field(default=None, description="Generated hypothesis payload")
    raw_proposal: Optional[Dict[str, Any]] = Field(default=None, description="Generated proposal payload")
    reviewer_critique: Optional[Dict[str, Any]] = Field(default=None, description="Skeptical reviewer critique")
    scores: DimensionScores = Field(..., description="Deterministic dimensional evaluation scores")
    prompt_tokens: int = Field(default=0, ge=0, description="Tokens consumed by prompt")
    completion_tokens: int = Field(default=0, ge=0, description="Tokens generated in completion")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens consumed")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Execution duration in milliseconds")
    error_message: Optional[str] = Field(default=None, description="Failure description if run erred")


class BenchmarkSuiteSummary(BaseModel):
    """Aggregate summary statistics across an entire benchmark configuration run."""

    model_config = ConfigDict(extra="forbid")

    config_id: str = Field(..., description="Configuration ID evaluated")
    total_tasks: int = Field(..., ge=1, description="Number of tasks evaluated")
    mean_overall_score: float = Field(..., ge=0.0, le=100.0, description="Mean overall score")
    dimension_means: Dict[str, float] = Field(..., description="Mean score by dimension")
    rejection_precision: float = Field(..., ge=0.0, le=100.0, description="Precision on rejecting flawed tasks")
    rejection_recall: float = Field(..., ge=0.0, le=100.0, description="Recall on rejecting flawed tasks")
    total_tokens: int = Field(..., ge=0, description="Cumulative tokens consumed")
    mean_latency_ms: float = Field(..., ge=0.0, description="Mean execution latency in milliseconds")
    task_results: List[TaskRunResult] = Field(..., description="Detailed per-task run records")

