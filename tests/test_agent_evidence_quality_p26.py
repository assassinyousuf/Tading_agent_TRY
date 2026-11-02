"""Unit and Integration Test Suite for Phase 2.6 Research Evidence Quality System.

Enforces:
1. Manifest integrity, deterministic canonical JSON hashing, and partition isolation.
2. Immutability triggers on experiment_evidence_manifests and reproducibility_verifications.
3. Four-way discrete reproducibility verification:
   - EXACT_REPRODUCIBILITY
   - NUMERICALLY_EQUIVALENT
   - NON_REPRODUCIBLE
   - INSUFFICIENT_INFORMATION
4. Provenance tracking across research lifecycle.
5. Negative result, failure, and crash preservation.
6. Multi-dimensional evidence reporting without score collapse.
7. Database migration from schema v6 to v7 with backup verification.

Strict safety invariants:
- Zero live trading, paper trading, testnet, or exchange credentials.
- Strictly TRAIN partition data. Zero access to TEST or VALIDATION partitions.
- Zero em dashes (\\u2014) or en dashes (\\u2013).
"""

from datetime import datetime, timezone
import json
import math
import os
import shutil
import sqlite3
import tempfile
import unittest
from typing import Any, Dict

from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ResearchExecutionEngine,
)
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.db.schema import (
    CURRENT_SCHEMA_VERSION,
    create_schema,
    migrate_database,
)
from trader.agent.evidence.manifest import (
    ExperimentEvidenceManifest,
    ProvenanceType,
    compute_manifest_hash,
    generate_evidence_manifest,
)
from trader.agent.evidence.provenance import (
    ProvenanceRecord,
    ProvenanceTracker,
)
from trader.agent.evidence.reporting import (
    DeterministicEvidenceReporter,
    EvidenceEvaluationDimensions,
)
from trader.agent.evidence.reproducibility import (
    ReproducibilityEngine,
    ReproducibilityResult,
    ReproducibilityTolerances,
    ReproducibilityVerdict,
)
from trader.agent.tools.market_bridge import MarketDataProvider


class TestAgentEvidenceQualityP26(unittest.TestCase):
    """Test suite for Phase 2.6 Research Evidence Quality System."""

    def setUp(self) -> None:
        """Initialize in-memory database and market bridge."""
        self.ledger = ResearchLedger(db_path=":memory:")
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)
        self.strategy = SyntheticSMAStrategy()

    def tearDown(self) -> None:
        """Close database connection."""
        self.ledger.close()

    def test_01_manifest_creation_and_deterministic_hashing(self) -> None:
        """Verify that evidence manifests generate deterministic hashes from authoritative data."""
        params = {"fast_period": 10.0, "slow_period": 30.0}
        metrics = {"sharpe_ratio": 1.45, "total_trades": 42, "total_net_return_pct": 12.8}

        m1 = generate_evidence_manifest(
            experiment_id="EXP-TEST-01",
            hypothesis_id="H-TEST-01",
            prereg_id="PREREG-TEST-01",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters=params,
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_sha_abc123",
            code_revision="commit_sha_def456",
            metrics=metrics,
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            random_seeds={"simulation_seed": 42},
        )

        m2 = generate_evidence_manifest(
            experiment_id="EXP-TEST-01",
            hypothesis_id="H-TEST-01",
            prereg_id="PREREG-TEST-01",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters=params,
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_sha_abc123",
            code_revision="commit_sha_def456",
            metrics=metrics,
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            random_seeds={"simulation_seed": 42},
        )

        self.assertEqual(m1.manifest_id, "MAN-EXP-TEST-01")
        self.assertEqual(m1.manifest_hash, m2.manifest_hash)
        self.assertEqual(m1.strategy_digest, m2.strategy_digest)
        self.assertEqual(m1.input_artifact_hash, m2.input_artifact_hash)
        self.assertEqual(m1.output_artifact_hash, m2.output_artifact_hash)

    def test_02_partition_isolation_blocks_test_manifest(self) -> None:
        """Verify that attempting to create an evidence manifest for TEST partition raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            generate_evidence_manifest(
                experiment_id="EXP-TEST-LEAK",
                hypothesis_id="H-LEAK",
                prereg_id="PREREG-LEAK",
                strategy_name="SyntheticSMAStrategy",
                strategy_version="1.0.0",
                parameters={"fast_period": 10.0},
                dataset_id="BTCUSDT_TEST",
                partition_name="TEST",
                dataset_fingerprint="leak_fingerprint",
                code_revision="commit_sha",
                metrics={},
                result_status="COMPLETED",
                start_timestamp_utc="2026-11-02T00:00:00Z",
                completion_timestamp_utc="2026-11-02T00:00:05Z",
            )
        self.assertIn("strictly require TRAIN data", str(ctx.exception))

    def test_03_manifest_persistence_and_database_triggers(self) -> None:
        """Verify that manifests persist to SQLite and are protected by immutability triggers."""
        # Setup foreign key references
        self.ledger.record_hypothesis("H-DB-01", "SyntheticSMA", "Trend hypothesis", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-DB-01",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )
        # Record parent experiment
        trial_id = self.ledger.start_trial(
            hypothesis_id="H-DB-01",
            prereg_id=prereg_id,
            experiment_id="EXP-DB-01",
            parameters_evaluated={"fast_period": 10.0},
        )
        exp_data = {
            "experiment_id": "EXP-DB-01",
            "prereg_id": prereg_id,
            "trial_id": trial_id,
            "partition_name": "TRAIN",
            "strategy_name": "SyntheticSMAStrategy",
            "strategy_version": "1.0.0",
            "parameters": {"fast_period": 10.0},
            "random_seed_63bit": 42,
            "code_git_hash": "commit_123",
            "data_hash": "data_456",
            "status": "SUCCESS",
        }
        full_metrics = {
            "initial_capital": 10000.0,
            "final_capital": 11200.0,
            "net_total_return_pct": 12.0,
            "gross_total_return_pct": 12.5,
            "cagr_pct": 12.0,
            "annualized_vol_pct": 15.0,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.5,
            "max_drawdown_pct": 10.0,
            "total_trades": 45,
            "win_rate_pct": 55.0,
            "total_friction_usd": 50.0,
            "compounding_error": 0.0,
            "raw_metrics": {"sharpe_ratio": 1.2},
        }
        self.ledger.record_experiment_result(exp_data, full_metrics, None)

        manifest = generate_evidence_manifest(
            experiment_id="EXP-DB-01",
            hypothesis_id="H-DB-01",
            prereg_id=prereg_id,
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast_period": 10.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_456",
            code_revision="commit_123",
            metrics={"sharpe_ratio": 1.2},
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
        )

        m_id = self.ledger.record_evidence_manifest(manifest.to_dict())
        self.assertEqual(m_id, "MAN-EXP-DB-01")

        retrieved = self.ledger.get_evidence_manifest("EXP-DB-01")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["manifest_id"], "MAN-EXP-DB-01")
        self.assertEqual(retrieved["strategy_digest"], manifest.strategy_digest)

        # Trigger test: direct UPDATE must be blocked
        cur = self.ledger.conn.cursor()
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            cur.execute("UPDATE experiment_evidence_manifests SET strategy_name = 'Hacked' WHERE manifest_id = ?;", (m_id,))
        self.assertIn("Immutability violation", str(ctx.exception))

        # Trigger test: direct DELETE must be blocked
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            cur.execute("DELETE FROM experiment_evidence_manifests WHERE manifest_id = ?;", (m_id,))
        self.assertIn("Immutability violation", str(ctx.exception))

    def test_04_exact_reproducibility_verification(self) -> None:
        """Verify that identical execution reproduction yields EXACT_REPRODUCIBILITY."""
        params = {"fast_period": 10.0, "slow_period": 30.0}
        metrics = {"sharpe_ratio": 1.5, "total_trades": 50, "max_drawdown_pct": 14.2}
        seeds = {"simulation_seed": 42}
        data_fp = "data_hash_999"

        manifest = generate_evidence_manifest(
            experiment_id="EXP-EXACT-01",
            hypothesis_id="H-EXACT",
            prereg_id="PREREG-EXACT",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters=params,
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint=data_fp,
            code_revision="git_commit_789",
            metrics=metrics,
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            random_seeds=seeds,
        )

        engine = ReproducibilityEngine()
        res = engine.verify_reproducibility(
            reference_manifest=manifest,
            reproduced_metrics=metrics,
            reproduced_output_hash=manifest.output_artifact_hash,
            reproduced_parameters=params,
            reproduced_dataset_fingerprint=data_fp,
            reproduced_seeds=seeds,
            reproduced_code_revision="git_commit_789",
        )

        self.assertEqual(res.verdict, ReproducibilityVerdict.EXACT_REPRODUCIBILITY)
        self.assertTrue(res.is_exact_match)
        self.assertTrue(res.is_numerically_equivalent)
        self.assertEqual(len(res.metric_discrepancies), 0)

    def test_05_numerical_equivalence_within_tolerances(self) -> None:
        """Verify that micro-differences below tolerance yield NUMERICALLY_EQUIVALENT."""
        params = {"fast_period": 10.0, "slow_period": 30.0}
        orig_metrics = {"sharpe_ratio": 1.5000000, "total_trades": 50, "max_drawdown_pct": 14.2000000}
        # Minor floating point epsilon perturbation (1e-7)
        repro_metrics = {"sharpe_ratio": 1.5000001, "total_trades": 50, "max_drawdown_pct": 14.2000002}
        seeds = {"simulation_seed": 42}
        data_fp = "data_hash_999"

        manifest = generate_evidence_manifest(
            experiment_id="EXP-EQUIV-01",
            hypothesis_id="H-EQUIV",
            prereg_id="PREREG-EQUIV",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters=params,
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint=data_fp,
            code_revision="git_commit_789",
            metrics=orig_metrics,
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            random_seeds=seeds,
        )

        # Output artifact hash differs because floats differ in low digits
        repro_out_hash = "different_hash_due_to_low_bits"

        engine = ReproducibilityEngine(tolerances=ReproducibilityTolerances(metric_abs_tol=1e-6, metric_rel_tol=1e-5))
        res = engine.verify_reproducibility(
            reference_manifest=manifest,
            reproduced_metrics=repro_metrics,
            reproduced_output_hash=repro_out_hash,
            reproduced_parameters=params,
            reproduced_dataset_fingerprint=data_fp,
            reproduced_seeds=seeds,
        )

        self.assertEqual(res.verdict, ReproducibilityVerdict.NUMERICALLY_EQUIVALENT)
        self.assertFalse(res.is_exact_match)
        self.assertTrue(res.is_numerically_equivalent)
        self.assertGreater(len(res.metric_discrepancies), 0)
        self.assertTrue(all(not d.exceeds_tolerance for d in res.metric_discrepancies))

    def test_06_non_reproducible_detection_on_parameter_divergence(self) -> None:
        """Verify that parameter modification immediately triggers NON_REPRODUCIBLE."""
        params = {"fast_period": 10.0, "slow_period": 30.0}
        altered_params = {"fast_period": 15.0, "slow_period": 30.0}
        metrics = {"sharpe_ratio": 1.5, "total_trades": 50}

        manifest = generate_evidence_manifest(
            experiment_id="EXP-PARAM-DIV",
            hypothesis_id="H-PARAM",
            prereg_id="PREREG-PARAM",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters=params,
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_fp",
            code_revision="git_rev",
            metrics=metrics,
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            random_seeds={"simulation_seed": 42},
        )

        engine = ReproducibilityEngine()
        res = engine.verify_reproducibility(
            reference_manifest=manifest,
            reproduced_metrics=metrics,
            reproduced_output_hash=manifest.output_artifact_hash,
            reproduced_parameters=altered_params,
            reproduced_dataset_fingerprint="data_fp",
            reproduced_seeds={"simulation_seed": 42},
        )

        self.assertEqual(res.verdict, ReproducibilityVerdict.NON_REPRODUCIBLE)
        self.assertFalse(res.is_exact_match)
        self.assertFalse(res.is_numerically_equivalent)
        self.assertIn("Parameters modified", res.audit_notes)

    def test_07_non_reproducible_detection_on_metric_divergence(self) -> None:
        """Verify that metric changes exceeding tolerance trigger NON_REPRODUCIBLE."""
        params = {"fast_period": 10.0, "slow_period": 30.0}
        orig_metrics = {"sharpe_ratio": 1.5, "total_trades": 50}
        divergent_metrics = {"sharpe_ratio": 0.8, "total_trades": 35}

        manifest = generate_evidence_manifest(
            experiment_id="EXP-METRIC-DIV",
            hypothesis_id="H-METRIC",
            prereg_id="PREREG-METRIC",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters=params,
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_fp",
            code_revision="git_rev",
            metrics=orig_metrics,
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            random_seeds={"simulation_seed": 42},
        )

        engine = ReproducibilityEngine()
        res = engine.verify_reproducibility(
            reference_manifest=manifest,
            reproduced_metrics=divergent_metrics,
            reproduced_output_hash="different_output",
            reproduced_parameters=params,
            reproduced_dataset_fingerprint="data_fp",
            reproduced_seeds={"simulation_seed": 42},
        )

        self.assertEqual(res.verdict, ReproducibilityVerdict.NON_REPRODUCIBLE)
        bad_discrepancies = [d for d in res.metric_discrepancies if d.exceeds_tolerance]
        self.assertGreaterEqual(len(bad_discrepancies), 1)

    def test_08_insufficient_information_handling(self) -> None:
        """Verify that missing seeds or parameters triggers INSUFFICIENT_INFORMATION."""
        manifest = generate_evidence_manifest(
            experiment_id="EXP-INSUF-01",
            hypothesis_id="H-INSUF",
            prereg_id="PREREG-INSUF",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast_period": 10.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_fp",
            code_revision="git_rev",
            metrics={"sharpe_ratio": 1.1},
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            random_seeds={"simulation_seed": 42},
        )

        engine = ReproducibilityEngine()
        res = engine.verify_reproducibility(
            reference_manifest=manifest,
            reproduced_metrics={"sharpe_ratio": 1.1},
            reproduced_output_hash="hash",
            reproduced_parameters=None,  # Missing
            reproduced_dataset_fingerprint=None,  # Missing
            reproduced_seeds=None,  # Missing
        )

        self.assertEqual(res.verdict, ReproducibilityVerdict.INSUFFICIENT_INFORMATION)
        self.assertIn("missing fields", res.audit_notes)

    def test_09_provenance_tracking_and_chain_verification(self) -> None:
        """Verify that the provenance tracker validates the unbroken lineage chain."""
        valid_rec = ProvenanceTracker.build_lineage_chain(
            hypothesis_id="H-PROV-01",
            provenance_type=ProvenanceType.HUMAN_APPROVED.value,
            origin_author="Quantitative Researcher",
            initial_proposal_id="PROP-01",
            revision_ids=["REV-01"],
            approved_proposal_id="PROP-01-REV1",
            ticket_id="TICKET-01",
            approver_identity="Human Lead",
            approval_timestamp_utc="2026-11-02T00:00:00Z",
            request_id="REQ-01",
            trial_id=1,
            experiment_id="EXP-01",
            manifest_id="MAN-EXP-01",
        )
        is_valid, violations = ProvenanceTracker.verify_provenance_integrity(valid_rec)
        self.assertTrue(is_valid)
        self.assertEqual(len(violations), 0)

        # Invalid chain: Approved proposal lacking ticket_id
        broken_rec = ProvenanceTracker.build_lineage_chain(
            hypothesis_id="H-PROV-02",
            provenance_type=ProvenanceType.HUMAN_APPROVED.value,
            origin_author="Researcher",
            approved_proposal_id="PROP-BROKEN",
            ticket_id=None,  # Broken
            approver_identity=None,
        )
        is_valid_broken, violations_broken = ProvenanceTracker.verify_provenance_integrity(broken_rec)
        self.assertFalse(is_valid_broken)
        self.assertIn("Approved proposal lacks bound ticket_id", violations_broken)

    def test_10_negative_result_and_failure_preservation(self) -> None:
        """Verify that failed and interrupted experiments are preserved in manifests."""
        # Setup references
        self.ledger.record_hypothesis("H-FAIL-01", "SyntheticSMA", "Failing strategy", {"min_sharpe": 1.0})
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-FAIL-01",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0]},
            cost_model={"taker_fee": 0.001},
            partition_protocol={"target_partition": "TRAIN"},
            benchmark_ids=["BTC_NET"],
        )
        trial_id = self.ledger.start_trial(
            hypothesis_id="H-FAIL-01",
            prereg_id=prereg_id,
            experiment_id="EXP-FAIL-01",
            parameters_evaluated={"fast_period": 10.0},
        )
        exp_data = {
            "experiment_id": "EXP-FAIL-01",
            "prereg_id": prereg_id,
            "trial_id": trial_id,
            "partition_name": "TRAIN",
            "strategy_name": "SyntheticSMAStrategy",
            "strategy_version": "1.0.0",
            "parameters": {"fast_period": 10.0},
            "random_seed_63bit": 42,
            "code_git_hash": "commit_123",
            "data_hash": "data_456",
            "status": "FAILED",
        }
        fail_metrics = {
            "initial_capital": 10000.0,
            "final_capital": 8460.0,
            "net_total_return_pct": -15.4,
            "gross_total_return_pct": -15.0,
            "cagr_pct": -15.4,
            "annualized_vol_pct": 22.0,
            "sharpe_ratio": -0.8,
            "sortino_ratio": -0.9,
            "max_drawdown_pct": 20.0,
            "total_trades": 30,
            "win_rate_pct": 33.3,
            "total_friction_usd": 40.0,
            "compounding_error": 0.0,
            "raw_metrics": {"sharpe_ratio": -0.8, "total_net_return_pct": -15.4},
        }
        self.ledger.record_experiment_result(exp_data, fail_metrics, None)

        manifest = generate_evidence_manifest(
            experiment_id="EXP-FAIL-01",
            hypothesis_id="H-FAIL-01",
            prereg_id=prereg_id,
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast_period": 10.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_456",
            code_revision="commit_123",
            metrics={"sharpe_ratio": -0.8, "total_net_return_pct": -15.4},
            result_status="FAILED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            failure_or_interruption_status="Negative return threshold triggered",
        )
        self.ledger.record_evidence_manifest(manifest.to_dict())

        manifests = self.ledger.list_evidence_manifests("H-FAIL-01")
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0]["result_status"], "FAILED")

    def test_11_deterministic_evidence_reporter_multi_dimensional_separation(self) -> None:
        """Verify that the evidence reporter separates all 5 dimensions without score collapse."""
        manifest = generate_evidence_manifest(
            experiment_id="EXP-REP-01",
            hypothesis_id="H-REP-01",
            prereg_id="PREREG-REP-01",
            strategy_name="SyntheticSMAStrategy",
            strategy_version="1.0.0",
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            dataset_id="BTCUSDT_TRAIN",
            partition_name="TRAIN",
            dataset_fingerprint="data_fp",
            code_revision="git_commit",
            metrics={"sharpe_ratio": 1.8, "total_trades": 60, "total_net_return_pct": 24.5, "max_drawdown_pct": 11.2},
            result_status="COMPLETED",
            start_timestamp_utc="2026-11-02T00:00:00Z",
            completion_timestamp_utc="2026-11-02T00:00:05Z",
            ticket_id="TICKET-REP-01",
            request_id="REQ-REP-01",
            trial_id=1,
            fee_assumptions={"taker_fee": 0.001},
        )

        repro_engine = ReproducibilityEngine()
        repro_res = repro_engine.verify_reproducibility(
            reference_manifest=manifest,
            reproduced_metrics=manifest.metrics_summary,
            reproduced_output_hash=manifest.output_artifact_hash,
            reproduced_parameters=manifest.parameter_definitions,
            reproduced_dataset_fingerprint=manifest.dataset_fingerprint,
            reproduced_seeds=manifest.random_seeds,
        )

        eval_dims = DeterministicEvidenceReporter.evaluate_experiment(
            manifest=manifest,
            reproducibility_result=repro_res,
            m_total=3,
        )

        # Verify separation of dimensions
        self.assertTrue(eval_dims.methodological.partition_strictly_train)
        self.assertTrue(eval_dims.statistical.has_sufficient_power)
        self.assertTrue(eval_dims.economic.economically_viable)
        self.assertEqual(eval_dims.reproducibility, ReproducibilityVerdict.EXACT_REPRODUCIBILITY)
        self.assertTrue(eval_dims.governance.preregistration_frozen)
        self.assertEqual(eval_dims.overall_disposition, "METHODOLOGICALLY_SOUND_RESEARCH_EVIDENCE")

        md_report = DeterministicEvidenceReporter.generate_markdown_report(eval_dims, manifest, repro_res)
        self.assertIn("## 1. Research Governance & Provenance", md_report)
        self.assertIn("## 2. Methodological Validity", md_report)
        self.assertIn("## 3. Statistical Evidence & Power", md_report)
        self.assertIn("## 4. Economic Performance", md_report)
        self.assertIn("## 5. Independent Reproducibility", md_report)
        self.assertIn("## 6. Immutable Artifact Digests", md_report)

    def test_12_database_migration_v6_to_v7(self) -> None:
        """Verify that migrate_database upgrades schema from v6 to v7 with backup verification."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "legacy_v6.db")
        try:
            # Build v6 database directly
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("CREATE TABLE schema_metadata (version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT);")
            cur.execute("INSERT INTO schema_metadata VALUES (6, '2026-11-01', 'v6');")
            cur.execute("CREATE TABLE hypotheses (hypothesis_id TEXT PRIMARY KEY, created_at TEXT, strategy_name TEXT, economic_rationale TEXT, max_evaluation_budget INTEGER, validation_criteria_json TEXT);")
            cur.execute("INSERT INTO hypotheses VALUES ('H-MIG-01', '2026-11-01', 'SMA', 'Rationale', 5, '{}');")
            conn.commit()
            conn.close()

            # Migrate to v7
            res = migrate_database(db_path=db_path, target_version=7)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["previous_version"], 6)
            self.assertEqual(res["new_version"], 7)
            self.assertTrue(os.path.exists(res["backup_path"]))

            # Inspect upgraded database
            upgraded_ledger = ResearchLedger(db_path=db_path)
            self.assertEqual(upgraded_ledger.get_schema_version(), 7)

            # Verify tables exist and can accept records
            cur_upgraded = upgraded_ledger.conn.cursor()
            cur_upgraded.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_evidence_manifests';")
            self.assertIsNotNone(cur_upgraded.fetchone())
            cur_upgraded.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reproducibility_verifications';")
            self.assertIsNotNone(cur_upgraded.fetchone())

            upgraded_ledger.close()
        finally:
            shutil.rmtree(temp_dir)

    def test_13_execution_engine_records_manifest_automatically(self) -> None:
        """Verify that ResearchExecutionEngine automatically persists an evidence manifest on completion."""
        hyp_id = "H-AUTO-MANIFEST-01"
        self.ledger.record_hypothesis(hyp_id, "SyntheticSMA", "Auto manifest hypothesis", {"min_sharpe": 1.0})
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
            request_id="REQ-AUTO-01",
        )

        res = self.engine.execute(req)
        self.assertEqual(res.status, "SUCCESS")

        manifest = self.ledger.get_evidence_manifest(res.experiment_id)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["experiment_id"], res.experiment_id)
        self.assertEqual(manifest["hypothesis_id"], hyp_id)
        self.assertEqual(manifest["prereg_id"], prereg_id)
        self.assertEqual(manifest["result_status"], "COMPLETED")
        self.assertEqual(manifest["partition_name"], "TRAIN")
        self.assertIn("sharpe_ratio", manifest["metrics_summary"])

        # Verify manifest can be independently replicated
        repro_engine = ReproducibilityEngine()
        from trader.agent.evidence.manifest import ExperimentEvidenceManifest
        manifest_obj = ExperimentEvidenceManifest(
            manifest_id=manifest["manifest_id"],
            experiment_id=manifest["experiment_id"],
            hypothesis_id=manifest["hypothesis_id"],
            prereg_id=manifest["prereg_id"],
            strategy_name=manifest["strategy_name"],
            strategy_version=manifest["strategy_version"],
            strategy_digest=manifest["strategy_digest"],
            provenance_type=manifest["provenance_type"],
            dataset_id=manifest["dataset_id"],
            partition_name=manifest["partition_name"],
            dataset_fingerprint=manifest["dataset_fingerprint"],
            code_revision=manifest["code_revision"],
            feature_definitions=manifest["feature_definitions"],
            parameter_definitions=manifest["parameter_definitions"],
            execution_engine_version=manifest["execution_engine_version"],
            fee_assumptions=manifest["fee_assumptions"],
            slippage_assumptions=manifest["slippage_assumptions"],
            funding_assumptions=manifest["funding_assumptions"],
            benchmark_id=manifest["benchmark_id"],
            statistical_test_identifiers=manifest["statistical_test_identifiers"],
            random_seeds=manifest["random_seeds"],
            start_timestamp_utc=manifest["start_timestamp_utc"],
            completion_timestamp_utc=manifest["completion_timestamp_utc"],
            input_artifact_hash=manifest["input_artifact_hash"],
            output_artifact_hash=manifest["output_artifact_hash"],
            result_status=manifest["result_status"],
            failure_or_interruption_status=manifest["failure_or_interruption_status"],
            metrics_summary=manifest["metrics_summary"],
            manifest_hash=manifest["manifest_hash"],
            created_at=manifest["created_at"],
        )
        repro_res = repro_engine.verify_reproducibility(
            reference_manifest=manifest_obj,
            reproduced_metrics=res.metrics,
            reproduced_output_hash=manifest["output_artifact_hash"],
            reproduced_parameters=manifest["parameter_definitions"],
            reproduced_dataset_fingerprint=manifest["dataset_fingerprint"],
            reproduced_seeds=manifest["random_seeds"],
        )
        self.assertEqual(repro_res.verdict, ReproducibilityVerdict.EXACT_REPRODUCIBILITY)
        self.assertTrue(repro_res.is_exact_match)

