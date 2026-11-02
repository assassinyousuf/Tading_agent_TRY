"""Unit and integration test suite for autonomous research revision workflow.

Tests:
1. Fully corrected revision: Researcher resolves all weaknesses -> Reviewer approves.
2. Partially corrected revision: Researcher resolves subset -> Re-review requests revision again -> Next cycle approves.
3. Uncorrected revision: Researcher ignores critique -> Reviewer rejects.
4. Invalidated revision: Researcher introduces new defect -> Reviewer rejects.
5. Max revision loop enforcement: Halts with SkepticalReviewRevisionExhaustedError when bound is reached.
6. Gating consistency: RECOMMEND_APPROVAL proceeds to ticket, others halt.
7. Interrupted evaluation handling: Resuming never automatically reruns without authorization.
"""

from collections import deque
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel

from trader.agent.coordinator import (
    ResearchCoordinator,
    SkepticalReviewInvestigationRequiredError,
    SkepticalReviewInsufficientEvidenceError,
    SkepticalReviewRejectionError,
    SkepticalReviewRevisionExhaustedError,
)
from trader.agent.core.execution import ResearchExecutionEngine
from trader.agent.db.ledger import ResearchLedger
from trader.agent.providers.base import LLMProvider, LLMResponse
from trader.agent.schemas import (
    HypothesisPayload,
    PreregistrationProposalPayload,
    SkepticalReviewerOutput,
)
from trader.agent.tools.market_bridge import MarketDataProvider


class QueueLLMProvider(LLMProvider):
    """Deterministic queued LLM provider for exact scripted multi-turn workflows."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        super().__init__(name="queue_mock", model_identifier="mock-queue-v1")
        self.queue = deque(responses)
        self.calls: List[Dict[str, Any]] = []

    def generate(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        self.calls.append({"role": role, "prompt": prompt})
        if not self.queue:
            raise RuntimeError(f"Queue exhausted when generating for role '{role}'")
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
            prompt_tokens=50,
            completion_tokens=50,
            latency_ms=5.0,
            prompt_sha256=self.compute_sha256(prompt),
            response_sha256=self.compute_sha256(content),
            provider_name=self.name,
            model_identifier=self.model_identifier,
        )


class TestAgentRevisionWorkflow(unittest.TestCase):
    """Test suite verifying iterative hypothesis revision and reviewer calibration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_research.db")
        self.lock_path = os.path.join(self.temp_dir, "test.lock")

        self.ledger = ResearchLedger(db_path=self.db_path)
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)

    def tearDown(self) -> None:
        self.ledger.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_coordinator(self, llm_provider: LLMProvider, max_revisions: int = 2) -> ResearchCoordinator:
        return ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm_provider,
            lock_path=self.lock_path,
            max_revisions=max_revisions,
        )

    def test_fully_corrected_revision_workflow(self) -> None:
        """Verify that a hypothesis with fixable issues is revised, approved, and creates a ticket."""
        responses = [
            # 1. Initial Hypothesis (has subtle lag defect)
            {
                "hypothesis_id": "H-REV-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Trend following momentum on daily closes with fast/slow SMA crossing.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 2. Initial Review: RECOMMEND_REVISION
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Missing explicit 1-bar execution lag; potential same-bar close execution creates lookahead risk.",
                "required_revisions": ["Specify explicit execution lag on next bar open."],
                "revision_assessment": "NOT_APPLICABLE",
            },
            # 3. Revised Hypothesis (corrected with explicit lag)
            {
                "hypothesis_id": "H-REV-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Trend following momentum with explicit open[t+1] execution delay after close cross.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 4. Re-review: FULLY_CORRECTED -> RECOMMEND_APPROVAL
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Researcher explicitly specified next-bar execution. Methodology is now causal and sound.",
                "required_revisions": [],
                "revision_assessment": "FULLY_CORRECTED",
            },
            # 5. Preregistration Proposal Formulation
            {
                "proposal_id": "PROP-TEMP",
                "hypothesis_id": "H-REV-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0, 15.0], "slow_period": [30.0]},
                "partition_protocol_id": "PROTO-V1-TRAIN",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            state = coord.run_vertical_slice(
                objective="Evaluate SMA trend with execution delay.",
                session_id="SESS-REV-FULL-01",
            )
            self.assertFalse(state.is_failed)
            self.assertEqual(state.current_stage, "HUMAN_APPROVAL_GATE")
            self.assertIsNotNone(state.ticket)
            self.assertEqual(state.ticket.status, "PENDING")
            self.assertEqual(len(state.revision_history), 1)
            self.assertEqual(state.revision_history[0]["cycle"], 1)
            self.assertEqual(state.critique.overall_recommendation, "RECOMMEND_APPROVAL")
            self.assertEqual(state.critique.revision_assessment, "FULLY_CORRECTED")
        finally:
            coord.close()

    def test_partially_corrected_two_cycle_revision(self) -> None:
        """Verify multi-cycle revision where cycle 1 partially corrects and cycle 2 completes correction."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-REV-02",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Dual moving average breakout on daily cryptocurrency returns.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 30.0},
                "max_evaluation_budget": 5,
            },
            # 2. Review 1: RECOMMEND_REVISION (2 weaknesses)
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 3,
                "granularity_risk_score": 3,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Missing execution delay and parameter grid budget is too loose for Phase 2 bound.",
                "required_revisions": ["Add execution delay", "Bound parameter budget M <= 2"],
                "revision_assessment": "NOT_APPLICABLE",
            },
            # 3. Revision 1: fixes delay but parameter budget remains loose
            {
                "hypothesis_id": "H-REV-02",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Dual moving average with open[t+1] execution delay after daily close signal.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 30.0},
                "max_evaluation_budget": 5,
            },
            # 4. Re-review 1: PARTIALLY_CORRECTED -> RECOMMEND_REVISION
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 3,
                "granularity_risk_score": 3,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Execution delay fixed, but evaluation budget still excessive for multiple-testing bound.",
                "required_revisions": ["Bound parameter budget M <= 2"],
                "revision_assessment": "PARTIALLY_CORRECTED",
            },
            # 5. Revision 2: fixes parameter budget to M=2
            {
                "hypothesis_id": "H-REV-02",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Dual moving average with open[t+1] execution delay and strictly bounded budget M=2.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 30.0},
                "max_evaluation_budget": 2,
            },
            # 6. Re-review 2: FULLY_CORRECTED -> RECOMMEND_APPROVAL
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "All weaknesses resolved. Parameter grid is strictly bounded M=2 and delay is enforced.",
                "required_revisions": [],
                "revision_assessment": "FULLY_CORRECTED",
            },
            # 7. Proposal
            {
                "proposal_id": "PROP-TEMP",
                "hypothesis_id": "H-REV-02",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0, 15.0], "slow_period": [30.0]},
                "partition_protocol_id": "PROTO-V1-TRAIN",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            state = coord.run_vertical_slice(
                objective="Evaluate dual SMA strategy.",
                session_id="SESS-REV-PARTIAL-02",
            )
            self.assertFalse(state.is_failed)
            self.assertEqual(len(state.revision_history), 2)
            self.assertEqual(state.revision_history[0]["critique"]["revision_assessment"], "NOT_APPLICABLE")
            self.assertEqual(state.revision_history[1]["critique"]["revision_assessment"], "PARTIALLY_CORRECTED")
            self.assertEqual(state.critique.revision_assessment, "FULLY_CORRECTED")
            self.assertEqual(state.critique.overall_recommendation, "RECOMMEND_APPROVAL")
        finally:
            coord.close()

    def test_uncorrected_revision_leads_to_rejection(self) -> None:
        """Verify that an uncorrected revision results in rejection and halts the pipeline."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-REV-03",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Centered rolling window mean reversion on daily closing price series.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            },
            # 2. Review: RECOMMEND_REVISION
            {
                "lookahead_risk_score": 4,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Centered window uses future bars t+k, introducing temporal lookahead bias.",
                "required_revisions": ["Replace centered window with trailing causal window."],
                "revision_assessment": "NOT_APPLICABLE",
            },
            # 3. Revision: Researcher ignores critique, returns same centered window
            {
                "hypothesis_id": "H-REV-03",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Still centered rolling window on daily closing price series without trailing lag.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            },
            # 4. Re-review: UNCORRECTED -> RECOMMEND_REJECTION
            {
                "lookahead_risk_score": 5,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Researcher ignored critique; centered window was retained with fatal lookahead bias.",
                "required_revisions": ["Centered window unchanged."],
                "revision_assessment": "UNCORRECTED",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            with self.assertRaises(SkepticalReviewRejectionError):
                coord.run_vertical_slice(
                    objective="Centered window strategy.",
                    session_id="SESS-REV-UNCORRECTED-03",
                )
        finally:
            coord.close()

    def test_invalidated_revision_leads_to_rejection(self) -> None:
        """Verify that a revision introducing a new critical flaw is identified as INVALID_REVISION and rejected."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-REV-04",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Standard momentum on daily Bitcoin closes.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 2. Review: RECOMMEND_REVISION
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Specify transaction fee model and realistic turnover frictions.",
                "required_revisions": ["Acknowledge transaction fees."],
                "revision_assessment": "NOT_APPLICABLE",
            },
            # 3. Revision: Researcher introduces test partition peeking
            {
                "hypothesis_id": "H-REV-04",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Peeking into TEST partition to optimize fees and parameters over holdout data.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 4. Re-review: INVALID_REVISION -> RECOMMEND_REJECTION
            {
                "lookahead_risk_score": 5,
                "snooping_risk_score": 5,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Revision introduced partition contamination by optimizing on TEST holdout partition.",
                "required_revisions": ["TEST partition peeking introduced."],
                "revision_assessment": "INVALID_REVISION",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            with self.assertRaises(SkepticalReviewRejectionError):
                coord.run_vertical_slice(
                    objective="Momentum with fees.",
                    session_id="SESS-REV-INVALID-04",
                )
        finally:
            coord.close()

    def test_revision_loop_limit_enforcement(self) -> None:
        """Verify that when reviewer repeatedly issues RECOMMEND_REVISION, loop halts at max_revisions."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-REV-05",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Complex breakout on daily cryptocurrency time series.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 2. Initial Review: RECOMMEND_REVISION
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 3,
                "granularity_risk_score": 1,
                "turnover_realism_score": 3,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Issue 1 requires revision before proposal approval can be considered.",
                "required_revisions": ["Fix issue 1"],
                "revision_assessment": "NOT_APPLICABLE",
            },
            # 3. Revision 1
            {
                "hypothesis_id": "H-REV-05",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Revised complex breakout cycle 1 addressing issue 1 partially.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 4. Re-review 1: Still RECOMMEND_REVISION
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 3,
                "granularity_risk_score": 1,
                "turnover_realism_score": 3,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Issue 2 still requires revision before approval can be granted.",
                "required_revisions": ["Fix issue 2"],
                "revision_assessment": "PARTIALLY_CORRECTED",
            },
            # 5. Revision 2
            {
                "hypothesis_id": "H-REV-05",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Revised complex breakout cycle 2 addressing issue 2 partially.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 6. Re-review 2: Still RECOMMEND_REVISION
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 3,
                "granularity_risk_score": 1,
                "turnover_realism_score": 3,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Issue 3 still requires revision; residual specification defects persist.",
                "required_revisions": ["Fix issue 3"],
                "revision_assessment": "PARTIALLY_CORRECTED",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            with self.assertRaises(SkepticalReviewRevisionExhaustedError):
                coord.run_vertical_slice(
                    objective="Endless revision objective.",
                    session_id="SESS-REV-EXHAUST-05",
                )
        finally:
            coord.close()

    def test_investigation_required_decision_halts(self) -> None:
        """Verify that REVIEW_REQUIRED halts with SkepticalReviewInvestigationRequiredError."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-REV-06",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Ambiguous institutional data trading on undisclosed signal feeds.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            },
            # 2. Review: REVIEW_REQUIRED
            {
                "lookahead_risk_score": 2,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "REVIEW_REQUIRED",
                "critique_rationale": "Requires legal / institutional compliance clearance before research execution.",
                "required_revisions": [],
                "revision_assessment": "NOT_APPLICABLE",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            with self.assertRaises(SkepticalReviewInvestigationRequiredError):
                coord.run_vertical_slice(
                    objective="Ambiguous compliance objective.",
                    session_id="SESS-REV-INVESTIGATE-06",
                )
        finally:
            coord.close()

    def test_insufficient_evidence_decision_halts(self) -> None:
        """Verify that INSUFFICIENT_EVIDENCE halts with SkepticalReviewInsufficientEvidenceError."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-REV-07",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Signal based on order book depth feature that is missing from dataset.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            },
            # 2. Review: INSUFFICIENT_EVIDENCE
            {
                "lookahead_risk_score": 2,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "INSUFFICIENT_EVIDENCE",
                "critique_rationale": "Market dataset lacks required depth features to evaluate this hypothesis.",
                "required_revisions": [],
                "revision_assessment": "NOT_APPLICABLE",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=2)
        try:
            with self.assertRaises(SkepticalReviewInsufficientEvidenceError):
                coord.run_vertical_slice(
                    objective="Insufficient evidence objective.",
                    session_id="SESS-REV-INSUFFICIENT-07",
                )
        finally:
            coord.close()

    def test_proposal_revisions_persisted_and_immutable(self) -> None:
        """Verify proposal_revisions table persists revision cycles and prevents UPDATE/DELETE."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-REV-PERSIST-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Initial momentum rationale.",
                "validation_criteria": {"min_sharpe": 0.8},
                "max_evaluation_budget": 2,
            },
            # 2. Initial Review -> RECOMMEND_REVISION
            {
                "lookahead_risk_score": 2,
                "snooping_risk_score": 3,
                "granularity_risk_score": 2,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Narrow moving average range looks prone to data snooping.",
                "required_revisions": ["Widen SMA period grid"],
                "revision_assessment": "NOT_APPLICABLE",
            },
            # 3. Revised Hypothesis
            {
                "hypothesis_id": "H-REV-PERSIST-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Revised momentum rationale with wider baseline.",
                "validation_criteria": {"min_sharpe": 0.8},
                "max_evaluation_budget": 2,
            },
            # 4. Re-review -> RECOMMEND_APPROVAL
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "All snooping concerns addressed thoroughly with an expanded parameter grid and clear economic rationale.",
                "required_revisions": [],
                "revision_assessment": "FULLY_CORRECTED",
            },
            # 5. Proposal
            {
                "proposal_id": "PROP-REV-PERSIST-01",
                "hypothesis_id": "H-REV-PERSIST-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0, 15.0], "slow_period": [30.0]},
                "partition_protocol_id": "CANONICAL-V5-TRAIN-ONLY",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = self._make_coordinator(llm, max_revisions=3)
        try:
            state = coord.run_vertical_slice(
                objective="Persistence test objective",
                session_id="SESS-PERSIST-01",
            )
            self.assertEqual(state.current_stage, "HUMAN_APPROVAL_GATE")

            # Check ledger query for revisions
            revisions = self.ledger.get_proposal_revisions("H-REV-PERSIST-01")
            self.assertEqual(len(revisions), 1)
            rev = revisions[0]
            self.assertEqual(rev["hypothesis_id"], "H-REV-PERSIST-01")
            self.assertEqual(rev["revision_number"], 1)
            self.assertEqual(rev["reviewer_decision"], "RECOMMEND_APPROVAL")
            self.assertTrue(len(rev["configuration_digest"]) > 0)
            self.assertTrue(rev["prompt_hash"] is not None)
            self.assertTrue(rev["response_hash"] is not None)

            # Test Immutability: UPDATE prohibited
            cur = self.ledger.conn.cursor()
            with self.assertRaises(Exception):
                cur.execute(
                    "UPDATE proposal_revisions SET reviewer_decision = 'TAMPERED' WHERE revision_id = ?;",
                    (rev["revision_id"],),
                )
                self.ledger.conn.commit()

            # Test Immutability: DELETE prohibited
            with self.assertRaises(Exception):
                cur.execute(
                    "DELETE FROM proposal_revisions WHERE revision_id = ?;",
                    (rev["revision_id"],),
                )
                self.ledger.conn.commit()

        finally:
            coord.close()


if __name__ == "__main__":
    unittest.main()
