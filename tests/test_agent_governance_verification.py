"""Adversarial Governance Verification Suite for Interrupted Evaluation Handling.

Phase 2.2.1 Independent Governance Verification:
1. Resuming a session must never automatically rerun an interrupted evaluation.
2. Preserve the original interrupted request and its complete audit history.
3. Require a separate, explicit human authorization for a new evaluation.
4. Bind that authorization to exact interrupted request, configuration digest, hypothesis, and proposed rerun.
5. Generate a new request ID only after authorization.
6. Record the authorization identity, rationale, timestamp, original request ID, and new request ID.
7. Count the new authorized evaluation as a new attempt in M_total and the evaluation budget.
8. Preserve idempotency for identical requests and reject conflicting payloads.
9. Database triggers prevent UPDATE or DELETE on rerun_authorizations.
10. CLI commands for rerun authorization and inspection operate properly.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from trader.agent.coordinator import (
    HumanApprovalPendingError,
    InterruptedEvaluationAuthorizationRequiredError,
    ResearchCoordinator,
    ResearchCoordinatorError,
)
from trader.agent.core.execution import (
    ControlledExperimentRequest,
    InterruptedTrialRetryError,
    ResearchExecutionEngine,
    canonical_parameter_json,
)
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.db.schema import (
    CURRENT_SCHEMA_VERSION,
    IncompatibleSchemaVersionError,
    create_schema,
    migrate_database,
)
from trader.agent.providers import MockLLMProvider
from trader.agent.tools.market_bridge import MarketDataProvider


class TestAgentGovernanceVerification(unittest.TestCase):
    """Adversarial verification of interrupted evaluation rerun governance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "gov_verification.db")
        self.lock_path = os.path.join(self.temp_dir, "test.lock")

        self.ledger = ResearchLedger(db_path=self.db_path)
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)
        self.llm = MockLLMProvider()
        self.strategy = SyntheticSMAStrategy()

        self.coordinator = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=self.llm,
            lock_path=self.lock_path,
        )

    def tearDown(self) -> None:
        self.coordinator.close()
        self.ledger.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_interrupted_evaluation_never_automatically_reruns_on_resume(self) -> None:
        """Verify resuming a session strictly refuses to automatically rerun an interrupted evaluation."""
        os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"
        try:
            state = self.coordinator.run_vertical_slice(objective="Test no auto rerun")
            ticket_id = state.ticket.ticket_id
            session_id = state.session_id

            self.coordinator.approve_proposal(
                ticket_id=ticket_id,
                human_identity="Director A",
                rationale="Approved proposal",
            )
            ticket = self.ledger.get_ticket(ticket_id)
            prereg_id = ticket["prereg_id"]

            base_req_id = f"REQ-{session_id}-01"
            payload_str = f"{prereg_id}:{canonical_parameter_json({'fast_period': 10.0, 'slow_period': 30.0})}:TRAIN:BTCUSDT"
            payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

            # Record interrupted request
            self.ledger.record_execution_request(
                request_id=base_req_id,
                hypothesis_id=state.hypothesis.hypothesis_id,
                prereg_id=prereg_id,
                payload_hash=payload_hash,
                status="INTERRUPTED",
            )

            # Attempting to resume session MUST raise InterruptedEvaluationAuthorizationRequiredError
            with self.assertRaises(InterruptedEvaluationAuthorizationRequiredError) as ctx:
                self.coordinator.resume_session(session_id=session_id)

            self.assertIn(base_req_id, str(ctx.exception))
            self.assertIn("Automatic rerun on session resume is strictly prohibited", str(ctx.exception))

            # Verify original request remains strictly INTERRUPTED
            req_info = self.ledger.get_execution_request(base_req_id)
            self.assertEqual(req_info["status"], "INTERRUPTED")

            # Verify no retry request ID was generated
            cur = self.ledger.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM execution_requests WHERE request_id LIKE ?;", (f"{base_req_id}%",))
            self.assertEqual(cur.fetchone()[0], 1)
        finally:
            os.environ.pop("PHASE2_VERTICAL_SLICE_TRAIN_ONLY", None)

    def test_rerun_authorization_requires_non_empty_credentials(self) -> None:
        """Verify rerun authorization rejects empty or whitespace-only identity and rationale."""
        base_req_id = "REQ-MOCK-INT-01"
        self.ledger.record_hypothesis("H-CRED-01", "SyntheticSMA", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment("H-CRED-01", ["BTCUSDT"], "1d", {}, {}, {}, [])
        self.ledger.record_execution_request(
            request_id=base_req_id,
            hypothesis_id="H-CRED-01",
            prereg_id=prereg_id,
            payload_hash="mock_hash",
            status="INTERRUPTED",
        )

        # Empty identity
        with self.assertRaises(ValueError) as ctx:
            self.ledger.authorize_rerun_transaction(
                original_request_id=base_req_id,
                authorized_by="",
                rationale="Valid rationale",
            )
        self.assertIn("Human authorization identity cannot be empty", str(ctx.exception))

        # Whitespace identity
        with self.assertRaises(ValueError):
            self.ledger.authorize_rerun_transaction(
                original_request_id=base_req_id,
                authorized_by="   ",
                rationale="Valid rationale",
            )

        # Empty rationale
        with self.assertRaises(ValueError) as ctx:
            self.ledger.authorize_rerun_transaction(
                original_request_id=base_req_id,
                authorized_by="Director Name",
                rationale="",
            )
        self.assertIn("Decision rationale cannot be empty", str(ctx.exception))

    def test_rerun_authorization_rejects_non_interrupted_requests(self) -> None:
        """Verify rerun authorization cannot be granted for COMPLETED, STARTED, or non-existent requests."""
        self.ledger.record_hypothesis("H-STATE-01", "SyntheticSMA", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment("H-STATE-01", ["BTCUSDT"], "1d", {}, {}, {}, [])

        # Non-existent request
        with self.assertRaises(ValueError) as ctx:
            self.ledger.authorize_rerun_transaction(
                original_request_id="NON-EXISTENT-REQ",
                authorized_by="Director",
                rationale="Rationale",
            )
        self.assertIn("not found", str(ctx.exception))

        # Completed request
        self.ledger.record_execution_request(
            request_id="REQ-COMPLETED-01",
            hypothesis_id="H-STATE-01",
            prereg_id=prereg_id,
            payload_hash="hash_c",
            status="COMPLETED",
        )
        with self.assertRaises(ValueError) as ctx:
            self.ledger.authorize_rerun_transaction(
                original_request_id="REQ-COMPLETED-01",
                authorized_by="Director",
                rationale="Rationale",
            )
        self.assertIn("expected 'INTERRUPTED'", str(ctx.exception))

        # Started request
        self.ledger.record_execution_request(
            request_id="REQ-STARTED-01",
            hypothesis_id="H-STATE-01",
            prereg_id=prereg_id,
            payload_hash="hash_s",
            status="STARTED",
        )
        with self.assertRaises(ValueError) as ctx:
            self.ledger.authorize_rerun_transaction(
                original_request_id="REQ-STARTED-01",
                authorized_by="Director",
                rationale="Rationale",
            )
        self.assertIn("expected 'INTERRUPTED'", str(ctx.exception))

    def test_rerun_authorization_binds_to_exact_configuration_digest(self) -> None:
        """Verify rerun authorization enforces exact configuration digest match."""
        base_req_id = "REQ-DIGEST-01"
        self.ledger.record_hypothesis("H-DIGEST-01", "SyntheticSMA", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment("H-DIGEST-01", ["BTCUSDT"], "1d", {}, {}, {}, [])

        expected_hash = hashlib.sha256("exact_parameters".encode("utf-8")).hexdigest()
        self.ledger.record_execution_request(
            request_id=base_req_id,
            hypothesis_id="H-DIGEST-01",
            prereg_id=prereg_id,
            payload_hash=expected_hash,
            status="INTERRUPTED",
        )

        # Mismatched configuration digest submitted during authorization
        tampered_hash = hashlib.sha256("tampered_parameters".encode("utf-8")).hexdigest()
        with self.assertRaises(ValueError) as ctx:
            self.ledger.authorize_rerun_transaction(
                original_request_id=base_req_id,
                authorized_by="Director",
                rationale="Authorizing rerun",
                configuration_digest=tampered_hash,
            )
        self.assertIn("Configuration digest mismatch", str(ctx.exception))

    def test_rerun_idempotency_and_conflict_rejection(self) -> None:
        """Verify identical authorization requests are idempotent, while conflicting ones are rejected."""
        base_req_id = "REQ-IDEMP-01"
        self.ledger.record_hypothesis("H-IDEMP-01", "SyntheticSMA", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment("H-IDEMP-01", ["BTCUSDT"], "1d", {}, {}, {}, [])
        payload_hash = hashlib.sha256("payload".encode("utf-8")).hexdigest()

        self.ledger.record_execution_request(
            request_id=base_req_id,
            hypothesis_id="H-IDEMP-01",
            prereg_id=prereg_id,
            payload_hash=payload_hash,
            status="INTERRUPTED",
        )

        auth1 = self.ledger.authorize_rerun_transaction(
            original_request_id=base_req_id,
            authorized_by="Director Smith",
            rationale="Approved after investigation of power drop",
        )

        # Identical request returns identical record idempotently
        auth2 = self.ledger.authorize_rerun_transaction(
            original_request_id=base_req_id,
            authorized_by="Director Smith",
            rationale="Approved after investigation of power drop",
        )
        self.assertEqual(auth1["auth_id"], auth2["auth_id"])
        self.assertEqual(auth1["new_request_id"], auth2["new_request_id"])

        # Conflicting request (different rationale/identity) is rejected
        with self.assertRaises(ValueError) as ctx:
            self.ledger.authorize_rerun_transaction(
                original_request_id=base_req_id,
                authorized_by="Director Jones",
                rationale="Conflicting rationale",
            )
        self.assertIn("Conflicting active rerun authorization", str(ctx.exception))

    def test_authorized_rerun_generates_new_request_id_and_increments_m_total(self) -> None:
        """Verify rerun creates distinct request ID, increments M_total, and counts against hypothesis budget."""
        os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"
        try:
            state = self.coordinator.run_vertical_slice(objective="Test M_total increment on rerun")
            ticket_id = state.ticket.ticket_id
            session_id = state.session_id
            hyp_id = state.hypothesis.hypothesis_id

            self.coordinator.approve_proposal(
                ticket_id=ticket_id,
                human_identity="Director Alpha",
                rationale="Initial proposal approved",
            )
            prereg_id = self.ledger.get_ticket(ticket_id)["prereg_id"]

            base_req_id = f"REQ-{session_id}-01"
            canonical_params = canonical_parameter_json({"fast_period": 10.0, "slow_period": 30.0})
            payload_str = f"{prereg_id}:{canonical_params}:TRAIN:BTCUSDT"
            payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

            # Record interrupted trial and request
            trial_id = self.ledger.start_trial(
                hypothesis_id=hyp_id,
                prereg_id=prereg_id,
                experiment_id="EXP-INTERRUPTED-01",
                parameters_evaluated={"fast_period": 10.0, "slow_period": 30.0},
                disposition="INTERRUPTED",
            )
            self.ledger.record_execution_request(
                request_id=base_req_id,
                hypothesis_id=hyp_id,
                prereg_id=prereg_id,
                payload_hash=payload_hash,
                experiment_id="EXP-INTERRUPTED-01",
                trial_id=trial_id,
                status="INTERRUPTED",
            )

            counts_before = self.ledger.get_trial_counts()
            budget_consumed_before = self.ledger.get_hypothesis_evaluation_count(hyp_id)

            # Explicit human authorization
            auth = self.coordinator.authorize_interrupted_rerun(
                original_request_id=base_req_id,
                human_identity="Director Alpha",
                rationale="Interrupted trial authorized for one fresh attempt",
            )
            new_req_id = auth["new_request_id"]
            self.assertEqual(new_req_id, f"{base_req_id}-RERUN1")
            self.assertNotEqual(new_req_id, base_req_id)

            # Resume session executes with authorized new request ID
            resumed_state = self.coordinator.resume_session(session_id=session_id)
            self.assertTrue(resumed_state.is_completed)

            counts_after = self.ledger.get_trial_counts()
            budget_consumed_after = self.ledger.get_hypothesis_evaluation_count(hyp_id)

            # M_total and budget must have accounted for both trials
            self.assertGreater(counts_after["M_total"], counts_before["M_total"])
            self.assertGreater(budget_consumed_after, budget_consumed_before)

            # Verify original request remains INTERRUPTED
            orig_req = self.ledger.get_execution_request(base_req_id)
            self.assertEqual(orig_req["status"], "INTERRUPTED")

            # Verify new request is COMPLETED
            new_req = self.ledger.get_execution_request(new_req_id)
            self.assertEqual(new_req["status"], "COMPLETED")

            # Verify authorization is marked consumed
            cur = self.ledger.conn.cursor()
            cur.execute("SELECT is_consumed, consumed_by FROM rerun_authorizations WHERE auth_id = ?;", (auth["auth_id"],))
            row = cur.fetchone()
            self.assertEqual(row[0], 1)
            self.assertIn("ResearchCoordinator.resume_session", row[1])
        finally:
            os.environ.pop("PHASE2_VERTICAL_SLICE_TRAIN_ONLY", None)

    def test_database_triggers_prevent_rerun_auth_tampering_and_delete(self) -> None:
        """Verify database triggers block UPDATE of immutable fields and completely block DELETE on rerun_authorizations."""
        base_req_id = "REQ-TRIG-01"
        self.ledger.record_hypothesis("H-TRIG-01", "SyntheticSMA", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment("H-TRIG-01", ["BTCUSDT"], "1d", {}, {}, {}, [])
        payload_hash = hashlib.sha256("trig_payload".encode("utf-8")).hexdigest()

        self.ledger.record_execution_request(
            request_id=base_req_id,
            hypothesis_id="H-TRIG-01",
            prereg_id=prereg_id,
            payload_hash=payload_hash,
            status="INTERRUPTED",
        )

        auth = self.ledger.authorize_rerun_transaction(
            original_request_id=base_req_id,
            authorized_by="Director Trigger",
            rationale="Testing trigger enforcement",
        )
        auth_id = auth["auth_id"]

        cur = self.ledger.conn.cursor()

        # Prohibit DELETE
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            cur.execute("DELETE FROM rerun_authorizations WHERE auth_id = ?;", (auth_id,))
        self.assertIn("DELETE prohibited on historical table rerun_authorizations", str(ctx.exception))

        # Prohibit modifying authorized_by
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            cur.execute("UPDATE rerun_authorizations SET authorized_by = 'Malicious Actor' WHERE auth_id = ?;", (auth_id,))
        self.assertIn("Cannot modify authorized_by", str(ctx.exception))

        # Prohibit modifying configuration_digest
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            cur.execute("UPDATE rerun_authorizations SET configuration_digest = 'fake' WHERE auth_id = ?;", (auth_id,))
        self.assertIn("Cannot modify configuration_digest", str(ctx.exception))

        # Prohibit modifying new_request_id
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            cur.execute("UPDATE rerun_authorizations SET new_request_id = 'HIJACKED' WHERE auth_id = ?;", (auth_id,))
        self.assertIn("Cannot modify new_request_id", str(ctx.exception))

        # Allow valid consumption (is_consumed: 0 -> 1)
        cur.execute("UPDATE rerun_authorizations SET is_consumed = 1 WHERE auth_id = ?;", (auth_id,))
        self.ledger.conn.commit()

        # Prohibit un-consuming (is_consumed: 1 -> 0)
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            cur.execute("UPDATE rerun_authorizations SET is_consumed = 0 WHERE auth_id = ?;", (auth_id,))
        self.assertIn("Cannot un-consume rerun authorization", str(ctx.exception))

    def test_cli_authorize_rerun_and_list_authorizations(self) -> None:
        """Verify CLI authorize-rerun and list-rerun-authorizations commands operate correctly."""
        base_req_id = "REQ-CLI-01"
        self.ledger.record_hypothesis("H-CLI-01", "SyntheticSMA", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment("H-CLI-01", ["BTCUSDT"], "1d", {}, {}, {}, [])
        payload_hash = hashlib.sha256("cli_payload".encode("utf-8")).hexdigest()

        self.ledger.record_execution_request(
            request_id=base_req_id,
            hypothesis_id="H-CLI-01",
            prereg_id=prereg_id,
            payload_hash=payload_hash,
            status="INTERRUPTED",
        )

        # Execute CLI authorize-rerun
        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "trader.agent.cli",
                "authorize-rerun",
                base_req_id,
                "--identity",
                "Executive Director",
                "--rationale",
                "Approved via CLI governance workflow",
                "--db-path",
                self.db_path,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"CLI stderr: {res.stderr}")
        self.assertIn("=== Interrupted Evaluation Rerun Authorized ===", res.stdout)
        self.assertIn(f"Original Request ID: {base_req_id}", res.stdout)
        self.assertIn(f"New Authorized Request ID: {base_req_id}-RERUN1", res.stdout)

        # Execute CLI list-rerun-authorizations
        res_list = subprocess.run(
            [
                sys.executable,
                "-m",
                "trader.agent.cli",
                "list-rerun-authorizations",
                "--db-path",
                self.db_path,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_list.returncode, 0)
        self.assertIn("=== Interrupted Evaluation Rerun Authorizations", res_list.stdout)
        self.assertIn(base_req_id, res_list.stdout)
        self.assertIn(f"{base_req_id}-RERUN1", res_list.stdout)
        self.assertIn("Executive Director", res_list.stdout)


if __name__ == "__main__":
    unittest.main()
