"""Deterministic Mock LLM Provider for unit and integration testing.

Returns pre-baked, strictly validated payloads for each research role
without making external network requests or incurring live API costs.
"""

import json
import time
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel

from trader.agent.providers.base import LLMProvider, LLMResponse
from trader.agent.schemas import (
    HypothesisPayload,
    PreregistrationProposalPayload,
    SkepticalReviewerOutput,
)


class MockLLMProvider(LLMProvider):
    """Deterministic mock provider returning pre-configured valid responses."""

    def __init__(
        self,
        model_identifier: str = "mock-quant-v1",
        custom_payloads: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name="mock", model_identifier=model_identifier)
        self.custom_payloads = custom_payloads or {}
        self.call_history = []

    def generate(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        """Return deterministic pre-baked response matching role and requested schema."""
        start_time = time.perf_counter()
        prompt_sha = self.compute_sha256(prompt)

        # Check for role-specific custom override
        if role in self.custom_payloads:
            payload = self.custom_payloads[role]
        else:
            payload = self._get_default_payload(role, schema_class)

        content = json.dumps(payload, indent=2) if isinstance(payload, dict) else str(payload)
        response_sha = self.compute_sha256(content)

        # Validate with schema_class if provided
        structured_data = None
        if schema_class is not None and isinstance(payload, dict):
            validated_obj = schema_class.model_validate(payload)
            structured_data = validated_obj.model_dump()
        elif isinstance(payload, dict):
            structured_data = payload

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        resp = LLMResponse(
            content=content,
            structured_data=structured_data,
            prompt_tokens=max(10, len(prompt) // 4),
            completion_tokens=max(10, len(content) // 4),
            latency_ms=round(latency_ms, 3),
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            provider_name=self.name,
            model_identifier=self.model_identifier,
        )

        self.call_history.append({"role": role, "response": resp})
        return resp

    def _get_default_payload(
        self,
        role: str,
        schema_class: Optional[Type[BaseModel]],
    ) -> Any:
        """Provide standard valid payloads for the 2-configuration SMA vertical slice."""
        role_lower = role.lower()

        if "hypothesis" in role_lower or schema_class is HypothesisPayload:
            return {
                "hypothesis_id": "H-MOM-SMA-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Medium-term momentum effect in BTCUSDT daily price discovery causes trend continuation following moving average crossovers with low transaction costs.",
                "validation_criteria": {
                    "min_sharpe": 0.5,
                    "max_drawdown_pct": 35.0,
                },
                "max_evaluation_budget": 10,
            }

        if "skeptical" in role_lower or "reviewer" in role_lower or schema_class is SkepticalReviewerOutput:
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "The proposed SMA crossover logic is economically grounded and uses a minimal parameter grid of M=2 points on daily data, minimizing data snooping risk.",
                "required_revisions": [],
            }

        if "proposal" in role_lower or "preregistration" in role_lower or schema_class is PreregistrationProposalPayload:
            return {
                "proposal_id": "PROP-SMA-M2-01",
                "hypothesis_id": "H-MOM-SMA-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {
                    "fast_period": [10.0, 15.0],
                    "slow_period": [30.0],
                },
                "partition_protocol_id": "CHRONOLOGICAL_V1",
            }

        if "report" in role_lower:
            return {
                "summary": "Quantitative evaluation of SyntheticSMAStrategy on BTCUSDT Daily TRAIN partition.",
                "observations": "Both fast period configurations (10.0 and 15.0) achieved positive risk-adjusted returns with controlled drawdown over the 400 training intervals.",
                "risks_and_limitations": "Performance was evaluated strictly within TRAIN (bars 30-430). Out-of-sample validation and testing have not been conducted.",
            }

        return {"status": "SUCCESS", "message": f"Default mock response for {role}"}

