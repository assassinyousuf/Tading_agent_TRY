"""Deterministic Python scoring engine for Phase 2.3 Research Agent Benchmark.

Calculates independent scores across 7 qualitative dimensions (0.0 to 100.0 scale)
using strictly deterministic Python validation rules against task ground truth.
Never relies on subjective LLM grading for benchmark evaluations.
"""

from typing import Any, Dict, List, Optional
from trader.agent.benchmark.schema import (
    BenchmarkCategory,
    BenchmarkTask,
    DimensionScores,
)


class BenchmarkScorer:
    """Authoritative deterministic evaluation engine for quantitative research agent outputs."""

    @classmethod
    def evaluate(
        cls,
        task: BenchmarkTask,
        decision: str,
        hypothesis: Optional[Dict[str, Any]] = None,
        proposal: Optional[Dict[str, Any]] = None,
        critique: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> DimensionScores:
        """Deterministically score agent artifacts against task ground truth."""
        notes: List[str] = []

        # 1. Hypothesis Quality Score
        hyp_score, hyp_notes = cls._score_hypothesis_quality(task, hypothesis, decision)
        notes.extend(hyp_notes)

        # 2. Experimental Reproducibility Score
        rep_score, rep_notes = cls._score_reproducibility(task, proposal, decision)
        notes.extend(rep_notes)

        # 3. Leakage Detection Score
        leak_score, leak_notes = cls._score_leakage_detection(task, critique, proposal, decision)
        notes.extend(leak_notes)

        # 4. Statistical Reasoning Score
        stat_score, stat_notes = cls._score_statistical_reasoning(task, critique, proposal, decision)
        notes.extend(stat_notes)

        # 5. Cost Model Validity Score
        cost_score, cost_notes = cls._score_cost_model(task, critique, proposal, decision)
        notes.extend(cost_notes)

        # 6. Appropriate Rejection Score
        rej_score, rej_notes = cls._score_appropriate_rejection(task, decision, critique)
        notes.extend(rej_notes)

        # 7. Evidence-Grounded Conclusions Score
        evid_score, evid_notes = cls._score_evidence_groundedness(task, hypothesis, proposal, decision)
        notes.extend(evid_notes)

        overall = round(
            (hyp_score + rep_score + leak_score + stat_score + cost_score + rej_score + evid_score) / 7.0,
            2,
        )

        return DimensionScores(
            hypothesis_quality=hyp_score,
            experimental_reproducibility=rep_score,
            leakage_detection=leak_score,
            statistical_reasoning=stat_score,
            cost_model_validity=cost_score,
            appropriate_rejection=rej_score,
            evidence_grounded_conclusions=evid_score,
            overall_score=overall,
            detailed_audit_notes=notes,
        )

    @classmethod
    def _score_hypothesis_quality(
        cls,
        task: BenchmarkTask,
        hypothesis: Optional[Dict[str, Any]],
        decision: str,
    ) -> tuple[float, List[str]]:
        """Score hypothesis structure, falsifiability, and economic rationale."""
        notes = []
        if decision == "REJECTED" and not task.ground_truth.is_valid:
            notes.append("Hypothesis Quality: Flawed task was correctly rejected (100.0)")
            return 100.0, notes

        if not hypothesis:
            notes.append("Hypothesis Quality: Missing hypothesis payload (0.0)")
            return 0.0, notes

        score = 0.0
        # Strategy primitive validity (25 pts)
        strat = hypothesis.get("strategy_name", "")
        if strat and isinstance(strat, str) and len(strat) >= 5:
            score += 25.0
            notes.append("Hypothesis Quality: Valid strategy primitive identified (+25.0)")
        else:
            notes.append("Hypothesis Quality: Missing or invalid strategy primitive (+0.0)")

        # Economic rationale depth (25 pts)
        rationale = hypothesis.get("economic_rationale", "")
        if rationale and len(rationale) >= 50:
            score += 25.0
            notes.append("Hypothesis Quality: Substantive economic rationale (+25.0)")
        elif rationale and len(rationale) >= 20:
            score += 15.0
            notes.append("Hypothesis Quality: Minimal economic rationale (+15.0)")
        else:
            notes.append("Hypothesis Quality: Insufficient economic rationale (+0.0)")

        # Validation hurdles (25 pts)
        hurdles = hypothesis.get("validation_criteria", {})
        if isinstance(hurdles, dict) and len(hurdles) >= 1:
            score += 25.0
            notes.append("Hypothesis Quality: Quantitative validation criteria present (+25.0)")
        else:
            notes.append("Hypothesis Quality: Missing validation criteria (+0.0)")

        # Evaluation budget bounded (25 pts)
        budget = hypothesis.get("max_evaluation_budget")
        if isinstance(budget, int) and 1 <= budget <= 20:
            score += 25.0
            notes.append("Hypothesis Quality: Evaluation budget strictly bounded (+25.0)")
        else:
            notes.append("Hypothesis Quality: Unbounded or missing evaluation budget (+0.0)")

        # Penalty if task was flawed but hypothesis blindly proceeded without critique
        if not task.ground_truth.is_valid and decision != "REJECTED":
            score = max(0.0, score - 50.0)
            notes.append("Hypothesis Quality: Penalty applied for uncritical generation on flawed task (-50.0)")

        return score, notes

    @classmethod
    def _score_reproducibility(
        cls,
        task: BenchmarkTask,
        proposal: Optional[Dict[str, Any]],
        decision: str,
    ) -> tuple[float, List[str]]:
        """Score determinism, universe specification, parameter grid boundedness, and timeframe."""
        notes = []
        if decision == "REJECTED" and not task.ground_truth.is_valid:
            notes.append("Reproducibility: Non-reproducible / flawed task was correctly rejected (100.0)")
            return 100.0, notes

        if not proposal:
            notes.append("Reproducibility: Missing proposal payload (0.0)")
            return 0.0, notes

        score = 0.0
        # Target universe defined (25 pts)
        univ = proposal.get("universe")
        if isinstance(univ, list) and len(univ) >= 1 and "BTCUSDT" in univ:
            score += 25.0
            notes.append("Reproducibility: Explicit asset universe specified (+25.0)")

        # Fixed timeframe (25 pts)
        tf = proposal.get("timeframe")
        if tf == "1d":
            score += 25.0
            notes.append("Reproducibility: Explicit '1d' timeframe (+25.0)")

        # Parameter grid bounded and numeric (25 pts)
        grid = proposal.get("parameter_grid")
        if isinstance(grid, dict) and grid:
            valid_grid = all(
                isinstance(vals, list) and len(vals) >= 1 and all(isinstance(v, (int, float)) for v in vals)
                for vals in grid.values()
            )
            if valid_grid:
                total_combos = 1
                for vals in grid.values():
                    total_combos *= len(vals)
                if total_combos <= task.ground_truth.max_allowed_grid_size:
                    score += 25.0
                    notes.append(f"Reproducibility: Parameter grid bounded (M={total_combos} <= {task.ground_truth.max_allowed_grid_size}) (+25.0)")
                else:
                    score += 10.0
                    notes.append(f"Reproducibility: Parameter grid too large (M={total_combos}) (+10.0)")
        
        # Partition protocol specified (25 pts)
        protocol = proposal.get("partition_protocol_id")
        if protocol and isinstance(protocol, str):
            score += 25.0
            notes.append("Reproducibility: Partition protocol ID specified (+25.0)")

        if task.category == BenchmarkCategory.NON_REPRODUCIBLE and decision != "REJECTED":
            score = 0.0
            notes.append("Reproducibility: Failed to reject non-reproducible task (0.0)")

        return score, notes

    @classmethod
    def _score_leakage_detection(
        cls,
        task: BenchmarkTask,
        critique: Optional[Dict[str, Any]],
        proposal: Optional[Dict[str, Any]],
        decision: str,
    ) -> tuple[float, List[str]]:
        """Score identification and prevention of lookahead bias and data leakage."""
        notes = []
        is_leakage_task = task.category in (BenchmarkCategory.DATA_LEAKAGE, BenchmarkCategory.LOOKAHEAD_BIAS)

        if is_leakage_task:
            if decision == "REJECTED":
                score = 100.0
                notes.append("Leakage Detection: Leakage/Lookahead trap was correctly rejected (100.0)")
                return score, notes
            elif critique and critique.get("lookahead_risk_score", 1) >= 4:
                score = 80.0
                notes.append("Leakage Detection: High lookahead risk flagged by reviewer (+80.0)")
                return score, notes
            else:
                score = 0.0
                notes.append("Leakage Detection: Failed to detect severe data leakage / lookahead bias (0.0)")
                return score, notes

        # For non-leakage tasks: verify lookahead risk score is low (<= 2)
        if critique:
            lh_risk = critique.get("lookahead_risk_score", 1)
            if lh_risk <= 2:
                notes.append(f"Leakage Detection: Clean proposal accurately identified with low lookahead risk={lh_risk} (100.0)")
                return 100.0, notes
            else:
                notes.append(f"Leakage Detection: False lookahead alarm on valid task risk={lh_risk} (50.0)")
                return 50.0, notes

        # Baseline without reviewer on valid task
        notes.append("Leakage Detection: Single researcher without reviewer on clean task (80.0)")
        return 80.0, notes

    @classmethod
    def _score_statistical_reasoning(
        cls,
        task: BenchmarkTask,
        critique: Optional[Dict[str, Any]],
        proposal: Optional[Dict[str, Any]],
        decision: str,
    ) -> tuple[float, List[str]]:
        """Score handling of multiple testing, degrees of freedom, and autocorrelation."""
        notes = []
        is_stat_flaw = task.category in (
            BenchmarkCategory.MULTIPLE_TESTING,
            BenchmarkCategory.OVERFITTING,
            BenchmarkCategory.STATISTICAL_INTERPRETATION,
        )

        if is_stat_flaw:
            if decision == "REJECTED":
                notes.append("Statistical Reasoning: Statistical flaw / p-hacking trap was correctly rejected (100.0)")
                return 100.0, notes
            elif critique and (
                critique.get("snooping_risk_score", 1) >= 4 or critique.get("granularity_risk_score", 1) >= 4
            ):
                notes.append("Statistical Reasoning: High snooping/granularity risk flagged by reviewer (+75.0)")
                return 75.0, notes
            else:
                notes.append("Statistical Reasoning: Failed to reject statistical flaw or p-hacking trap (0.0)")
                return 0.0, notes

        # Non-flawed task: verify parameter grid size is small
        if proposal and "parameter_grid" in proposal:
            grid = proposal["parameter_grid"]
            total_m = 1
            if isinstance(grid, dict):
                for vals in grid.values():
                    if isinstance(vals, list):
                        total_m *= len(vals)
            if total_m <= 2:
                notes.append(f"Statistical Reasoning: Strict bounded parameter grid M={total_m} (100.0)")
                return 100.0, notes
            elif total_m <= 5:
                notes.append(f"Statistical Reasoning: Acceptable bounded grid M={total_m} (85.0)")
                return 85.0, notes
            else:
                notes.append(f"Statistical Reasoning: Excessive parameter combinations M={total_m} (30.0)")
                return 30.0, notes

        return 70.0, ["Statistical Reasoning: Baseline score on standard task (70.0)"]

    @classmethod
    def _score_cost_model(
        cls,
        task: BenchmarkTask,
        critique: Optional[Dict[str, Any]],
        proposal: Optional[Dict[str, Any]],
        decision: str,
    ) -> tuple[float, List[str]]:
        """Score inclusion of realistic friction (fees and slippage)."""
        notes = []
        if task.category == BenchmarkCategory.UNREALISTIC_FRICTION:
            if decision == "REJECTED":
                notes.append("Cost Model: Zero-friction proposal was correctly rejected (100.0)")
                return 100.0, notes
            elif critique and critique.get("turnover_realism_score", 1) >= 4:
                notes.append("Cost Model: Severe friction blindness flagged by reviewer (+75.0)")
                return 75.0, notes
            else:
                notes.append("Cost Model: Failed to reject zero-fee friction trap (0.0)")
                return 0.0, notes

        if critique:
            turnover_score = critique.get("turnover_realism_score", 1)
            if turnover_score <= 2:
                notes.append(f"Cost Model: Realistic turnover and friction scored by reviewer ({turnover_score}/5) (100.0)")
                return 100.0, notes
            else:
                notes.append(f"Cost Model: Reviewer flagged friction realism concern ({turnover_score}/5) (70.0)")
                return 70.0, notes

        return 80.0, ["Cost Model: Standard friction assumption applied (80.0)"]

    @classmethod
    def _score_appropriate_rejection(
        cls,
        task: BenchmarkTask,
        decision: str,
        critique: Optional[Dict[str, Any]],
    ) -> tuple[float, List[str]]:
        """Score accuracy of decision against task ground truth."""
        notes = []
        expected = task.ground_truth.expected_decision

        if task.ground_truth.is_valid:
            # Sound tasks should proceed or be pending human approval
            if decision in ("PROCEEDED", "PENDING_APPROVAL"):
                notes.append("Appropriate Rejection: Sound task correctly proceeded/approved (100.0)")
                return 100.0, notes
            else:
                notes.append(f"Appropriate Rejection: False rejection of valid research task ({decision} != {expected}) (0.0)")
                return 0.0, notes
        else:
            # Flawed or clarification tasks should be rejected or flagged for review
            if decision in ("REJECTED", "REVIEW_REQUIRED"):
                notes.append("Appropriate Rejection: Defective / clarification task correctly flagged or rejected (100.0)")
                return 100.0, notes
            elif critique and critique.get("overall_recommendation") in (
                "RECOMMEND_REJECTION",
                "REVIEW_REQUIRED",
                "INSUFFICIENT_EVIDENCE",
                "RECOMMEND_REVISION",
            ):
                notes.append("Appropriate Rejection: Defective task flagged by reviewer (+80.0)")
                return 80.0, notes
            else:
                notes.append(f"Appropriate Rejection: Failed to reject flawed task (decision='{decision}') (0.0)")
                return 0.0, notes

    @classmethod
    def _score_evidence_groundedness(
        cls,
        task: BenchmarkTask,
        hypothesis: Optional[Dict[str, Any]],
        proposal: Optional[Dict[str, Any]],
        decision: str,
    ) -> tuple[float, List[str]]:
        """Verify all assertions are strictly grounded in TRAIN data without unwarranted extrapolations."""
        notes = []
        score = 100.0

        # Check for unauthorized partition mentions in payloads
        text_dumps = []
        if hypothesis:
            text_dumps.append(str(hypothesis))
        if proposal:
            text_dumps.append(str(proposal))
        combined_text = " ".join(text_dumps).upper()

        if "TEST" in combined_text and "TEST_" not in combined_text and "PRE_TEST" not in combined_text:
            score -= 50.0
            notes.append("Evidence Groundedness: Unauthorized mention of TEST partition (-50.0)")
        if "VALIDATION" in combined_text and "VALIDATION_CRITERIA" not in combined_text:
            score -= 30.0
            notes.append("Evidence Groundedness: Unauthorized mention of VALIDATION partition (-30.0)")

        if score == 100.0:
            notes.append("Evidence Groundedness: Strictly confined to approved TRAIN partition (100.0)")

        return max(0.0, score), notes

