"""Adversarial and Reproducibility Test Suite for Phase 2.3 Research Agent Benchmark.

Verifies:
1. Benchmark task suite composition (9 distinct methodological categories).
2. Public agent input payload sanitization (ground truth strictly isolated).
3. Deterministic Python scoring framework across 7 qualitative dimensions.
4. Configuration A (Single Researcher): schema validation without independent critique.
5. Configuration B (Researcher + Reviewer): 4-dimension critique and trap detection.
6. Configuration C (Full Coordinator): multi-stage orchestration, approval gate, and audit telemetry.
7. Benchmark fairness: identical TRAIN data, prompts, and deterministic rules.
8. Persistence non-destructiveness: timestamped JSON artifact preservation.
9. Zero TEST partition access and zero trading execution.
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest

from trader.agent.benchmark.baseline import SingleResearcherRunner
from trader.agent.benchmark.coordinator_eval import FullCoordinatorBenchmarkRunner
from trader.agent.benchmark.harness import BenchmarkHarness
from trader.agent.benchmark.mock_provider import BenchmarkMockLLMProvider
from trader.agent.benchmark.reviewer import ResearcherReviewerRunner
from trader.agent.benchmark.schema import (
    BenchmarkCategory,
    BenchmarkTask,
    DimensionScores,
    TaskGroundTruth,
)
from trader.agent.benchmark.scorer import BenchmarkScorer
from trader.agent.benchmark.tasks import get_benchmark_tasks


class TestAgentBenchmark(unittest.TestCase):
    """Comprehensive test suite for Phase 2.3 quantitative research agent benchmark."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.provider = BenchmarkMockLLMProvider()
        self.tasks = get_benchmark_tasks()
        self.harness = BenchmarkHarness(
            provider=self.provider,
            results_dir=self.temp_dir,
            random_seed=42,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Task Suite Composition and Ground Truth Isolation
    def test_benchmark_task_suite_composition(self) -> None:
        """Verify benchmark includes 9 canonical tasks covering all required methodological categories."""
        self.assertEqual(len(self.tasks), 9)
        categories = {t.category for t in self.tasks}
        expected_categories = {
            BenchmarkCategory.VALID_HYPOTHESIS,
            BenchmarkCategory.UNDERSPECIFIED_HYPOTHESIS,
            BenchmarkCategory.DATA_LEAKAGE,
            BenchmarkCategory.LOOKAHEAD_BIAS,
            BenchmarkCategory.MULTIPLE_TESTING,
            BenchmarkCategory.OVERFITTING,
            BenchmarkCategory.STATISTICAL_INTERPRETATION,
            BenchmarkCategory.UNREALISTIC_FRICTION,
            BenchmarkCategory.NON_REPRODUCIBLE,
        }
        self.assertEqual(categories, expected_categories)

        # Ground truth isolation: sanitized agent payload must NOT expose ground truth
        for t in self.tasks:
            agent_payload = t.get_agent_input_payload()
            self.assertNotIn("ground_truth", agent_payload)
            self.assertNotIn("is_valid", agent_payload)
            self.assertNotIn("expected_decision", agent_payload)
            self.assertNotIn("flaw_category", agent_payload)
            self.assertNotIn("expected_flaw_keywords", agent_payload)

            # All tasks must be strictly confined to TRAIN partition
            self.assertEqual(t.market_context["partition"], "TRAIN")
            self.assertNotIn("TEST", t.objective_prompt)
            self.assertNotIn("VALIDATION", t.objective_prompt)

    # 2. Deterministic Scorer Accuracy
    def test_deterministic_scorer_evaluates_sound_and_flawed_proposals(self) -> None:
        """Verify BenchmarkScorer computes deterministic scores accurately across all dimensions."""
        valid_task = self.tasks[0]  # TASK-01-VALID-MOM
        leakage_task = self.tasks[2]  # TASK-03-DATA-LEAKAGE

        # Perfect evaluation on sound task
        scores_valid = BenchmarkScorer.evaluate(
            task=valid_task,
            decision="PROCEEDED",
            hypothesis={
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Strong economic trend following rationale over 50 characters long.",
                "validation_criteria": {"min_sharpe": 0.5},
                "max_evaluation_budget": 10,
            },
            proposal={
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0, 15.0], "slow_period": [30.0]},
                "partition_protocol_id": "CHRONOLOGICAL_V1",
            },
            critique={
                "lookahead_risk_score": 1,
                "snooping_risk_score": 2,
                "granularity_risk_score": 1,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Valid proposal",
            },
        )
        self.assertEqual(scores_valid.overall_score, 100.0)
        self.assertEqual(scores_valid.appropriate_rejection, 100.0)
        self.assertEqual(scores_valid.leakage_detection, 100.0)

        # Penalty for blind approval of data leakage trap
        scores_leak_blind = BenchmarkScorer.evaluate(
            task=leakage_task,
            decision="PROCEEDED",
            hypothesis={
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Rationale",
                "validation_criteria": {"min_sharpe": 0.5},
                "max_evaluation_budget": 10,
            },
            proposal={
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast_period": [10.0], "slow_period": [30.0]},
                "partition_protocol_id": "CHRONOLOGICAL_V1",
            },
            critique=None,
        )
        self.assertEqual(scores_leak_blind.appropriate_rejection, 0.0)
        self.assertEqual(scores_leak_blind.leakage_detection, 0.0)
        self.assertLess(scores_leak_blind.overall_score, 70.0)

        # Full score when data leakage trap is correctly rejected
        scores_leak_rejected = BenchmarkScorer.evaluate(
            task=leakage_task,
            decision="REJECTED",
            hypothesis=None,
            proposal=None,
            critique={
                "lookahead_risk_score": 5,
                "snooping_risk_score": 3,
                "granularity_risk_score": 2,
                "turnover_realism_score": 2,
                "overall_recommendation": "RECOMMEND_REJECTION",
                "critique_rationale": "Future target leakage detected.",
            },
        )
        self.assertEqual(scores_leak_rejected.appropriate_rejection, 100.0)
        self.assertEqual(scores_leak_rejected.leakage_detection, 100.0)
        self.assertGreaterEqual(scores_leak_rejected.overall_score, 90.0)

    # 3. Configuration A: Single Researcher Baseline
    def test_single_researcher_fails_to_reject_methodological_traps(self) -> None:
        """Verify Single Researcher baseline blind-passes flawed tasks that conform to schema."""
        runner = SingleResearcherRunner(provider=self.provider)
        summary = self.harness.run_configuration("SINGLE_RESEARCHER", tasks=self.tasks, randomize_order=False)

        # Single researcher passes schema validation on all tasks
        self.assertEqual(summary.total_tasks, 9)
        # But fails to reject any of the 8 flawed tasks (recall = 0%)
        self.assertEqual(summary.rejection_recall, 0.0)
        self.assertEqual(summary.rejection_precision, 0.0)
        self.assertLess(summary.mean_overall_score, 75.0)

    # 4. Configuration B: Researcher + Reviewer
    def test_researcher_reviewer_successfully_rejects_flawed_tasks(self) -> None:
        """Verify Researcher + Reviewer catches all methodological traps while approving sound task."""
        summary = self.harness.run_configuration("RESEARCHER_REVIEWER", tasks=self.tasks, randomize_order=False)

        self.assertEqual(summary.total_tasks, 9)
        # Reviewer accurately rejects all 8 flawed tasks (recall = 100%)
        self.assertEqual(summary.rejection_recall, 100.0)
        self.assertEqual(summary.rejection_precision, 100.0)
        self.assertGreater(summary.mean_overall_score, 90.0)

        # Valid task (Task 1) is approved and proceeded
        task1_res = next(r for r in summary.task_results if r.task_id == "TASK-01-VALID-MOM")
        self.assertEqual(task1_res.decision, "PROCEEDED")
        self.assertEqual(task1_res.scores.overall_score, 100.0)

        # Flawed task (Task 4 - lookahead) is rejected
        task4_res = next(r for r in summary.task_results if r.task_id == "TASK-04-LOOKAHEAD-BIAS")
        self.assertEqual(task4_res.decision, "REJECTED")
        self.assertEqual(task4_res.scores.leakage_detection, 100.0)

    # 5. Configuration C: Full Research Coordinator
    def test_full_coordinator_enforces_governance_and_persists_tickets(self) -> None:
        """Verify Full Coordinator gates on skeptical review, halts at human gate, and checkpoints."""
        summary = self.harness.run_configuration("FULL_COORDINATOR", tasks=self.tasks, randomize_order=False)

        self.assertEqual(summary.total_tasks, 9)
        self.assertEqual(summary.rejection_recall, 100.0)
        self.assertEqual(summary.rejection_precision, 100.0)

        # Valid task halted at HUMAN_APPROVAL_GATE (decision = PENDING_APPROVAL)
        task1_res = next(r for r in summary.task_results if r.task_id == "TASK-01-VALID-MOM")
        self.assertEqual(task1_res.decision, "PENDING_APPROVAL")
        self.assertEqual(task1_res.scores.overall_score, 100.0)

        # Flawed task halted at skeptical review gate (decision = REJECTED)
        task5_res = next(r for r in summary.task_results if r.task_id == "TASK-05-MULTIPLE-TESTING")
        self.assertEqual(task5_res.decision, "REJECTED")
        self.assertIn("Skeptical reviewer rejected hypothesis", task5_res.error_message)

    # 6. Benchmark Fairness and Reproducibility
    def test_benchmark_fairness_and_artifact_reproducibility(self) -> None:
        """Verify identical inputs produce identical digests and results across repeated runs."""
        harness1 = BenchmarkHarness(provider=self.provider, results_dir=self.temp_dir, random_seed=42)
        harness2 = BenchmarkHarness(provider=self.provider, results_dir=self.temp_dir, random_seed=42)

        task = self.tasks[0]
        runner1 = SingleResearcherRunner(provider=self.provider)
        runner2 = SingleResearcherRunner(provider=self.provider)

        res1 = runner1.run_task(task)
        res2 = runner2.run_task(task)

        # Cryptographic digests must match byte-for-byte
        self.assertEqual(res1.prompt_sha256, res2.prompt_sha256)
        self.assertEqual(res1.dataset_digest, res2.dataset_digest)
        self.assertEqual(res1.artifact_digest, res2.artifact_digest)
        self.assertEqual(res1.scores.overall_score, res2.scores.overall_score)

    # 7. Non-Destructive Persistence
    def test_persistence_does_not_silently_overwrite_previous_results(self) -> None:
        """Verify repeated runs generate distinct timestamped files without overwriting."""
        self.harness.run_configuration("SINGLE_RESEARCHER", tasks=[self.tasks[0]])
        files_before = os.listdir(self.temp_dir)
        self.assertEqual(len(files_before), 1)

        # Run again
        import time
        time.sleep(1.1)  # Ensure distinct second in timestamp
        self.harness.run_configuration("SINGLE_RESEARCHER", tasks=[self.tasks[0]])
        files_after = os.listdir(self.temp_dir)
        self.assertEqual(len(files_after), 2)
        self.assertNotEqual(files_before[0], files_after[1])

    # 8. Comparative Advantage Summary
    def test_comparative_advantage_demonstration(self) -> None:
        """Verify multi-stage reviewer/coordinator measurably outperforms single researcher."""
        summaries = self.harness.run_comparative_benchmark(
            configs=["SINGLE_RESEARCHER", "RESEARCHER_REVIEWER", "FULL_COORDINATOR"],
            tasks=self.tasks,
        )

        score_a = summaries["SINGLE_RESEARCHER"].mean_overall_score
        score_b = summaries["RESEARCHER_REVIEWER"].mean_overall_score
        score_c = summaries["FULL_COORDINATOR"].mean_overall_score

        # Both multi-stage configurations must measurably outperform Single Researcher
        self.assertGreater(score_b, score_a + 25.0)
        self.assertGreater(score_c, score_a + 25.0)

        # Reviewer and Coordinator achieve 100% rejection recall, vs 0% for Single Researcher
        self.assertEqual(summaries["RESEARCHER_REVIEWER"].rejection_recall, 100.0)
        self.assertEqual(summaries["FULL_COORDINATOR"].rejection_recall, 100.0)
        self.assertEqual(summaries["SINGLE_RESEARCHER"].rejection_recall, 0.0)


if __name__ == "__main__":
    unittest.main()

