"""Configuration A: Single Researcher Baseline runner for Phase 2.3 Benchmark.

Enforces:
1. One LLM generates research hypothesis and experimental proposal.
2. Deterministic Python validation evaluates structural schema correctness.
3. No independent LLM reviewer.
4. Strictly confined to approved TRAIN partition market context.
5. Deterministic scoring via BenchmarkScorer.
"""

import hashlib
import json
import time
from typing import Any, Dict, Optional

from trader.agent.benchmark.schema import (
    BenchmarkTask,
    DimensionScores,
    TaskRunResult,
)
from trader.agent.benchmark.scorer import BenchmarkScorer
from trader.agent.providers.base import LLMProvider
from trader.agent.schemas import (
    HypothesisPayload,
    PreregistrationProposalPayload,
)


class SingleResearcherRunner:
    """Benchmark evaluation runner for Configuration A: Single Researcher baseline."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.config_id = "SINGLE_RESEARCHER"

    def run_task(self, task: BenchmarkTask) -> TaskRunResult:
        """Execute a benchmark task through the unassisted single-researcher configuration."""
        start_time = time.perf_counter()
        public_input = task.get_agent_input_payload()
        prompt_str = task.objective_prompt
        prompt_sha = hashlib.sha256(prompt_str.encode("utf-8")).hexdigest()
        dataset_digest = hashlib.sha256(json.dumps(task.market_context, sort_keys=True).encode("utf-8")).hexdigest()

        total_prompt_tokens = 0
        total_completion_tokens = 0
        error_msg: Optional[str] = None
        decision = "FAILED"
        raw_hypothesis: Optional[Dict[str, Any]] = None
        raw_proposal: Optional[Dict[str, Any]] = None

        try:
            # Stage 1: Generate Hypothesis
            hyp_prompt = (
                f"Task ID: {task.task_id}\n"
                f"Research Objective: {task.objective_prompt}\n"
                f"Market Context (TRAIN partition): {json.dumps(task.market_context)}\n"
                "Formulate a falsifiable quantitative hypothesis with explicit validation criteria."
            )
            hyp_resp = self.provider.generate(
                role="Hypothesis Researcher",
                prompt=hyp_prompt,
                schema_class=HypothesisPayload,
            )
            total_prompt_tokens += hyp_resp.prompt_tokens
            total_completion_tokens += hyp_resp.completion_tokens
            raw_hypothesis = hyp_resp.structured_data

            # Deterministic Python schema validation
            try:
                HypothesisPayload.model_validate(raw_hypothesis)
            except Exception as e:
                error_msg = f"Deterministic hypothesis schema validation failed: {str(e)}"
                decision = "FAILED"
                scores = BenchmarkScorer.evaluate(
                    task=task,
                    decision=decision,
                    hypothesis=raw_hypothesis,
                    proposal=None,
                    critique=None,
                    error_message=error_msg,
                )
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                return TaskRunResult(
                    task_id=task.task_id,
                    config_id=self.config_id,
                    model_identifier=self.provider.model_identifier,
                    prompt_sha256=prompt_sha,
                    dataset_digest=dataset_digest,
                    artifact_digest=hashlib.sha256(b"").hexdigest(),
                    decision=decision,
                    decision_rationale=error_msg,
                    raw_hypothesis=raw_hypothesis,
                    raw_proposal=None,
                    reviewer_critique=None,
                    scores=scores,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                    latency_ms=round(latency_ms, 3),
                    error_message=error_msg,
                )

            # Stage 2: Generate Proposal
            prop_prompt = (
                f"Task ID: {task.task_id}\n"
                f"Formulate a preregistration proposal for hypothesis {raw_hypothesis.get('hypothesis_id', 'H-UNKNOWN')}.\n"
                f"Mandate: {task.objective_prompt}"
            )
            prop_resp = self.provider.generate(
                role="Research Coordinator",
                prompt=prop_prompt,
                schema_class=PreregistrationProposalPayload,
            )
            total_prompt_tokens += prop_resp.prompt_tokens
            total_completion_tokens += prop_resp.completion_tokens
            raw_proposal = prop_resp.structured_data

            # Deterministic Python proposal schema validation
            try:
                PreregistrationProposalPayload.model_validate(raw_proposal)
                decision = "PROCEEDED"
            except Exception as e:
                error_msg = f"Deterministic proposal schema validation failed: {str(e)}"
                decision = "FAILED"

        except Exception as exc:
            error_msg = f"Single researcher execution exception: {str(exc)}"
            decision = "FAILED"

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Artifact serialization and digest
        artifact_dict = {
            "hypothesis": raw_hypothesis,
            "proposal": raw_proposal,
            "decision": decision,
        }
        artifact_json = json.dumps(artifact_dict, sort_keys=True)
        artifact_digest = hashlib.sha256(artifact_json.encode("utf-8")).hexdigest()

        scores = BenchmarkScorer.evaluate(
            task=task,
            decision=decision,
            hypothesis=raw_hypothesis,
            proposal=raw_proposal,
            critique=None,
            error_message=error_msg,
        )

        return TaskRunResult(
            task_id=task.task_id,
            config_id=self.config_id,
            model_identifier=self.provider.model_identifier,
            prompt_sha256=prompt_sha,
            dataset_digest=dataset_digest,
            artifact_digest=artifact_digest,
            decision=decision,
            decision_rationale="Deterministic schema validation passed without independent review" if decision == "PROCEEDED" else error_msg,
            raw_hypothesis=raw_hypothesis,
            raw_proposal=raw_proposal,
            reviewer_critique=None,
            scores=scores,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            latency_ms=round(latency_ms, 3),
            error_message=error_msg,
        )

