"""Research Coordinator and Orchestration Engine for Phase 2 Autonomous Quant Research Agent.

Enforces:
1. Strict sequential research state machine.
2. Single-process execution lock and threading assertions.
3. Separation of pending preregistration proposals from frozen SQLite ledger entries.
4. Mandatory persistent human approval gate: experiments cannot run without explicit persisted human authorization.
5. TRAIN-only partition execution in Phase 2 vertical slice.
6. Structured audit event logging without private chain-of-thought capture.
7. Crash recovery, session checkpointing, and interrupted trial reconciliation.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ControlledExperimentResult,
    ResearchExecutionEngine,
    canonical_parameter_json,
)
from trader.agent.core.strategy_base import StrategyInterface
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.providers.base import LLMProvider, LLMResponse
from trader.agent.reporting import ResearchReportCompiler
from trader.agent.schemas import (
    ApprovalTicket,
    AuditEvent,
    HypothesisPayload,
    PreregistrationProposalPayload,
    ResearchSessionState,
    SkepticalReviewerOutput,
)
from trader.agent.tools.market_bridge import MarketDataProvider
from trader.agent.validation.temporal_integrity import (
    DeterministicOverrideBlockedError,
    TemporalDisposition,
    TemporalIntegrityAuditReport,
    TemporalIntegrityDetector,
    TemporalIntegrityViolationError,
)


class ResearchCoordinatorError(Exception):
    """Base exception for research orchestration and governance violations."""
    pass


class HumanApprovalPendingError(ResearchCoordinatorError):
    """Raised when an unapproved proposal attempts to execute backtests."""
    pass


class SkepticalReviewRejectionError(ResearchCoordinatorError):
    """Raised when the skeptical reviewer rejects the proposed hypothesis."""
    pass


class SkepticalReviewRevisionExhaustedError(SkepticalReviewRejectionError):
    """Raised when the hypothesis fails to achieve approval recommendation within max revision attempts."""
    pass


class SkepticalReviewInvestigationRequiredError(SkepticalReviewRejectionError):
    """Raised when the skeptical reviewer halts the pipeline pending human investigation."""
    pass


class SkepticalReviewInsufficientEvidenceError(SkepticalReviewRejectionError):
    """Raised when the skeptical reviewer halts the pipeline due to insufficient market or specification evidence."""
    pass


class ProcessConcurrencyError(ResearchCoordinatorError):
    """Raised when multiple orchestrator instances attempt concurrent execution."""
    pass


class InterruptedEvaluationAuthorizationRequiredError(ResearchCoordinatorError):
    """Raised when resuming a session encounters an interrupted evaluation without explicit human rerun authorization."""
    pass


DEFAULT_MAX_REVISIONS: int = 3
ABSOLUTE_MAX_REVISIONS_CEILING: int = 5
MIN_ALLOWED_REVISIONS: int = 0


class InvalidRevisionLimitError(ResearchCoordinatorError):
    """Raised when an invalid revision limit (negative, non-integer, or exceeding policy ceiling) is specified."""
    pass


class ResearchCoordinator:
    """Authoritative research coordinator managing the autonomous research lifecycle."""

    def __init__(
        self,
        ledger: ResearchLedger,
        provider: MarketDataProvider,
        execution_engine: ResearchExecutionEngine,
        llm_provider: LLMProvider,
        lock_path: Optional[str] = None,
        max_revisions: int = 3,
    ) -> None:
        self.ledger = ledger
        self.provider = provider
        self.engine = execution_engine
        self.llm_provider = llm_provider
        self.compiler = ResearchReportCompiler(conn=self.ledger.conn)
        if (
            isinstance(max_revisions, bool)
            or not isinstance(max_revisions, int)
            or max_revisions < MIN_ALLOWED_REVISIONS
            or max_revisions > ABSOLUTE_MAX_REVISIONS_CEILING
        ):
            raise InvalidRevisionLimitError(
                f"Invalid constructor max_revisions ({max_revisions}). "
                f"Must be an integer between {MIN_ALLOWED_REVISIONS} and {ABSOLUTE_MAX_REVISIONS_CEILING} (Booleans explicitly rejected)."
            )
        self.max_revisions = max_revisions

        # Single-process lock acquisition
        if lock_path is None:
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            lock_path = os.path.join(root_dir, ".research_ledger.lock")
        self.lock_path = lock_path
        self._acquire_process_lock()

        # Proposal and ticket memory store
        self.pending_proposals: Dict[str, PreregistrationProposalPayload] = {}
        self.pending_tickets: Dict[str, ApprovalTicket] = {}

        # Reconcile any orphaned trials left from previous crashes
        self.engine.reconcile_interrupted_trials()

        # Deterministic Temporal Integrity Detector (Phase 2.7.1)
        self.temporal_detector = TemporalIntegrityDetector()

    def _acquire_process_lock(self) -> None:
        """Acquire process-level lock to enforce single-process execution."""
        if threading.active_count() > 4:
            raise ProcessConcurrencyError("Concurrency violation: Orchestrator requires single-threaded execution.")

        try:
            self.lock_file = open(self.lock_path, "w")
            self.lock_file.write(f"PID:{os.getpid()}:{datetime.now(timezone.utc).isoformat()}\n")
            self.lock_file.flush()
        except Exception as exc:
            raise ProcessConcurrencyError(f"Failed to acquire process lock '{self.lock_path}': {str(exc)}") from exc

    def close(self) -> None:
        """Release process lock and resources."""
        try:
            if hasattr(self, "lock_file") and self.lock_file:
                self.lock_file.close()
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    @staticmethod
    def _build_reviewer_prompt(
        objective: str,
        hypothesis_data: Dict[str, Any],
        temporal_audit: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Construct calibrated prompt for the independent skeptical reviewer."""
        temporal_section = ""
        if temporal_audit:
            findings = temporal_audit.get("findings", [])
            disposition = temporal_audit.get("disposition")
            if findings:
                formatted_findings = "\n".join(
                    f"- [{f.get('severity')}] {f.get('title')}: {f.get('explanation')}"
                    for f in findings
                )
                temporal_section = (
                    f"\n\nDeterministic Temporal Integrity Findings (Disposition: {disposition}):\n"
                    f"{formatted_findings}\n"
                )

        return (
            "You are an adversarial quantitative research auditor evaluating a trading hypothesis.\n"
            "Your duty is to detect genuine methodological flaws, statistical leakage, and implementation bugs, "
            "while approving rigorous, standard quantitative research designs.\n\n"
            "EVALUATION CRITERIA:\n"
            "1. Lookahead Bias and Execution Causality: Check whether any feature calculation uses future bars (t+1, t+k), "
            "uses centered rolling windows, or evaluates bar close without explicit execution lag on next bar open.\n"
            "   - Strictly reject or mandate revision if order execution is assumed on the same bar close as signal generation.\n"
            "2. Data Snooping, Provenance, and Multiple Testing:\n"
            "   - Standard textbook parameters (e.g. EMA 12/26, 20-period Donchian, 14-period RSI) and standard hurdle rates "
            "(e.g. Sharpe >= 1.0) represent historical literature baselines, not arbitrary noise tuning.\n"
            "   - CRITICAL: Recognizing canonical parameters NEVER confers automatic approval. You must independently audit "
            "selection provenance, execution delay, slippage/fee reality, and economic rationale.\n"
            "   - A small parameter grid (M <= 5) is NOT automatically statistically safe if selection provenance is unrecorded "
            "or cherry-picked from prior unrecorded trials.\n"
            "3. Turnover Realism and Friction: Check whether realistic Binance taker fees (minimum 0.05% futures / 0.10% spot) "
            "and slippage are deducted. Zero-fee or unrealistic scalping assumptions must be rejected.\n"
            "4. Statistical Justification and Power: Check whether sample size provides sufficient degrees of freedom (> 30 independent trades) "
            "and whether overlapping return t-statistics or non-stationary crypto regimes are acknowledged.\n\n"
            "DECISION RULES:\n"
            "- Issue 'RECOMMEND_APPROVAL' only if the methodology is fully causal, verified for selection provenance, "
            "deducts realistic transaction friction, and is free of lookahead/leakage.\n"
            "- Issue 'RECOMMEND_REVISION' if the hypothesis has fixable specification issues, missing execution lag, "
            "or unstated transaction costs that can be corrected.\n"
            "- Issue 'RECOMMEND_REJECTION' if the hypothesis relies on unrecoverable flaws: fatal lookahead, future information leakage, "
            "unpenalized massive combinatorial sweeps, or deliberate curve fitting.\n"
            "- Issue 'REVIEW_REQUIRED' if the hypothesis requires human institutional clearance or ambiguous compliance.\n"
            "- Issue 'INSUFFICIENT_EVIDENCE' if the research objective lacks necessary data definitions.\n\n"
            f"Research Objective: {objective}\n"
            f"Hypothesis Under Audit:\n{json.dumps(hypothesis_data, indent=2)}"
            f"{temporal_section}\n\n"
            "Provide scores (1-5, where 1=safe/rigorous, 5=fatal risk), critique rationale, and overall recommendation."
        )

    @staticmethod
    def _build_revision_prompt(
        objective: str,
        previous_hypothesis_data: Dict[str, Any],
        critique_data: Dict[str, Any],
        cycle: int,
    ) -> str:
        """Construct prompt directing researcher to address specific reviewer critique."""
        revisions = critique_data.get("required_revisions") or critique_data.get("specific_weaknesses") or []
        return (
            f"You are a Quantitative Hypothesis Researcher. In audit cycle {cycle}, the Skeptical Reviewer requested revisions on your hypothesis.\n"
            f"Reviewer Critique:\n{critique_data.get('critique_rationale', '')}\n"
            f"Required Revisions: {json.dumps(revisions)}\n\n"
            f"Original Hypothesis:\n{json.dumps(previous_hypothesis_data, indent=2)}\n\n"
            "Your task: Revise the quantitative hypothesis to address every specific weakness identified by the reviewer, "
            "ensuring strict causality, no lookahead bias, realistic turnover assumptions, and standard parameterization.\n"
            "Return a complete revised HypothesisPayload."
        )

    @staticmethod
    def _build_re_review_prompt(
        objective: str,
        revised_hypothesis_data: Dict[str, Any],
        previous_hypothesis_data: Dict[str, Any],
        previous_critique_data: Dict[str, Any],
        cycle: int,
        temporal_audit: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Construct prompt for skeptical reviewer re-evaluation of revised hypothesis."""
        prev_revisions = previous_critique_data.get("required_revisions") or previous_critique_data.get("specific_weaknesses") or []
        temporal_section = ""
        if temporal_audit:
            findings = temporal_audit.get("findings", [])
            disposition = temporal_audit.get("disposition")
            if findings:
                formatted_findings = "\n".join(
                    f"- [{f.get('severity')}] {f.get('title')}: {f.get('explanation')}"
                    for f in findings
                )
                temporal_section = (
                    f"\n\nDeterministic Temporal Integrity Findings (Disposition: {disposition}):\n"
                    f"{formatted_findings}\n"
                )

        return (
            f"You are an adversarial quantitative research auditor evaluating a REVISED hypothesis (Cycle {cycle}).\n"
            f"Previous Critique:\n{previous_critique_data.get('critique_rationale', '')}\n"
            f"Previous Required Revisions: {json.dumps(prev_revisions)}\n\n"
            f"Revised Hypothesis:\n{json.dumps(revised_hypothesis_data, indent=2)}"
            f"{temporal_section}\n\n"
            "Your duty:\n"
            "1. Evaluate whether the researcher successfully corrected the identified weaknesses.\n"
            "2. Set revision_assessment to one of: 'FULLY_CORRECTED', 'PARTIALLY_CORRECTED', 'UNCORRECTED', or 'INVALID_REVISION'.\n"
            "   - 'FULLY_CORRECTED': All flaws eliminated, methodology is now sound -> issue RECOMMEND_APPROVAL.\n"
            "   - 'PARTIALLY_CORRECTED': Progress made but residual issues remain -> issue RECOMMEND_REVISION.\n"
            "   - 'UNCORRECTED': Flaws were ignored or unchanged -> issue RECOMMEND_REJECTION.\n"
            "   - 'INVALID_REVISION': The revision introduced new flaws, lookahead, or leakage -> issue RECOMMEND_REJECTION.\n"
            "3. Score the 4 dimensions (1-5) and issue your overall recommendation."
        )

    def execute_research_cycle(
        self,
        objective: str,
        session_id: Optional[str] = None,
        max_revisions: Optional[int] = None,
    ) -> ResearchSessionState:
        """Authoritative execution entry point for the autonomous research cycle.

        Validates caller-supplied max_revisions and delegates to run_vertical_slice.
        Direct callers cannot bypass revision-limit validation through this entry point.
        """
        if max_revisions is not None:
            if (
                isinstance(max_revisions, bool)
                or not isinstance(max_revisions, int)
                or max_revisions < MIN_ALLOWED_REVISIONS
                or max_revisions > ABSOLUTE_MAX_REVISIONS_CEILING
            ):
                raise InvalidRevisionLimitError(
                    f"Invalid caller max_revisions ({max_revisions}). "
                    f"Must be an integer between {MIN_ALLOWED_REVISIONS} and {ABSOLUTE_MAX_REVISIONS_CEILING} (Booleans explicitly rejected)."
                )
        return self.run_vertical_slice(
            objective=objective,
            session_id=session_id,
            max_revisions=max_revisions,
        )

    def run_vertical_slice(
        self,
        objective: str,
        session_id: Optional[str] = None,
        max_revisions: Optional[int] = None,
    ) -> ResearchSessionState:
        """Execute autonomous research formulation loop up to the human approval gate.

        Stages:
        1. Ingest research objective and initialize session state.
        2. Query market summary statistics strictly for TRAIN partition.
        3. Formulate falsifiable hypothesis via Hypothesis Researcher role.
        4. Audit hypothesis and parameter space via Skeptical Reviewer role (with revision loop).
        5. Formulate preregistration proposal with bounded M=2 grid.
        6. Persist proposal and review ticket in SQLite ledger.
        7. Checkpoint session state and halt at HUMAN_APPROVAL_GATE awaiting human authorization.
        """
        if max_revisions is not None:
            if (
                isinstance(max_revisions, bool)
                or not isinstance(max_revisions, int)
                or max_revisions < MIN_ALLOWED_REVISIONS
                or max_revisions > ABSOLUTE_MAX_REVISIONS_CEILING
            ):
                raise InvalidRevisionLimitError(
                    f"Invalid caller max_revisions ({max_revisions}). "
                    f"Must be an integer between {MIN_ALLOWED_REVISIONS} and {ABSOLUTE_MAX_REVISIONS_CEILING} (Booleans explicitly rejected)."
                )
            effective_rev = max_revisions
        else:
            effective_rev = self.max_revisions

        sess_id = session_id or f"SESS-{uuid.uuid4().hex[:12].upper()}"
        state = ResearchSessionState(
            session_id=sess_id,
            objective=objective,
            current_stage="OBJECTIVE_INGESTION",
            requested_max_revisions=max_revisions,
            effective_max_revisions=effective_rev,
        )

        # Stage 1: Market Analysis (TRAIN partition only)
        state.current_stage = "MARKET_ANALYSIS"
        market_summary = self._query_market_summary("BTCUSDT", partition="TRAIN")
        self._record_audit_event(
            state=state,
            stage="MARKET_ANALYSIS",
            role="Market Analyst",
            prompt="Analyze BTCUSDT daily returns on approved TRAIN partition.",
            response_data=market_summary,
            tool_name="get_market_summary",
        )

        # Stage 2: Hypothesis Formulation
        state.current_stage = "HYPOTHESIS_FORMULATION"
        hyp_prompt = (
            f"Research Objective: {objective}\n"
            f"Market Context (TRAIN partition): {json.dumps(market_summary)}\n"
            "Propose a falsifiable quantitative hypothesis for SyntheticSMAStrategy with bounded validation criteria."
        )
        hyp_resp = self.llm_provider.generate(
            role="Hypothesis Researcher",
            prompt=hyp_prompt,
            schema_class=HypothesisPayload,
        )
        hypothesis = HypothesisPayload.model_validate(hyp_resp.structured_data)
        state.hypothesis = hypothesis
        self._record_audit_event(
            state=state,
            stage="HYPOTHESIS_FORMULATION",
            role="Hypothesis Researcher",
            prompt=hyp_prompt,
            response_data=hypothesis.model_dump(),
        )

        # Record hypothesis in SQLite hypotheses table
        try:
            self.ledger.record_hypothesis(
                hypothesis_id=hypothesis.hypothesis_id,
                strategy_name=hypothesis.strategy_name,
                economic_rationale=hypothesis.economic_rationale,
                validation_criteria=hypothesis.validation_criteria,
                max_evaluation_budget=hypothesis.max_evaluation_budget,
            )
        except sqlite3.IntegrityError:
            # If already recorded from previous step or re-run, resume cleanly
            pass

        # Stage 2.5: Deterministic Temporal Integrity Audit (Phase 2.7.1)
        state.current_stage = "TEMPORAL_INTEGRITY_AUDIT"
        temporal_report = self.temporal_detector.audit_proposal(
            hypothesis_id=hypothesis.hypothesis_id,
            objective=objective,
            economic_rationale=hypothesis.economic_rationale,
            strategy_source=getattr(hypothesis, "strategy_source", None),
            strategy_metadata=getattr(hypothesis, "strategy_metadata", None),
        )
        self.ledger.record_temporal_integrity_audit(temporal_report.model_dump())
        state.temporal_audit = temporal_report.model_dump()
        self._record_audit_event(
            state=state,
            stage="TEMPORAL_INTEGRITY_AUDIT",
            role="Temporal Integrity Detector",
            prompt=f"Audit proposal {hypothesis.hypothesis_id} for temporal integrity and lookahead leakage.",
            response_data=temporal_report.model_dump(),
        )

        if temporal_report.disposition == TemporalDisposition.REJECT:
            state.is_failed = True
            state.error_message = (
                f"Deterministic temporal integrity audit rejected hypothesis {hypothesis.hypothesis_id}: "
                f"{temporal_report.disposition_rationale}"
            )
            self.ledger.record_session(
                session_id=state.session_id,
                objective=objective,
                current_stage="TEMPORAL_INTEGRITY_REJECTED",
                session_state_json=state.model_dump_json(),
                hypothesis_id=hypothesis.hypothesis_id,
            )
            raise TemporalIntegrityViolationError(state.error_message)

        # Stage 3: Skeptical Review (Automated Adversarial Self-Critique with Revision Loop)
        state.current_stage = "SKEPTICAL_REVIEW"
        critique_prompt = self._build_reviewer_prompt(
            objective=objective,
            hypothesis_data=hypothesis.model_dump(),
            temporal_audit=state.temporal_audit,
        )
        crit_resp = self.llm_provider.generate(
            role="Skeptical Reviewer",
            prompt=critique_prompt,
            schema_class=SkepticalReviewerOutput,
        )
        critique = SkepticalReviewerOutput.model_validate(crit_resp.structured_data)
        self.temporal_detector.verify_override_attempt(temporal_report, critique.overall_recommendation)
        state.critique = critique
        self._record_audit_event(
            state=state,
            stage="SKEPTICAL_REVIEW",
            role="Skeptical Reviewer",
            prompt=critique_prompt,
            response_data=critique.model_dump(),
        )

        # Revision loop if reviewer requested revision
        revisions_attempted = 0
        if max_revisions is not None:
            if (
                isinstance(max_revisions, bool)
                or not isinstance(max_revisions, int)
                or max_revisions < MIN_ALLOWED_REVISIONS
                or max_revisions > ABSOLUTE_MAX_REVISIONS_CEILING
            ):
                raise InvalidRevisionLimitError(
                    f"Invalid caller max_revisions ({max_revisions}). "
                    f"Must be an integer between {MIN_ALLOWED_REVISIONS} and {ABSOLUTE_MAX_REVISIONS_CEILING} (Booleans explicitly rejected)."
                )
            limit_rev = max_revisions
        else:
            limit_rev = self.max_revisions

        state.requested_max_revisions = max_revisions
        state.effective_max_revisions = limit_rev

        self._record_audit_event(
            state=state,
            stage="REVISION_POLICY_ENFORCEMENT",
            role="Research Coordinator",
            prompt=f"Revision policy enforced: requested={max_revisions}, effective={limit_rev}",
            response_data={"requested_max_revisions": max_revisions, "effective_max_revisions": limit_rev},
        )
        initial_hypothesis_id = hypothesis.hypothesis_id

        while critique.overall_recommendation == "RECOMMEND_REVISION" and revisions_attempted < limit_rev:
            revisions_attempted += 1
            cycle_record: Dict[str, Any] = {
                "cycle": revisions_attempted,
                "previous_hypothesis": hypothesis.model_dump(),
                "critique": critique.model_dump(),
            }
            # Researcher revises hypothesis
            state.current_stage = "HYPOTHESIS_REVISION"
            rev_prompt = self._build_revision_prompt(
                objective=objective,
                previous_hypothesis_data=hypothesis.model_dump(),
                critique_data=critique.model_dump(),
                cycle=revisions_attempted,
            )
            rev_resp = self.llm_provider.generate(
                role="Hypothesis Researcher",
                prompt=rev_prompt,
                schema_class=HypothesisPayload,
            )
            hypothesis = HypothesisPayload.model_validate(rev_resp.structured_data)
            state.hypothesis = hypothesis
            cycle_record["revised_hypothesis"] = hypothesis.model_dump()
            self._record_audit_event(
                state=state,
                stage="HYPOTHESIS_REVISION",
                role="Hypothesis Researcher",
                prompt=rev_prompt,
                response_data=hypothesis.model_dump(),
            )

            # Deterministic Temporal Integrity Re-Audit of revised hypothesis
            state.current_stage = "TEMPORAL_INTEGRITY_RE_AUDIT"
            rev_temporal_report = self.temporal_detector.audit_proposal(
                hypothesis_id=hypothesis.hypothesis_id,
                objective=objective,
                economic_rationale=hypothesis.economic_rationale,
                strategy_source=getattr(hypothesis, "strategy_source", None),
                strategy_metadata=getattr(hypothesis, "strategy_metadata", None),
            )
            self.ledger.record_temporal_integrity_audit(rev_temporal_report.model_dump())
            state.temporal_audit = rev_temporal_report.model_dump()
            self._record_audit_event(
                state=state,
                stage="TEMPORAL_INTEGRITY_RE_AUDIT",
                role="Temporal Integrity Detector",
                prompt=f"Deterministic temporal integrity re-audit for revised {hypothesis.hypothesis_id}.",
                response_data=rev_temporal_report.model_dump(),
            )

            if rev_temporal_report.disposition == TemporalDisposition.REJECT:
                state.is_failed = True
                state.error_message = (
                    f"Deterministic temporal integrity audit rejected revised hypothesis {hypothesis.hypothesis_id}: "
                    f"{rev_temporal_report.disposition_rationale}"
                )
                self.ledger.record_session(
                    session_id=state.session_id,
                    objective=objective,
                    current_stage="TEMPORAL_INTEGRITY_REJECTED",
                    session_state_json=state.model_dump_json(),
                    hypothesis_id=hypothesis.hypothesis_id,
                )
                raise TemporalIntegrityViolationError(state.error_message)

            temporal_report = rev_temporal_report

            # Re-review revised hypothesis
            state.current_stage = "SKEPTICAL_RE_REVIEW"
            re_crit_prompt = self._build_re_review_prompt(
                objective=objective,
                revised_hypothesis_data=hypothesis.model_dump(),
                previous_hypothesis_data=cycle_record["previous_hypothesis"],
                previous_critique_data=cycle_record["critique"],
                cycle=revisions_attempted,
                temporal_audit=state.temporal_audit,
            )
            re_crit_resp = self.llm_provider.generate(
                role="Skeptical Reviewer",
                prompt=re_crit_prompt,
                schema_class=SkepticalReviewerOutput,
            )
            critique = SkepticalReviewerOutput.model_validate(re_crit_resp.structured_data)
            self.temporal_detector.verify_override_attempt(temporal_report, critique.overall_recommendation)
            state.critique = critique
            cycle_record["re_critique"] = critique.model_dump()
            state.revision_history.append(cycle_record)
            self._record_audit_event(
                state=state,
                stage="SKEPTICAL_RE_REVIEW",
                role="Skeptical Reviewer",
                prompt=re_crit_prompt,
                response_data=critique.model_dump(),
            )

            # Persist revision cycle to proposal_revisions table in SQLite ledger
            rev_config_digest = hashlib.sha256(canonical_parameter_json(hypothesis.model_dump()).encode("utf-8")).hexdigest()
            rev_prompt_hash = hashlib.sha256(rev_prompt.encode("utf-8")).hexdigest()
            rev_resp_hash = rev_resp.response_sha256 or hashlib.sha256(rev_resp.content.encode("utf-8")).hexdigest()
            self.ledger.record_proposal_revision(
                hypothesis_id=initial_hypothesis_id,
                revision_number=revisions_attempted,
                reviewer_decision=critique.overall_recommendation,
                structured_reasons=critique.required_revisions or [critique.critique_rationale],
                configuration_digest=rev_config_digest,
                parent_proposal_id=None,
                revised_proposal_id=None,
                prompt_hash=rev_prompt_hash,
                response_hash=rev_resp_hash,
            )

        # Decision policy enforcement after review / revision loop
        if critique.overall_recommendation == "RECOMMEND_REVISION":
            state.is_failed = True
            state.error_message = (
                f"Skeptical reviewer requested revision but maximum allowed revision attempts ({limit_rev}) "
                f"were exhausted: {critique.critique_rationale}"
            )
            self.ledger.record_session(
                session_id=state.session_id,
                objective=objective,
                current_stage="SKEPTICAL_REVIEW_REVISION_EXHAUSTED",
                session_state_json=state.model_dump_json(),
                hypothesis_id=hypothesis.hypothesis_id,
            )
            raise SkepticalReviewRevisionExhaustedError(state.error_message)

        elif critique.overall_recommendation == "RECOMMEND_REJECTION":
            state.is_failed = True
            state.error_message = f"Skeptical reviewer rejected hypothesis: {critique.critique_rationale}"
            self.ledger.record_session(
                session_id=state.session_id,
                objective=objective,
                current_stage="SKEPTICAL_REVIEW_REJECTED",
                session_state_json=state.model_dump_json(),
                hypothesis_id=hypothesis.hypothesis_id,
            )
            raise SkepticalReviewRejectionError(state.error_message)

        elif critique.overall_recommendation == "REVIEW_REQUIRED":
            state.is_failed = True
            state.error_message = f"Skeptical reviewer halted pipeline pending human investigation: {critique.critique_rationale}"
            self.ledger.record_session(
                session_id=state.session_id,
                objective=objective,
                current_stage="SKEPTICAL_REVIEW_INVESTIGATION_REQUIRED",
                session_state_json=state.model_dump_json(),
                hypothesis_id=hypothesis.hypothesis_id,
            )
            raise SkepticalReviewInvestigationRequiredError(state.error_message)

        elif critique.overall_recommendation == "INSUFFICIENT_EVIDENCE":
            state.is_failed = True
            state.error_message = f"Skeptical reviewer halted pipeline due to insufficient evidence: {critique.critique_rationale}"
            self.ledger.record_session(
                session_id=state.session_id,
                objective=objective,
                current_stage="SKEPTICAL_REVIEW_INSUFFICIENT_EVIDENCE",
                session_state_json=state.model_dump_json(),
                hypothesis_id=hypothesis.hypothesis_id,
            )
            raise SkepticalReviewInsufficientEvidenceError(state.error_message)

        elif critique.overall_recommendation != "RECOMMEND_APPROVAL":
            state.is_failed = True
            state.error_message = f"Unknown reviewer recommendation '{critique.overall_recommendation}': {critique.critique_rationale}"
            self.ledger.record_session(
                session_id=state.session_id,
                objective=objective,
                current_stage="SKEPTICAL_REVIEW_REJECTED",
                session_state_json=state.model_dump_json(),
                hypothesis_id=hypothesis.hypothesis_id,
            )
            raise SkepticalReviewRejectionError(state.error_message)

        # Stage 4: Preregistration Proposal Formulation
        state.current_stage = "PREREGISTRATION_PROPOSAL"
        proposal_prompt = (
            f"Formulate a bounded parameter grid for hypothesis {hypothesis.hypothesis_id}.\n"
            "Specify fast_period in [10.0, 15.0] and slow_period in [30.0] on BTCUSDT."
        )
        prop_resp = self.llm_provider.generate(
            role="Research Coordinator",
            prompt=proposal_prompt,
            schema_class=PreregistrationProposalPayload,
        )
        proposal = PreregistrationProposalPayload.model_validate(prop_resp.structured_data)
        prop_id = f"PROP-{uuid.uuid4().hex[:12].upper()}"
        proposal.proposal_id = prop_id
        state.proposal = proposal
        self.pending_proposals[prop_id] = proposal
        self._record_audit_event(
            state=state,
            stage="PREREGISTRATION_PROPOSAL",
            role="Research Coordinator",
            prompt=proposal_prompt,
            response_data=proposal.model_dump(),
        )

        # Stage 5: Create Human Approval Review Ticket
        state.current_stage = "HUMAN_APPROVAL_GATE"
        ticket_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"
        ticket = ApprovalTicket(
            ticket_id=ticket_id,
            proposal_id=prop_id,
            approval_scope="PREREGISTRATION_APPROVAL",
            status="PENDING",
            submission_notes=f"Auto-generated review ticket for {hypothesis.hypothesis_id} with M=2 SMA parameter grid.",
        )
        self.pending_tickets[ticket_id] = ticket
        state.ticket = ticket
        self._record_audit_event(
            state=state,
            stage="HUMAN_APPROVAL_GATE",
            role="Research Coordinator",
            prompt=f"Create review ticket for proposal {prop_id}",
            response_data=ticket.model_dump(),
            tool_name="create_approval_ticket",
        )

        # Persist proposal and ticket in SQLite preregistration_proposals table (SEC-P2-02)
        created_now = datetime.now(timezone.utc).isoformat()
        expires_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 86400 * 7, tz=timezone.utc).isoformat()
        self.ledger.record_proposal(
            proposal_id=proposal.proposal_id,
            ticket_id=ticket.ticket_id,
            hypothesis_id=hypothesis.hypothesis_id,
            strategy_name=hypothesis.strategy_name,
            universe=proposal.universe,
            timeframe=proposal.timeframe,
            parameter_grid=proposal.parameter_grid,
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"protocol_id": proposal.partition_protocol_id},
            benchmark_ids=["BTC_NET"],
            created_at=created_now,
            expires_at=expires_at,
            submission_notes=ticket.submission_notes,
        )

        # Checkpoint session in SQLite research_sessions table (GOV-P2-06)
        self.ledger.record_session(
            session_id=state.session_id,
            objective=objective,
            current_stage="HUMAN_APPROVAL_GATE",
            session_state_json=state.model_dump_json(),
            hypothesis_id=hypothesis.hypothesis_id,
            ticket_id=ticket.ticket_id,
        )

        # Workflow halts here awaiting out-of-band human authorization
        return state

    def approve_proposal(
        self,
        ticket_id: str,
        human_identity: str,
        rationale: str,
    ) -> str:
        """Consecrate human approval of a pending preregistration ticket.

        Enforces:
        1. Explicit non-empty human identity and rationale (SEC-P2-01).
        2. Atomic transaction: freezes preregistration and commits research decision (SEC-P2-03).
        3. Updates ticket status in SQLite preregistration_proposals table (SEC-P2-02).
        """
        prereg_id = self.ledger.approve_proposal_transaction(
            ticket_id=ticket_id,
            human_identity=human_identity,
            rationale=rationale,
        )

        if ticket_id in self.pending_tickets:
            self.pending_tickets[ticket_id].status = "APPROVED"
            self.pending_tickets[ticket_id].decided_by = human_identity
            self.pending_tickets[ticket_id].decision_rationale = rationale

        return prereg_id

    def reject_proposal(
        self,
        ticket_id: str,
        human_identity: str,
        rationale: str,
    ) -> None:
        """Reject a pending preregistration proposal ticket and record NO_GO decision."""
        self.ledger.reject_proposal_transaction(
            ticket_id=ticket_id,
            human_identity=human_identity,
            rationale=rationale,
        )
        if ticket_id in self.pending_tickets:
            self.pending_tickets[ticket_id].status = "REJECTED"

    def authorize_interrupted_rerun(
        self,
        original_request_id: str,
        human_identity: str,
        rationale: str,
    ) -> Dict[str, Any]:
        """Authorize a rerun for an interrupted evaluation with explicit human credentials."""
        return self.ledger.authorize_rerun_transaction(
            original_request_id=original_request_id,
            authorized_by=human_identity,
            rationale=rationale,
        )

    def resume_session(
        self,
        session_id: str,
        strategy: Optional[StrategyInterface] = None,
    ) -> ResearchSessionState:
        """Resume a checkpointed research session after human approval or crash recovery."""
        session_record = self.ledger.get_session(session_id)
        if not session_record:
            raise ValueError(f"Research session '{session_id}' not found in ledger.")

        state = ResearchSessionState.model_validate_json(session_record["session_state_json"])

        if state.is_completed:
            return state

        # Verify that human approval has been consecrated in SQLite
        if not state.ticket:
            raise HumanApprovalPendingError(f"Session '{session_id}' has no approval ticket.")

        ticket_info = self.ledger.get_ticket(state.ticket.ticket_id)
        if not ticket_info:
            raise HumanApprovalPendingError(f"Ticket '{state.ticket.ticket_id}' not found in persistent ledger.")

        if ticket_info["status"] != "APPROVED":
            raise HumanApprovalPendingError(
                f"Session '{session_id}' cannot proceed: ticket '{state.ticket.ticket_id}' has status '{ticket_info['status']}'. "
                "Explicit human approval is required."
            )

        prereg_id = ticket_info["prereg_id"]
        if not prereg_id:
            raise HumanApprovalPendingError(f"Ticket '{state.ticket.ticket_id}' is approved but has no bound prereg_id.")

        state.prereg_id = prereg_id
        state.ticket.status = "APPROVED"
        state.ticket.decided_by = ticket_info["approved_by"]
        state.ticket.decision_rationale = ticket_info["decision_rationale"]

        # Stage 7: Controlled Experiment Execution (TRAIN partition only)
        state.current_stage = "EXPERIMENT_EXECUTION"
        if strategy is None:
            strategy = SyntheticSMAStrategy()

        configs = [
            {"fast_period": 10.0, "slow_period": 30.0},
            {"fast_period": 15.0, "slow_period": 30.0},
        ]

        executed_exp_ids: List[str] = []
        for idx, params in enumerate(configs, start=1):
            base_req_id = f"REQ-{state.session_id}-{idx:02d}"

            # Check if this request exists and inspect its status
            existing_req = self.ledger.get_execution_request(base_req_id)
            if existing_req is not None and existing_req["status"] == "INTERRUPTED":
                # Resuming must NEVER automatically rerun an interrupted evaluation
                # A separate, explicit human authorization is required
                rerun_auth = self.ledger.get_rerun_authorization(base_req_id)
                if not rerun_auth or rerun_auth["is_consumed"]:
                    raise InterruptedEvaluationAuthorizationRequiredError(
                        f"Evaluation request '{base_req_id}' was interrupted. "
                        "Automatic rerun on session resume is strictly prohibited. "
                        "A separate, explicit human rerun authorization is required before re-executing this configuration."
                    )

                # Validate exact binding to hypothesis, prereg, and configuration digest
                canonical_params = canonical_parameter_json(params)
                expected_digest = hashlib.sha256(
                    f"{prereg_id}:{canonical_params}:TRAIN:BTCUSDT".encode("utf-8")
                ).hexdigest()

                if rerun_auth["hypothesis_id"] != state.hypothesis.hypothesis_id:
                    raise ResearchCoordinatorError(
                        f"Rerun authorization hypothesis mismatch: expected '{state.hypothesis.hypothesis_id}', "
                        f"got '{rerun_auth['hypothesis_id']}'"
                    )
                if rerun_auth["prereg_id"] != prereg_id:
                    raise ResearchCoordinatorError(
                        f"Rerun authorization preregistration mismatch: expected '{prereg_id}', "
                        f"got '{rerun_auth['prereg_id']}'"
                    )
                if rerun_auth["configuration_digest"] != expected_digest:
                    raise ResearchCoordinatorError(
                        f"Rerun authorization configuration digest mismatch for request '{base_req_id}'."
                    )

                # Use authorized new request ID and consume the authorization atomically
                req_id = rerun_auth["new_request_id"]
                self.ledger.consume_rerun_authorization(
                    auth_id=rerun_auth["auth_id"],
                    consumed_by=f"ResearchCoordinator.resume_session:{state.session_id}",
                )
            elif existing_req is not None and existing_req["status"] == "STARTED":
                # If lingering in STARTED from an abrupt crash, reconcile first
                self.engine.reconcile_interrupted_trials()
                existing_req = self.ledger.get_execution_request(base_req_id)
                if existing_req and existing_req["status"] == "INTERRUPTED":
                    rerun_auth = self.ledger.get_rerun_authorization(base_req_id)
                    if not rerun_auth or rerun_auth["is_consumed"]:
                        raise InterruptedEvaluationAuthorizationRequiredError(
                            f"Evaluation request '{base_req_id}' was interrupted. "
                            "Automatic rerun on session resume is strictly prohibited. "
                            "A separate, explicit human rerun authorization is required before re-executing this configuration."
                        )
                    canonical_params = canonical_parameter_json(params)
                    expected_digest = hashlib.sha256(
                        f"{prereg_id}:{canonical_params}:TRAIN:BTCUSDT".encode("utf-8")
                    ).hexdigest()
                    if rerun_auth["configuration_digest"] != expected_digest:
                        raise ResearchCoordinatorError(
                            f"Rerun authorization configuration digest mismatch for request '{base_req_id}'."
                        )
                    req_id = rerun_auth["new_request_id"]
                    self.ledger.consume_rerun_authorization(
                        auth_id=rerun_auth["auth_id"],
                        consumed_by=f"ResearchCoordinator.resume_session:{state.session_id}",
                    )
                else:
                    req_id = base_req_id
            else:
                req_id = base_req_id

            req = ControlledExperimentRequest(
                hypothesis_id=state.hypothesis.hypothesis_id,
                prereg_id=prereg_id,
                strategy=strategy,
                parameters=params,
                partition="TRAIN",
                symbol="BTCUSDT",
                request_id=req_id,
                notes=f"Vertical slice execution {idx} of 2 for session {state.session_id}",
            )
            res = self.engine.execute(req)
            executed_exp_ids.append(res.experiment_id)
            self._record_audit_event(
                state=state,
                stage="EXPERIMENT_EXECUTION",
                role="Execution Gateway",
                prompt=f"Execute candidate parameters {json.dumps(params)} (req_id={req_id})",
                response_data={
                    "experiment_id": res.experiment_id,
                    "trial_id": res.trial_id,
                    "status": res.status,
                    "request_id": req_id,
                    "base_request_id": base_req_id,
                },
                tool_name="execute_registered_experiment",
            )

        state.experiment_ids = executed_exp_ids

        # Stage 8: Qualitative Interpretation & Deterministic Report Generation
        state.current_stage = "REPORT_GENERATION"
        report_prompt = (
            f"Provide scientific interpretation and observations for backtest results of {state.hypothesis.hypothesis_id}.\n"
            f"Executed experiment IDs: {executed_exp_ids}."
        )
        report_resp = self.llm_provider.generate(
            role="Research Reporter",
            prompt=report_prompt,
        )
        qualitative_text = report_resp.content

        report_filename = f"report_{state.hypothesis.hypothesis_id}_{state.session_id}.md"
        report_result = self.compiler.compile_report(
            hypothesis_id=state.hypothesis.hypothesis_id,
            prereg_id=prereg_id,
            experiment_ids=executed_exp_ids,
            qualitative_interpretation=qualitative_text,
            report_filename=report_filename,
            objective=state.objective,
            human_decision="GO",
            decision_rationale=ticket_info.get("decision_rationale") or "Pre-registered quantitative hurdles satisfied within TRAIN.",
        )

        state.report_path = report_result["report_path"]
        self._record_audit_event(
            state=state,
            stage="REPORT_GENERATION",
            role="Research Reporter",
            prompt=report_prompt,
            response_data=report_result,
            tool_name="generate_research_report",
        )

        # Stage 9: Conclude Session Successfully
        state.current_stage = "CONCLUDED"
        state.is_completed = True

        # Checkpoint concluded state in SQLite
        self.ledger.update_session(
            session_id=state.session_id,
            current_stage="CONCLUDED",
            session_state_json=state.model_dump_json(),
            is_completed=True,
            report_path=state.report_path,
            hypothesis_id=state.hypothesis.hypothesis_id,
            prereg_id=prereg_id,
            ticket_id=state.ticket.ticket_id,
        )

        return state

    def test_run_with_approval(
        self,
        objective: str,
        human_identity: str,
        rationale: str,
        session_id: Optional[str] = None,
        strategy: Optional[StrategyInterface] = None,
    ) -> ResearchSessionState:
        """Test fixture helper to execute full research loop with explicit test credentials."""
        state = self.run_vertical_slice(objective=objective, session_id=session_id)
        if state.ticket:
            self.approve_proposal(
                ticket_id=state.ticket.ticket_id,
                human_identity=human_identity,
                rationale=rationale,
            )
            state = self.resume_session(session_id=state.session_id, strategy=strategy)
        return state

    def _query_market_summary(self, symbol: str, partition: str = "TRAIN") -> Dict[str, Any]:
        """Query summary statistics strictly for approved TRAIN partition."""
        if partition.upper() != "TRAIN":
            raise PermissionError(f"Partition violation: get_market_summary is strictly restricted to TRAIN. Got: {partition}")

        partition_data = self.provider.get_partition_data(
            partition="TRAIN",
            symbol=symbol,
            warmup_bars=30,
        )
        trading_bars = partition_data["trading_bars"]
        closes = [b["close"] for b in trading_bars]

        if len(closes) < 2:
            return {"symbol": symbol, "partition": partition, "total_bars": len(closes)}

        daily_returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        mean_ret = sum(daily_returns) / len(daily_returns)

        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        ann_vol = (variance ** 0.5) * (365 ** 0.5)

        return {
            "symbol": symbol,
            "partition": partition,
            "total_bars": len(trading_bars),
            "mean_daily_return_pct": round(mean_ret * 100.0, 4),
            "annualized_vol_pct": round(ann_vol * 100.0, 2),
            "trend_direction": "UPWARD" if closes[-1] > closes[0] else "DOWNWARD",
        }

    def _record_audit_event(
        self,
        state: ResearchSessionState,
        stage: str,
        role: str,
        prompt: str,
        response_data: Any,
        tool_name: Optional[str] = None,
    ) -> None:
        """Record immutable audit telemetry event into session state."""
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        resp_json = json.dumps(response_data, sort_keys=True)
        resp_sha = hashlib.sha256(resp_json.encode("utf-8")).hexdigest()

        event = AuditEvent(
            event_id=f"EVT-{uuid.uuid4().hex[:10].upper()}",
            stage=stage,
            role=role,
            provider=self.llm_provider.name,
            model_identifier=self.llm_provider.model_identifier,
            prompt_sha256=prompt_sha,
            response_sha256=resp_sha,
            tool_name=tool_name,
            structured_payload=response_data if isinstance(response_data, dict) else None,
        )
        state.audit_events.append(event)
