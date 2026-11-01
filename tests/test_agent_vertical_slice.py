"""End-to-End Vertical Slice Integration Tests for Phase 2 Quant Research Agent."""

import os
import shutil
import tempfile
import unittest

from trader.agent.coordinator import (
    HumanApprovalPendingError,
    ProcessConcurrencyError,
    ResearchCoordinator,
    SkepticalReviewRejectionError,
)
from trader.agent.core.execution import ResearchExecutionEngine
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.providers import MockLLMProvider
from trader.agent.tools.market_bridge import MarketDataProvider


class TestAgentVerticalSlice(unittest.TestCase):
    """Integration test suite for the autonomous research agent vertical slice."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_research.db")
        self.lock_path = os.path.join(self.temp_dir, "test.lock")

        self.ledger = ResearchLedger(db_path=self.db_path)
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)
        self.llm_provider = MockLLMProvider()

        self.coordinator = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=self.llm_provider,
            lock_path=self.lock_path,
        )

    def tearDown(self) -> None:
        self.coordinator.close()
        self.ledger.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_vertical_slice_execution(self) -> None:
        """Verify full end-to-end research loop with test approval fixture in TRAIN partition."""
        os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"
        try:
            state = self.coordinator.test_run_with_approval(
                objective="Evaluate SyntheticSMAStrategy momentum on BTCUSDT Daily TRAIN data.",
                human_identity="Human_Research_Director",
                rationale="Approved M=2 parameter grid on BTCUSDT Daily TRAIN data for vertical slice demonstration.",
            )

            # 1. State machine completion
            self.assertTrue(state.is_completed)
            self.assertFalse(state.is_failed)
            self.assertEqual(state.current_stage, "CONCLUDED")

            # 2. Hypothesis and Review verification
            self.assertIsNotNone(state.hypothesis)
            self.assertEqual(state.hypothesis.hypothesis_id, "H-MOM-SMA-01")
            self.assertIsNotNone(state.critique)
            self.assertEqual(state.critique.overall_recommendation, "RECOMMEND_APPROVAL")

            # 3. Preregistration freezing in SQLite
            self.assertIsNotNone(state.prereg_id)
            cur = self.ledger.conn.cursor()
            cur.execute("SELECT status FROM preregistrations WHERE prereg_id = ?;", (state.prereg_id,))
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "FROZEN")

            # 4. Two configurations executed on TRAIN
            self.assertEqual(len(state.experiment_ids), 2)
            counts = self.ledger.get_trial_counts()
            self.assertEqual(counts["M_total"], 2)
            self.assertEqual(counts["M_evaluated"], 2)
            self.assertEqual(counts["M_test"], 0)  # ZERO TEST partition access!

            # 5. Deterministic report compiled
            self.assertIsNotNone(state.report_path)
            self.assertTrue(os.path.isfile(state.report_path))
            with open(state.report_path, "r", encoding="utf-8") as fh:
                report_text = fh.read()
            self.assertIn("Quantitative Research Report", report_text)
            self.assertIn("Deterministic Numerical Results", report_text)
            self.assertIn("FROZEN", report_text)
            self.assertIn("EXP-", report_text)

            # 6. Audit telemetry recorded
            self.assertGreaterEqual(len(state.audit_events), 6)
            for event in state.audit_events:
                self.assertEqual(len(event.prompt_sha256), 64)
                self.assertEqual(len(event.response_sha256), 64)

        finally:
            os.environ.pop("PHASE2_VERTICAL_SLICE_TRAIN_ONLY", None)

    def test_human_gate_pauses_workflow_without_simulation(self) -> None:
        """Verify workflow halts at HUMAN_APPROVAL_GATE until human authorization."""
        state = self.coordinator.run_vertical_slice(
            objective="Wait for human authorization.",
        )

        # Workflow paused at approval gate
        self.assertFalse(state.is_completed)
        self.assertEqual(state.current_stage, "HUMAN_APPROVAL_GATE")
        self.assertIsNotNone(state.ticket)
        self.assertEqual(state.ticket.status, "PENDING")
        self.assertIsNone(state.prereg_id)
        self.assertEqual(len(state.experiment_ids), 0)

        # Zero simulations ran; M_total is 0
        counts = self.ledger.get_trial_counts()
        self.assertEqual(counts["M_total"], 0)

        # Now approve out-of-band
        prereg_id = self.coordinator.approve_proposal(
            ticket_id=state.ticket.ticket_id,
            human_identity="Director",
            rationale="Approved after manual audit",
        )
        self.assertTrue(prereg_id.startswith("PREREG-"))

        # Verify preregistration exists in SQLite with status FROZEN
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT status FROM preregistrations WHERE prereg_id = ?;", (prereg_id,))
        self.assertEqual(cur.fetchone()[0], "FROZEN")

    def test_skeptical_review_rejection_halts_workflow(self) -> None:
        """Verify workflow halts if skeptical reviewer issues RECOMMEND_REJECTION."""
        rejection_payload = {
            "lookahead_risk_score": 5,
            "snooping_risk_score": 4,
            "granularity_risk_score": 4,
            "turnover_realism_score": 4,
            "overall_recommendation": "RECOMMEND_REJECTION",
            "critique_rationale": "Severe lookahead bias detected in future indicator calculation parameters.",
            "required_revisions": ["Eliminate non-causal moving average"],
        }
        mock_llm = MockLLMProvider(custom_payloads={"Skeptical Reviewer": rejection_payload})
        reject_coordinator = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=mock_llm,
            lock_path=os.path.join(self.temp_dir, "reject.lock"),
        )

        try:
            with self.assertRaises(SkepticalReviewRejectionError):
                reject_coordinator.run_vertical_slice(
                    objective="Test adversarial critique rejection.",
                )
            # Ensure zero trials consumed
            self.assertEqual(self.ledger.get_trial_counts()["M_total"], 0)
        finally:
            reject_coordinator.close()

    def test_crash_recovery_reconciliation(self) -> None:
        """Verify orphaned trial from crashed process is reconciled on startup."""
        # Manually create a started trial that crashed before completion
        self.ledger.start_trial(
            hypothesis_id="H-CRASH-01",
            prereg_id="PREREG-CRASH-01",
            experiment_id="EXP-CRASH-01",
            parameters_evaluated={"fast_period": 10.0},
            disposition="STARTED",
        )

        # Fresh coordinator initializes and connects to same database
        fresh_coordinator = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=self.llm_provider,
            lock_path=os.path.join(self.temp_dir, "fresh.lock"),
        )
        try:
            cur = self.ledger.conn.cursor()
            cur.execute("SELECT disposition, notes FROM trial_ledger WHERE experiment_id = 'EXP-CRASH-01';")
            row = cur.fetchone()
            self.assertEqual(row[0], "INTERRUPTED")
            self.assertIn("Reconciled: process terminated unexpectedly", row[1])
        finally:
            fresh_coordinator.close()

    def test_report_path_traversal_rejection(self) -> None:
        """Verify resolve_secure_report_path strips path traversal attempts."""
        from trader.agent.reporting import resolve_secure_report_path, REPORTS_DIR

        # Attempt directory traversal
        secure_path = resolve_secure_report_path("../../../evil_report.md")
        # Basename stripping must force file into REPORTS_DIR
        self.assertTrue(os.path.normcase(secure_path).startswith(os.path.normcase(REPORTS_DIR)))
        self.assertEqual(os.path.basename(secure_path), "evil_report.md")


if __name__ == "__main__":
    unittest.main()

