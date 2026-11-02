"""Comprehensive Verification Test Suite for Phase 2.6.2.1 Final Verification Closure.

Covers:
1. Revision Limit Validation:
   - Explicit rejection of Boolean values (True, False) across constructor,
     run_vertical_slice, and execute_research_cycle entry points.
   - Validation of boundary values: 0 (minimum), 3 (default), 5 (absolute ceiling).
   - Rejection of negative integers (-1, -10), above-ceiling integers (6, 100),
     and non-integer types (float, string, None).
2. Authoritative Entry Point:
   - Verification of ResearchCoordinator.execute_research_cycle() delegating cleanly
     with full parameter validation.
3. ResearcherReviewerRunner Validation:
   - Verification that ResearcherReviewerRunner rejects Booleans and invalid limits.
4. Subprocess-Based Interruption Lifecycle Test:
   - Simulation completes.
   - Experiment record is persisted in SQLite experiments table.
   - Process terminates abruptly via os._exit() before evidence manifest persistence.
   - Process restarts.
   - Recovery detects and reconciles the incomplete evidence lifecycle.
   - Verifies trial is marked INTERRUPTED, execution request is marked INTERRUPTED.
   - Verifies recovery does not silently mark experiment complete without manifest.
   - Verifies no duplicate or contradictory records are created.

Invariants Enforced:
- Strictly offline research only. No live trading, paper trading, or exchange credentials.
- Zero access to VALIDATION or TEST partitions. M_test = 0 maintained.
- Zero em dashes (\\u2014) and zero en dashes (\\u2013).
"""

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Type
import unittest

from pydantic import BaseModel

from trader.agent.benchmark.reviewer import ResearcherReviewerRunner
from trader.agent.coordinator import (
    ABSOLUTE_MAX_REVISIONS_CEILING,
    DEFAULT_MAX_REVISIONS,
    InvalidRevisionLimitError,
    MIN_ALLOWED_REVISIONS,
    ResearchCoordinator,
    SkepticalReviewRevisionExhaustedError,
)
from trader.agent.core.execution import (
    ControlledExperimentRequest,
    EvidenceManifestContradictionError,
    InterruptedTrialRetryError,
    ResearchExecutionEngine,
    generate_evidence_manifest,
)
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.providers.base import LLMProvider, LLMResponse
from trader.agent.schemas import HypothesisPayload, SkepticalReviewerOutput
from trader.agent.tools.market_bridge import MarketDataProvider


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class QueueLLMProvider(LLMProvider):
    """Deterministic queue-based LLM provider for unit tests."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        super().__init__(name="QueueLLMProvider", model_identifier="queue-mock-v1")
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


class TestPhase2621Verification(unittest.TestCase):
    """Test suite verifying Phase 2.6.2.1 Final Verification Closure deliverables."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.temp_dir, "test_coord.lock")
        self.db_path = os.path.join(self.temp_dir, "test_research.db")
        self.ledger = ResearchLedger(db_path=self.db_path)
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)

    def tearDown(self) -> None:
        self.ledger.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Revision Limit Validation: Booleans & Integers in Constructor
    # -------------------------------------------------------------------------

    def test_01_revision_limit_rejects_booleans_in_coordinator_init(self) -> None:
        """Verify that ResearchCoordinator.__init__ explicitly rejects True and False."""
        dummy_llm = QueueLLMProvider([])

        # True is a subclass of int in Python, must be rejected
        with self.assertRaises(InvalidRevisionLimitError) as ctx_true:
            ResearchCoordinator(
                ledger=self.ledger,
                provider=self.provider,
                execution_engine=self.engine,
                llm_provider=dummy_llm,
                lock_path=os.path.join(self.temp_dir, "lock_true.lock"),
                max_revisions=True,  # type: ignore
            )
        self.assertIn("Booleans explicitly rejected", str(ctx_true.exception))

        # False is a subclass of int in Python, must be rejected
        with self.assertRaises(InvalidRevisionLimitError) as ctx_false:
            ResearchCoordinator(
                ledger=self.ledger,
                provider=self.provider,
                execution_engine=self.engine,
                llm_provider=dummy_llm,
                lock_path=os.path.join(self.temp_dir, "lock_false.lock"),
                max_revisions=False,  # type: ignore
            )
        self.assertIn("Booleans explicitly rejected", str(ctx_false.exception))

    def test_02_revision_limit_rejects_booleans_in_reviewer_runner_init(self) -> None:
        """Verify that ResearcherReviewerRunner.__init__ explicitly rejects True and False."""
        dummy_llm = QueueLLMProvider([])

        with self.assertRaises(InvalidRevisionLimitError) as ctx_true:
            ResearcherReviewerRunner(provider=dummy_llm, max_revisions=True)  # type: ignore
        self.assertIn("Booleans explicitly rejected", str(ctx_true.exception))

        with self.assertRaises(InvalidRevisionLimitError) as ctx_false:
            ResearcherReviewerRunner(provider=dummy_llm, max_revisions=False)  # type: ignore
        self.assertIn("Booleans explicitly rejected", str(ctx_false.exception))

    def test_03_revision_limit_constructor_boundaries_and_invalid_integers(self) -> None:
        """Verify boundary values (0, 3, 5) and invalid integers (-1, 6) in constructor."""
        dummy_llm = QueueLLMProvider([])

        # Valid boundaries: 0, 3, 5
        coord_0 = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=dummy_llm,
            lock_path=os.path.join(self.temp_dir, "lock_b0.lock"),
            max_revisions=0,
        )
        self.assertEqual(coord_0.max_revisions, 0)

        coord_3 = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=dummy_llm,
            lock_path=os.path.join(self.temp_dir, "lock_b3.lock"),
            max_revisions=3,
        )
        self.assertEqual(coord_3.max_revisions, 3)

        coord_5 = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=dummy_llm,
            lock_path=os.path.join(self.temp_dir, "lock_b5.lock"),
            max_revisions=5,
        )
        self.assertEqual(coord_5.max_revisions, 5)

        # Invalid integers: below 0, above 5
        with self.assertRaises(InvalidRevisionLimitError):
            ResearchCoordinator(
                ledger=self.ledger,
                provider=self.provider,
                execution_engine=self.engine,
                llm_provider=dummy_llm,
                lock_path=os.path.join(self.temp_dir, "lock_neg1.lock"),
                max_revisions=-1,
            )

        with self.assertRaises(InvalidRevisionLimitError):
            ResearchCoordinator(
                ledger=self.ledger,
                provider=self.provider,
                execution_engine=self.engine,
                llm_provider=dummy_llm,
                lock_path=os.path.join(self.temp_dir, "lock_6.lock"),
                max_revisions=6,
            )

        # Invalid non-integer types
        for invalid_val in [2.5, "3", [2], None]:
            with self.assertRaises(InvalidRevisionLimitError):
                ResearchCoordinator(
                    ledger=self.ledger,
                    provider=self.provider,
                    execution_engine=self.engine,
                    llm_provider=dummy_llm,
                    lock_path=os.path.join(self.temp_dir, f"lock_inv_{type(invalid_val).__name__}.lock"),
                    max_revisions=invalid_val,  # type: ignore
                )

    # -------------------------------------------------------------------------
    # 2. Revision Limit Validation: Booleans & Integers in run_vertical_slice
    # -------------------------------------------------------------------------

    def test_04_run_vertical_slice_rejects_booleans_and_invalid_overrides(self) -> None:
        """Verify that run_vertical_slice explicitly rejects True, False, -1, 6, and non-ints."""
        dummy_llm = QueueLLMProvider([])
        coord = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=dummy_llm,
            lock_path=os.path.join(self.temp_dir, "lock_rvs.lock"),
            max_revisions=3,
        )

        # Boolean rejection
        with self.assertRaises(InvalidRevisionLimitError) as ctx_true:
            coord.run_vertical_slice(objective="Test", max_revisions=True)  # type: ignore
        self.assertIn("Booleans explicitly rejected", str(ctx_true.exception))

        with self.assertRaises(InvalidRevisionLimitError) as ctx_false:
            coord.run_vertical_slice(objective="Test", max_revisions=False)  # type: ignore
        self.assertIn("Booleans explicitly rejected", str(ctx_false.exception))

        # Boundary violations
        with self.assertRaises(InvalidRevisionLimitError):
            coord.run_vertical_slice(objective="Test", max_revisions=-1)

        with self.assertRaises(InvalidRevisionLimitError):
            coord.run_vertical_slice(objective="Test", max_revisions=6)

        with self.assertRaises(InvalidRevisionLimitError):
            coord.run_vertical_slice(objective="Test", max_revisions=3.14)  # type: ignore

        with self.assertRaises(InvalidRevisionLimitError):
            coord.run_vertical_slice(objective="Test", max_revisions="4")  # type: ignore

    # -------------------------------------------------------------------------
    # 3. Revision Limit Validation: execute_research_cycle Authoritative Entry Point
    # -------------------------------------------------------------------------

    def test_05_execute_research_cycle_exists_and_enforces_validation(self) -> None:
        """Verify that execute_research_cycle is callable and enforces identical validation."""
        responses = [
            # 1. Initial Hypothesis
            {
                "hypothesis_id": "H-CYCLE-01",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Trend momentum signal on daily closes.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            },
            # 2. Initial Review: RECOMMEND_APPROVAL
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Sound methodology with zero lookahead and verified causal timing.",
                "required_revisions": [],
                "revision_assessment": "FULLY_CORRECTED",
            },
            # 3. Preregistration Proposal Formulation
            {
                "proposal_id": "PROP-CYCLE-01",
                "hypothesis_id": "H-CYCLE-01",
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
            lock_path=os.path.join(self.temp_dir, "lock_cycle.lock"),
            max_revisions=3,
        )

        # Test boolean rejection on execute_research_cycle
        with self.assertRaises(InvalidRevisionLimitError):
            coord.execute_research_cycle(objective="Test", max_revisions=True)  # type: ignore

        with self.assertRaises(InvalidRevisionLimitError):
            coord.execute_research_cycle(objective="Test", max_revisions=False)  # type: ignore

        with self.assertRaises(InvalidRevisionLimitError):
            coord.execute_research_cycle(objective="Test", max_revisions=-2)

        with self.assertRaises(InvalidRevisionLimitError):
            coord.execute_research_cycle(objective="Test", max_revisions=7)

        # Valid call through execute_research_cycle
        state = coord.execute_research_cycle(
            objective="Evaluate trend momentum on BTCUSDT",
            max_revisions=4,
        )
        self.assertEqual(state.requested_max_revisions, 4)
        self.assertEqual(state.effective_max_revisions, 4)
        self.assertEqual(state.current_stage, "HUMAN_APPROVAL_GATE")
        self.assertIsNotNone(state.proposal)
        self.assertIsNotNone(state.ticket)

    # -------------------------------------------------------------------------
    # 4. Subprocess-Based Interruption Lifecycle Test
    # -------------------------------------------------------------------------

    def test_06_subprocess_interruption_reconciliation_lifecycle(self) -> None:
        """Verify subprocess crash after simulation and experiment persistence but before manifest persistence.

        Lifecycle verified:
        1. Simulation completes.
        2. Experiment record is persisted in SQLite experiments table.
        3. Child process abruptly terminates via os._exit(77) before evidence manifest persistence.
        4. Parent process restarts / invokes recovery.
        5. Recovery detects orphaned trial and reconciles trial_ledger to 'INTERRUPTED'
           and execution_requests to 'INTERRUPTED'.
        6. Recovery does NOT silently mark the experiment complete without manifest.
        7. Incomplete evidence lifecycle is cleanly reconciled with an INTERRUPTED manifest.
        8. Zero duplicate or contradictory records are created.
        """
        # Set up shared prerequisites in SQLite database
        self.ledger.record_hypothesis(
            hypothesis_id="H-INT-P2621",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Momentum crossover on BTCUSDT",
            validation_criteria={"min_sharpe": 1.0},
            max_evaluation_budget=5,
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-INT-P2621",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0, 15.0], "slow_period": [30.0]},
            cost_model={"commission_rate": 0.001, "slippage_rate": 0.0005},
            partition_protocol={"train_ratio": 0.6, "validation_ratio": 0.2, "test_ratio": 0.2},
            benchmark_ids=["BENCH-01"],
        )
        self.ledger.conn.commit()

        # Close ledger connection before subprocess runs to ensure clean SQLite access on Windows
        self.ledger.close()

        escaped_root = ROOT_DIR.replace("\\", "\\\\")
        escaped_db = self.db_path.replace("\\", "\\\\")

        # Child worker script: completes simulation and persists experiment, but crashes right before manifest
        child_script = f"""
import os
import sys

sys.path.insert(0, r"{escaped_root}")

from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ResearchExecutionEngine,
)
import trader.agent.core.execution as exec_module
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.tools.market_bridge import MarketDataProvider

# Monkeypatch generate_evidence_manifest to crash immediately before manifest generation/persistence
def crash_before_manifest(*args, **kwargs):
    # Abrupt termination with exit code 77
    os._exit(77)

exec_module.generate_evidence_manifest = crash_before_manifest

ledger = ResearchLedger(db_path=r"{escaped_db}")
provider = MarketDataProvider(ledger=ledger)
engine = ResearchExecutionEngine(ledger=ledger, provider=provider)

req = ControlledExperimentRequest(
    request_id="REQ-INT-P2621-01",
    hypothesis_id="H-INT-P2621",
    prereg_id="{prereg_id}",
    strategy=SyntheticSMAStrategy(),
    parameters={{"fast_period": 10.0, "slow_period": 30.0}},
    partition="TRAIN",
    symbol="BTCUSDT",
    experiment_id="EXP-INT-P2621-01",
)

engine.execute(req)
"""
        proc = subprocess.run(
            [sys.executable, "-c", child_script],
            capture_output=True,
            text=True,
            timeout=20,
        )

        # Process must have exited with code 77
        self.assertEqual(proc.returncode, 77, f"Child process failed to crash as expected: stderr={proc.stderr}")

        # Reopen ledger and inspect raw database state before recovery
        raw_conn = sqlite3.connect(self.db_path)
        cur = raw_conn.cursor()

        # 1. Experiment was persisted before crash
        cur.execute("SELECT experiment_id, status FROM experiments WHERE experiment_id = 'EXP-INT-P2621-01';")
        exp_row = cur.fetchone()
        self.assertIsNotNone(exp_row, "Experiment was not persisted before crash")
        self.assertEqual(exp_row[0], "EXP-INT-P2621-01")
        self.assertEqual(exp_row[1], "SUCCESS")

        # 2. Experiment metrics were persisted before crash
        cur.execute("SELECT metric_id, initial_capital FROM experiment_metrics WHERE experiment_id = 'EXP-INT-P2621-01';")
        metric_row = cur.fetchone()
        self.assertIsNotNone(metric_row, "Experiment metrics were not persisted before crash")

        # 3. Evidence manifest was NOT persisted before crash
        cur.execute("SELECT COUNT(*) FROM experiment_evidence_manifests WHERE experiment_id = 'EXP-INT-P2621-01';")
        manifest_count_before = cur.fetchone()[0]
        self.assertEqual(manifest_count_before, 0, "Evidence manifest should not exist before recovery")

        # 4. Trial ledger is still in STARTED state
        cur.execute("SELECT trial_id, disposition FROM trial_ledger WHERE experiment_id = 'EXP-INT-P2621-01';")
        trial_row = cur.fetchone()
        self.assertIsNotNone(trial_row)
        trial_id, disp_before = trial_row
        self.assertEqual(disp_before, "STARTED")

        # 5. Execution request is still in STARTED state
        cur.execute("SELECT request_id, status FROM execution_requests WHERE request_id = 'REQ-INT-P2621-01';")
        req_row = cur.fetchone()
        self.assertIsNotNone(req_row)
        self.assertEqual(req_row[1], "STARTED")
        raw_conn.close()

        # Now restart the system / initialize fresh execution engine
        self.ledger = ResearchLedger(db_path=self.db_path)
        self.provider = MarketDataProvider(ledger=self.ledger)

        # ResearchExecutionEngine.__init__ invokes reconcile_interrupted_trials() automatically
        fresh_engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)

        # Verify post-recovery database state
        cur = self.ledger.conn.cursor()

        # A. Trial is reconciled to INTERRUPTED (never silently marked COMPLETED)
        cur.execute("SELECT disposition, notes FROM trial_ledger WHERE trial_id = ?;", (trial_id,))
        disp_after, notes_after = cur.fetchone()
        self.assertEqual(disp_after, "INTERRUPTED")
        self.assertIn("terminated unexpectedly", notes_after)

        # B. Execution request is reconciled to INTERRUPTED
        cur.execute("SELECT status FROM execution_requests WHERE request_id = 'REQ-INT-P2621-01';")
        req_status_after = cur.fetchone()[0]
        self.assertEqual(req_status_after, "INTERRUPTED")

        # C. Evidence manifest is still 0 (recovery does not fabricate a completed manifest)
        cur.execute("SELECT COUNT(*) FROM experiment_evidence_manifests WHERE experiment_id = 'EXP-INT-P2621-01';")
        self.assertEqual(cur.fetchone()[0], 0)

        # D. Retry with same request_id must be rejected with InterruptedTrialRetryError
        retry_req = ControlledExperimentRequest(
            request_id="REQ-INT-P2621-01",
            hypothesis_id="H-INT-P2621",
            prereg_id=prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            symbol="BTCUSDT",
            experiment_id="EXP-INT-P2621-01",
        )
        with self.assertRaises(InterruptedTrialRetryError):
            fresh_engine.execute(retry_req)

        # E. Incomplete evidence lifecycle is reconciled by recording an INTERRUPTED manifest
        interrupted_manifest = generate_evidence_manifest(
            experiment_id="EXP-INT-P2621-01",
            hypothesis_id="H-INT-P2621",
            prereg_id=prereg_id,
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="test_data_fingerprint",
            code_revision="test_code_rev",
            metrics={"interrupted": True},
            result_status="INTERRUPTED",
            failure_or_interruption_status="INTERRUPTED: Process crashed prior to manifest completion",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            request_id="REQ-INT-P2621-01",
            trial_id=trial_id,
        )
        man_id = self.ledger.record_evidence_manifest(interrupted_manifest.to_dict())
        self.assertIsNotNone(man_id)

        # F. Verify manifest is recorded with result_status='INTERRUPTED'
        cur.execute(
            "SELECT result_status, failure_or_interruption_status FROM experiment_evidence_manifests WHERE manifest_id = ?;",
            (man_id,),
        )
        man_row = cur.fetchone()
        self.assertEqual(man_row[0], "INTERRUPTED")
        self.assertIn("Process crashed", man_row[1])

        # G. Idempotent manifest persistence returns manifest_id without creating duplicate
        idempotent_id = self.ledger.record_evidence_manifest(interrupted_manifest.to_dict())
        self.assertEqual(idempotent_id, man_id)

        # H. Contradictory manifest record raises EvidenceManifestContradictionError
        contradictory_manifest = generate_evidence_manifest(
            experiment_id="EXP-INT-P2621-01",
            hypothesis_id="H-INT-P2621",
            prereg_id=prereg_id,
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast_period": 50.0},  # Conflicting parameters
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="conflicting_hash",
            code_revision="conflicting_rev",
            metrics={"interrupted": False},
            result_status="COMPLETED",  # Contradictory status
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            request_id="REQ-INT-P2621-01",
            trial_id=trial_id,
        )
        with self.assertRaises(EvidenceManifestContradictionError):
            self.ledger.record_evidence_manifest(contradictory_manifest.to_dict())

        # I. Total counts: Exactly 1 trial, 1 experiment, 1 evidence manifest
        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE hypothesis_id = 'H-INT-P2621';")
        self.assertEqual(cur.fetchone()[0], 1)

        cur.execute("SELECT COUNT(*) FROM experiments WHERE prereg_id = ?;", (prereg_id,))
        self.assertEqual(cur.fetchone()[0], 1)

        cur.execute("SELECT COUNT(*) FROM experiment_evidence_manifests WHERE hypothesis_id = 'H-INT-P2621';")
        self.assertEqual(cur.fetchone()[0], 1)

        # J. Subsequent call to reconcile_interrupted_trials is idempotent and alters nothing
        self.assertEqual(fresh_engine.reconcile_interrupted_trials(), 0)

    def test_07_recovery_does_not_silently_mark_experiment_complete_without_manifest(self) -> None:
        """Verify that interrupted recovery never marks an experiment complete without evidence manifest."""
        hyp_id = "H-INCOMPLETE-01"
        self.ledger.record_hypothesis(
            hypothesis_id=hyp_id,
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Momentum hypothesis for recovery validation",
            validation_criteria={"min_sharpe": 1.0},
            max_evaluation_budget=5,
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=hyp_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"commission_rate": 0.001, "slippage_rate": 0.0005},
            partition_protocol={"train_ratio": 0.6, "validation_ratio": 0.2, "test_ratio": 0.2},
            benchmark_ids=["BENCH-01"],
        )

        # Register trial in STARTED state
        tid = self.ledger.start_trial(
            hypothesis_id=hyp_id,
            prereg_id=prereg_id,
            experiment_id="EXP-INCOMPLETE-01",
            parameters_evaluated={"fast_period": 10.0, "slow_period": 30.0},
            disposition="STARTED",
            notes="Simulation started but interrupted before manifest generation",
        )

        # Persist experiment record to simulate successful simulation before crash
        self.ledger.record_experiment_result(
            experiment_data={
                "experiment_id": "EXP-INCOMPLETE-01",
                "prereg_id": prereg_id,
                "trial_id": tid,
                "partition_name": "TRAIN",
                "strategy_name": "SyntheticSMAStrategy",
                "strategy_version": "1.0.0",
                "parameters": {"fast_period": 10.0, "slow_period": 30.0},
                "random_seed_63bit": 42,
                "code_git_hash": "githash123",
                "data_hash": "datahash123",
                "status": "SUCCESS",
            },
            metrics_data={
                "initial_capital": 10000.0,
                "final_capital": 11500.0,
                "net_total_return_pct": 15.0,
                "gross_total_return_pct": 16.0,
                "cagr_pct": 15.0,
                "annualized_vol_pct": 12.0,
                "sharpe_ratio": 1.5,
                "sortino_ratio": 2.0,
                "max_drawdown_pct": 5.0,
                "total_trades": 20,
                "win_rate_pct": 55.0,
                "total_friction_usd": 100.0,
            },
        )

        # Trigger recovery
        reconciled = self.engine.reconcile_interrupted_trials()
        self.assertEqual(reconciled, 1)

        # Verify that trial disposition is strictly INTERRUPTED, never COMPLETED
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition FROM trial_ledger WHERE trial_id = ?;", (tid,))
        self.assertEqual(cur.fetchone()[0], "INTERRUPTED")

        # Verify that no evidence manifest was created by recovery
        manifest = self.ledger.get_evidence_manifest("EXP-INCOMPLETE-01")
        self.assertIsNone(manifest)

        # Verify that completed trial queries exclude this interrupted experiment
        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition = 'COMPLETED';")
        self.assertEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
