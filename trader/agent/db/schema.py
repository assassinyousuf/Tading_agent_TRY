"""SQLite Database Schema, Migrations, and Immutability Triggers.

Establishes relational tables and constraints for:
1. schema_metadata: Version tracking and migration history.
2. hypotheses: Falsifiable hypotheses with preregistered validation hurdles.
3. preregistrations: Frozen experimental specifications (universe, params, cost, partition).
4. experiments: Immutable execution instances (seeds, hashes, status).
5. experiment_metrics: Net/gross returns, drawdowns, trade statistics, compounding error.
6. audit_results: Statistical validation (raw DSR, raw bootstrap p-value, diagnostic M_eff).
7. research_decisions: Formal GO, NO_GO, INCONCLUSIVE verdicts and scientific rationale.
8. test_evaluation_authorizations: Cryptographically hashed, one-time human tokens.
9. test_evaluation_records: Auditable logs of all TEST access attempts (granted and denied).
10. trial_ledger: Append-only multiple-testing ledger tracking M_total and diagnostic M_eff.
11. execution_requests: Idempotency and concurrency governance tracking request status.
12. preregistration_proposals: Persistent proposals and human approval tickets.
13. research_sessions: Persistent session state checkpoints and crash recovery tracking.
14. rerun_authorizations: Explicit human authorizations for re-evaluating interrupted requests.

Database-level triggers enforce append-only immutability against accidental UPDATE or DELETE.
"""

from datetime import datetime, timezone
import os
import shutil
import sqlite3
from typing import Any, Dict, List, Optional

CURRENT_SCHEMA_VERSION = 5


class IncompatibleSchemaVersionError(Exception):
    """Raised when an existing database has an outdated schema version."""
    pass


def create_schema(conn: sqlite3.Connection) -> None:
    """Initialize relational research ledger tables, indexes, and triggers for a new database."""
    cur = conn.cursor()

    # Enable foreign keys and Write-Ahead Logging (WAL) for concurrency and durability
    cur.execute("PRAGMA foreign_keys = ON;")

    # 1. Schema Metadata / Versioning Table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );
        """
    )

    # 2. Hypotheses Table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hypotheses (
            hypothesis_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            economic_rationale TEXT NOT NULL,
            status TEXT NOT NULL, -- DRAFT, PREREGISTERED, EVALUATING, REJECTED, CONCLUDED
            validation_criteria_json TEXT NOT NULL, -- Preregistered hypothesis-specific hurdles
            metadata_json TEXT NOT NULL,
            max_evaluation_budget INTEGER NOT NULL DEFAULT 10
        );
        """
    )

    # 3. Preregistrations Table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS preregistrations (
            prereg_id TEXT PRIMARY KEY,
            hypothesis_id TEXT NOT NULL,
            preregistered_at TEXT NOT NULL,
            universe_json TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            parameter_grid_json TEXT NOT NULL,
            cost_model_json TEXT NOT NULL,
            partition_protocol_json TEXT NOT NULL,
            benchmark_ids_json TEXT NOT NULL,
            status TEXT NOT NULL, -- FROZEN, EVALUATING, COMPLETED, ABANDONED
            FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(hypothesis_id)
        );
        """
    )

    # 4. Experiments Table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS experiments (
            experiment_id TEXT PRIMARY KEY,
            prereg_id TEXT NOT NULL,
            trial_id INTEGER,
            executed_at TEXT NOT NULL,
            partition_name TEXT NOT NULL, -- TRAIN, VALIDATION, TEST
            strategy_name TEXT NOT NULL DEFAULT 'UNKNOWN',
            strategy_version TEXT NOT NULL DEFAULT '1.0.0',
            parameters_json TEXT NOT NULL,
            random_seed_63bit INTEGER NOT NULL,
            code_git_hash TEXT NOT NULL,
            data_hash TEXT NOT NULL,
            status TEXT NOT NULL, -- SUCCESS, FAILED, INTERRUPTED
            error_message TEXT,
            FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id),
            FOREIGN KEY (trial_id) REFERENCES trial_ledger(trial_id)
        );
        """
    )

    # 5. Experiment Metrics Table (Raw metrics preserved separately from adjusted metrics)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS experiment_metrics (
            metric_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL UNIQUE,
            initial_capital REAL NOT NULL,
            final_capital REAL NOT NULL,
            net_total_return_pct REAL NOT NULL,
            gross_total_return_pct REAL NOT NULL,
            cagr_pct REAL NOT NULL,
            annualized_vol_pct REAL NOT NULL,
            sharpe_ratio REAL NOT NULL,
            sortino_ratio REAL NOT NULL,
            max_drawdown_pct REAL NOT NULL,
            total_trades INTEGER NOT NULL,
            win_rate_pct REAL NOT NULL,
            total_friction_usd REAL NOT NULL,
            compounding_error REAL NOT NULL,
            raw_metrics_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
        );
        """
    )

    # 6. Audit Results Table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_results (
            audit_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL UNIQUE,
            benchmark_id TEXT NOT NULL,
            bootstrap_p_value_raw REAL NOT NULL, -- Raw (k+1)/(B+1) finite Monte Carlo p-value
            bootstrap_ci_lower REAL NOT NULL,
            bootstrap_ci_upper REAL NOT NULL,
            screening_category TEXT NOT NULL, -- CATEGORY_A, CATEGORY_B, CATEGORY_C, INCONCLUSIVE
            ar2_max_root REAL,
            dsr_value_raw REAL NOT NULL, -- Deflated Sharpe Ratio computed with M_total
            m_total_used INTEGER NOT NULL,
            m_eff_diagnostic REAL, -- Eigenvalue participation ratio diagnostic (NOT substituted into DSR)
            raw_audit_json TEXT NOT NULL,
            adjusted_metrics_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
        );
        """
    )

    # 7. Research Decisions Table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS research_decisions (
            decision_id TEXT PRIMARY KEY,
            hypothesis_id TEXT NOT NULL,
            prereg_id TEXT,
            experiment_id TEXT,
            decision TEXT NOT NULL, -- GO, NO_GO, INCONCLUSIVE
            rationale TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            decided_by TEXT NOT NULL,
            FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(hypothesis_id),
            FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
        );
        """
    )

    # 8. Test Evaluation Authorizations (Human-signed, single-use, hashed tokens)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS test_evaluation_authorizations (
            auth_id TEXT PRIMARY KEY,
            prereg_id TEXT NOT NULL,
            experiment_id TEXT,
            token_hash TEXT NOT NULL UNIQUE, -- SHA-256 digest of secret token, NEVER stored plaintext
            authorized_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_revoked INTEGER NOT NULL DEFAULT 0,
            max_uses INTEGER NOT NULL DEFAULT 1,
            uses_count INTEGER NOT NULL DEFAULT 0,
            purpose TEXT NOT NULL,
            FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
        );
        """
    )

    # 9. Test Evaluation Records (Auditable logs of granted and denied TEST access attempts)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS test_evaluation_records (
            record_id TEXT PRIMARY KEY,
            auth_id TEXT,
            prereg_id TEXT NOT NULL,
            experiment_id TEXT,
            evaluated_at TEXT NOT NULL,
            caller_identity TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            access_granted INTEGER NOT NULL, -- 1 if authorized, 0 if denied
            failure_reason TEXT,
            execution_duration_ms REAL,
            FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
        );
        """
    )

    # 10. Append-Only Trial Ledger
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trial_ledger (
            trial_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            hypothesis_id TEXT NOT NULL,
            prereg_id TEXT NOT NULL,
            experiment_id TEXT,
            parameters_evaluated_json TEXT NOT NULL,
            disposition TEXT NOT NULL, -- TRAIN_EVALUATED, VALIDATION_EVALUATED, TEST_EVALUATED, PRE_TEST_REJECTED, VALIDATION_FAILED, DUPLICATE_DISCARDED, STARTED, COMPLETED, FAILED, INTERRUPTED
            cumulative_m_total INTEGER NOT NULL,
            diagnostic_m_eff REAL,
            notes TEXT
        );
        """
    )

    # 11. Execution Requests Table (Idempotency and Concurrency Governance)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_requests (
            request_id TEXT PRIMARY KEY,
            hypothesis_id TEXT NOT NULL,
            prereg_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            experiment_id TEXT,
            trial_id INTEGER,
            status TEXT NOT NULL, -- STARTED, COMPLETED, FAILED, INTERRUPTED
            created_at TEXT NOT NULL,
            FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
        );
        """
    )

    # 12. Preregistration Proposals and Approval Tickets Table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS preregistration_proposals (
            proposal_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL UNIQUE,
            hypothesis_id TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            universe_json TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            parameter_grid_json TEXT NOT NULL,
            cost_model_json TEXT NOT NULL,
            partition_protocol_json TEXT NOT NULL,
            benchmark_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL, -- PENDING, APPROVED, REJECTED, EXPIRED
            submission_notes TEXT,
            approved_at TEXT,
            approved_by TEXT,
            decision_rationale TEXT,
            prereg_id TEXT,
            FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(hypothesis_id),
            FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
        );
        """
    )

    # 13. Research Sessions Table (Session Checkpointing and Workflow Recovery)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS research_sessions (
            session_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            hypothesis_id TEXT,
            prereg_id TEXT,
            ticket_id TEXT,
            current_stage TEXT NOT NULL,
            is_completed INTEGER NOT NULL DEFAULT 0,
            is_failed INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            report_path TEXT,
            session_state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    # 14. Rerun Authorizations Table (Human Authorizations for Interrupted Evaluations)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rerun_authorizations (
            auth_id TEXT PRIMARY KEY,
            original_request_id TEXT NOT NULL,
            new_request_id TEXT NOT NULL UNIQUE,
            hypothesis_id TEXT NOT NULL,
            prereg_id TEXT NOT NULL,
            configuration_digest TEXT NOT NULL,
            authorized_by TEXT NOT NULL,
            rationale TEXT NOT NULL,
            authorized_at TEXT NOT NULL,
            is_consumed INTEGER NOT NULL DEFAULT 0,
            consumed_at TEXT,
            consumed_by TEXT,
            FOREIGN KEY (original_request_id) REFERENCES execution_requests(request_id),
            FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(hypothesis_id),
            FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
        );
        """
    )

    # Relational Indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prereg_hypothesis ON preregistrations(hypothesis_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exp_prereg ON experiments(prereg_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_metrics_exp ON experiment_metrics(experiment_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_exp ON audit_results(experiment_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_decisions_hyp ON research_decisions(hypothesis_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auth_token_hash ON test_evaluation_authorizations(token_hash);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_test_records_prereg ON test_evaluation_records(prereg_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trials_prereg ON trial_ledger(prereg_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exec_req_prereg ON execution_requests(prereg_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_proposals_ticket ON preregistration_proposals(ticket_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_proposals_hyp ON preregistration_proposals(hypothesis_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_hyp ON research_sessions(hypothesis_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rerun_orig_req ON rerun_authorizations(original_request_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rerun_new_req ON rerun_authorizations(new_request_id);")

    # Record Schema Version History
    cur.execute(
        """
        INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
        VALUES (1, datetime('now'), 'Initial Phase 1 relational research ledger schema');
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
        VALUES (2, datetime('now'), 'Phase 1.2: Hardened research execution governance and trial lifecycle');
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
        VALUES (3, datetime('now'), 'Phase 2: Add max_evaluation_budget and execution_requests idempotency table');
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
        VALUES (4, datetime('now'), 'Phase 2.2: Add persistent preregistration_proposals and research_sessions');
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
        VALUES (5, datetime('now'), 'Phase 2.2.1: Add rerun_authorizations table and immutability triggers');
        """
    )

    # Immutability Triggers: Block UPDATE and DELETE on historical records
    immutable_update_tables = [
        ("hypotheses", "hypothesis"),
        ("preregistrations", "preregistration"),
        ("experiments", "experiment"),
        ("experiment_metrics", "metric"),
        ("audit_results", "audit"),
        ("research_decisions", "decision"),
        ("test_evaluation_records", "test_record"),
    ]

    for table, name in immutable_update_tables:
        cur.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS prevent_{name}_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: UPDATE prohibited on historical table {table}');
            END;
            """
        )

    # All tables completely prohibited from DELETE
    all_immutable_delete_tables = immutable_update_tables + [
        ("trial_ledger", "trial"),
        ("execution_requests", "execution_request"),
        ("preregistration_proposals", "proposal"),
        ("research_sessions", "session"),
        ("rerun_authorizations", "rerun_auth"),
    ]
    for table, name in all_immutable_delete_tables:
        cur.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS prevent_{name}_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: DELETE prohibited on historical table {table}');
            END;
            """
        )

    # Proposal lifecycle trigger: protects proposed configuration against tampering and terminal modifications
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_proposal_tampering
        BEFORE UPDATE ON preregistration_proposals
        BEGIN
            SELECT CASE
                WHEN NEW.proposal_id != OLD.proposal_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify proposal_id')
                WHEN NEW.ticket_id != OLD.ticket_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify ticket_id')
                WHEN NEW.hypothesis_id != OLD.hypothesis_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify hypothesis_id')
                WHEN NEW.parameter_grid_json != OLD.parameter_grid_json THEN
                    RAISE(ABORT, 'Security violation: Cannot modify proposed parameter_grid_json')
                WHEN NEW.universe_json != OLD.universe_json THEN
                    RAISE(ABORT, 'Security violation: Cannot modify proposed universe_json')
                WHEN NEW.created_at != OLD.created_at THEN
                    RAISE(ABORT, 'Security violation: Cannot modify created_at')
                WHEN OLD.status IN ('APPROVED', 'REJECTED', 'EXPIRED') THEN
                    RAISE(ABORT, 'Lifecycle violation: Cannot modify proposal in terminal status')
                WHEN NEW.status NOT IN ('APPROVED', 'REJECTED', 'EXPIRED') THEN
                    RAISE(ABORT, 'Lifecycle violation: Invalid target status for proposal')
            END;
        END;
        """
    )

    # Trial Ledger lifecycle trigger: allows STARTED -> COMPLETED/FAILED/INTERRUPTED while preventing tampering
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_trial_tampering
        BEFORE UPDATE ON trial_ledger
        BEGIN
            SELECT CASE
                WHEN NEW.trial_id != OLD.trial_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify trial_id')
                WHEN NEW.timestamp != OLD.timestamp THEN
                    RAISE(ABORT, 'Security violation: Cannot modify trial creation timestamp')
                WHEN NEW.hypothesis_id != OLD.hypothesis_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify hypothesis_id')
                WHEN NEW.prereg_id != OLD.prereg_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify prereg_id')
                WHEN NEW.parameters_evaluated_json != OLD.parameters_evaluated_json THEN
                    RAISE(ABORT, 'Security violation: Cannot modify evaluated parameters')
                WHEN NEW.cumulative_m_total != OLD.cumulative_m_total THEN
                    RAISE(ABORT, 'Security violation: Cannot modify cumulative_m_total')
                WHEN OLD.disposition IN ('COMPLETED', 'FAILED', 'INTERRUPTED', 'PRE_TEST_REJECTED', 'TRAIN_EVALUATED', 'VALIDATION_EVALUATED', 'TEST_EVALUATED') THEN
                    RAISE(ABORT, 'Immutability violation: Cannot modify trial in terminal disposition')
                WHEN OLD.disposition NOT IN ('STARTED', 'ATTEMPTED') THEN
                    RAISE(ABORT, 'Lifecycle violation: Invalid initial trial state for update')
                WHEN NEW.disposition NOT IN ('COMPLETED', 'FAILED', 'INTERRUPTED') THEN
                    RAISE(ABORT, 'Lifecycle violation: Invalid target trial disposition')
            END;
        END;
        """
    )

    # Authorization trigger: prevent modifying token_hash, prereg_id, max_uses, expires_at, or exceeding max_uses
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_auth_tampering
        BEFORE UPDATE ON test_evaluation_authorizations
        BEGIN
            SELECT CASE
                WHEN NEW.token_hash != OLD.token_hash THEN
                    RAISE(ABORT, 'Security violation: Cannot modify token_hash')
                WHEN NEW.prereg_id != OLD.prereg_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify prereg_id binding')
                WHEN NEW.auth_id != OLD.auth_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify auth_id')
                WHEN NEW.authorized_by != OLD.authorized_by THEN
                    RAISE(ABORT, 'Security violation: Cannot modify authorized_by')
                WHEN NEW.created_at != OLD.created_at THEN
                    RAISE(ABORT, 'Security violation: Cannot modify created_at')
                WHEN NEW.expires_at != OLD.expires_at THEN
                    RAISE(ABORT, 'Security violation: Cannot modify expires_at')
                WHEN NEW.max_uses != OLD.max_uses THEN
                    RAISE(ABORT, 'Security violation: Cannot modify max_uses')
                WHEN NEW.uses_count < OLD.uses_count THEN
                    RAISE(ABORT, 'Security violation: Cannot decrement uses_count')
                WHEN NEW.uses_count > OLD.max_uses THEN
                    RAISE(ABORT, 'Security violation: uses_count cannot exceed max_uses')
                WHEN OLD.is_revoked = 1 AND NEW.is_revoked = 0 THEN
                    RAISE(ABORT, 'Security violation: Cannot un-revoke authorization token')
            END;
        END;
        """
    )

    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_auth_delete
        BEFORE DELETE ON test_evaluation_authorizations
        BEGIN
            SELECT RAISE(ABORT, 'Security violation: DELETE prohibited on test_evaluation_authorizations');
        END;
        """
    )

    # Rerun authorization trigger: prevent modifying authorization identity, rationale, binding, or unconsuming
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_rerun_auth_tampering
        BEFORE UPDATE ON rerun_authorizations
        BEGIN
            SELECT CASE
                WHEN NEW.auth_id != OLD.auth_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify auth_id')
                WHEN NEW.original_request_id != OLD.original_request_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify original_request_id')
                WHEN NEW.new_request_id != OLD.new_request_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify new_request_id')
                WHEN NEW.hypothesis_id != OLD.hypothesis_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify hypothesis_id')
                WHEN NEW.prereg_id != OLD.prereg_id THEN
                    RAISE(ABORT, 'Security violation: Cannot modify prereg_id')
                WHEN NEW.configuration_digest != OLD.configuration_digest THEN
                    RAISE(ABORT, 'Security violation: Cannot modify configuration_digest')
                WHEN NEW.authorized_by != OLD.authorized_by THEN
                    RAISE(ABORT, 'Security violation: Cannot modify authorized_by')
                WHEN NEW.rationale != OLD.rationale THEN
                    RAISE(ABORT, 'Security violation: Cannot modify rationale')
                WHEN NEW.authorized_at != OLD.authorized_at THEN
                    RAISE(ABORT, 'Security violation: Cannot modify authorized_at')
                WHEN OLD.is_consumed = 1 AND NEW.is_consumed = 0 THEN
                    RAISE(ABORT, 'Security violation: Cannot un-consume rerun authorization')
                WHEN NEW.is_consumed NOT IN (0, 1) THEN
                    RAISE(ABORT, 'Lifecycle violation: Invalid is_consumed state')
            END;
        END;
        """
    )

    conn.commit()


def get_db_schema_version(conn: sqlite3.Connection) -> int:
    """Return the maximum recorded schema version from schema_metadata, or 0 if uninitialized."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_metadata';")
        if not cur.fetchone():
            return 0
        cur.execute("SELECT MAX(version) FROM schema_metadata;")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def migrate_database(db_path: str, target_version: int = CURRENT_SCHEMA_VERSION) -> Dict[str, Any]:
    """Explicitly migrate an existing research ledger database to target_version with backup and validation.

    Execution contract:
    1. Rejects non-existent database paths.
    2. Inspects existing schema version; returns early if already current.
    3. Creates a timestamped .bak backup file using sqlite3 backup API.
    4. Validates backup file using PRAGMA integrity_check before touching live database.
    5. Records pre-migration row counts across existing tables.
    6. Applies DDL migrations incrementally within a single transaction.
    7. Verifies post-migration integrity and confirms pre-migration row counts are preserved.
    8. Automatically rolls back and restores from backup if any migration step fails.
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Database file not found: {db_path}")

    # Inspect current schema version
    inspect_conn = sqlite3.connect(db_path)
    try:
        current_version = get_db_schema_version(inspect_conn)
    finally:
        inspect_conn.close()

    if current_version >= target_version:
        return {
            "status": "ALREADY_CURRENT",
            "current_version": current_version,
            "target_version": target_version,
            "message": f"Database is already at schema version {current_version}.",
        }

    # Step 3: Create timestamped backup copy
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.{timestamp}.v{current_version}.bak"

    src_conn = sqlite3.connect(db_path)
    bak_conn = sqlite3.connect(backup_path)
    try:
        src_conn.backup(bak_conn)
    finally:
        bak_conn.close()
        src_conn.close()

    # Step 4: Validate backup file
    val_conn = sqlite3.connect(backup_path)
    try:
        val_cur = val_conn.cursor()
        val_cur.execute("PRAGMA integrity_check;")
        check_res = val_cur.fetchone()
        if not check_res or check_res[0] != "ok":
            raise RuntimeError(f"Backup validation failed for {backup_path}: {check_res}")
    finally:
        val_conn.close()

    # Step 5: Capture pre-migration row counts
    pre_counts: Dict[str, int] = {}
    audit_conn = sqlite3.connect(db_path)
    try:
        audit_cur = audit_conn.cursor()
        audit_cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        existing_tables = [r[0] for r in audit_cur.fetchall()]
        for tbl in existing_tables:
            if not tbl.startswith("sqlite_"):
                audit_cur.execute(f"SELECT COUNT(*) FROM {tbl};")
                pre_counts[tbl] = audit_cur.fetchone()[0]
    finally:
        audit_conn.close()

    # Step 6: Apply incremental migrations
    mig_conn = sqlite3.connect(db_path)
    try:
        mig_cur = mig_conn.cursor()
        mig_cur.execute("PRAGMA foreign_keys = ON;")

        # v2 -> v3: max_evaluation_budget on hypotheses, execution_requests table
        if current_version < 3:
            mig_cur.execute("PRAGMA table_info(hypotheses);")
            cols = [col[1] for col in mig_cur.fetchall()]
            if "max_evaluation_budget" not in cols:
                mig_cur.execute("ALTER TABLE hypotheses ADD COLUMN max_evaluation_budget INTEGER NOT NULL DEFAULT 10;")

            mig_cur.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_requests (
                    request_id TEXT PRIMARY KEY,
                    hypothesis_id TEXT NOT NULL,
                    prereg_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    experiment_id TEXT,
                    trial_id INTEGER,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
                );
                """
            )
            mig_cur.execute("CREATE INDEX IF NOT EXISTS idx_exec_req_prereg ON execution_requests(prereg_id);")
            mig_cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_execution_request_delete
                BEFORE DELETE ON execution_requests
                BEGIN
                    SELECT RAISE(ABORT, 'Immutability violation: DELETE prohibited on historical table execution_requests');
                END;
                """
            )
            mig_cur.execute(
                """
                INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
                VALUES (3, datetime('now'), 'Phase 2: Add max_evaluation_budget and execution_requests idempotency table');
                """
            )

        # v3 -> v4: preregistration_proposals, research_sessions, lifecycle triggers
        if current_version < 4:
            mig_cur.execute(
                """
                CREATE TABLE IF NOT EXISTS preregistration_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL UNIQUE,
                    hypothesis_id TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    universe_json TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    parameter_grid_json TEXT NOT NULL,
                    cost_model_json TEXT NOT NULL,
                    partition_protocol_json TEXT NOT NULL,
                    benchmark_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submission_notes TEXT,
                    approved_at TEXT,
                    approved_by TEXT,
                    decision_rationale TEXT,
                    prereg_id TEXT,
                    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(hypothesis_id),
                    FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
                );
                """
            )
            mig_cur.execute(
                """
                CREATE TABLE IF NOT EXISTS research_sessions (
                    session_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    hypothesis_id TEXT,
                    prereg_id TEXT,
                    ticket_id TEXT,
                    current_stage TEXT NOT NULL,
                    is_completed INTEGER NOT NULL DEFAULT 0,
                    is_failed INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    report_path TEXT,
                    session_state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            mig_cur.execute("CREATE INDEX IF NOT EXISTS idx_proposals_ticket ON preregistration_proposals(ticket_id);")
            mig_cur.execute("CREATE INDEX IF NOT EXISTS idx_proposals_hyp ON preregistration_proposals(hypothesis_id);")
            mig_cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_hyp ON research_sessions(hypothesis_id);")

            mig_cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_proposal_delete
                BEFORE DELETE ON preregistration_proposals
                BEGIN
                    SELECT RAISE(ABORT, 'Security violation: DELETE prohibited on preregistration_proposals');
                END;
                """
            )
            mig_cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_session_delete
                BEFORE DELETE ON research_sessions
                BEGIN
                    SELECT RAISE(ABORT, 'Security violation: DELETE prohibited on research_sessions');
                END;
                """
            )
            mig_cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_proposal_tampering
                BEFORE UPDATE ON preregistration_proposals
                BEGIN
                    SELECT CASE
                        WHEN NEW.proposal_id != OLD.proposal_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify proposal_id')
                        WHEN NEW.ticket_id != OLD.ticket_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify ticket_id')
                        WHEN NEW.hypothesis_id != OLD.hypothesis_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify hypothesis_id')
                        WHEN NEW.parameter_grid_json != OLD.parameter_grid_json THEN
                            RAISE(ABORT, 'Security violation: Cannot modify proposed parameter_grid_json')
                        WHEN NEW.universe_json != OLD.universe_json THEN
                            RAISE(ABORT, 'Security violation: Cannot modify proposed universe_json')
                        WHEN NEW.created_at != OLD.created_at THEN
                            RAISE(ABORT, 'Security violation: Cannot modify created_at')
                        WHEN OLD.status IN ('APPROVED', 'REJECTED', 'EXPIRED') THEN
                            RAISE(ABORT, 'Lifecycle violation: Cannot modify proposal in terminal status')
                        WHEN NEW.status NOT IN ('APPROVED', 'REJECTED', 'EXPIRED') THEN
                            RAISE(ABORT, 'Lifecycle violation: Invalid target status for proposal')
                    END;
                END;
                """
            )
            mig_cur.execute(
                """
                INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
                VALUES (4, datetime('now'), 'Phase 2.2: Add persistent preregistration_proposals and research_sessions');
                """
            )

        # v4 -> v5: rerun_authorizations table, indexes, and immutability triggers
        if current_version < 5:
            mig_cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rerun_authorizations (
                    auth_id TEXT PRIMARY KEY,
                    original_request_id TEXT NOT NULL,
                    new_request_id TEXT NOT NULL UNIQUE,
                    hypothesis_id TEXT NOT NULL,
                    prereg_id TEXT NOT NULL,
                    configuration_digest TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    authorized_at TEXT NOT NULL,
                    is_consumed INTEGER NOT NULL DEFAULT 0,
                    consumed_at TEXT,
                    consumed_by TEXT,
                    FOREIGN KEY (original_request_id) REFERENCES execution_requests(request_id),
                    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(hypothesis_id),
                    FOREIGN KEY (prereg_id) REFERENCES preregistrations(prereg_id)
                );
                """
            )
            mig_cur.execute("CREATE INDEX IF NOT EXISTS idx_rerun_orig_req ON rerun_authorizations(original_request_id);")
            mig_cur.execute("CREATE INDEX IF NOT EXISTS idx_rerun_new_req ON rerun_authorizations(new_request_id);")
            mig_cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_rerun_auth_delete
                BEFORE DELETE ON rerun_authorizations
                BEGIN
                    SELECT RAISE(ABORT, 'Security violation: DELETE prohibited on rerun_authorizations');
                END;
                """
            )
            mig_cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_rerun_auth_tampering
                BEFORE UPDATE ON rerun_authorizations
                BEGIN
                    SELECT CASE
                        WHEN NEW.auth_id != OLD.auth_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify auth_id')
                        WHEN NEW.original_request_id != OLD.original_request_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify original_request_id')
                        WHEN NEW.new_request_id != OLD.new_request_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify new_request_id')
                        WHEN NEW.hypothesis_id != OLD.hypothesis_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify hypothesis_id')
                        WHEN NEW.prereg_id != OLD.prereg_id THEN
                            RAISE(ABORT, 'Security violation: Cannot modify prereg_id')
                        WHEN NEW.configuration_digest != OLD.configuration_digest THEN
                            RAISE(ABORT, 'Security violation: Cannot modify configuration_digest')
                        WHEN NEW.authorized_by != OLD.authorized_by THEN
                            RAISE(ABORT, 'Security violation: Cannot modify authorized_by')
                        WHEN NEW.rationale != OLD.rationale THEN
                            RAISE(ABORT, 'Security violation: Cannot modify rationale')
                        WHEN NEW.authorized_at != OLD.authorized_at THEN
                            RAISE(ABORT, 'Security violation: Cannot modify authorized_at')
                        WHEN OLD.is_consumed = 1 AND NEW.is_consumed = 0 THEN
                            RAISE(ABORT, 'Security violation: Cannot un-consume rerun authorization')
                        WHEN NEW.is_consumed NOT IN (0, 1) THEN
                            RAISE(ABORT, 'Lifecycle violation: Invalid is_consumed state')
                    END;
                END;
                """
            )
            mig_cur.execute(
                """
                INSERT OR IGNORE INTO schema_metadata (version, applied_at, description)
                VALUES (5, datetime('now'), 'Phase 2.2.1: Add rerun_authorizations table and immutability triggers');
                """
            )

        mig_conn.commit()

        # Step 7: Post-migration verification
        mig_cur.execute("PRAGMA integrity_check;")
        post_check = mig_cur.fetchone()
        if not post_check or post_check[0] != "ok":
            raise RuntimeError(f"Post-migration integrity check failed: {post_check}")

        for tbl, expected_count in pre_counts.items():
            if tbl == "schema_metadata":
                continue
            mig_cur.execute(f"SELECT COUNT(*) FROM {tbl};")
            actual_count = mig_cur.fetchone()[0]
            if actual_count != expected_count:
                raise RuntimeError(
                    f"Post-migration row count mismatch for table '{tbl}': expected {expected_count}, got {actual_count}"
                )

    except Exception as exc:
        mig_conn.rollback()
        mig_conn.close()
        # Restore backup file immediately
        shutil.copy2(backup_path, db_path)
        raise RuntimeError(
            f"Migration failed and database was restored from backup {backup_path}: {str(exc)}"
        ) from exc
    finally:
        mig_conn.close()

    return {
        "status": "SUCCESS",
        "backup_path": backup_path,
        "previous_version": current_version,
        "new_version": target_version,
        "tables_verified": list(pre_counts.keys()),
        "message": f"Successfully migrated database from schema v{current_version} to v{target_version}.",
    }
