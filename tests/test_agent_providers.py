"""Unit tests for Phase 2 LLM Providers, Schemas, and Role Validation."""

import json
import unittest
from pydantic import ValidationError

from trader.agent.providers import MockLLMProvider, LocalResearchAssistant
from trader.agent.schemas import (
    ApprovalTicket,
    AuditEvent,
    ExecutionRequestPayload,
    HypothesisPayload,
    PreregistrationProposalPayload,
    ResearchSessionState,
    SkepticalReviewerOutput,
)


class TestAgentSchemas(unittest.TestCase):
    """Test validation and invariants of agent Pydantic schemas."""

    def test_hypothesis_payload_validation(self) -> None:
        """Verify hypothesis payload validation rules."""
        valid_data = {
            "hypothesis_id": "H-TREND-01",
            "strategy_name": "SyntheticSMAStrategy",
            "economic_rationale": "Sustained momentum in daily spot volume leads to trend continuation.",
            "validation_criteria": {"min_sharpe": 0.6, "max_drawdown_pct": 25.0},
            "max_evaluation_budget": 10,
        }
        hp = HypothesisPayload.model_validate(valid_data)
        self.assertEqual(hp.hypothesis_id, "H-TREND-01")
        self.assertEqual(hp.max_evaluation_budget, 10)

        # Invalid hypothesis_id format (must start with H-)
        invalid_data = valid_data.copy()
        invalid_data["hypothesis_id"] = "INVALID-ID"
        with self.assertRaises(ValidationError):
            HypothesisPayload.model_validate(invalid_data)

        # Too short economic rationale (< 20 chars)
        invalid_data = valid_data.copy()
        invalid_data["economic_rationale"] = "Too short"
        with self.assertRaises(ValidationError):
            HypothesisPayload.model_validate(invalid_data)

    def test_skeptical_reviewer_output_validation(self) -> None:
        """Verify skeptical reviewer rubric scoring and recommendation constraints."""
        valid_review = {
            "lookahead_risk_score": 1,
            "snooping_risk_score": 2,
            "granularity_risk_score": 1,
            "turnover_realism_score": 2,
            "overall_recommendation": "RECOMMEND_APPROVAL",
            "critique_rationale": "The parameter grid is conservative with only two points tested on daily returns with no future leakage.",
            "required_revisions": [],
        }
        sro = SkepticalReviewerOutput.model_validate(valid_review)
        self.assertEqual(sro.overall_recommendation, "RECOMMEND_APPROVAL")
        self.assertEqual(sro.lookahead_risk_score, 1)

        # Invalid score (> 5)
        invalid_review = valid_review.copy()
        invalid_review["snooping_risk_score"] = 6
        with self.assertRaises(ValidationError):
            SkepticalReviewerOutput.model_validate(invalid_review)

        # Invalid recommendation enum
        invalid_review = valid_review.copy()
        invalid_review["overall_recommendation"] = "APPROVE_IMMEDIATELY"
        with self.assertRaises(ValidationError):
            SkepticalReviewerOutput.model_validate(invalid_review)

    def test_preregistration_proposal_validation(self) -> None:
        """Verify preregistration proposal payload constraints."""
        valid_prop = {
            "proposal_id": "PROP-01",
            "hypothesis_id": "H-MOM-01",
            "universe": ["BTCUSDT"],
            "timeframe": "1d",
            "parameter_grid": {"fast_period": [10.0, 15.0], "slow_period": [30.0]},
            "partition_protocol_id": "CHRONOLOGICAL_V1",
        }
        prop = PreregistrationProposalPayload.model_validate(valid_prop)
        self.assertEqual(len(prop.parameter_grid["fast_period"]), 2)

    def test_approval_ticket_validation(self) -> None:
        """Verify ticket creation and status transitions."""
        ticket = ApprovalTicket(
            ticket_id="TICK-001",
            proposal_id="PROP-01",
            approval_scope="PREREGISTRATION_APPROVAL",
            status="PENDING",
        )
        self.assertEqual(ticket.status, "PENDING")
        self.assertIsNotNone(ticket.created_at)

    def test_execution_request_validation(self) -> None:
        """Verify backtest execution request payload."""
        req = ExecutionRequestPayload(
            request_id="REQ-01",
            prereg_id="PREREG-01",
            candidate_parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
        )
        self.assertEqual(req.partition, "TRAIN")


class TestMockLLMProvider(unittest.TestCase):
    """Test deterministic mock LLM provider behavior."""

    def setUp(self) -> None:
        self.provider = MockLLMProvider()

    def test_hypothesis_generation(self) -> None:
        """Verify mock provider generates valid HypothesisPayload."""
        resp = self.provider.generate(
            role="Hypothesis Researcher",
            prompt="Propose a trend following strategy for BTCUSDT.",
            schema_class=HypothesisPayload,
        )
        self.assertIsNotNone(resp.structured_data)
        self.assertIn("hypothesis_id", resp.structured_data)
        self.assertTrue(resp.structured_data["hypothesis_id"].startswith("H-"))
        self.assertGreater(resp.prompt_tokens, 0)
        self.assertGreater(resp.completion_tokens, 0)
        self.assertEqual(len(resp.prompt_sha256), 64)
        self.assertEqual(len(resp.response_sha256), 64)

    def test_skeptical_review_generation(self) -> None:
        """Verify mock provider generates valid SkepticalReviewerOutput."""
        resp = self.provider.generate(
            role="Skeptical Reviewer",
            prompt="Audit the proposed hypothesis and grid.",
            schema_class=SkepticalReviewerOutput,
        )
        self.assertIsNotNone(resp.structured_data)
        self.assertIn("overall_recommendation", resp.structured_data)
        self.assertIn(
            resp.structured_data["overall_recommendation"],
            ["RECOMMEND_APPROVAL", "RECOMMEND_REVISION", "RECOMMEND_REJECTION"],
        )

    def test_proposal_generation(self) -> None:
        """Verify mock provider generates valid PreregistrationProposalPayload."""
        resp = self.provider.generate(
            role="Research Coordinator",
            prompt="Prepare preregistration proposal.",
            schema_class=PreregistrationProposalPayload,
        )
        self.assertIsNotNone(resp.structured_data)
        self.assertIn("parameter_grid", resp.structured_data)
        self.assertEqual(resp.structured_data["parameter_grid"]["fast_period"], [10.0, 15.0])

    def test_custom_override(self) -> None:
        """Verify custom payload override for negative testing."""
        rejection_payload = {
            "lookahead_risk_score": 4,
            "snooping_risk_score": 4,
            "granularity_risk_score": 3,
            "turnover_realism_score": 3,
            "overall_recommendation": "RECOMMEND_REJECTION",
            "critique_rationale": "High lookahead risk detected in indicator calculation window specification.",
            "required_revisions": ["Remove centered moving average"],
        }
        provider = MockLLMProvider(custom_payloads={"Skeptical Reviewer": rejection_payload})
        resp = provider.generate(
            role="Skeptical Reviewer",
            prompt="Critique",
            schema_class=SkepticalReviewerOutput,
        )
        self.assertEqual(resp.structured_data["overall_recommendation"], "RECOMMEND_REJECTION")


class TestLocalResearchAssistant(unittest.TestCase):
    """Test LocalResearchAssistant error handling and offline interface."""

    def test_connection_error_handling(self) -> None:
        """Verify clean error raised when local endpoint is unreachable."""
        assistant = LocalResearchAssistant(
            endpoint_url="http://127.0.0.1:59999/v1/chat/completions",
            timeout_seconds=0.5,
        )
        with self.assertRaises(ConnectionError) as ctx:
            assistant.generate(
                role="Coordinator",
                prompt="Hello",
            )
        self.assertIn("failed to reach endpoint", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

