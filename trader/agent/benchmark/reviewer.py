"""Configuration B: Researcher + Independent Reviewer runner for Phase 2.3 Benchmark.

Enforces:
1. One LLM generates hypothesis and experimental proposal.
2. An independent LLM reviewer evaluates methodological validity, leakage risks,
   statistical assumptions, and reproducibility via the 4-dimension scoring rubric.
3. Deterministic Python validation remains authoritative.
4. If the reviewer recommends rejection (or critical risk score == 5), decision is REJECTED.
5. Strictly confined to approved TRAIN partition market context.
6. Deterministic scoring via BenchmarkScorer.
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
    SkepticalReviewerOutput,
)


from trader.agent.coordinator import (
    ABSOLUTE_MAX_REVISIONS_CEILING,
    DEFAULT_MAX_REVISIONS,
    InvalidRevisionLimitError,
    MIN_ALLOWED_REVISIONS,
    ResearchCoordinator,
)


class ResearcherReviewerRunner:
    """Benchmark evaluation runner for Configuration B: Researcher + Independent Reviewer."""

    def __init__(self, provider: LLMProvider, max_revisions: int = 3) -> None:
        self.provider = provider
        self.config_id = "RESEARCHER_REVIEWER"
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

    def run_task(self, task: BenchmarkTask) -> TaskRunResult:
        """Execute a benchmark task through the Researcher + Independent Reviewer configuration."""
        start_time = time.perf_counter()
        prompt_str = task.objective_prompt
        prompt_sha = hashlib.sha256(prompt_str.encode("utf-8")).hexdigest()
        dataset_digest = hashlib.sha256(json.dumps(task.market_context, sort_keys=True).encode("utf-8")).hexdigest()

        total_prompt_tokens = 0
        total_completion_tokens = 0
        error_msg: Optional[str] = None
        decision = "FAILED"
        raw_hypothesis: Optional[Dict[str, Any]] = None
        raw_proposal: Optional[Dict[str, Any]] = None
        raw_critique: Optional[Dict[str, Any]] = None

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

            # Deterministic Python schema validation on hypothesis
            HypothesisPayload.model_validate(raw_hypothesis)

            # Stage 2: Independent Skeptical Review (using calibrated prompt)
            review_prompt = ResearchCoordinator._build_reviewer_prompt(task.objective_prompt, raw_hypothesis)
            crit_resp = self.provider.generate(
                role="Skeptical Reviewer",
                prompt=review_prompt,
                schema_class=SkepticalReviewerOutput,
            )
            total_prompt_tokens += crit_resp.prompt_tokens
            total_completion_tokens += crit_resp.completion_tokens
            raw_critique = crit_resp.structured_data

            # Deterministic Python schema validation on critique
            critique_obj = SkepticalReviewerOutput.model_validate(raw_critique)

            # Revision loop if reviewer requested revision
            revisions_attempted = 0
            while critique_obj.overall_recommendation == "RECOMMEND_REVISION" and revisions_attempted < self.max_revisions:
                revisions_attempted += 1
                previous_hyp = raw_hypothesis

                # Researcher revises hypothesis based on critique
                rev_prompt = ResearchCoordinator._build_revision_prompt(
                    objective=task.objective_prompt,
                    previous_hypothesis_data=previous_hyp,
                    critique_data=raw_critique,
                    cycle=revisions_attempted,
                )
                rev_resp = self.provider.generate(
                    role="Hypothesis Researcher",
                    prompt=rev_prompt,
                    schema_class=HypothesisPayload,
                )
                total_prompt_tokens += rev_resp.prompt_tokens
                total_completion_tokens += rev_resp.completion_tokens
                raw_hypothesis = rev_resp.structured_data
                HypothesisPayload.model_validate(raw_hypothesis)

                # Re-review revised hypothesis
                re_crit_prompt = ResearchCoordinator._build_re_review_prompt(
                    objective=task.objective_prompt,
                    revised_hypothesis_data=raw_hypothesis,
                    previous_hypothesis_data=previous_hyp,
                    previous_critique_data=raw_critique,
                    cycle=revisions_attempted,
                )
                re_crit_resp = self.provider.generate(
                    role="Skeptical Reviewer",
                    prompt=re_crit_prompt,
                    schema_class=SkepticalReviewerOutput,
                )
                total_prompt_tokens += re_crit_resp.prompt_tokens
                total_completion_tokens += re_crit_resp.completion_tokens
                raw_critique = re_crit_resp.structured_data
                critique_obj = SkepticalReviewerOutput.model_validate(raw_critique)

            # Decision policy enforcement
            if critique_obj.overall_recommendation == "RECOMMEND_APPROVAL":
                # Stage 3: Generate Proposal if reviewer approved
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

                PreregistrationProposalPayload.model_validate(raw_proposal)
                decision = "PROCEEDED"
                decision_rationale = f"Reviewer approved proposal: {critique_obj.critique_rationale}"

            elif critique_obj.overall_recommendation == "RECOMMEND_REVISION":
                decision = "REVISION_EXHAUSTED"
                decision_rationale = (
                    f"Reviewer requested revisions but max revision attempts ({self.max_revisions}) "
                    f"were exhausted: {critique_obj.critique_rationale}"
                )

            elif critique_obj.overall_recommendation == "RECOMMEND_REJECTION" or (
                critique_obj.lookahead_risk_score == 5 or critique_obj.snooping_risk_score == 5
            ):
                decision = "REJECTED"
                decision_rationale = f"Independent reviewer rejected proposal: {critique_obj.critique_rationale}"

            elif critique_obj.overall_recommendation in {"REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"}:
                decision = "REVIEW_REQUIRED"
                decision_rationale = f"Independent reviewer flagged proposal for review: {critique_obj.critique_rationale}"

            else:
                decision = "REJECTED"
                decision_rationale = f"Unknown reviewer recommendation '{critique_obj.overall_recommendation}': {critique_obj.critique_rationale}"

        except Exception as exc:
            error_msg = f"Researcher + Reviewer execution exception: {str(exc)}"
            decision = "FAILED"
            decision_rationale = error_msg

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        artifact_dict = {
            "hypothesis": raw_hypothesis,
            "critique": raw_critique,
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
            critique=raw_critique,
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
            decision_rationale=decision_rationale,
            raw_hypothesis=raw_hypothesis,
            raw_proposal=raw_proposal,
            reviewer_critique=raw_critique,
            scores=scores,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            latency_ms=round(latency_ms, 3),
            error_message=error_msg,
        )

