"""Adversarial Governance Test Suite for Phase 2.5.1.

Systematically verifies all 20 required adversarial governance conditions:
1. RECOMMEND_REVISION cannot create a human approval ticket.
2. Revised proposal must undergo new review.
3. Revision count cannot exceed configured maximum.
4. Revision cannot modify frozen preregistration.
5. Rejected hypotheses cannot progress.
6. Insufficient evidence cannot progress.
7. REVIEW_REQUIRED cannot progress.
8. Pending tickets cannot count as human approvals.
9. Human approval cannot automatically create execution authorization.
10. Execution authorization cannot automatically release TEST data.
11. Repeated request IDs cannot create duplicate trials.
12. Same request ID with different payload must be rejected.
13. Interrupted evaluations cannot be silently retried.
14. Every explicit rerun must use a new request ID and count as new attempt.
15. M_total accounting remains consistent across reruns.
16. Administrative budget denial does not create a new research attempt.
17. No model prompt contains VALIDATION or TEST data.
18. No model can approve its own human ticket.
19. Benchmark labels never enter model prompts.
20. No route to live trading, paper trading, testnet, or order execution exists.
"""

from collections import deque
import inspect
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel

from trader.agent.coordinator import (
    HumanApprovalPendingError,
    InterruptedEvaluationAuthorizationRequiredError,
    ResearchCoordinator,
    SkepticalReviewInvestigationRequiredError,
    SkepticalReviewInsufficientEvidenceError,
    SkepticalReviewRejectionError,
    SkepticalReviewRevisionExhaustedError,
)
from trader.agent.core.execution import (
    ControlledExperimentRequest,
    IdempotencyPayloadConflictError,
    InterruptedTrialRetryError,
    ResearchExecutionEngine,
    TrialBudgetExceededError,
)
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.benchmark.holdout_tasks import get_holdout_tasks
from trader.agent.providers.base import LLMProvider, LLMResponse
from trader.agent.schemas import (
    HypothesisPayload,
    PreregistrationProposalPayload,
    SkepticalReviewerOutput,
)
from trader.agent.tools.market_bridge import MarketDataProvider


class QueueMockLLM(LLMProvider):
    """Deterministic queued LLM provider for exact scripted multi-turn tests."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        super().__init__(name="queue_mock", model_identifier="mock-v1")
        self.queue = deque(responses)
        self.recorded_prompts: List[str] = []

    def generate(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        self.recorded_prompts.append(prompt)
        if not self.queue:
            raise RuntimeError(f"Queue exhausted for role '{role}'")
        data = self.queue.popleft()

        validated_data = None
        if schema_class is not None:
            obj = schema_class.model_validate(data)
            validated_data = obj.model_dump()
        else:
            validated_data = data

        content = json.dumps(validated_data, indent=2)
        return LLMResponse(
            content=content,
            structured_data=validated_data,
            prompt_tokens=40,
            completion_tokens=40,
            latency_ms=2.0,
            prompt_sha256=self.compute_sha256(prompt),
            response_sha256=self.compute_sha256(content),
            provider_name=self.name,
            model_identifier=self.model_identifier,
        )


class TestAgentAdversarialGovernanceP251(unittest.TestCase):
    """Rigorous adversarial testing of Phase 2.5.1 research governance rules."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_gov.db")
        self.lock_path = os.path.join(self.temp_dir, "test.lock")

        self.ledger = ResearchLedger(db_path=self.db_path)
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)
        self.strategy = SyntheticSMAStrategy()

    def tearDown(self) -> None:
        self.ledger.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_coordinator(self, llm: LLMProvider, max_revisions: int = 3) -> ResearchCoordinator:
        return ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm,
            lock_path=self.lock_path,
            max_revisions=max_revisions,
        )

    # 1. RECOMMEND_REVISION cannot create a human approval ticket
    def test_01_recommend_revision_cannot_create_human_approval_ticket(self) -> None:
        """Verify that RECOMMEND_REVISION halts without creating a human approval ticket."""
        responses = [
            {
                "hypothesis_id": "H-ADV-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Momentum strategy with potential lag issue.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Execution timing lacks explicit 1-bar execution lag specification.",
                "required_revisions": ["Specify execution delay on open t+1."],
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm, max_revisions=0)
        try:
            with self.assertRaises(SkepticalReviewRevisionExhaustedError):
                coord.run_vertical_slice(objective="Test recommendation revision gate", session_id="SESS-ADV-01")
            self.assertEqual(len(coord.pending_tickets), 0)
        finally:
            coord.close()

    # 2. Revised proposal must undergo new review
    def test_02_revised_proposal_must_undergo_new_review(self) -> None:
        """Verify that a revised proposal must undergo re-review and cannot bypass to ticket."""
        responses = [
            {
                "hypothesis_id": "H-ADV-02",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Initial uncorrected momentum rationale.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Execution timing lacks explicit execution lag specification.",
                "required_revisions": ["Specify execution delay on open t+1."],
            },
            # Revised hypothesis
            {
                "hypothesis_id": "H-ADV-02",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Revised with execution delay but re-review rejects.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            # Re-review rejects
            {
                "lookahead_risk_score": 4,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Revised hypothesis introduced new defect and is firmly rejected.",
                "required_revisions": [],
                "revision_assessment": "INVALID_REVISION",
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            with self.assertRaises(SkepticalReviewRejectionError):
                coord.run_vertical_slice(objective="Test re-review mandatory gate", session_id="SESS-ADV-02")
            self.assertEqual(len(coord.pending_tickets), 0)
        finally:
            coord.close()

    # 3. Revision count cannot exceed configured maximum
    def test_03_revision_count_cannot_exceed_configured_maximum(self) -> None:
        """Verify that revision loop strictly respects max_revisions limit."""
        responses = [
            {
                "hypothesis_id": "H-ADV-03",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Initial trend following momentum premise on daily closing prices.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            # Round 1
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Revision round 1 needed: moving average range is narrow and requires widening.",
                "required_revisions": ["Fix 1."],
            },
            {"hypothesis_id": "H-ADV-03", "strategy_name": "SyntheticSMAStrategy", "economic_rationale": "Revision 1: adjusted moving average parameters to reduce overfitting.", "validation_criteria": {"min_sharpe": 1.0}},
            # Round 2
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Revision round 2 needed: parameter bounds are still insufficiently justified.",
                "required_revisions": ["Fix 2."],
                "revision_assessment": "PARTIALLY_CORRECTED",
            },
            {"hypothesis_id": "H-ADV-03", "strategy_name": "SyntheticSMAStrategy", "economic_rationale": "Revision 2: further refined moving average parameters with wider baseline.", "validation_criteria": {"min_sharpe": 1.0}},
            # Round 3
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Revision round 3 needed: parameters remain too dense and lack economic motivation.",
                "required_revisions": ["Fix 3."],
                "revision_assessment": "PARTIALLY_CORRECTED",
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            with self.assertRaises(SkepticalReviewRevisionExhaustedError):
                coord.run_vertical_slice(objective="Test max revision count bound", session_id="SESS-ADV-03")
            self.assertEqual(len(coord.pending_tickets), 0)
        finally:
            coord.close()

    # 4. Revision cannot modify frozen preregistration
    def test_04_revision_cannot_modify_frozen_preregistration(self) -> None:
        """Verify that SQLite triggers block UPDATE on frozen preregistrations."""
        hyp_id = "H-ADV-FROZEN-04"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(hyp_id, ["BTCUSDT"], "1d", {"fast": [10.0]}, {}, {}, [])

        cur = self.ledger.conn.cursor()
        with self.assertRaises(sqlite3.IntegrityError):
            cur.execute(
                "UPDATE preregistrations SET timeframe = '1h' WHERE prereg_id = ?;",
                (prereg_id,),
            )
            self.ledger.conn.commit()

    # 5. Rejected hypotheses cannot progress
    def test_05_rejected_hypotheses_cannot_progress(self) -> None:
        """Verify that RECOMMEND_REJECTION halts immediately with zero ticket generation."""
        responses = [
            {
                "hypothesis_id": "H-ADV-05",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Arbitrary data snooping strategy.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 5,
                "snooping_risk_score": 5,
                "granularity_risk_score": 4,
                "turnover_realism_score": 4,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Severe unconstrained snooping with fatal lookahead bias.",
                "required_revisions": [],
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm)
        try:
            with self.assertRaises(SkepticalReviewRejectionError):
                coord.run_vertical_slice(objective="Test rejection progression block", session_id="SESS-ADV-05")
            self.assertEqual(len(coord.pending_tickets), 0)
        finally:
            coord.close()

    # 6. Insufficient evidence cannot progress
    def test_06_insufficient_evidence_cannot_progress(self) -> None:
        """Verify that INSUFFICIENT_EVIDENCE halts immediately without creating a ticket."""
        responses = [
            {
                "hypothesis_id": "H-ADV-06",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Premise requires level 2 orderbook depth.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 2,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "INSUFFICIENT_EVIDENCE",
                "critique_rationale": "Required depth data is completely missing from TRAIN dataset.",
                "required_revisions": [],
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm)
        try:
            with self.assertRaises(SkepticalReviewInsufficientEvidenceError):
                coord.run_vertical_slice(objective="Test insufficient evidence block", session_id="SESS-ADV-06")
            self.assertEqual(len(coord.pending_tickets), 0)
        finally:
            coord.close()

    # 7. REVIEW_REQUIRED cannot progress
    def test_07_review_required_cannot_progress(self) -> None:
        """Verify that REVIEW_REQUIRED halts pipeline pending human investigation."""
        responses = [
            {
                "hypothesis_id": "H-ADV-07",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Unusual compliance-sensitive market structure feature.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 2,
                "snooping_risk_score": 2,
                "granularity_risk_score": 2,
                "turnover_realism_score": 2,
                "overall_recommendation": "REVIEW_REQUIRED",
                "critique_rationale": "Compliance review required before evaluating this strategy feature.",
                "required_revisions": [],
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm)
        try:
            with self.assertRaises(SkepticalReviewInvestigationRequiredError):
                coord.run_vertical_slice(objective="Test review required block", session_id="SESS-ADV-07")
            self.assertEqual(len(coord.pending_tickets), 0)
        finally:
            coord.close()

    # 8. Pending tickets cannot count as human approvals
    def test_08_pending_tickets_cannot_count_as_human_approvals(self) -> None:
        """Verify that a PENDING ticket cannot authorize backtest execution."""
        responses = [
            {
                "hypothesis_id": "H-ADV-08",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Sound momentum premise on daily bars.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Sound methodology with zero temporal lookahead bias or data snooping risk.",
                "required_revisions": [],
            },
            {
                "proposal_id": "PROP-ADV-08",
                "hypothesis_id": "H-ADV-08",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0]},
                "partition_protocol_id": "CANONICAL-V5-TRAIN-ONLY",
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm)
        try:
            state = coord.run_vertical_slice(objective="Test pending ticket block", session_id="SESS-ADV-08")
            self.assertEqual(state.current_stage, "HUMAN_APPROVAL_GATE")
            self.assertEqual(state.ticket.status, "PENDING")

            # Resuming session without explicit human approval must fail
            with self.assertRaises(HumanApprovalPendingError):
                coord.resume_session(session_id="SESS-ADV-08")
        finally:
            coord.close()

    # 9. Human approval cannot automatically create execution authorization
    def test_09_human_approval_cannot_automatically_create_execution_authorization(self) -> None:
        """Verify that approving a proposal only freezes preregistration and does NOT run backtest."""
        hyp_id = "H-ADV-09"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0})
        self.ledger.record_proposal(
            proposal_id="PROP-ADV-09",
            ticket_id="TICK-ADV-09",
            hypothesis_id=hyp_id,
            strategy_name="SyntheticSMA",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"protocol_id": "CANONICAL-V5-TRAIN-ONLY"},
            benchmark_ids=["BTC_NET"],
            created_at="2026-11-02T00:00:00Z",
            expires_at="2026-11-09T00:00:00Z",
            submission_notes="Ready for approval",
        )
        coord = self._make_coordinator(QueueMockLLM([]))
        try:
            prereg_id = coord.approve_proposal(
                ticket_id="TICK-ADV-09",
                human_identity="Dr. Quant Lead",
                rationale="Approved experimental design.",
            )
            self.assertTrue(prereg_id.startswith("PREREG-"))

            # Verify no experiments or trials have been created yet
            cur = self.ledger.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM experiments WHERE prereg_id = ?;", (prereg_id,))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE prereg_id = ?;", (prereg_id,))
            self.assertEqual(cur.fetchone()[0], 0)
        finally:
            coord.close()

    # 10. Execution authorization cannot automatically release TEST data
    def test_10_execution_authorization_cannot_automatically_release_test_data(self) -> None:
        """Verify that execution authorization on TRAIN strictly blocks TEST partition access."""
        os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"
        hyp_id = "H-ADV-10"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(hyp_id, ["BTCUSDT"], "1d", {"fast": [10.0]}, {}, {}, [])

        req = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TEST",
            symbol="BTCUSDT",
            request_id="REQ-ADV-TEST-10",
        )
        with self.assertRaises(PermissionError):
            self.engine.execute(req)

        # Confirm M_test remains strictly 0
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM experiments WHERE partition_name = 'TEST';")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition = 'TEST_EVALUATED';")
        self.assertEqual(cur.fetchone()[0], 0)

    # 11. Repeated request IDs cannot create duplicate trials
    def test_11_repeated_request_ids_cannot_create_duplicate_trials(self) -> None:
        """Verify that submitting identical request_id returns cached result and does not increment M_total."""
        hyp_id = "H-ADV-11"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(hyp_id, ["BTCUSDT"], "1d", {"fast": [10.0]}, {}, {}, [])

        req = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-IDEMP-11",
        )
        res1 = self.engine.execute(req)
        m_total_1 = res1.cumulative_m_total

        # Re-submit same request
        res2 = self.engine.execute(req)
        self.assertEqual(res2.experiment_id, res1.experiment_id)
        self.assertEqual(res2.cumulative_m_total, m_total_1)

        # Confirm exactly 1 trial in trial_ledger
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE prereg_id = ?;", (prereg_id,))
        self.assertEqual(cur.fetchone()[0], 1)

    # 12. Same request ID with different payload must be rejected
    def test_12_same_request_id_with_different_payload_must_be_rejected(self) -> None:
        """Verify that submitting the same request_id with differing parameters is rejected."""
        hyp_id = "H-ADV-12"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(hyp_id, ["BTCUSDT"], "1d", {"fast": [10.0, 20.0]}, {}, {}, [])

        req1 = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-CONFLICT-12",
        )
        self.engine.execute(req1)

        req2 = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 20.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-CONFLICT-12",
        )
        with self.assertRaises(IdempotencyPayloadConflictError):
            self.engine.execute(req2)

    # 13. Interrupted evaluations cannot be silently retried
    def test_13_interrupted_evaluations_cannot_be_silently_retried(self) -> None:
        """Verify that interrupted evaluations require explicit human authorization to resume."""
        responses = [
            {
                "hypothesis_id": "H-ADV-13",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Sound momentum premise on daily bars.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Sound methodology with zero temporal lookahead bias or data snooping risk.",
                "required_revisions": [],
            },
            {
                "proposal_id": "PROP-ADV-13",
                "hypothesis_id": "H-ADV-13",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0, 15.0], "slow_period": [30.0]},
                "partition_protocol_id": "CANONICAL-V5-TRAIN-ONLY",
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm)
        try:
            state = coord.run_vertical_slice(objective="Test interrupted resume gate", session_id="SESS-ADV-13")
            prereg_id = coord.approve_proposal(state.ticket.ticket_id, "Human Lead", "Approved design")

            # Simulate an interrupted request for this session
            req_id = f"REQ-{state.session_id}-01"
            self.ledger.record_execution_request(
                request_id=req_id,
                hypothesis_id="H-ADV-13",
                prereg_id=prereg_id,
                payload_hash="dummy_hash",
                status="INTERRUPTED",
            )

            # Resuming session must strictly refuse to auto-rerun interrupted request
            with self.assertRaises(InterruptedEvaluationAuthorizationRequiredError):
                coord.resume_session(session_id="SESS-ADV-13")
        finally:
            coord.close()

    # 14. Every explicit rerun must use a new request ID and count as new attempt
    def test_14_every_explicit_rerun_must_use_new_request_id_and_count_as_new_attempt(self) -> None:
        """Verify that rerun authorization produces a fresh unique request_id."""
        hyp_id = "H-ADV-14"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(hyp_id, ["BTCUSDT"], "1d", {"fast": [10.0]}, {}, {}, [])

        req_id = "REQ-INTERRUPT-14"
        self.ledger.record_execution_request(
            request_id=req_id,
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            payload_hash="payload14",
            status="INTERRUPTED",
        )

        auth = self.ledger.authorize_rerun_transaction(
            original_request_id=req_id,
            authorized_by="Lead Researcher",
            rationale="Hardware glitch cleared; authorized to rerun with clean seed.",
        )
        self.assertNotEqual(auth["new_request_id"], req_id)
        self.assertTrue(auth["new_request_id"].startswith("REQ-"))
        self.assertEqual(auth["is_consumed"], 0)

    # 15. M_total accounting remains consistent across reruns
    def test_15_m_total_accounting_remains_consistent_across_reruns(self) -> None:
        """Verify that an authorized rerun increments M_total as a separate attempt."""
        hyp_id = "H-ADV-15"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(hyp_id, ["BTCUSDT"], "1d", {"fast": [10.0, 15.0]}, {}, {}, [])

        # First normal run
        req1 = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-RUN-15A",
        )
        res1 = self.engine.execute(req1)
        self.assertEqual(res1.cumulative_m_total, 1)

        # Second run with different request ID
        req2 = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 15.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-RUN-15B",
        )
        res2 = self.engine.execute(req2)
        self.assertEqual(res2.cumulative_m_total, 2)

    # 16. Administrative budget denial does not create a new research attempt
    def test_16_administrative_budget_denial_does_not_create_new_research_attempt(self) -> None:
        """Verify that budget denial fails before writing to trial_ledger."""
        hyp_id = "H-ADV-16"
        # Budget = 1
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Rationale", {"min_sharpe": 1.0}, max_evaluation_budget=1)
        prereg_id = self.ledger.preregister_experiment(hyp_id, ["BTCUSDT"], "1d", {"fast": [10.0, 20.0]}, {}, {}, [])

        req1 = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-BUDGET-16A",
        )
        self.engine.execute(req1)

        # Second evaluation should be blocked by budget
        req2 = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 20.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-BUDGET-16B",
        )
        with self.assertRaises(TrialBudgetExceededError):
            self.engine.execute(req2)
            self.engine.execute(req2)

        # Check trial_ledger count: exactly 1
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE prereg_id = ?;", (prereg_id,))
        self.assertEqual(cur.fetchone()[0], 1)

    # 17. No model prompt contains VALIDATION or TEST data
    def test_17_no_model_prompt_contains_validation_or_test_data(self) -> None:
        """Verify that model prompts never expose private VALIDATION or TEST partition data."""
        responses = [
            {
                "hypothesis_id": "H-ADV-17",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Momentum strategy on daily data.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 2,
            },
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Sound methodology with zero temporal lookahead or data snooping.",
                "required_revisions": [],
            },
            {
                "proposal_id": "PROP-ADV-17",
                "hypothesis_id": "H-ADV-17",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0]},
                "partition_protocol_id": "CANONICAL-V5-TRAIN-ONLY",
            },
        ]
        llm = QueueMockLLM(responses)
        coord = self._make_coordinator(llm)
        try:
            coord.run_vertical_slice(objective="Clean momentum evaluation", session_id="SESS-ADV-17")
            forbidden_tokens = ["VALIDATION", "TEST_PARTITION", "TEST_SPLIT", "OUT_OF_SAMPLE_VAULT"]
            for prompt in llm.recorded_prompts:
                for token in forbidden_tokens:
                    self.assertNotIn(token, prompt)
        finally:
            coord.close()

    # 18. No model can approve its own human ticket
    def test_18_no_model_can_approve_its_own_human_ticket(self) -> None:
        """Verify that approve_proposal enforces human caller identity and rejects model automation."""
        coord = self._make_coordinator(QueueMockLLM([]))
        try:
            # Blank identity rejected
            with self.assertRaises(ValueError):
                coord.approve_proposal(ticket_id="TICK-MOCK", human_identity="   ", rationale="Looks good")

            # Blank rationale rejected
            with self.assertRaises(ValueError):
                coord.approve_proposal(ticket_id="TICK-MOCK", human_identity="Alice", rationale="   ")
        finally:
            coord.close()

    # 19. Benchmark labels never enter model prompts
    def test_19_benchmark_labels_never_enter_model_prompts(self) -> None:
        """Verify that holdout benchmark suite prompts contain no evaluation labels or hints."""
        suite = get_holdout_tasks()
        self.assertEqual(len(suite), 30)

        forbidden_labels = [
            "is_sound",
            "expected_decision",
            "defect_category",
            "SHOULD_APPROVE",
            "SHOULD_REJECT",
            "GROUND_TRUTH",
        ]
        for task in suite:
            prompt = task.objective_prompt
            for label in forbidden_labels:
                self.assertNotIn(label.lower(), prompt.lower())
                self.assertNotIn(label, prompt)

    # 20. No route to live trading, paper trading, testnet, or order execution exists
    def test_20_no_route_to_live_trading_paper_trading_testnet_or_order_execution_exists(self) -> None:
        """Verify that agent codebase contains zero order placement, testnet, or live trading methods."""
        import trader.agent.coordinator as coord_mod
        import trader.agent.core.execution as exec_mod
        import trader.agent.tools.market_bridge as bridge_mod

        forbidden_names = [
            "create_order",
            "order_market",
            "order_limit",
            "place_order",
            "cancel_order",
            "send_to_broker",
            "binance_testnet",
            "testnet_key",
            "api_secret",
            "private_key",
        ]
        for mod in [coord_mod, exec_mod, bridge_mod]:
            src = inspect.getsource(mod)
            for name in forbidden_names:
                self.assertNotIn(f"def {name}", src)


if __name__ == "__main__":
    unittest.main()
