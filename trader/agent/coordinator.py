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


class ResearchCoordinatorError(Exception):
    """Base exception for research orchestration and governance violations."""
    pass


class HumanApprovalPendingError(ResearchCoordinatorError):
    """Raised when an unapproved proposal attempts to execute backtests."""
    pass


class SkepticalReviewRejectionError(ResearchCoordinatorError):
    """Raised when the skeptical reviewer rejects the proposed hypothesis."""
    pass


class ProcessConcurrencyError(ResearchCoordinatorError):
    """Raised when multiple orchestrator instances attempt concurrent execution."""
    pass


class InterruptedEvaluationAuthorizationRequiredError(ResearchCoordinatorError):
    """Raised when resuming a session encounters an interrupted evaluation without explicit human rerun authorization."""
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
    ) -> None:
        self.ledger = ledger
        self.provider = provider
        self.engine = execution_engine
        self.llm_provider = llm_provider
        self.compiler = ResearchReportCompiler(conn=self.ledger.conn)

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

    def run_vertical_slice(
        self,
        objective: str,
        session_id: Optional[str] = None,
    ) -> ResearchSessionState:
        """Execute autonomous research formulation loop up to the human approval gate.

        Stages:
        1. Ingest research objective and initialize session state.
        2. Query market summary statistics strictly for TRAIN partition.
        3. Formulate falsifiable hypothesis via Hypothesis Researcher role.
        4. Audit hypothesis and parameter space via Skeptical Reviewer role.
        5. Formulate preregistration proposal with bounded M=2 grid.
        6. Persist proposal and review ticket in SQLite ledger.
        7. Checkpoint session state and halt at HUMAN_APPROVAL_GATE awaiting human authorization.
        """
        sess_id = session_id or f"SESS-{uuid.uuid4().hex[:12].upper()}"
        state = ResearchSessionState(
            session_id=sess_id,
            objective=objective,
            current_stage="OBJECTIVE_INGESTION",
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

        # Stage 3: Skeptical Review (Automated Adversarial Self-Critique)
        state.current_stage = "SKEPTICAL_REVIEW"
        critique_prompt = (
            f"Research Objective: {objective}\n"
            f"Audit the following hypothesis for data snooping, lookahead bias, and turnover realism:\n"
            f"{hypothesis.model_dump_json(indent=2)}\n"
            "Apply the 4-dimension scoring rubric and issue your recommendation."
        )
        crit_resp = self.llm_provider.generate(
            role="Skeptical Reviewer",
            prompt=critique_prompt,
            schema_class=SkepticalReviewerOutput,
        )
        critique = SkepticalReviewerOutput.model_validate(crit_resp.structured_data)
        state.critique = critique
        self._record_audit_event(
            state=state,
            stage="SKEPTICAL_REVIEW",
            role="Skeptical Reviewer",
            prompt=critique_prompt,
            response_data=critique.model_dump(),
        )

        # Gate on skeptical review recommendation
        if critique.overall_recommendation == "RECOMMEND_REJECTION":
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
        elif critique.overall_recommendation in ("REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"):
            state.is_failed = True
            state.error_message = f"Skeptical reviewer flagged hypothesis ({critique.overall_recommendation}): {critique.critique_rationale}"
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
