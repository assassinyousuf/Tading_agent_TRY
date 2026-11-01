"""Benchmark-aware deterministic Mock LLM Provider for Phase 2.3 evaluation tasks.

Generates realistic role-conditioned responses for each benchmark task scenario:
- Single Researcher: Generates naive hypotheses/proposals reflecting the prompt's instructions.
- Skeptical Reviewer: Adversarially audits proposals, accurately flagging methodological flaws.
- Research Coordinator: Synthesizes bounded experimental parameters and structured tickets.
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


class BenchmarkMockLLMProvider(LLMProvider):
    """Deterministic LLM Provider calibrated for the 9 canonical benchmark tasks."""

    def __init__(
        self,
        model_identifier: str = "benchmark-mock-v1",
    ) -> None:
        super().__init__(name="benchmark_mock", model_identifier=model_identifier)
        self.call_history: list[Dict[str, Any]] = []

    def generate(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        """Generate deterministic, task-aware mock response."""
        start_time = time.perf_counter()
        prompt_sha = self.compute_sha256(prompt)

        payload = self._resolve_payload(role, prompt, schema_class)
        content = json.dumps(payload, indent=2) if isinstance(payload, dict) else str(payload)
        response_sha = self.compute_sha256(content)

        structured_data = None
        if schema_class is not None and isinstance(payload, dict):
            try:
                validated = schema_class.model_validate(payload)
                structured_data = validated.model_dump()
            except Exception:
                # If intentional validation test failure, preserve raw dict
                structured_data = payload
        elif isinstance(payload, dict):
            structured_data = payload

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        resp = LLMResponse(
            content=content,
            structured_data=structured_data,
            prompt_tokens=max(20, len(prompt) // 4),
            completion_tokens=max(20, len(content) // 4),
            latency_ms=round(latency_ms, 3),
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            provider_name=self.name,
            model_identifier=self.model_identifier,
        )
        self.call_history.append({"role": role, "response": resp})
        return resp

    def _resolve_payload(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[Type[BaseModel]],
    ) -> Any:
        """Route to appropriate payload based on role and task prompt signatures."""
        prompt_upper = prompt.upper()
        role_lower = role.lower()

        # Identify task category from explicit task ID if present
        if "TASK-01" in prompt_upper or "H-MOM-SMA-01" in prompt_upper:
            task_type = "VALID"
        elif "TASK-02" in prompt_upper or "H-VAGUE-01" in prompt_upper:
            task_type = "UNDERSPECIFIED"
        elif "TASK-03" in prompt_upper or "H-LEAKAGE-01" in prompt_upper:
            task_type = "LEAKAGE"
        elif "TASK-04" in prompt_upper or "H-LOOKAHEAD-01" in prompt_upper:
            task_type = "LOOKAHEAD"
        elif "TASK-05" in prompt_upper or "H-SWEEP-01" in prompt_upper:
            task_type = "MULTIPLE_TESTING"
        elif "TASK-06" in prompt_upper or "H-OVERFITTING-01" in prompt_upper:
            task_type = "OVERFITTING"
        elif "TASK-07" in prompt_upper or "H-STAT_INTERPRET-01" in prompt_upper:
            task_type = "STAT_INTERPRET"
        elif "TASK-08" in prompt_upper or "H-FRICTION-01" in prompt_upper:
            task_type = "FRICTION"
        elif "TASK-09" in prompt_upper or "H-NON_REPRODUCIBLE-01" in prompt_upper:
            task_type = "NON_REPRODUCIBLE"
        elif "TASK-10" in prompt_upper or "H-MEAN-REV-01" in prompt_upper:
            task_type = "VALID"
        elif "TASK-11" in prompt_upper or "H-SURVIVORSHIP-01" in prompt_upper:
            task_type = "SURVIVORSHIP"
        elif "TASK-12" in prompt_upper or "H-OVER_OPT-01" in prompt_upper:
            task_type = "OVER_OPTIMIZATION"
        elif "TASK-13" in prompt_upper or "H-CONTAM-01" in prompt_upper:
            task_type = "PARTITION_CONTAMINATION"
        elif "TASK-14" in prompt_upper or "H-VOL-BREAK-01" in prompt_upper:
            task_type = "VALID"
        elif "TASK-15" in prompt_upper:
            task_type = "VALID"
        elif "TASK-16" in prompt_upper:
            task_type = "VALID"
        elif "TASK-17" in prompt_upper:
            task_type = "CLARIFICATION"
        elif "TASK-18" in prompt_upper:
            task_type = "LOOKAHEAD"
        elif "TASK-19" in prompt_upper:
            task_type = "OVERFITTING"
        elif "TASK-20" in prompt_upper:
            task_type = "VALID"
        # Fallback to specific non-colliding phrase signatures
        elif "SMA_CENTERED" in prompt_upper or "CENTERED AVERAGE" in prompt_upper:
            task_type = "LOOKAHEAD"
        elif "NORMALIZED BY DIVIDING BY THE MAXIMUM" in prompt_upper or "FUTURE PERIODS T+1" in prompt_upper:
            task_type = "LEAKAGE"
        elif "2,000 PARAMETER" in prompt_upper or "FAST_PERIOD IN [1, 2, ..., 100]" in prompt_upper:
            task_type = "MULTIPLE_TESTING"
        elif "12TH-DEGREE POLYNOMIAL" in prompt_upper or "13 UNCONSTRAINED FREE PARAMETERS" in prompt_upper:
            task_type = "OVERFITTING"
        elif "AUTOCORRELATED" in prompt_upper or "AR(1) = 0.42" in prompt_upper:
            task_type = "STAT_INTERPRET"
        elif "0.00% TAKER FEES" in prompt_upper or "15 ROUND-TRIP TRADES" in prompt_upper:
            task_type = "FRICTION"
        elif "UNSEEDED" in prompt_upper or "RANDOM PARAMETERS ARE RANDOMLY SAMPLED" in prompt_upper:
            task_type = "NON_REPRODUCIBLE"
        elif "SURVIVORSHIP" in prompt_upper or "BEST-PERFORMING TOKENS OF 2025" in prompt_upper:
            task_type = "SURVIVORSHIP"
        elif "0.001% INCREMENTS" in prompt_upper or "OVER-OPTIMIZATION" in prompt_upper:
            task_type = "OVER_OPTIMIZATION"
        elif "TEST PARTITION" in prompt_upper or "OUT-OF-SAMPLE TEST PARTITION" in prompt_upper:
            task_type = "PARTITION_CONTAMINATION"
        elif "MAXIMIZES PROFITS" in prompt_upper or "WITHOUT LOSING MONEY" in prompt_upper:
            task_type = "UNDERSPECIFIED"
        else:
            task_type = "VALID"

        # 1. Skeptical Reviewer Critique Payloads
        if "skeptical" in role_lower or "reviewer" in role_lower or schema_class is SkepticalReviewerOutput:
            return self._get_reviewer_payload(task_type)

        # 2. Hypothesis Payloads
        if "hypothesis" in role_lower or schema_class is HypothesisPayload:
            return self._get_hypothesis_payload(task_type)

        # 3. Proposal Payloads
        if "proposal" in role_lower or "preregistration" in role_lower or schema_class is PreregistrationProposalPayload:
            return self._get_proposal_payload(task_type)

        # 4. Default / Report
        return {
            "summary": f"Benchmark evaluation output for task type {task_type}.",
            "status": "COMPLETED",
        }

    def _get_reviewer_payload(self, task_type: str) -> Dict[str, Any]:
        """Reviewer accurately diagnoses flaws and rejects invalid setups."""
        if task_type == "VALID":
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "The proposed SMA crossover logic is economically grounded and uses a minimal parameter grid of M=2 points on daily data, minimizing data snooping risk.",
                "required_revisions": [],
            }
        elif task_type in ("LOOKAHEAD", "LEAKAGE"):
            return {
                "lookahead_risk_score": 5,
                "snooping_risk_score": 3,
                "granularity_risk_score": 2,
                "turnover_realism_score": 3,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Critical failure: Severe data leakage and lookahead bias detected in feature/indicator calculation using future candle data (t+1, t+2). Completely unrealizable in production.",
                "required_revisions": ["Eliminate all future time index references", "Use strictly causal indicators computed on bar close t-1."],
            }
        elif task_type in ("MULTIPLE_TESTING", "OVERFITTING", "OVER_OPTIMIZATION"):
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 5,
                "granularity_risk_score": 5,
                "turnover_realism_score": 3,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Critical failure: Unbounded parameter sweep (excessive parameter points / over-optimization) represents blatant data snooping and overfitting without multiple testing control.",
                "required_revisions": ["Bound parameter grid to M <= 5 points", "Preregister hypothesis before testing", "Apply Deflated Sharpe Ratio with full M_total accounting."],
            }
        elif task_type in ("SURVIVORSHIP", "PARTITION_CONTAMINATION"):
            return {
                "lookahead_risk_score": 5 if task_type == "PARTITION_CONTAMINATION" else 3,
                "snooping_risk_score": 5,
                "granularity_risk_score": 3,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Critical methodological defect: Selection bias (survivorship) or unauthorized access to TEST partition invalidates statistical inference.",
                "required_revisions": ["Enforce point-in-time universe definition", "Preserve sealed partition boundaries (M_test = 0)."],
            }
        elif task_type == "STAT_INTERPRET":
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 4,
                "granularity_risk_score": 2,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Methodological defect: Nominal p-value on strongly autocorrelated non-stationary returns produces spurious statistical significance. Stationary bootstrap and AR adjustment required.",
                "required_revisions": ["Perform stationary block bootstrap", "Compute Deflated Sharpe Ratio against benchmark."],
            }
        elif task_type == "FRICTION":
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 2,
                "granularity_risk_score": 2,
                "turnover_realism_score": 5,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Cost model failure: 0.00% transaction fee and zero slippage on high turnover intraday strategy violates execution realism. Strategy return will be entirely erased by real friction.",
                "required_revisions": ["Incorporate realistic Binance taker fees (0.10% spot / 0.05% futures)", "Add 0.05% slippage buffer."],
            }
        elif task_type == "CLARIFICATION":
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 3,
                "granularity_risk_score": 3,
                "turnover_realism_score": 2,
                "overall_recommendation": "REVIEW_REQUIRED",
                "critique_rationale": "Methodological ambiguity: Strategy mentions volatility channel without specifying indicator primitive (ATR vs Bollinger vs Keltner). Clarification required.",
                "defect_type": "CLARIFICATION_REQUIRED",
                "affected_field": "strategy_name",
                "invalidation_reason": "Ambiguous indicator specification prevents deterministic execution and validation.",
                "corrective_action": "Specify concrete channel calculation primitive and parameter bounds.",
                "required_revisions": ["Specify concrete channel calculation primitive and parameter bounds."],
            }
        elif task_type == "NON_REPRODUCIBLE":
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 3,
                "granularity_risk_score": 4,
                "turnover_realism_score": 3,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Reproducibility failure: Stochastic unseeded draws and undefined partition boundaries cannot be preregistered or verified deterministically in SQLite.",
                "required_revisions": ["Define deterministic seed", "Preregister fixed parameter grid", "Specify chronological partition boundaries."],
            }
        else:  # UNDERSPECIFIED
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 5,
                "granularity_risk_score": 3,
                "turnover_realism_score": 3,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Unfalsifiable mandate: Vague claims to maximize profit without loss cannot be validated quantitatively. Requires concrete strategy primitive and explicit hurdle criteria.",
                "required_revisions": ["Specify concrete mathematical strategy", "Define explicit falsifiable validation criteria."],
            }

    def _get_hypothesis_payload(self, task_type: str) -> Dict[str, Any]:
        """Researcher generates hypothesis reflecting task premise."""
        if task_type == "VALID":
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
        elif task_type in ("LOOKAHEAD", "LEAKAGE"):
            return {
                "hypothesis_id": "H-LEAKAGE-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Feature normalization using future closing prices across sample allows perfect anticipation of cyclical turns.",
                "validation_criteria": {
                    "min_sharpe": 1.5,
                    "max_drawdown_pct": 10.0,
                },
                "max_evaluation_budget": 10,
            }
        elif task_type in ("MULTIPLE_TESTING", "OVERFITTING", "OVER_OPTIMIZATION"):
            return {
                "hypothesis_id": "H-SWEEP-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Exhaustive grid search across parameter combinations to empirically locate the global maximum in-sample Sharpe ratio.",
                "validation_criteria": {
                    "min_sharpe": 0.8,
                    "max_drawdown_pct": 20.0,
                },
                "max_evaluation_budget": 10,
            }
        elif task_type == "UNDERSPECIFIED":
            return {
                "hypothesis_id": "H-VAGUE-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "AI strategy that generates maximum profit across all market regimes without losing money.",
                "validation_criteria": {
                    "min_sharpe": 2.0,
                },
                "max_evaluation_budget": 10,
            }
        else:
            return {
                "hypothesis_id": f"H-{task_type}-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": f"Quantitative research hypothesis addressing {task_type} scenario under TRAIN market conditions.",
                "validation_criteria": {
                    "min_sharpe": 0.5,
                    "max_drawdown_pct": 30.0,
                },
                "max_evaluation_budget": 10,
            }

    def _get_proposal_payload(self, task_type: str) -> Dict[str, Any]:
        """Researcher generates proposal reflecting task premise."""
        if task_type == "VALID":
            return {
                "proposal_id": "PROP-VALID-SMA-01",
                "hypothesis_id": "H-MOM-SMA-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {
                    "fast_period": [10.0, 15.0],
                    "slow_period": [30.0],
                },
                "partition_protocol_id": "CHRONOLOGICAL_V1",
            }
        elif task_type == "MULTIPLE_TESTING":
            # Naive unconstrained grid
            return {
                "proposal_id": "PROP-SWEEP-M2000-01",
                "hypothesis_id": "H-SWEEP-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {
                    "fast_period": [float(i) for i in range(1, 101)],
                    "slow_period": [float(j) for j in range(5, 201, 4)],
                },
                "partition_protocol_id": "CHRONOLOGICAL_V1",
            }
        elif task_type == "CLARIFICATION":
            return {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 3,
                "granularity_risk_score": 3,
                "turnover_realism_score": 2,
                "overall_recommendation": "REVIEW_REQUIRED",
                "critique_rationale": "Methodological ambiguity: Strategy mentions volatility channel without specifying indicator primitive (ATR vs Bollinger vs Keltner). Clarification required.",
                "defect_type": "CLARIFICATION_REQUIRED",
                "affected_field": "strategy_name",
                "invalidation_reason": "Ambiguous indicator specification prevents deterministic execution and validation.",
                "corrective_action": "Specify concrete channel calculation primitive and parameter bounds.",
                "required_revisions": ["Specify concrete channel calculation primitive and parameter bounds."],
            }
        elif task_type == "NON_REPRODUCIBLE":
            # Non-deterministic or floating
            return {
                "proposal_id": "PROP-STOCH-01",
                "hypothesis_id": "H-NON_REPRODUCIBLE-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {
                    "fast_period": [10.0],
                    "slow_period": [30.0],
                },
                "partition_protocol_id": "UNSEEDED_RANDOM",
            }
        else:
            return {
                "proposal_id": f"PROP-{task_type}-01",
                "hypothesis_id": f"H-{task_type}-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {
                    "fast_period": [10.0, 15.0],
                    "slow_period": [30.0],
                },
                "partition_protocol_id": "CHRONOLOGICAL_V1",
            }
