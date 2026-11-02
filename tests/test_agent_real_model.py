"""Regression tests for Phase 2.4 Real Model Integration and Repeated Evaluation.

Verifies:
1. LocalResearchAssistant configuration (endpoint, model, temperature, timeout, token limits).
2. Loopback network containment, external redirect blocking, and permission checks.
3. Robust JSON extraction across raw text, markdown code blocks, and embedded braces.
4. Authentic HTTP REST communication via in-process LocalOpenAITestServer (OpenAI and Ollama wire protocols).
5. Delta token accounting in FullCoordinatorBenchmarkRunner (no quadratic accumulation).
6. Expanded 14-task benchmark suite (canonical 9 + expanded 5 tasks).
7. Repeated multi-temperature benchmark runner across temperatures 0.0, 0.2, 0.5, 0.8.
"""

import json
import os
import shutil
import tempfile
import unittest

from trader.agent.benchmark.baseline import SingleResearcherRunner
from trader.agent.benchmark.coordinator_eval import FullCoordinatorBenchmarkRunner
from trader.agent.benchmark.repeated_runner import RepeatedBenchmarkRunner
from trader.agent.benchmark.reviewer import ResearcherReviewerRunner
from trader.agent.benchmark.schema import BenchmarkCategory
from trader.agent.benchmark.tasks import (
    get_benchmark_tasks,
    get_canonical_tasks,
    get_expanded_tasks,
)
from trader.agent.providers.local import (
    LocalEndpointSecurityError,
    LocalResearchAssistant,
    extract_json_object,
)
from trader.agent.providers.local_server import LocalOpenAITestServer
from trader.agent.schemas import HypothesisPayload, SkepticalReviewerOutput


class TestLocalAssistantConfigurationAndSecurity(unittest.TestCase):
    """Test LocalResearchAssistant parameters, security barriers, and redirect policies."""

    def test_configurable_parameters(self) -> None:
        """Verify model, temperature, timeout, max_tokens, and seed are configurable."""
        assistant = LocalResearchAssistant(
            endpoint_url="http://127.0.0.1:11434/v1/chat/completions",
            model_identifier="qwen2.5:14b",
            temperature=0.7,
            timeout_seconds=45.0,
            max_tokens=2048,
            top_p=0.9,
            seed=1234,
            api_key="custom-secret",
        )
        self.assertEqual(assistant.model_identifier, "qwen2.5:14b")
        self.assertEqual(assistant.temperature, 0.7)
        self.assertEqual(assistant.timeout_seconds, 45.0)
        self.assertEqual(assistant.max_tokens, 2048)
        self.assertEqual(assistant.top_p, 0.9)
        self.assertEqual(assistant.seed, 1234)
        self.assertEqual(assistant.api_key, "custom-secret")

    def test_non_loopback_endpoint_rejected_by_default(self) -> None:
        """Verify external non-loopback endpoints are blocked to prevent prompt exfiltration."""
        with self.assertRaises(LocalEndpointSecurityError) as ctx:
            LocalResearchAssistant(
                endpoint_url="https://api.openai.com/v1/chat/completions",
                allow_external_endpoint=False,
            )
        self.assertIn("Non-loopback endpoint", str(ctx.exception))
        self.assertIn("rejected", str(ctx.exception))

    def test_external_endpoint_allowed_only_when_explicitly_authorized(self) -> None:
        """Verify external endpoint is accepted only when allow_external_endpoint=True."""
        assistant = LocalResearchAssistant(
            endpoint_url="https://api.openai.com/v1/chat/completions",
            allow_external_endpoint=True,
        )
        self.assertEqual(assistant.endpoint_url, "https://api.openai.com/v1/chat/completions")

    def test_invalid_scheme_rejected(self) -> None:
        """Verify non-http/https schemes are rejected."""
        with self.assertRaises(LocalEndpointSecurityError):
            LocalResearchAssistant(endpoint_url="file:///etc/passwd")


class TestRobustJSONExtraction(unittest.TestCase):
    """Test JSON extraction from raw completions, markdown code blocks, and noisy text."""

    def test_direct_raw_json(self) -> None:
        """Verify standard raw JSON parsing."""
        raw = '{"key": "value", "count": 42}'
        data = extract_json_object(raw)
        self.assertEqual(data["key"], "value")
        self.assertEqual(data["count"], 42)

    def test_markdown_code_fence_json(self) -> None:
        """Verify extraction from ```json ... ``` code blocks."""
        raw = "Here is the requested quantitative hypothesis:\n```json\n{\n  \"hypothesis_id\": \"H-01\",\n  \"status\": \"VALID\"\n}\n```\nPlease let me know if changes are required."
        data = extract_json_object(raw)
        self.assertEqual(data["hypothesis_id"], "H-01")
        self.assertEqual(data["status"], "VALID")

    def test_outer_braces_with_preamble_and_postscript(self) -> None:
        """Verify extraction when model includes reasoning before and after JSON braces."""
        raw = "Based on my analysis of the trend momentum:\n{\"decision\": \"APPROVE\", \"score\": 95}\nThis concludes my review."
        data = extract_json_object(raw)
        self.assertEqual(data["decision"], "APPROVE")
        self.assertEqual(data["score"], 95)

    def test_empty_or_invalid_text_raises_value_error(self) -> None:
        """Verify empty or non-JSON string raises informative ValueError."""
        with self.assertRaises(ValueError):
            extract_json_object("")

        with self.assertRaises(ValueError):
            extract_json_object("Just plain text with no braces.")


class TestAuthenticHTTPLoopbackCommunication(unittest.TestCase):
    """Test real TCP HTTP socket communication between LocalResearchAssistant and LocalOpenAITestServer."""

    def test_openai_chat_completions_protocol(self) -> None:
        """Verify end-to-end OpenAI wire protocol over local loopback."""
        with LocalOpenAITestServer() as server:
            assistant = LocalResearchAssistant(
                endpoint_url=server.endpoint_url,
                model_identifier="test-llm-v1",
                temperature=0.0,
            )

            resp = assistant.generate(
                role="Hypothesis Researcher",
                prompt="Evaluate momentum for SyntheticSMAStrategy on BTCUSDT Daily TRAIN.",
                schema_class=HypothesisPayload,
            )

            self.assertIsNotNone(resp.structured_data)
            self.assertEqual(resp.structured_data["strategy_name"], "SyntheticSMAStrategy")
            self.assertGreater(resp.prompt_tokens, 0)
            self.assertGreater(resp.completion_tokens, 0)
            self.assertGreater(resp.latency_ms, 0.0)
            self.assertEqual(len(resp.prompt_sha256), 64)
            self.assertEqual(len(resp.response_sha256), 64)
            self.assertEqual(len(assistant.call_history), 1)

    def test_ollama_native_chat_protocol(self) -> None:
        """Verify end-to-end Ollama native /api/chat wire protocol."""
        with LocalOpenAITestServer() as server:
            assistant = LocalResearchAssistant(
                endpoint_url=server.ollama_endpoint_url,
                model_identifier="ollama-model-v1",
                temperature=0.2,
            )

            resp = assistant.generate(
                role="Skeptical Reviewer",
                prompt="Audit hypothesis for data snooping and lookahead bias.",
                schema_class=SkepticalReviewerOutput,
            )

            self.assertIsNotNone(resp.structured_data)
            self.assertIn("overall_recommendation", resp.structured_data)
            self.assertGreater(resp.prompt_tokens, 0)

    def test_constrained_decoding_parameter(self) -> None:
        """Verify constrained decoding configuration on OpenAI and Ollama protocols."""
        with LocalOpenAITestServer() as server:
            # OpenAI wire protocol
            assistant_openai = LocalResearchAssistant(
                endpoint_url=server.endpoint_url,
                model_identifier="test-model",
                constrained_decoding=True,
            )
            self.assertTrue(assistant_openai.constrained_decoding)
            resp = assistant_openai.generate(
                role="Hypothesis Researcher",
                prompt="Propose strategy.",
                schema_class=HypothesisPayload,
            )
            self.assertIsNotNone(resp.structured_data)

            # Ollama wire protocol
            assistant_ollama = LocalResearchAssistant(
                endpoint_url=server.ollama_endpoint_url,
                model_identifier="ollama-model",
                constrained_decoding=True,
            )
            self.assertTrue(assistant_ollama.constrained_decoding)
            resp_ollama = assistant_ollama.generate(
                role="Hypothesis Researcher",
                prompt="Propose strategy.",
                schema_class=HypothesisPayload,
            )
            self.assertIsNotNone(resp_ollama.structured_data)

    def test_redirect_to_external_host_is_blocked(self) -> None:
        """Verify HTTP redirect from loopback server to external host is intercepted and blocked."""
        with LocalOpenAITestServer(inject_error="EXTERNAL_REDIRECT") as server:
            assistant = LocalResearchAssistant(
                endpoint_url=server.endpoint_url,
                allow_external_endpoint=False,
            )
            with self.assertRaises(LocalEndpointSecurityError) as ctx:
                assistant.generate(role="Coordinator", prompt="Test redirect")
            self.assertIn("redirect to non-loopback host", str(ctx.exception))

    def test_connection_timeout_handling(self) -> None:
        """Verify server timeout is cleanly converted to ConnectionError."""
        with LocalOpenAITestServer(inject_error="TIMEOUT") as server:
            assistant = LocalResearchAssistant(
                endpoint_url=server.endpoint_url,
                timeout_seconds=0.2,
            )
            with self.assertRaises(ConnectionError) as ctx:
                assistant.generate(role="Coordinator", prompt="Test timeout")
            self.assertIn("failed to reach endpoint", str(ctx.exception))


class TestExpandedBenchmarkSuite(unittest.TestCase):
    """Test expanded 14-task benchmark composition and ground truth isolation."""

    def test_canonical_vs_expanded_task_counts(self) -> None:
        """Verify canonical suite contains 9 tasks and expanded suite contains 20 tasks."""
        canonical = get_canonical_tasks()
        expanded = get_expanded_tasks()
        default_tasks = get_benchmark_tasks()

        self.assertEqual(len(canonical), 9)
        self.assertEqual(len(expanded), 20)
        self.assertEqual(len(default_tasks), 9)

    def test_expanded_task_composition(self) -> None:
        """Verify categories of new tasks in expanded suite."""
        expanded = get_expanded_tasks()
        task_ids = [t.task_id for t in expanded]

        self.assertIn("TASK-10-VALID-MEAN-REV", task_ids)
        self.assertIn("TASK-11-SURVIVORSHIP-BIAS", task_ids)
        self.assertIn("TASK-12-IN-SAMPLE-OVER-OPTIMIZATION", task_ids)
        self.assertIn("TASK-13-TEST-DATA-CONTAMINATION", task_ids)
        self.assertIn("TASK-14-VALID-VOL-BREAKOUT", task_ids)
        self.assertIn("TASK-15-VALID-IMPERFECT-ASSUMPTION", task_ids)
        self.assertIn("TASK-16-VALID-LIMITED-POWER", task_ids)
        self.assertIn("TASK-17-NEED-CLARIFICATION", task_ids)
        self.assertIn("TASK-18-SUBTLE-LOOKAHEAD", task_ids)
        self.assertIn("TASK-19-INADEQUATE-SAMPLE-SIZE", task_ids)
        self.assertIn("TASK-20-VALID-CONSERVATIVE-CHALLENGE", task_ids)

        # Total sound/valid tasks is 6 (TASK-01, TASK-10, TASK-14, TASK-15, TASK-16, TASK-20)
        valid_tasks = [t for t in expanded if t.ground_truth.is_valid]
        self.assertEqual(len(valid_tasks), 6)

        # Total flawed/clarification tasks is 14
        flawed_tasks = [t for t in expanded if not t.ground_truth.is_valid]
        self.assertEqual(len(flawed_tasks), 14)

    def test_ground_truth_isolation(self) -> None:
        """Verify expected decisions and flaw keywords are not leaked into objective prompts."""
        expanded = get_expanded_tasks()
        for t in expanded:
            self.assertNotIn(t.ground_truth.expected_decision, t.objective_prompt)
            if t.ground_truth.flaw_category:
                self.assertNotIn(t.ground_truth.flaw_category, t.objective_prompt)


class TestTokenAccountingFix(unittest.TestCase):
    """Verify delta token accounting in FullCoordinatorBenchmarkRunner."""

    def test_delta_tokens_not_accumulated_quadratically(self) -> None:
        """Verify runner counts only task-specific tokens across multiple executions."""
        with LocalOpenAITestServer() as server:
            assistant = LocalResearchAssistant(endpoint_url=server.endpoint_url)
            runner = FullCoordinatorBenchmarkRunner(provider=assistant)

            canonical = get_canonical_tasks()
            # Run two tasks
            res1 = runner.run_task(canonical[0])
            toks_task1 = res1.total_tokens

            res2 = runner.run_task(canonical[1])
            toks_task2 = res2.total_tokens

            # Before the fix, res2 would have included all tokens from task 1 plus task 2.
            # With delta accounting, toks_task2 should be independent and reflect only task 2.
            self.assertGreater(toks_task1, 0)
            self.assertGreater(toks_task2, 0)
            # Both tasks are bounded, single-task tokens should not exceed 5000 tokens
            self.assertLess(toks_task1, 5000)
            self.assertLess(toks_task2, 5000)


class TestRepeatedBenchmarkRunner(unittest.TestCase):
    """Test multi-temperature repeated evaluation harness."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_repeated_runner_execution_and_aggregation(self) -> None:
        """Verify repeated runner calculates mean, std, precision, recall, and cost-effectiveness."""
        with LocalOpenAITestServer() as server:
            def p_factory(temp: float) -> LocalResearchAssistant:
                return LocalResearchAssistant(
                    endpoint_url=server.endpoint_url,
                    temperature=temp,
                )

            # Test on a small subset of 3 tasks for fast unit test execution
            tasks_subset = get_canonical_tasks()[:3]

            runner = RepeatedBenchmarkRunner(
                provider_factory=p_factory,
                temperatures=[0.0, 0.2],
                repetitions_per_temp=2,
                configurations=["SINGLE_RESEARCHER", "RESEARCHER_REVIEWER"],
                results_dir=self.temp_dir,
                random_seed=42,
            )

            summary = runner.run_repeated_evaluation(tasks=tasks_subset)

            self.assertIn("aggregated_by_temp_and_config", summary)
            self.assertIn("raw_run_records", summary)
            self.assertEqual(len(summary["raw_run_records"]), 2 * 2 * 2)  # 2 temps * 2 reps * 2 configs = 8 runs

            # Verify aggregation keys
            for key, agg in summary["aggregated_by_temp_and_config"].items():
                self.assertIn("overall_score", agg)
                self.assertIn("rejection_recall", agg)
                self.assertIn("cost_effectiveness", agg)
                self.assertIn("quality_score_per_1k_tokens", agg["cost_effectiveness"])
                self.assertIn("quality_score_per_second", agg["cost_effectiveness"])
                self.assertEqual(agg["repetitions"], 2)


if __name__ == "__main__":
    unittest.main()

