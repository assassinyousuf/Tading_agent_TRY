"""Append-Only Research Ledger & Experiment Manager.

Manages SQLite storage for:
- Hypotheses & Preregistrations
- Experiments & Backtest Metrics (Preserving raw statistical outputs)
- Audit Results & Formal Research Decisions
- Hashed, single-use Test Vault Authorizations & Access Records
- Append-only Trial Ledger tracking M_total and correlation-based M_eff
"""

import datetime
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from trader.agent.evidence.manifest import EvidenceManifestContradictionError
from trader.agent.db.schema import (
    CURRENT_SCHEMA_VERSION,
    IncompatibleSchemaVersionError,
    create_schema,
    get_db_schema_version,
)


class ResearchLedger:
    """ACID-compliant append-only research ledger and trial manager."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        """Initialize research ledger connection.

        Args:
            db_path: Path to SQLite database file or ':memory:' for tests.
                     Defaults to 'trader/research_ledger.db'.
        """
        if db_path is None:
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(root, "research_ledger.db")

        self.db_path = db_path
        is_memory = (self.db_path == ":memory:")
        file_exists = os.path.isfile(self.db_path) if not is_memory else False

        self.conn = sqlite3.connect(self.db_path)

        if is_memory or not file_exists:
            create_schema(self.conn)
        else:
            version = get_db_schema_version(self.conn)
            if version == 0:
                cur = self.conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                tables = cur.fetchall()
                if not tables:
                    create_schema(self.conn)
                else:
                    raise IncompatibleSchemaVersionError(
                        f"Database schema version {version} is unversioned/outdated (expected {CURRENT_SCHEMA_VERSION}). "
                        f"Run 'python -m trader.agent.cli db migrate --db-path {self.db_path}' to upgrade."
                    )
            elif version < CURRENT_SCHEMA_VERSION:
                raise IncompatibleSchemaVersionError(
                    f"Database schema version {version} is outdated (expected {CURRENT_SCHEMA_VERSION}). "
                    f"Run 'python -m trader.agent.cli db migrate --db-path {self.db_path}' to upgrade."
                )

        self.reconcile_interrupted_trials()

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()

    def get_schema_version(self) -> int:
        """Return the current database schema version."""
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_metadata;")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0

    def record_hypothesis(
        self,
        hypothesis_id: str,
        strategy_name: str,
        economic_rationale: str,
        validation_criteria: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        max_evaluation_budget: int = 10,
    ) -> None:
        """Record a research hypothesis with hypothesis-specific preregistered hurdles."""
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO hypotheses (
                hypothesis_id, created_at, strategy_name, economic_rationale,
                status, validation_criteria_json, metadata_json, max_evaluation_budget
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                hypothesis_id,
                created_at,
                strategy_name,
                economic_rationale,
                "PREREGISTERED",
                json.dumps(validation_criteria),
                json.dumps(metadata or {}),
                max_evaluation_budget,
            ),
        )
        self.conn.commit()

    def preregister_experiment(
        self,
        hypothesis_id: str,
        universe: List[str],
        timeframe: str,
        parameter_grid: Dict[str, List[float]],
        cost_model: Dict[str, float],
        partition_protocol: Dict[str, Any],
        benchmark_ids: List[str],
    ) -> str:
        """Preregister and freeze experiment specifications before running simulations.

        Returns:
            Unique preregistration ID (e.g. 'PREREG-<uuid>').
        """
        prereg_id = f"PREREG-{uuid.uuid4().hex[:12].upper()}"
        preregistered_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO preregistrations (
                prereg_id, hypothesis_id, preregistered_at, universe_json,
                timeframe, parameter_grid_json, cost_model_json,
                partition_protocol_json, benchmark_ids_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                prereg_id,
                hypothesis_id,
                preregistered_at,
                json.dumps(universe),
                timeframe,
                json.dumps(parameter_grid),
                json.dumps(cost_model),
                json.dumps(partition_protocol),
                json.dumps(benchmark_ids),
                "FROZEN",
            ),
        )
        self.conn.commit()
        return prereg_id

    def verify_preregistration(
        self,
        prereg_id: str,
        config_params: Dict[str, float],
        partition: str,
        universe: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """Verify that proposed simulation parameters match the frozen preregistration.

        Returns:
            Tuple of (is_valid: bool, rejection_reason: str).
        """
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT parameter_grid_json, universe_json, partition_protocol_json, status
            FROM preregistrations WHERE prereg_id = ?;
            """,
            (prereg_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, f"Preregistration ID '{prereg_id}' not found in research ledger."

        grid_json, universe_json, protocol_json, status = row
        if status != "FROZEN":
            return False, f"Preregistration '{prereg_id}' is not in FROZEN state (status: {status})."

        grid = json.loads(grid_json)
        reg_universe = json.loads(universe_json)

        # Check universe
        if universe is not None and sorted(universe) != sorted(reg_universe):
            return False, f"Universe mismatch: expected {reg_universe}, got {universe}."

        # Check parameter grid
        for param_key, param_val in config_params.items():
            if param_key not in grid:
                return False, f"Parameter '{param_key}' was not declared in preregistration grid."
            allowed_vals = grid[param_key]
            # Match with floating point tolerance
            if not any(abs(param_val - v) < 1e-9 for v in allowed_vals):
                return False, f"Value {param_val} for '{param_key}' not in approved grid {allowed_vals}."

        return True, "Verification successful."

    def generate_test_vault_token(
        self,
        prereg_id: str,
        authorized_by: str,
        purpose: str,
        expires_in_seconds: int = 3600,
        experiment_id: Optional[str] = None,
    ) -> str:
        """Issue a cryptographically secure, single-use token permitting access to the sealed TEST partition.

        Security Invariant:
            The raw secret token is returned to the authorizer once.
            Only its SHA-256 digest is stored in the database.
        """
        raw_secret_token = f"TEST-TOKEN-{secrets.token_hex(24).upper()}"
        token_hash = hashlib.sha256(raw_secret_token.encode("utf-8")).hexdigest()

        auth_id = f"AUTH-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.datetime.now(datetime.timezone.utc)
        created_at = now.isoformat()
        expires_at = (now + datetime.timedelta(seconds=expires_in_seconds)).isoformat()

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO test_evaluation_authorizations (
                auth_id, prereg_id, experiment_id, token_hash,
                authorized_by, created_at, expires_at, is_revoked,
                max_uses, uses_count, purpose
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 0, ?);
            """,
            (
                auth_id,
                prereg_id,
                experiment_id,
                token_hash,
                authorized_by,
                created_at,
                expires_at,
                purpose,
            ),
        )
        self.conn.commit()
        return raw_secret_token

    def revoke_test_token(self, token_or_hash: str) -> bool:
        """Revoke an active test evaluation authorization token."""
        cur = self.conn.cursor()
        token_hash = (
            hashlib.sha256(token_or_hash.strip().encode("utf-8")).hexdigest()
            if token_or_hash.startswith("TEST-TOKEN-")
            else token_or_hash
        )
        cur.execute(
            "UPDATE test_evaluation_authorizations SET is_revoked = 1 WHERE token_hash = ?;",
            (token_hash,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def consume_test_vault_token(
        self,
        raw_token: str,
        prereg_id: str,
        experiment_id: Optional[str] = None,
        caller_identity: str = "test_runner",
        parameters: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Convenience alias for verify_and_consume_test_token."""
        return self.verify_and_consume_test_token(
            raw_token=raw_token,
            prereg_id=prereg_id,
            experiment_id=experiment_id,
            caller_identity=caller_identity,
            parameters=parameters,
        )

    def verify_and_consume_test_token(
        self,
        raw_token: str,
        prereg_id: str,
        experiment_id: Optional[str] = None,
        caller_identity: str = "test_runner",
        parameters: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Validate and consume a single-use test vault token.

        Records every access attempt (both granted and denied) into test_evaluation_records.

        Raises:
            PermissionError: If token is missing, malformed, expired, already used, or mismatched.
        """
        record_id = f"TEST-REC-{uuid.uuid4().hex[:12].upper()}"
        evaluated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        params_json = json.dumps(parameters or {})

        if not raw_token or not raw_token.strip():
            self._log_test_attempt(
                record_id=record_id,
                auth_id=None,
                prereg_id=prereg_id,
                experiment_id=experiment_id,
                evaluated_at=evaluated_at,
                caller_identity=caller_identity,
                parameters_json=params_json,
                access_granted=0,
                failure_reason="Empty or missing authorization token",
            )
            raise PermissionError("Access denied: TEST partition is sealed. Requires valid TEST_EVALUATION_TOKEN.")

        token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT auth_id, prereg_id, expires_at, is_revoked, max_uses, uses_count
            FROM test_evaluation_authorizations WHERE token_hash = ?;
            """,
            (token_hash,),
        )
        row = cur.fetchone()

        if not row:
            self._log_test_attempt(
                record_id=record_id,
                auth_id=None,
                prereg_id=prereg_id,
                experiment_id=experiment_id,
                evaluated_at=evaluated_at,
                caller_identity=caller_identity,
                parameters_json=params_json,
                access_granted=0,
                failure_reason="Token hash not found in authorization registry",
            )
            raise PermissionError("Access denied: Invalid or unrecognized test authorization token.")

        auth_id, reg_prereg_id, expires_at_str, is_revoked, max_uses, uses_count = row

        # Check preregistration binding
        if reg_prereg_id != prereg_id:
            reason = f"Token bound to preregistration '{reg_prereg_id}', cannot access '{prereg_id}'."
            self._log_test_attempt(
                record_id=record_id,
                auth_id=auth_id,
                prereg_id=prereg_id,
                experiment_id=experiment_id,
                evaluated_at=evaluated_at,
                caller_identity=caller_identity,
                parameters_json=params_json,
                access_granted=0,
                failure_reason=reason,
            )
            raise PermissionError(f"Access denied: {reason}")

        # Check revocation
        if is_revoked:
            reason = "Authorization token has been explicitly revoked."
            self._log_test_attempt(
                record_id=record_id,
                auth_id=auth_id,
                prereg_id=prereg_id,
                experiment_id=experiment_id,
                evaluated_at=evaluated_at,
                caller_identity=caller_identity,
                parameters_json=params_json,
                access_granted=0,
                failure_reason=reason,
            )
            raise PermissionError(f"Access denied: {reason}")

        # Check expiration
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        expires_dt = datetime.datetime.fromisoformat(expires_at_str)
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=datetime.timezone.utc)
        if now_dt > expires_dt:
            reason = f"Authorization token expired at {expires_at_str}."
            self._log_test_attempt(
                record_id=record_id,
                auth_id=auth_id,
                prereg_id=prereg_id,
                experiment_id=experiment_id,
                evaluated_at=evaluated_at,
                caller_identity=caller_identity,
                parameters_json=params_json,
                access_granted=0,
                failure_reason=reason,
            )
            raise PermissionError(f"Access denied: {reason}")

        # Check reuse / maximum uses
        if uses_count >= max_uses:
            reason = f"Authorization token already consumed (uses: {uses_count}/{max_uses})."
            self._log_test_attempt(
                record_id=record_id,
                auth_id=auth_id,
                prereg_id=prereg_id,
                experiment_id=experiment_id,
                evaluated_at=evaluated_at,
                caller_identity=caller_identity,
                parameters_json=params_json,
                access_granted=0,
                failure_reason=reason,
            )
            raise PermissionError(f"Access denied: {reason}")

        # Consume token with atomic concurrency check
        cur.execute(
            """
            UPDATE test_evaluation_authorizations
            SET uses_count = uses_count + 1
            WHERE auth_id = ? AND uses_count < max_uses;
            """,
            (auth_id,),
        )
        if cur.rowcount == 0:
            reason = f"Authorization token already consumed or concurrent collision (uses: {uses_count}/{max_uses})."
            self._log_test_attempt(
                record_id=record_id,
                auth_id=auth_id,
                prereg_id=prereg_id,
                experiment_id=experiment_id,
                evaluated_at=evaluated_at,
                caller_identity=caller_identity,
                parameters_json=params_json,
                access_granted=0,
                failure_reason=reason,
            )
            raise PermissionError(f"Access denied: {reason}")

        # Log granted attempt
        self._log_test_attempt(
            record_id=record_id,
            auth_id=auth_id,
            prereg_id=prereg_id,
            experiment_id=experiment_id,
            evaluated_at=evaluated_at,
            caller_identity=caller_identity,
            parameters_json=params_json,
            access_granted=1,
            failure_reason=None,
        )

        self.conn.commit()
        return True

    def _log_test_attempt(
        self,
        record_id: str,
        auth_id: Optional[str],
        prereg_id: str,
        experiment_id: Optional[str],
        evaluated_at: str,
        caller_identity: str,
        parameters_json: str,
        access_granted: int,
        failure_reason: Optional[str],
    ) -> None:
        """Internal helper to write audit record of TEST access attempt."""
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO test_evaluation_records (
                record_id, auth_id, prereg_id, experiment_id, evaluated_at,
                caller_identity, parameters_json, access_granted, failure_reason,
                execution_duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0);
            """,
            (
                record_id,
                auth_id,
                prereg_id,
                experiment_id,
                evaluated_at,
                caller_identity,
                parameters_json,
                access_granted,
                failure_reason,
            ),
        )

    def record_experiment_result(
        self,
        experiment_data: Dict[str, Any],
        metrics_data: Dict[str, Any],
        audit_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Atomically record an executed experiment, backtest metrics, and audit results."""
        try:
            with self.conn:
                cur = self.conn.cursor()

                # Resolve trial_id if not explicitly provided
                trial_id = experiment_data.get("trial_id")
                if trial_id is None:
                    cur.execute(
                        "SELECT trial_id FROM trial_ledger WHERE prereg_id = ? ORDER BY trial_id DESC LIMIT 1;",
                        (experiment_data["prereg_id"],),
                    )
                    row = cur.fetchone()
                    if row:
                        trial_id = row[0]
                    else:
                        trial_id = self.start_trial(
                            hypothesis_id="H-LEGACY",
                            prereg_id=experiment_data["prereg_id"],
                            experiment_id=experiment_data["experiment_id"],
                            parameters_evaluated=experiment_data.get("parameters", {}),
                            disposition="COMPLETED" if experiment_data.get("status") == "SUCCESS" else "FAILED",
                        )

                canonical_params = json.dumps(
                    experiment_data["parameters"], sort_keys=True, separators=(",", ":")
                )

                # 1. Insert Experiment
                cur.execute(
                    """
                    INSERT INTO experiments (
                        experiment_id, prereg_id, trial_id, executed_at, partition_name,
                        strategy_name, strategy_version, parameters_json, random_seed_63bit,
                        code_git_hash, data_hash, status, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        experiment_data["experiment_id"],
                        experiment_data["prereg_id"],
                        trial_id,
                        experiment_data.get("executed_at", datetime.datetime.now(datetime.timezone.utc).isoformat()),
                        experiment_data["partition_name"],
                        experiment_data.get("strategy_name", "UNKNOWN"),
                        experiment_data.get("strategy_version", "1.0.0"),
                        canonical_params,
                        experiment_data.get("random_seed_63bit", 42),
                        experiment_data.get("code_git_hash", "HEAD"),
                        experiment_data.get("data_hash", "UNKNOWN"),
                        experiment_data.get("status", "SUCCESS"),
                        experiment_data.get("error_message"),
                    ),
                )

                # 2. Insert Experiment Metrics
                metric_id = f"METRIC-{uuid.uuid4().hex[:12].upper()}"
                cur.execute(
                    """
                    INSERT INTO experiment_metrics (
                        metric_id, experiment_id, initial_capital, final_capital,
                        net_total_return_pct, gross_total_return_pct, cagr_pct,
                        annualized_vol_pct, sharpe_ratio, sortino_ratio,
                        max_drawdown_pct, total_trades, win_rate_pct,
                        total_friction_usd, compounding_error, raw_metrics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        metric_id,
                        experiment_data["experiment_id"],
                        metrics_data["initial_capital"],
                        metrics_data["final_capital"],
                        metrics_data["net_total_return_pct"],
                        metrics_data["gross_total_return_pct"],
                        metrics_data["cagr_pct"],
                        metrics_data["annualized_vol_pct"],
                        metrics_data["sharpe_ratio"],
                        metrics_data["sortino_ratio"],
                        metrics_data["max_drawdown_pct"],
                        metrics_data["total_trades"],
                        metrics_data["win_rate_pct"],
                        metrics_data["total_friction_usd"],
                        metrics_data.get("compounding_error", 0.0),
                        json.dumps(metrics_data.get("raw_metrics", {})),
                    ),
                )

                # 3. Insert Audit Results (if present)
                if audit_data:
                    audit_id = f"AUDIT-{uuid.uuid4().hex[:12].upper()}"
                    cur.execute(
                        """
                        INSERT INTO audit_results (
                            audit_id, experiment_id, benchmark_id, bootstrap_p_value_raw,
                            bootstrap_ci_lower, bootstrap_ci_upper, screening_category,
                            ar2_max_root, dsr_value_raw, m_total_used, m_eff_diagnostic,
                            raw_audit_json, adjusted_metrics_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            audit_id,
                            experiment_data["experiment_id"],
                            audit_data["benchmark_id"],
                            audit_data["bootstrap_p_value_raw"],
                            audit_data["bootstrap_ci_lower"],
                            audit_data["bootstrap_ci_upper"],
                            audit_data["screening_category"],
                            audit_data.get("ar2_max_root"),
                            audit_data["dsr_value_raw"],
                            audit_data["m_total_used"],
                            audit_data.get("m_eff_diagnostic"),
                            json.dumps(audit_data.get("raw_audit", {})),
                            json.dumps(audit_data.get("adjusted_metrics", {})),
                        ),
                    )
        except Exception:
            self.conn.rollback()
            raise

    def record_decision(
        self,
        hypothesis_id: str,
        decision: str,
        rationale: str,
        decided_by: str,
        prereg_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
    ) -> str:
        """Record an immutable formal research decision (GO, NO_GO, INCONCLUSIVE)."""
        if decision not in ("GO", "NO_GO", "INCONCLUSIVE"):
            raise ValueError(f"Invalid decision '{decision}'. Allowed: GO, NO_GO, INCONCLUSIVE")

        decision_id = f"DECISION-{uuid.uuid4().hex[:12].upper()}"
        decided_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO research_decisions (
                decision_id, hypothesis_id, prereg_id, experiment_id,
                decision, rationale, decided_at, decided_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                decision_id,
                hypothesis_id,
                prereg_id,
                experiment_id,
                decision,
                rationale,
                decided_at,
                decided_by,
            ),
        )
        self.conn.commit()
        return decision_id

    def record_trial(
        self,
        hypothesis_id: str,
        prereg_id: str,
        experiment_id: Optional[str],
        parameters_evaluated: Dict[str, Any],
        disposition: str,
        notes: Optional[str] = None,
        diagnostic_m_eff: Optional[float] = None,
    ) -> int:
        """Append an evaluation or pre-test rejection to the trial ledger.

        Returns:
            Updated cumulative raw M_total.
        """
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self.conn.cursor()

        # Get current cumulative M_total
        cur.execute("SELECT COUNT(*) FROM trial_ledger;")
        current_count = cur.fetchone()[0]
        new_m_total = current_count + 1

        cur.execute(
            """
            INSERT INTO trial_ledger (
                timestamp, hypothesis_id, prereg_id, experiment_id,
                parameters_evaluated_json, disposition, cumulative_m_total,
                diagnostic_m_eff, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                timestamp,
                hypothesis_id,
                prereg_id,
                experiment_id,
                json.dumps(parameters_evaluated),
                disposition,
                new_m_total,
                diagnostic_m_eff,
                notes or "",
            ),
        )
        self.conn.commit()
        return new_m_total

    def start_trial(
        self,
        hypothesis_id: str,
        prereg_id: str,
        experiment_id: Optional[str],
        parameters_evaluated: Dict[str, Any],
        disposition: str = "STARTED",
        notes: Optional[str] = None,
    ) -> int:
        """Start a research trial record BEFORE simulation execution begins.

        Enforces:
        - Immediate persistence to SQLite.
        - Monotonic increment of cumulative M_total.
        - Guarantees interrupted or crashed simulations are not lost from trial accounting.

        Returns:
            Unique trial_id (int).
        """
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        canonical_params = json.dumps(parameters_evaluated, sort_keys=True, separators=(",", ":"))
        cur = self.conn.cursor()

        cur.execute("SELECT COUNT(*) FROM trial_ledger;")
        current_count = cur.fetchone()[0]
        new_m_total = current_count + 1

        cur.execute(
            """
            INSERT INTO trial_ledger (
                timestamp, hypothesis_id, prereg_id, experiment_id,
                parameters_evaluated_json, disposition, cumulative_m_total,
                diagnostic_m_eff, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?);
            """,
            (
                timestamp,
                hypothesis_id,
                prereg_id,
                experiment_id,
                canonical_params,
                disposition,
                new_m_total,
                notes or "",
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def finalize_trial(
        self,
        trial_id: int,
        disposition: str,
        experiment_id: Optional[str] = None,
        notes: Optional[str] = None,
        diagnostic_m_eff: Optional[float] = None,
    ) -> bool:
        """Finalize an active trial (STARTED/ATTEMPTED) into a terminal disposition.

        Valid terminal dispositions: COMPLETED, FAILED, INTERRUPTED.
        Once finalized, the database trigger prevents any further modification.
        """
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE trial_ledger
            SET disposition = ?,
                experiment_id = COALESCE(?, experiment_id),
                notes = CASE WHEN ? IS NOT NULL AND ? != '' THEN ? ELSE notes END,
                diagnostic_m_eff = COALESCE(?, diagnostic_m_eff)
            WHERE trial_id = ? AND disposition IN ('STARTED', 'ATTEMPTED');
            """,
            (
                disposition,
                experiment_id,
                notes,
                notes,
                notes,
                diagnostic_m_eff,
                trial_id,
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def reconcile_interrupted_trials(self) -> int:
        """Reconcile any orphaned trials left in STARTED state from an interrupted process."""
        cur = self.conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trial_ledger';")
        if not cur.fetchone():
            return 0

        cur.execute("SELECT trial_id FROM trial_ledger WHERE disposition IN ('STARTED', 'ATTEMPTED');")
        pending_ids = [r[0] for r in cur.fetchall()]
        for tid in pending_ids:
            cur.execute(
                """
                UPDATE trial_ledger
                SET disposition = 'INTERRUPTED',
                    notes = 'Reconciled: process terminated unexpectedly prior to completion'
                WHERE trial_id = ? AND disposition IN ('STARTED', 'ATTEMPTED');
                """,
                (tid,),
            )
        # Reconcile orphaned execution requests if table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='execution_requests';")
        if cur.fetchone():
            cur.execute("UPDATE execution_requests SET status = 'INTERRUPTED' WHERE status = 'STARTED';")
        self.conn.commit()
        return len(pending_ids)

    def get_hypothesis_budget(self, hypothesis_id: str) -> int:
        """Return maximum allowed evaluation budget for hypothesis."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT max_evaluation_budget FROM hypotheses WHERE hypothesis_id = ?;",
            (hypothesis_id,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 10

    def get_hypothesis_evaluation_count(self, hypothesis_id: str) -> int:
        """Return total research trials consumed by hypothesis."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM trial_ledger
            WHERE hypothesis_id = ?
              AND disposition IN ('STARTED', 'COMPLETED', 'FAILED', 'INTERRUPTED', 'PRE_TEST_REJECTED');
            """,
            (hypothesis_id,),
        )
        row = cur.fetchone()
        return row[0] if row else 0

    def get_execution_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve execution request record by request_id."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT request_id, hypothesis_id, prereg_id, payload_hash,
                   experiment_id, trial_id, status, created_at
            FROM execution_requests WHERE request_id = ?;
            """,
            (request_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "request_id": row[0],
            "hypothesis_id": row[1],
            "prereg_id": row[2],
            "payload_hash": row[3],
            "experiment_id": row[4],
            "trial_id": row[5],
            "status": row[6],
            "created_at": row[7],
        }

    def record_execution_request(
        self,
        request_id: str,
        hypothesis_id: str,
        prereg_id: str,
        payload_hash: str,
        experiment_id: Optional[str] = None,
        trial_id: Optional[int] = None,
        status: str = "STARTED",
    ) -> None:
        """Record an execution request before simulation starts."""
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO execution_requests (
                request_id, hypothesis_id, prereg_id, payload_hash,
                experiment_id, trial_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                request_id,
                hypothesis_id,
                prereg_id,
                payload_hash,
                experiment_id,
                trial_id,
                status,
                created_at,
            ),
        )
        self.conn.commit()

    def update_execution_request(
        self,
        request_id: str,
        status: str,
        experiment_id: Optional[str] = None,
        trial_id: Optional[int] = None,
    ) -> bool:
        """Update terminal status of an execution request."""
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE execution_requests
            SET status = ?,
                experiment_id = COALESCE(?, experiment_id),
                trial_id = COALESCE(?, trial_id)
            WHERE request_id = ?;
            """,
            (status, experiment_id, trial_id, request_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def record_failed_experiment(
        self,
        experiment_id: str,
        prereg_id: str,
        trial_id: int,
        partition_name: str,
        parameters: Dict[str, Any],
        strategy_name: str = "UNKNOWN",
        strategy_version: str = "1.0.0",
        data_hash: str = "UNKNOWN",
        error_message: str = "",
        random_seed_63bit: int = 42,
        code_git_hash: str = "HEAD",
    ) -> None:
        """Record an experiment attempt that failed during simulation execution."""
        canonical_params = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        executed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO experiments (
                experiment_id, prereg_id, trial_id, executed_at, partition_name,
                strategy_name, strategy_version, parameters_json, random_seed_63bit,
                code_git_hash, data_hash, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'FAILED', ?);
            """,
            (
                experiment_id,
                prereg_id,
                trial_id,
                executed_at,
                partition_name,
                strategy_name,
                strategy_version,
                canonical_params,
                random_seed_63bit,
                code_git_hash,
                data_hash,
                error_message,
            ),
        )
        self.conn.commit()

    @staticmethod
    def calculate_effective_trials_diagnostic(return_series_matrix: List[List[float]]) -> float:
        """Compute the eigenvalue participation-ratio measure as a correlation diagnostic.

        Note:
            This metric provides an estimate of effective degrees of freedom based on
            return collinearity: M_eff = (sum lambda_i)^2 / sum (lambda_i^2).
            It is labeled and tracked as a correlation-based diagnostic and is NOT
            substituted into the production DSR calculation without independent verification.
        """
        if not return_series_matrix or len(return_series_matrix) <= 1:
            return 1.0

        M = len(return_series_matrix)
        arr = np.array(return_series_matrix, dtype=float)

        stds = np.std(arr, axis=1)
        valid_idx = np.where(stds > 1e-12)[0]
        if len(valid_idx) <= 1:
            return 1.0

        valid_arr = arr[valid_idx]
        corr = np.corrcoef(valid_arr)
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)

        eigvals = np.linalg.eigvalsh(corr)
        eigvals = np.maximum(eigvals, 0.0)

        sum_eigs = np.sum(eigvals)
        sum_sq_eigs = np.sum(eigvals**2)

        if sum_sq_eigs <= 1e-12:
            return float(len(valid_idx))

        m_eff = (sum_eigs**2) / sum_sq_eigs
        return float(min(M, max(1.0, m_eff)))

    def get_trial_counts(self) -> Dict[str, int]:
        """Return raw trial counts and disposition breakdown."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trial_ledger;")
        total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition IN ('COMPLETED', 'TRAIN_EVALUATED', 'VALIDATION_EVALUATED', 'TEST_EVALUATED');")
        evaluated_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition = 'TEST_EVALUATED';")
        test_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM trial_ledger WHERE disposition LIKE '%REJECTED%';")
        rejected_count = cur.fetchone()[0]

        return {
            "M_total": total,
            "M_evaluated": evaluated_count,
            "M_test": test_count,
            "M_rejected": rejected_count,
        }

    def record_proposal(
        self,
        proposal_id: str,
        ticket_id: str,
        hypothesis_id: str,
        strategy_name: str,
        universe: List[str],
        timeframe: str,
        parameter_grid: Dict[str, Any],
        cost_model: Dict[str, float],
        partition_protocol: Dict[str, Any],
        benchmark_ids: List[str],
        created_at: str,
        expires_at: str,
        submission_notes: Optional[str] = None,
    ) -> None:
        """Persist a preregistration proposal and human approval ticket to SQLite."""
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO preregistration_proposals (
                proposal_id, ticket_id, hypothesis_id, strategy_name,
                universe_json, timeframe, parameter_grid_json, cost_model_json,
                partition_protocol_json, benchmark_ids_json, created_at, expires_at,
                status, submission_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?);
            """,
            (
                proposal_id,
                ticket_id,
                hypothesis_id,
                strategy_name,
                json.dumps(universe),
                timeframe,
                json.dumps(parameter_grid),
                json.dumps(cost_model),
                json.dumps(partition_protocol),
                json.dumps(benchmark_ids),
                created_at,
                expires_at,
                submission_notes,
            ),
        )
        self.conn.commit()

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve proposal record by proposal_id."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT proposal_id, ticket_id, hypothesis_id, strategy_name,
                   universe_json, timeframe, parameter_grid_json, cost_model_json,
                   partition_protocol_json, benchmark_ids_json, created_at, expires_at,
                   status, submission_notes, approved_at, approved_by, decision_rationale, prereg_id
            FROM preregistration_proposals WHERE proposal_id = ?;
            """,
            (proposal_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "proposal_id": row[0],
            "ticket_id": row[1],
            "hypothesis_id": row[2],
            "strategy_name": row[3],
            "universe": json.loads(row[4]),
            "timeframe": row[5],
            "parameter_grid": json.loads(row[6]),
            "cost_model": json.loads(row[7]),
            "partition_protocol": json.loads(row[8]),
            "benchmark_ids": json.loads(row[9]),
            "created_at": row[10],
            "expires_at": row[11],
            "status": row[12],
            "submission_notes": row[13],
            "approved_at": row[14],
            "approved_by": row[15],
            "decision_rationale": row[16],
            "prereg_id": row[17],
        }

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve ticket and associated proposal by ticket_id."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT proposal_id, ticket_id, hypothesis_id, strategy_name,
                   universe_json, timeframe, parameter_grid_json, cost_model_json,
                   partition_protocol_json, benchmark_ids_json, created_at, expires_at,
                   status, submission_notes, approved_at, approved_by, decision_rationale, prereg_id
            FROM preregistration_proposals WHERE ticket_id = ?;
            """,
            (ticket_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "proposal_id": row[0],
            "ticket_id": row[1],
            "hypothesis_id": row[2],
            "strategy_name": row[3],
            "universe": json.loads(row[4]),
            "timeframe": row[5],
            "parameter_grid": json.loads(row[6]),
            "cost_model": json.loads(row[7]),
            "partition_protocol": json.loads(row[8]),
            "benchmark_ids": json.loads(row[9]),
            "created_at": row[10],
            "expires_at": row[11],
            "status": row[12],
            "submission_notes": row[13],
            "approved_at": row[14],
            "approved_by": row[15],
            "decision_rationale": row[16],
            "prereg_id": row[17],
        }

    def list_tickets(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List proposals and approval tickets, optionally filtered by status."""
        cur = self.conn.cursor()
        if status:
            cur.execute(
                """
                SELECT ticket_id, proposal_id, hypothesis_id, strategy_name, status, created_at, expires_at, prereg_id
                FROM preregistration_proposals WHERE status = ? ORDER BY created_at DESC;
                """,
                (status,),
            )
        else:
            cur.execute(
                """
                SELECT ticket_id, proposal_id, hypothesis_id, strategy_name, status, created_at, expires_at, prereg_id
                FROM preregistration_proposals ORDER BY created_at DESC;
                """
            )
        rows = cur.fetchall()
        return [
            {
                "ticket_id": r[0],
                "proposal_id": r[1],
                "hypothesis_id": r[2],
                "strategy_name": r[3],
                "status": r[4],
                "created_at": r[5],
                "expires_at": r[6],
                "prereg_id": r[7],
            }
            for r in rows
        ]

    def approve_proposal_transaction(
        self,
        ticket_id: str,
        human_identity: str,
        rationale: str,
    ) -> str:
        """Atomically approve a proposal ticket, commit frozen preregistration, and record research decision.

        Enforces:
        1. Human identity and rationale cannot be empty.
        2. Ticket must exist and have status 'PENDING'.
        3. Ticket must not be expired.
        4. Writes to preregistrations, research_decisions, and preregistration_proposals within a single transaction.
        5. Rolls back completely on any failure, preventing partial approval states.
        """
        if not human_identity or not human_identity.strip():
            raise ValueError("Human identity is required to authorize an approval ticket.")
        if not rationale or not rationale.strip():
            raise ValueError("Decision rationale is required to authorize an approval ticket.")

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise ValueError(f"Approval ticket '{ticket_id}' not found.")

        if ticket["status"] != "PENDING":
            raise ValueError(
                f"Ticket '{ticket_id}' cannot be approved: current status is '{ticket['status']}'."
            )

        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = datetime.datetime.fromisoformat(ticket["expires_at"])
        if now > expires_at:
            cur = self.conn.cursor()
            cur.execute("UPDATE preregistration_proposals SET status = 'EXPIRED' WHERE ticket_id = ?;", (ticket_id,))
            self.conn.commit()
            raise ValueError(f"Approval ticket '{ticket_id}' expired at {ticket['expires_at']}.")

        cur = self.conn.cursor()
        try:
            # Step A: Freeze preregistration in preregistrations table
            prereg_id = f"PREREG-{uuid.uuid4().hex[:12].upper()}"
            cur.execute(
                """
                INSERT INTO preregistrations (
                    prereg_id, hypothesis_id, preregistered_at, universe_json,
                    timeframe, parameter_grid_json, cost_model_json,
                    partition_protocol_json, benchmark_ids_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'FROZEN');
                """,
                (
                    prereg_id,
                    ticket["hypothesis_id"],
                    now.isoformat(),
                    json.dumps(ticket["universe"]),
                    ticket["timeframe"],
                    json.dumps(ticket["parameter_grid"]),
                    json.dumps(ticket["cost_model"]),
                    json.dumps(ticket["partition_protocol"]),
                    json.dumps(ticket["benchmark_ids"]),
                ),
            )

            # Step B: Record formal human decision in research_decisions table (SEC-P2-03)
            decision_id = f"DEC-{uuid.uuid4().hex[:10].upper()}"
            cur.execute(
                """
                INSERT INTO research_decisions (
                    decision_id, hypothesis_id, prereg_id, experiment_id,
                    decision, rationale, decided_at, decided_by
                ) VALUES (?, ?, ?, NULL, 'GO', ?, ?, ?);
                """,
                (
                    decision_id,
                    ticket["hypothesis_id"],
                    prereg_id,
                    rationale.strip(),
                    now.isoformat(),
                    human_identity.strip(),
                ),
            )

            # Step C: Update proposal ticket status to APPROVED
            cur.execute(
                """
                UPDATE preregistration_proposals
                SET status = 'APPROVED',
                    approved_at = ?,
                    approved_by = ?,
                    decision_rationale = ?,
                    prereg_id = ?
                WHERE ticket_id = ?;
                """,
                (
                    now.isoformat(),
                    human_identity.strip(),
                    rationale.strip(),
                    prereg_id,
                    ticket_id,
                ),
            )

            self.conn.commit()
            return prereg_id
        except Exception:
            self.conn.rollback()
            raise

    def reject_proposal_transaction(
        self,
        ticket_id: str,
        human_identity: str,
        rationale: str,
    ) -> None:
        """Reject a proposal ticket and record NO_GO decision in research_decisions."""
        if not human_identity or not human_identity.strip():
            raise ValueError("Human identity is required to reject an approval ticket.")
        if not rationale or not rationale.strip():
            raise ValueError("Decision rationale is required to reject an approval ticket.")

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise ValueError(f"Approval ticket '{ticket_id}' not found.")
        if ticket["status"] != "PENDING":
            raise ValueError(f"Ticket '{ticket_id}' cannot be rejected: current status is '{ticket['status']}'.")

        now = datetime.datetime.now(datetime.timezone.utc)
        cur = self.conn.cursor()
        try:
            decision_id = f"DEC-{uuid.uuid4().hex[:10].upper()}"
            cur.execute(
                """
                INSERT INTO research_decisions (
                    decision_id, hypothesis_id, prereg_id, experiment_id,
                    decision, rationale, decided_at, decided_by
                ) VALUES (?, ?, NULL, NULL, 'NO_GO', ?, ?, ?);
                """,
                (
                    decision_id,
                    ticket["hypothesis_id"],
                    rationale.strip(),
                    now.isoformat(),
                    human_identity.strip(),
                ),
            )

            cur.execute(
                """
                UPDATE preregistration_proposals
                SET status = 'REJECTED',
                    approved_at = ?,
                    approved_by = ?,
                    decision_rationale = ?
                WHERE ticket_id = ?;
                """,
                (
                    now.isoformat(),
                    human_identity.strip(),
                    rationale.strip(),
                    ticket_id,
                ),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def record_session(
        self,
        session_id: str,
        objective: str,
        current_stage: str,
        session_state_json: str,
        hypothesis_id: Optional[str] = None,
        prereg_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
    ) -> None:
        """Record initial research session checkpoint in SQLite."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO research_sessions (
                session_id, objective, hypothesis_id, prereg_id, ticket_id,
                current_stage, is_completed, is_failed, error_message,
                report_path, session_state_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, ?, ?, ?);
            """,
            (
                session_id,
                objective,
                hypothesis_id,
                prereg_id,
                ticket_id,
                current_stage,
                session_state_json,
                now,
                now,
            ),
        )
        self.conn.commit()

    def update_session(
        self,
        session_id: str,
        current_stage: str,
        session_state_json: str,
        is_completed: bool = False,
        is_failed: bool = False,
        error_message: Optional[str] = None,
        report_path: Optional[str] = None,
        hypothesis_id: Optional[str] = None,
        prereg_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
    ) -> None:
        """Update research session checkpoint with stage progression."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE research_sessions
            SET current_stage = ?,
                session_state_json = ?,
                is_completed = ?,
                is_failed = ?,
                error_message = COALESCE(?, error_message),
                report_path = COALESCE(?, report_path),
                hypothesis_id = COALESCE(?, hypothesis_id),
                prereg_id = COALESCE(?, prereg_id),
                ticket_id = COALESCE(?, ticket_id),
                updated_at = ?
            WHERE session_id = ?;
            """,
            (
                current_stage,
                session_state_json,
                1 if is_completed else 0,
                1 if is_failed else 0,
                error_message,
                report_path,
                hypothesis_id,
                prereg_id,
                ticket_id,
                now,
                session_id,
            ),
        )
        self.conn.commit()

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session checkpoint record by session_id."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT session_id, objective, hypothesis_id, prereg_id, ticket_id,
                   current_stage, is_completed, is_failed, error_message,
                   report_path, session_state_json, created_at, updated_at
            FROM research_sessions WHERE session_id = ?;
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "session_id": row[0],
            "objective": row[1],
            "hypothesis_id": row[2],
            "prereg_id": row[3],
            "ticket_id": row[4],
            "current_stage": row[5],
            "is_completed": bool(row[6]),
            "is_failed": bool(row[7]),
            "error_message": row[8],
            "report_path": row[9],
            "session_state_json": row[10],
            "created_at": row[11],
            "updated_at": row[12],
        }

    def authorize_rerun_transaction(
        self,
        original_request_id: str,
        authorized_by: str,
        rationale: str,
        configuration_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically authorize a human-signed rerun for an interrupted execution request.

        Enforces:
        1. Explicit non-empty human authorization identity and rationale.
        2. Original execution request must exist and have status 'INTERRUPTED'.
        3. Configuration digest binding matches original payload_hash.
        4. Idempotency: repeated identical authorization requests return existing active record.
        5. Conflicting active authorization requests for the same request are rejected.
        6. Unique new_request_id generated ONLY upon authorization.
        7. Records formal RERUN_AUTHORIZED decision in research_decisions table.
        """
        if not authorized_by or not authorized_by.strip():
            raise ValueError("Human authorization identity cannot be empty.")
        if not rationale or not rationale.strip():
            raise ValueError("Decision rationale cannot be empty.")

        orig_req = self.get_execution_request(original_request_id)
        if not orig_req:
            raise ValueError(f"Original execution request '{original_request_id}' not found.")

        if orig_req["status"] != "INTERRUPTED":
            raise ValueError(
                f"Cannot authorize rerun: request '{original_request_id}' has status '{orig_req['status']}', expected 'INTERRUPTED'."
            )

        expected_digest = orig_req["payload_hash"]
        if configuration_digest and configuration_digest != expected_digest:
            raise ValueError(
                f"Configuration digest mismatch for '{original_request_id}': expected '{expected_digest}', got '{configuration_digest}'."
            )

        target_digest = configuration_digest or expected_digest
        cur = self.conn.cursor()

        # Check for existing active (unconsumed) rerun authorization for this request
        cur.execute(
            """
            SELECT auth_id, original_request_id, new_request_id, hypothesis_id, prereg_id,
                   configuration_digest, authorized_by, rationale, authorized_at,
                   is_consumed, consumed_at, consumed_by
            FROM rerun_authorizations
            WHERE original_request_id = ? AND is_consumed = 0;
            """,
            (original_request_id,),
        )
        existing = cur.fetchone()
        if existing:
            existing_record = {
                "auth_id": existing[0],
                "original_request_id": existing[1],
                "new_request_id": existing[2],
                "hypothesis_id": existing[3],
                "prereg_id": existing[4],
                "configuration_digest": existing[5],
                "authorized_by": existing[6],
                "rationale": existing[7],
                "authorized_at": existing[8],
                "is_consumed": existing[9],
                "consumed_at": existing[10],
                "consumed_by": existing[11],
            }
            if (
                existing_record["authorized_by"] == authorized_by.strip()
                and existing_record["rationale"] == rationale.strip()
                and existing_record["configuration_digest"] == target_digest
            ):
                return existing_record
            raise ValueError(
                f"Conflicting active rerun authorization '{existing_record['auth_id']}' already exists for request '{original_request_id}'."
            )

        cur.execute(
            "SELECT COUNT(*) FROM rerun_authorizations WHERE original_request_id = ?;",
            (original_request_id,),
        )
        historical_reruns = cur.fetchone()[0]
        new_request_id = f"{original_request_id}-RERUN{historical_reruns + 1}"
        auth_id = f"RERUN-AUTH-{uuid.uuid4().hex[:8].upper()}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        try:
            cur.execute(
                """
                INSERT INTO rerun_authorizations (
                    auth_id, original_request_id, new_request_id, hypothesis_id,
                    prereg_id, configuration_digest, authorized_by, rationale,
                    authorized_at, is_consumed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0);
                """,
                (
                    auth_id,
                    original_request_id,
                    new_request_id,
                    orig_req["hypothesis_id"],
                    orig_req["prereg_id"],
                    target_digest,
                    authorized_by.strip(),
                    rationale.strip(),
                    now_iso,
                ),
            )

            decision_id = f"DEC-{uuid.uuid4().hex[:10].upper()}"
            cur.execute(
                """
                INSERT INTO research_decisions (
                    decision_id, hypothesis_id, prereg_id, experiment_id,
                    decision, rationale, decided_at, decided_by
                ) VALUES (?, ?, ?, NULL, 'RERUN_AUTHORIZED', ?, ?, ?);
                """,
                (
                    decision_id,
                    orig_req["hypothesis_id"],
                    orig_req["prereg_id"],
                    f"Authorized rerun of interrupted request {original_request_id} as {new_request_id}: {rationale.strip()}",
                    now_iso,
                    authorized_by.strip(),
                ),
            )

            self.conn.commit()
            return {
                "auth_id": auth_id,
                "original_request_id": original_request_id,
                "new_request_id": new_request_id,
                "hypothesis_id": orig_req["hypothesis_id"],
                "prereg_id": orig_req["prereg_id"],
                "configuration_digest": target_digest,
                "authorized_by": authorized_by.strip(),
                "rationale": rationale.strip(),
                "authorized_at": now_iso,
                "is_consumed": 0,
                "consumed_at": None,
                "consumed_by": None,
            }
        except Exception:
            self.conn.rollback()
            raise

    def get_rerun_authorization(self, original_request_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve active (unconsumed) rerun authorization for an interrupted request."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT auth_id, original_request_id, new_request_id, hypothesis_id, prereg_id,
                   configuration_digest, authorized_by, rationale, authorized_at,
                   is_consumed, consumed_at, consumed_by
            FROM rerun_authorizations
            WHERE original_request_id = ? AND is_consumed = 0
            ORDER BY authorized_at DESC LIMIT 1;
            """,
            (original_request_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "auth_id": row[0],
            "original_request_id": row[1],
            "new_request_id": row[2],
            "hypothesis_id": row[3],
            "prereg_id": row[4],
            "configuration_digest": row[5],
            "authorized_by": row[6],
            "rationale": row[7],
            "authorized_at": row[8],
            "is_consumed": row[9],
            "consumed_at": row[10],
            "consumed_by": row[11],
        }

    def list_rerun_authorizations(self, is_consumed: Optional[int] = None) -> List[Dict[str, Any]]:
        """List rerun authorizations, optionally filtered by consumed state."""
        cur = self.conn.cursor()
        if is_consumed is not None:
            cur.execute(
                """
                SELECT auth_id, original_request_id, new_request_id, hypothesis_id, prereg_id,
                       configuration_digest, authorized_by, rationale, authorized_at,
                       is_consumed, consumed_at, consumed_by
                FROM rerun_authorizations
                WHERE is_consumed = ?
                ORDER BY authorized_at DESC;
                """,
                (is_consumed,),
            )
        else:
            cur.execute(
                """
                SELECT auth_id, original_request_id, new_request_id, hypothesis_id, prereg_id,
                       configuration_digest, authorized_by, rationale, authorized_at,
                       is_consumed, consumed_at, consumed_by
                FROM rerun_authorizations
                ORDER BY authorized_at DESC;
                """
            )
        rows = cur.fetchall()
        return [
            {
                "auth_id": r[0],
                "original_request_id": r[1],
                "new_request_id": r[2],
                "hypothesis_id": r[3],
                "prereg_id": r[4],
                "configuration_digest": r[5],
                "authorized_by": r[6],
                "rationale": r[7],
                "authorized_at": r[8],
                "is_consumed": r[9],
                "consumed_at": r[10],
                "consumed_by": r[11],
            }
            for r in rows
        ]

    def consume_rerun_authorization(self, auth_id: str, consumed_by: str) -> bool:
        """Atomically transition a rerun authorization from active (0) to consumed (1)."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE rerun_authorizations
            SET is_consumed = 1,
                consumed_at = ?,
                consumed_by = ?
            WHERE auth_id = ? AND is_consumed = 0;
            """,
            (now_iso, consumed_by, auth_id),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"Rerun authorization '{auth_id}' is not active or has already been consumed.")
        return True

    def record_proposal_revision(
        self,
        hypothesis_id: str,
        revision_number: int,
        reviewer_decision: str,
        structured_reasons: List[str],
        configuration_digest: str,
        parent_proposal_id: Optional[str] = None,
        revised_proposal_id: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        response_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an immutable proposal revision entry in proposal_revisions."""
        revision_id = f"REV-{uuid.uuid4().hex[:12].upper()}"
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        reasons_json = json.dumps(structured_reasons, sort_keys=True)
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO proposal_revisions (
                revision_id, hypothesis_id, revision_number,
                parent_proposal_id, revised_proposal_id,
                reviewer_decision, structured_reasons_json,
                timestamp, configuration_digest,
                prompt_hash, response_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                revision_id,
                hypothesis_id,
                revision_number,
                parent_proposal_id,
                revised_proposal_id,
                reviewer_decision,
                reasons_json,
                timestamp,
                configuration_digest,
                prompt_hash,
                response_hash,
            ),
        )
        self.conn.commit()
        return {
            "revision_id": revision_id,
            "hypothesis_id": hypothesis_id,
            "revision_number": revision_number,
            "parent_proposal_id": parent_proposal_id,
            "revised_proposal_id": revised_proposal_id,
            "reviewer_decision": reviewer_decision,
            "structured_reasons_json": reasons_json,
            "timestamp": timestamp,
            "configuration_digest": configuration_digest,
            "prompt_hash": prompt_hash,
            "response_hash": response_hash,
        }

    def get_proposal_revisions(self, hypothesis_id: str) -> List[Dict[str, Any]]:
        """Retrieve all recorded revisions for a hypothesis in chronological order."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT revision_id, hypothesis_id, revision_number,
                   parent_proposal_id, revised_proposal_id,
                   reviewer_decision, structured_reasons_json,
                   timestamp, configuration_digest,
                   prompt_hash, response_hash
            FROM proposal_revisions
            WHERE hypothesis_id = ?
            ORDER BY revision_number ASC, timestamp ASC;
            """,
            (hypothesis_id,),
        )
        rows = cur.fetchall()
        return [
            {
                "revision_id": r[0],
                "hypothesis_id": r[1],
                "revision_number": r[2],
                "parent_proposal_id": r[3],
                "revised_proposal_id": r[4],
                "reviewer_decision": r[5],
                "structured_reasons": json.loads(r[6]),
                "timestamp": r[7],
                "configuration_digest": r[8],
                "prompt_hash": r[9],
                "response_hash": r[10],
            }
            for r in rows
        ]

    def record_evidence_manifest(self, manifest_data: Dict[str, Any]) -> str:
        """Persist an immutable experiment evidence manifest in SQLite."""
        cur = self.conn.cursor()
        manifest_id = manifest_data["manifest_id"]

        # Check if manifest already exists (idempotency and contradiction prevention)
        cur.execute("SELECT manifest_hash FROM experiment_evidence_manifests WHERE manifest_id = ?;", (manifest_id,))
        existing = cur.fetchone()
        if existing:
            if existing[0] == manifest_data["manifest_hash"]:
                return manifest_id  # Idempotent recovery
            raise EvidenceManifestContradictionError(
                f"Contradictory manifest record detected for {manifest_id}: existing hash {existing[0]} != {manifest_data['manifest_hash']}"
            )

        feat_json = json.dumps(manifest_data.get("feature_definitions", {}), sort_keys=True)
        param_json = json.dumps(manifest_data.get("parameter_definitions", {}), sort_keys=True)
        fee_json = json.dumps(manifest_data.get("fee_assumptions", {}), sort_keys=True)
        slip_json = json.dumps(manifest_data.get("slippage_assumptions", {}), sort_keys=True)
        fund_json = json.dumps(manifest_data["funding_assumptions"], sort_keys=True) if manifest_data.get("funding_assumptions") else None
        test_json = json.dumps(manifest_data.get("statistical_test_identifiers", []), sort_keys=True)
        seed_json = json.dumps(manifest_data.get("random_seeds", {}), sort_keys=True)
        metrics_json = json.dumps(manifest_data.get("metrics_summary", {}), sort_keys=True)

        cur.execute(
            """
            INSERT INTO experiment_evidence_manifests (
                manifest_id, experiment_id, hypothesis_id, prereg_id,
                session_id, ticket_id, request_id, trial_id,
                strategy_name, strategy_version, strategy_digest,
                provenance_type, dataset_id, partition_name,
                dataset_fingerprint, code_revision, feature_definitions_json,
                parameter_definitions_json, execution_engine_version,
                fee_assumptions_json, slippage_assumptions_json,
                funding_assumptions_json, benchmark_id,
                statistical_test_identifiers_json, random_seeds_json,
                start_timestamp_utc, completion_timestamp_utc,
                input_artifact_hash, output_artifact_hash,
                result_status, failure_or_interruption_status,
                metrics_summary_json, manifest_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                manifest_id,
                manifest_data["experiment_id"],
                manifest_data["hypothesis_id"],
                manifest_data["prereg_id"],
                manifest_data.get("session_id"),
                manifest_data.get("ticket_id"),
                manifest_data.get("request_id"),
                manifest_data.get("trial_id"),
                manifest_data["strategy_name"],
                manifest_data["strategy_version"],
                manifest_data["strategy_digest"],
                manifest_data.get("provenance_type", "PROGRAMMATIC_GENERATION"),
                manifest_data["dataset_id"],
                manifest_data["partition_name"],
                manifest_data["dataset_fingerprint"],
                manifest_data["code_revision"],
                feat_json,
                param_json,
                manifest_data.get("execution_engine_version", "2.6.0"),
                fee_json,
                slip_json,
                fund_json,
                manifest_data.get("benchmark_id", "BTC_NET"),
                test_json,
                seed_json,
                manifest_data["start_timestamp_utc"],
                manifest_data["completion_timestamp_utc"],
                manifest_data["input_artifact_hash"],
                manifest_data["output_artifact_hash"],
                manifest_data["result_status"],
                manifest_data.get("failure_or_interruption_status"),
                metrics_json,
                manifest_data["manifest_hash"],
                manifest_data["created_at"],
            ),
        )
        self.conn.commit()
        return manifest_id

    def get_evidence_manifest(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an experiment evidence manifest by experiment_id."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT manifest_id, experiment_id, hypothesis_id, prereg_id,
                   session_id, ticket_id, request_id, trial_id,
                   strategy_name, strategy_version, strategy_digest,
                   provenance_type, dataset_id, partition_name,
                   dataset_fingerprint, code_revision, feature_definitions_json,
                   parameter_definitions_json, execution_engine_version,
                   fee_assumptions_json, slippage_assumptions_json,
                   funding_assumptions_json, benchmark_id,
                   statistical_test_identifiers_json, random_seeds_json,
                   start_timestamp_utc, completion_timestamp_utc,
                   input_artifact_hash, output_artifact_hash,
                   result_status, failure_or_interruption_status,
                   metrics_summary_json, manifest_hash, created_at
            FROM experiment_evidence_manifests
            WHERE experiment_id = ?;
            """,
            (experiment_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "manifest_id": row[0],
            "experiment_id": row[1],
            "hypothesis_id": row[2],
            "prereg_id": row[3],
            "session_id": row[4],
            "ticket_id": row[5],
            "request_id": row[6],
            "trial_id": row[7],
            "strategy_name": row[8],
            "strategy_version": row[9],
            "strategy_digest": row[10],
            "provenance_type": row[11],
            "dataset_id": row[12],
            "partition_name": row[13],
            "dataset_fingerprint": row[14],
            "code_revision": row[15],
            "feature_definitions": json.loads(row[16]),
            "parameter_definitions": json.loads(row[17]),
            "execution_engine_version": row[18],
            "fee_assumptions": json.loads(row[19]),
            "slippage_assumptions": json.loads(row[20]),
            "funding_assumptions": json.loads(row[21]) if row[21] else None,
            "benchmark_id": row[22],
            "statistical_test_identifiers": json.loads(row[23]),
            "random_seeds": json.loads(row[24]),
            "start_timestamp_utc": row[25],
            "completion_timestamp_utc": row[26],
            "input_artifact_hash": row[27],
            "output_artifact_hash": row[28],
            "result_status": row[29],
            "failure_or_interruption_status": row[30],
            "metrics_summary": json.loads(row[31]),
            "manifest_hash": row[32],
            "created_at": row[33],
        }

    def list_evidence_manifests(self, hypothesis_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all experiment evidence manifests, optionally filtered by hypothesis_id."""
        cur = self.conn.cursor()
        if hypothesis_id:
            cur.execute(
                """
                SELECT manifest_id, experiment_id, hypothesis_id, prereg_id,
                       strategy_name, strategy_version, strategy_digest,
                       provenance_type, dataset_id, partition_name,
                       result_status, manifest_hash, created_at
                FROM experiment_evidence_manifests
                WHERE hypothesis_id = ?
                ORDER BY created_at ASC;
                """,
                (hypothesis_id,),
            )
        else:
            cur.execute(
                """
                SELECT manifest_id, experiment_id, hypothesis_id, prereg_id,
                       strategy_name, strategy_version, strategy_digest,
                       provenance_type, dataset_id, partition_name,
                       result_status, manifest_hash, created_at
                FROM experiment_evidence_manifests
                ORDER BY created_at ASC;
                """
            )
        rows = cur.fetchall()
        return [
            {
                "manifest_id": r[0],
                "experiment_id": r[1],
                "hypothesis_id": r[2],
                "prereg_id": r[3],
                "strategy_name": r[4],
                "strategy_version": r[5],
                "strategy_digest": r[6],
                "provenance_type": r[7],
                "dataset_id": r[8],
                "partition_name": r[9],
                "result_status": r[10],
                "manifest_hash": r[11],
                "created_at": r[12],
            }
            for r in rows
        ]

    def record_reproducibility_verification(self, verification_data: Dict[str, Any]) -> str:
        """Record an independent reproducibility verification result."""
        cur = self.conn.cursor()
        v_id = verification_data["verification_id"]
        tol_json = json.dumps(verification_data.get("tolerances", {}), sort_keys=True)
        disc_json = json.dumps(verification_data.get("metric_discrepancies", []), sort_keys=True)
        repro_metrics_json = json.dumps(verification_data.get("reproduced_metrics", {}), sort_keys=True)

        cur.execute(
            """
            INSERT INTO reproducibility_verifications (
                verification_id, manifest_id, experiment_id, verifier_identity,
                verification_timestamp_utc, verdict, tolerances_json,
                discrepancies_json, reproduced_metrics_json,
                reproduced_output_hash, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                v_id,
                verification_data["manifest_id"],
                verification_data["experiment_id"],
                verification_data.get("verifier_identity", "ReproducibilityEngine"),
                verification_data["verification_timestamp_utc"],
                verification_data["verdict"],
                tol_json,
                disc_json,
                repro_metrics_json,
                verification_data["reproduced_output_hash"],
                verification_data.get("audit_notes", ""),
            ),
        )
        self.conn.commit()
        return v_id

    def get_reproducibility_verifications(self, manifest_id: str) -> List[Dict[str, Any]]:
        """Retrieve all reproducibility verification records for a manifest."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT verification_id, manifest_id, experiment_id, verifier_identity,
                   verification_timestamp_utc, verdict, tolerances_json,
                   discrepancies_json, reproduced_metrics_json,
                   reproduced_output_hash, notes
            FROM reproducibility_verifications
            WHERE manifest_id = ?
            ORDER BY verification_timestamp_utc ASC;
            """,
            (manifest_id,),
        )
        rows = cur.fetchall()
        return [
            {
                "verification_id": r[0],
                "manifest_id": r[1],
                "experiment_id": r[2],
                "verifier_identity": r[3],
                "verification_timestamp_utc": r[4],
                "verdict": r[5],
                "tolerances": json.loads(r[6]),
                "metric_discrepancies": json.loads(r[7]),
                "reproduced_metrics": json.loads(r[8]),
                "reproduced_output_hash": r[9],
                "notes": r[10],
            }
            for r in rows
        ]


    def record_temporal_integrity_audit(self, audit_report_data: Dict[str, Any]) -> str:
        """Persist an immutable deterministic temporal integrity audit report in SQLite."""
        cur = self.conn.cursor()
        audit_id = audit_report_data["audit_id"]

        # Check if already persisted (idempotent return)
        cur.execute("SELECT audit_id FROM temporal_integrity_audits WHERE audit_id = ?;", (audit_id,))
        if cur.fetchone():
            return audit_id

        findings_json = json.dumps(
            audit_report_data.get("findings", []),
            sort_keys=True,
            default=str,
        )

        disp_raw = audit_report_data["disposition"]
        if hasattr(disp_raw, "value"):
            disp_str = str(disp_raw.value)
        elif isinstance(disp_raw, str) and "." in disp_raw:
            disp_str = disp_raw.split(".")[-1]
        else:
            disp_str = str(disp_raw)

        cur.execute(
            """
            INSERT INTO temporal_integrity_audits (
                audit_id, hypothesis_id, disposition, has_definitive_violation,
                total_findings, detector_version, input_digest, findings_json,
                disposition_rationale, audited_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                audit_id,
                audit_report_data.get("hypothesis_id") or "UNKNOWN",
                disp_str,
                1 if audit_report_data.get("has_definitive_violation") else 0,
                audit_report_data.get("total_findings", len(audit_report_data.get("findings", []))),
                audit_report_data.get("detector_version", "1.0.0"),
                audit_report_data.get("input_digest", "UNKNOWN"),
                findings_json,
                audit_report_data.get("disposition_rationale", ""),
                audit_report_data.get("timestamp_utc", datetime.datetime.now(datetime.timezone.utc).isoformat()),
            ),
        )
        self.conn.commit()
        return audit_id

    def get_temporal_integrity_audit(self, hypothesis_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve latest temporal integrity audit record for a hypothesis."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT audit_id, hypothesis_id, disposition, has_definitive_violation,
                   total_findings, detector_version, input_digest, findings_json,
                   disposition_rationale, audited_at
            FROM temporal_integrity_audits
            WHERE hypothesis_id = ?
            ORDER BY audited_at DESC LIMIT 1;
            """,
            (hypothesis_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "audit_id": row[0],
            "hypothesis_id": row[1],
            "disposition": row[2],
            "has_definitive_violation": bool(row[3]),
            "total_findings": row[4],
            "detector_version": row[5],
            "input_digest": row[6],
            "findings": json.loads(row[7]),
            "disposition_rationale": row[8],
            "audited_at": row[9],
        }
