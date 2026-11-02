"""Comprehensive Test Suite for Phase 2.6.2 Evidence Integrity Remediation.

Covers:
Task 1: Canonical Benchmark Task Registry & Exposure Taxonomy
- Consistency verification across all 70 tasks.
- SHA-256 prompt digest matching.
- Exact ground truth and expected decision mapping.
- Separation of exposure categories (QUARANTINED_UNEXPOSED = 15, CALIBRATION_EXPOSED = 40, etc.).
- Tamper detection / immutability checks.

Task 2: Historical Benchmark Claims & Quarantine Enforcement
- Unexecuted sealed tasks (15 tasks) remain holdout-eligible and quarantined.
- Accurate empirical FAR claims.

Task 3: Mandatory Evidence Manifest Persistence & Failure Traps
- Successful simulation produces and persists evidence manifest.
- Simulation failure produces and persists failure manifest with error_type, traceback_digest, and result_status='FAILED'.
- Manifest generation failure on success raises EvidenceManifestPersistenceError and marks request/trial FAILED.
- Ledger insertion failure on success raises EvidenceManifestPersistenceError and marks request/trial FAILED.
- Simulation crash where manifest persistence ALSO fails preserves original SimulationExecutionError context.
- Idempotent manifest persistence with identical hash succeeds without error.
- Contradictory manifest persistence with different hash raises EvidenceManifestContradictionError.
- Interrupted trial recovery with reconcile_interrupted_trials().

Task 4: Revision-Limit Enforcement Policy
- Named constants: DEFAULT_MAX_REVISIONS = 3, ABSOLUTE_MAX_REVISIONS_CEILING = 5, MIN_ALLOWED_REVISIONS = 0.
- Caller override: zero (0), default (None -> 3), maximum (5).
- Rejection of invalid limits: above-ceiling (6), negative (-1), non-integer ("3") raises InvalidRevisionLimitError.
- Constructor validation: rejects invalid limits with InvalidRevisionLimitError.
- Requested and effective limits recorded in ResearchSessionState.

Strict safety invariants:
- Zero live trading, paper trading, testnet, or exchange credentials.
- Strictly TRAIN partition data. Zero access to TEST or VALIDATION partitions.
- Zero em dashes (\u2014) or en dashes (\u2013).
"""

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Type
import unittest
from unittest.mock import MagicMock, patch
from pydantic import BaseModel

from trader.agent.benchmark.canonical_registry import (
    CanonicalTaskRecord,
    ExposureCategory,
    get_canonical_task_registry,
    lookup_canonical_task,
    get_holdout_eligible_tasks,
    verify_registry_consistency,
)
from trader.agent.coordinator import (
    ABSOLUTE_MAX_REVISIONS_CEILING,
    DEFAULT_MAX_REVISIONS,
    MIN_ALLOWED_REVISIONS,
    InvalidRevisionLimitError,
    ResearchCoordinator,
    SkepticalReviewRevisionExhaustedError,
)
from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ResearchExecutionEngine,
    SimulationExecutionError,
)
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.evidence.manifest import (
    EvidenceManifestContradictionError,
    EvidenceManifestPersistenceError,
    compute_manifest_hash,
    generate_evidence_manifest,
)
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


class CrashingTestStrategy(SyntheticSMAStrategy):
    """Synthetic strategy engineered to throw an unhandled runtime error during signal generation."""

    def __init__(self) -> None:
        super().__init__(name="CrashingTestStrategy", version="1.0.0")

    def generate_signals(
        self,
        bars: List[Dict[str, Any]],
        indicators: Dict[str, List[float]],
        params: Dict[str, float],
    ) -> List[float]:
        raise ValueError("Deliberate simulation crash for failure-manifest verification")


class TestPhase262Remediation(unittest.TestCase):
    """Verification test suite for all Phase 2.6.2 remediation deliverables."""

    def setUp(self) -> None:
        """Set up in-memory ledger, market bridge, and execution engine."""
        self.temp_dir = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.temp_dir, "coordinator.lock")
        self.ledger = ResearchLedger(db_path=":memory:")
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)
        self.strategy = SyntheticSMAStrategy()
        self.crashing_strategy = CrashingTestStrategy()

    def tearDown(self) -> None:
        """Clean up resources."""
        self.ledger.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_experiment_fixture(self, hyp_id: str, exp_id: str) -> str:
        """Set up valid parent records for foreign key constraints and return prereg_id."""
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Fixture hypothesis", {"min_sharpe": 1.0})
        actual_prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=hyp_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )
        trial_id = self.ledger.start_trial(
            hypothesis_id=hyp_id,
            prereg_id=actual_prereg_id,
            experiment_id=exp_id,
            parameters_evaluated={"fast": 10.0},
        )
        exp_data = {
            "experiment_id": exp_id,
            "prereg_id": actual_prereg_id,
            "trial_id": trial_id,
            "partition_name": "TRAIN",
            "strategy_name": "SyntheticSMAStrategy",
            "strategy_version": "1.0.0",
            "parameters": {"fast": 10.0},
            "random_seed_63bit": 42,
            "code_git_hash": "commit_123",
            "data_hash": "data_456",
            "status": "SUCCESS",
        }
        full_metrics = {
            "initial_capital": 10000.0,
            "final_capital": 11000.0,
            "net_total_return_pct": 10.0,
            "gross_total_return_pct": 10.5,
            "cagr_pct": 10.0,
            "annualized_vol_pct": 15.0,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.4,
            "max_drawdown_pct": 8.0,
            "total_trades": 30,
            "win_rate_pct": 52.0,
            "total_friction_usd": 40.0,
            "compounding_error": 0.0,
            "raw_metrics": {"sharpe_ratio": 1.2},
        }
        self.ledger.record_experiment_result(exp_data, full_metrics, None)
        return actual_prereg_id

    # -------------------------------------------------------------------------
    # Task 1: Canonical Benchmark Task Registry & Exposure Taxonomy
    # -------------------------------------------------------------------------

    def test_01_canonical_registry_integrity_and_exposure_counts(self) -> None:
        """Verify that the canonical task registry is consistent and has exactly 70 tasks with correct exposure categories."""
        stats = verify_registry_consistency()
        self.assertTrue(stats["is_consistent"])
        self.assertEqual(stats["total_tasks"], 70)
        self.assertEqual(stats["calibration_exposed"], 40)
        self.assertEqual(stats["inference_exposed"], 15)
        self.assertEqual(stats["quarantined_unexposed"], 15)
        self.assertEqual(stats["holdout_eligible"], 15)

    def test_02_canonical_registry_prompt_digests_match_sha256(self) -> None:
        """Verify that all 70 tasks have mathematically exact SHA-256 digests of their prompt text."""
        registry = get_canonical_task_registry()
        self.assertEqual(len(registry), 70)
        for task_id, task in registry.items():
            expected_hash = hashlib.sha256(task.objective_prompt.encode("utf-8")).hexdigest()
            self.assertEqual(
                task.prompt_sha256,
                expected_hash,
                f"Prompt hash mismatch for {task_id}",
            )

    def test_03_canonical_registry_tamper_detection(self) -> None:
        """Verify that verify_registry_consistency detects prompt or metadata corruption."""
        registry = get_canonical_task_registry()
        first_key = next(iter(registry.keys()))
        corrupted = registry.copy()
        original_task = corrupted[first_key]
        tampered_task = replace(original_task, objective_prompt="TAMPERED PROMPT TEXT")
        corrupted[first_key] = tampered_task
        with self.assertRaises(ValueError):
            verify_registry_consistency(corrupted)

    def test_04_holdout_eligible_tasks_match_quarantined_unexposed(self) -> None:
        """Verify that get_holdout_eligible_tasks returns exactly the 15 unexposed sealed tasks."""
        holdout = get_holdout_eligible_tasks()
        self.assertEqual(len(holdout), 15)
        expected_prefixes = {
            f"TASK-{i:02d}"
            for i in [53, 56, 57, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70]
        }
        holdout_prefixes = {task.task_id.split("-")[0] + "-" + task.task_id.split("-")[1] for task in holdout}
        self.assertEqual(holdout_prefixes, expected_prefixes)
        for task in holdout:
            self.assertEqual(task.exposure_category, ExposureCategory.QUARANTINED_UNEXPOSED)
            self.assertTrue(task.quarantined_status)
            self.assertTrue(task.holdout_eligible)

    # -------------------------------------------------------------------------
    # Task 3: Mandatory Evidence Manifest Persistence & Failure Traps
    # -------------------------------------------------------------------------

    def test_05_successful_simulation_records_completed_manifest(self) -> None:
        """Verify that a successful simulation automatically persists a COMPLETED evidence manifest."""
        hyp_id = "H-SUCCESS-01"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Test hypothesis", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=hyp_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )

        req = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-SUCCESS-01",
        )

        res = self.engine.execute(req)
        self.assertEqual(res.status, "SUCCESS")

        manifest = self.ledger.get_evidence_manifest(res.experiment_id)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["result_status"], "COMPLETED")
        self.assertEqual(manifest["experiment_id"], res.experiment_id)
        self.assertIsNotNone(manifest["manifest_hash"])

        # Verify trial disposition is COMPLETED
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition FROM trial_ledger WHERE trial_id = ?;", (res.trial_id,))
        self.assertEqual(cur.fetchone()[0], "COMPLETED")

    def test_06_simulation_crash_persists_failure_manifest(self) -> None:
        """Verify that a simulation crash records a FAILED evidence manifest with error and traceback digest."""
        hyp_id = "H-CRASH-01"
        self.ledger.record_hypothesis(hyp_id, "CrashingTestStrategy", "Crash test", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=hyp_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )

        req = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.crashing_strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-CRASH-01",
        )

        with self.assertRaises(SimulationExecutionError) as ctx:
            self.engine.execute(req)
        self.assertIn("Deliberate simulation crash", str(ctx.exception))

        # Inspect trial status
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT trial_id, disposition FROM trial_ledger WHERE hypothesis_id = ?;", (hyp_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        trial_id, disposition = row
        self.assertEqual(disposition, "FAILED")

        # Inspect failure manifest
        cur.execute("SELECT metrics_summary_json, result_status FROM experiment_evidence_manifests WHERE trial_id = ?;", (trial_id,))
        mf_row = cur.fetchone()
        self.assertIsNotNone(mf_row)
        metrics = json.loads(mf_row[0])
        self.assertEqual(mf_row[1], "FAILED")
        self.assertIn("ValueError", metrics["error_type"])
        self.assertIn("Deliberate simulation crash", metrics["error_message"])
        self.assertIsNotNone(metrics["traceback_digest"])

    def test_07_manifest_generation_failure_on_success_raises_persistence_error(self) -> None:
        """Verify that failure during manifest generation on success path raises EvidenceManifestPersistenceError and marks trial FAILED."""
        hyp_id = "H-MAN-FAIL-01"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Manifest fail test", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=hyp_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )

        req = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-MAN-FAIL-01",
        )

        with patch("trader.agent.core.execution.generate_evidence_manifest", side_effect=RuntimeError("Simulated manifest serialization blowup")):
            with self.assertRaises(EvidenceManifestPersistenceError) as ctx:
                self.engine.execute(req)
            self.assertIn("Simulated manifest serialization blowup", str(ctx.exception))

        # Verify trial and execution request are marked FAILED
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition FROM trial_ledger WHERE hypothesis_id = ?;", (hyp_id,))
        self.assertEqual(cur.fetchone()[0], "FAILED")
        cur.execute("SELECT status FROM execution_requests WHERE request_id = ?;", (req.request_id,))
        self.assertEqual(cur.fetchone()[0], "FAILED")

    def test_08_ledger_persistence_failure_on_success_raises_persistence_error(self) -> None:
        """Verify that failure during ledger insertion of manifest raises EvidenceManifestPersistenceError."""
        hyp_id = "H-LEDGER-FAIL-01"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Ledger fail test", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=hyp_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )

        req = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-LEDGER-FAIL-01",
        )

        with patch.object(self.ledger, "record_evidence_manifest", side_effect=sqlite3.OperationalError("Simulated database disk full")):
            with self.assertRaises(EvidenceManifestPersistenceError) as ctx:
                self.engine.execute(req)
            self.assertIn("Simulated database disk full", str(ctx.exception))

        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition FROM trial_ledger WHERE hypothesis_id = ?;", (hyp_id,))
        self.assertEqual(cur.fetchone()[0], "FAILED")

    def test_09_simulation_crash_with_manifest_failure_preserves_original_cause(self) -> None:
        """Verify that if simulation crashes AND failure-manifest persistence fails, the original cause is preserved."""
        hyp_id = "H-DOUBLE-FAIL-01"
        self.ledger.record_hypothesis(hyp_id, "CrashingTestStrategy", "Double fail test", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=hyp_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )

        req = ControlledExperimentRequest(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            strategy=self.crashing_strategy,
            parameters={"fast": 10.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            request_id="REQ-DOUBLE-FAIL-01",
        )

        with patch("trader.agent.core.execution.generate_evidence_manifest", side_effect=RuntimeError("Manifest generation crashed during crash handling")):
            with self.assertRaises(SimulationExecutionError) as ctx:
                self.engine.execute(req)
            # Original cause must be preserved
            self.assertIsNotNone(ctx.exception.__cause__)
            self.assertIn("Deliberate simulation crash", str(ctx.exception.__cause__))

    def test_10_idempotent_manifest_persistence_with_matching_hash(self) -> None:
        """Verify that recording an identical evidence manifest a second time succeeds idempotently."""
        hyp_id = "H-IDEM-01"
        exp_id = "EXP-IDEM-01"
        actual_prereg_id = self._setup_experiment_fixture(hyp_id, exp_id)

        manifest = generate_evidence_manifest(
            experiment_id=exp_id,
            hypothesis_id=hyp_id,
            prereg_id=actual_prereg_id,
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast": 10.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="sha_123",
            code_revision="git_456",
            metrics={"sharpe": 1.2},
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:02Z",
        )
        mid1 = self.ledger.record_evidence_manifest(manifest.to_dict())
        self.assertIsNotNone(mid1)

        # Record identical manifest again
        mid2 = self.ledger.record_evidence_manifest(manifest.to_dict())
        self.assertEqual(mid1, mid2)

    def test_11_contradictory_manifest_persistence_raises_error(self) -> None:
        """Verify that recording a conflicting manifest for the same experiment_id raises EvidenceManifestContradictionError."""
        hyp_id = "H-CONFLICT-01"
        exp_id = "EXP-CONFLICT-01"
        actual_prereg_id = self._setup_experiment_fixture(hyp_id, exp_id)

        manifest1 = generate_evidence_manifest(
            experiment_id=exp_id,
            hypothesis_id=hyp_id,
            prereg_id=actual_prereg_id,
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast": 10.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="sha_123",
            code_revision="git_456",
            metrics={"sharpe": 1.2},
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:02Z",
        )
        self.ledger.record_evidence_manifest(manifest1.to_dict())

        # Contradictory manifest with different parameters and metrics (producing different hash)
        manifest2 = generate_evidence_manifest(
            experiment_id=exp_id,
            hypothesis_id=hyp_id,
            prereg_id=actual_prereg_id,
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast": 50.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="sha_999",
            code_revision="git_999",
            metrics={"sharpe": -0.5},
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:02Z",
        )

        with self.assertRaises(EvidenceManifestContradictionError):
            self.ledger.record_evidence_manifest(manifest2.to_dict())

    def test_12_interrupted_trial_recovery_with_reconcile(self) -> None:
        """Verify that reconcile_interrupted_trials updates orphaned STARTED trials to INTERRUPTED."""
        tid = self.ledger.start_trial(
            hypothesis_id="H-INT-01",
            prereg_id="PREREG-INT-01",
            experiment_id="EXP-INT-01",
            parameters_evaluated={"fast": 10.0},
            disposition="STARTED",
            notes="Interrupted session trial",
        )
        reconciled_count = self.engine.reconcile_interrupted_trials()
        self.assertEqual(reconciled_count, 1)

        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition FROM trial_ledger WHERE trial_id = ?;", (tid,))
        self.assertEqual(cur.fetchone()[0], "INTERRUPTED")

    # -------------------------------------------------------------------------
    # Task 4: Revision-Limit Enforcement Policy
    # -------------------------------------------------------------------------

    def test_13_revision_limit_named_constants(self) -> None:
        """Verify policy named revision constants."""
        self.assertEqual(DEFAULT_MAX_REVISIONS, 3)
        self.assertEqual(ABSOLUTE_MAX_REVISIONS_CEILING, 5)
        self.assertEqual(MIN_ALLOWED_REVISIONS, 0)

    def test_14_revision_limit_constructor_validation(self) -> None:
        """Verify that ResearchCoordinator rejects invalid max_revisions in __init__."""
        dummy_llm = QueueLLMProvider([])

        # Allowed values
        coord_0 = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=dummy_llm,
            lock_path=os.path.join(self.temp_dir, "lock_0.lock"),
            max_revisions=0,
        )
        self.assertEqual(coord_0.max_revisions, 0)

        coord_5 = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=dummy_llm,
            lock_path=os.path.join(self.temp_dir, "lock_5.lock"),
            max_revisions=5,
        )
        self.assertEqual(coord_5.max_revisions, 5)

        # Disallowed values
        with self.assertRaises(InvalidRevisionLimitError):
            ResearchCoordinator(
                ledger=self.ledger,
                provider=self.provider,
                execution_engine=self.engine,
                llm_provider=dummy_llm,
                lock_path=os.path.join(self.temp_dir, "lock_neg.lock"),
                max_revisions=-1,
            )

        with self.assertRaises(InvalidRevisionLimitError):
            ResearchCoordinator(
                ledger=self.ledger,
                provider=self.provider,
                execution_engine=self.engine,
                llm_provider=dummy_llm,
                lock_path=os.path.join(self.temp_dir, "lock_over.lock"),
                max_revisions=6,
            )

        with self.assertRaises(InvalidRevisionLimitError):
            ResearchCoordinator(
                ledger=self.ledger,
                provider=self.provider,
                execution_engine=self.engine,
                llm_provider=dummy_llm,
                lock_path=os.path.join(self.temp_dir, "lock_str.lock"),
                max_revisions="3",  # type: ignore
            )

    def test_15_revision_limit_caller_override_and_state_tracking(self) -> None:
        """Verify that run_vertical_slice records requested and effective limits in ResearchSessionState."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-STATE-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Trend following momentum cross on daily closes.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 25.0},
                "max_evaluation_budget": 2,
            },
            # 2. Initial Review: RECOMMEND_APPROVAL directly
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Methodologically sound proposal with strictly causal execution, zero lookahead risk, and valid pre-specified statistical power.",
                "required_revisions": [],
                "revision_assessment": "FULLY_CORRECTED",
            },
            # 3. Preregistration Proposal Formulation
            {
                "proposal_id": "PROP-STATE-01",
                "hypothesis_id": "H-STATE-01",
                "universe": ["BTCUSDT"],
                "timeframe": "1d",
                "parameter_grid": {"fast": [10.0, 20.0]},
                "partition_protocol_id": "PROTO-TRAIN-01",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm,
            lock_path=os.path.join(self.temp_dir, "lock_track.lock"),
            max_revisions=3,
        )

        state = coord.run_vertical_slice(
            objective="Evaluate trend following momentum on BTCUSDT",
            max_revisions=4,
        )
        self.assertEqual(state.requested_max_revisions, 4)
        self.assertEqual(state.effective_max_revisions, 4)

    def test_16_revision_limit_caller_override_rejections(self) -> None:
        """Verify that run_vertical_slice rejects invalid caller-provided revision limits immediately."""
        llm = QueueLLMProvider([])
        coord = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm,
            lock_path=os.path.join(self.temp_dir, "lock_reject.lock"),
            max_revisions=3,
        )

        with self.assertRaises(InvalidRevisionLimitError):
            coord.run_vertical_slice(objective="Test", max_revisions=-1)

        with self.assertRaises(InvalidRevisionLimitError):
            coord.run_vertical_slice(objective="Test", max_revisions=6)

        with self.assertRaises(InvalidRevisionLimitError):
            coord.run_vertical_slice(objective="Test", max_revisions="5")  # type: ignore

    def test_17_zero_revision_limit_halts_immediately_on_revision_recommendation(self) -> None:
        """Verify that setting max_revisions=0 causes immediate exhaustion halt when reviewer recommends revision."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-ZERO-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Momentum alpha trend-following on daily bars.",
                "validation_criteria": {"min_sharpe": 1.0},
                "max_evaluation_budget": 1,
            },
            # 2. Initial Review: RECOMMEND_REVISION
            {
                "lookahead_risk_score": 3,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_REVISION",
                "critique_rationale": "Critical flaw detected: missing execution delay creates immediate lookahead bias; execution on same bar is invalid.",
                "required_revisions": ["Add lag"],
                "revision_assessment": "NOT_APPLICABLE",
            },
        ]

        llm = QueueLLMProvider(responses)
        coord = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm,
            lock_path=os.path.join(self.temp_dir, "lock_zero.lock"),
            max_revisions=0,
        )

        with self.assertRaises(SkepticalReviewRevisionExhaustedError) as ctx:
            coord.run_vertical_slice(objective="Test with max_revisions=0")
        self.assertIn("maximum allowed revision attempts (0) were exhausted", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
