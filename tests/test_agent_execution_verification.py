"""Phase 1.3: Independent Adversarial Verification of Execution Governance.

Verifies:
1. Subprocess-level abrupt crash recovery (os._exit) and trial reconciliation.
2. Exactly-once trial accounting across duplicate IDs, commit failures, and restarts.
3. Statistical record integrity: separation of failed/interrupted trials from valid metrics.
4. Execution boundary demarcation and documentation of Python/DB/filesystem bypass routes.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ControlledExperimentResult,
    DuplicateExperimentError,
    PreregistrationMismatchError,
    ResearchExecutionEngine,
    SimulationExecutionError,
    canonical_parameter_json,
)
from trader.agent.core.strategy_base import StrategyInterface
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.db.schema import create_schema
from trader.agent.tools.market_bridge import (
    MarketDataProvider,
    execute_strategy_simulation,
)


class TestAgentExecutionVerification(unittest.TestCase):
    """Adversarial verification of research execution governance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "research_ledger.db")
        self.ledger = ResearchLedger(db_path=self.db_path)
        self.conn = self.ledger.conn
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(self.ledger, self.provider)

        # Register standard hypothesis and preregistration
        self.hypo_id = "H-VERIFY-01"
        self.ledger.record_hypothesis(
            hypothesis_id=self.hypo_id,
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Trend momentum verification",
            validation_criteria={"min_sharpe": 0.5},
        )
        self.prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=self.hypo_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [5.0, 10.0, 15.0], "slow_period": [20.0, 30.0, 40.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"train_ratio": 0.60, "val_ratio": 0.20, "test_ratio": 0.20},
            benchmark_ids=["BTC_NET"],
        )

    def tearDown(self) -> None:
        self.ledger.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    # =========================================================================
    # 1. PROCESS-LEVEL CRASH RECOVERY
    # =========================================================================

    def test_subprocess_abrupt_crash_and_reconciliation(self) -> None:
        """Verify that a process abruptly killed via os._exit is cleanly reconciled on restart."""
        # Close setup connection so child process can exclusively access DB on Windows if needed
        self.ledger.close()

        escaped_root = ROOT.replace("\\", "\\\\")
        escaped_db = self.db_path.replace("\\", "\\\\")

        child_script = f"""
import os
import sqlite3
import sys

sys.path.insert(0, r"{escaped_root}")
tm_dir = os.path.join(r"{escaped_root}", "trader-timemachine")
sys.path.insert(0, tm_dir)

from trader.agent.core.execution import ControlledExperimentRequest, ResearchExecutionEngine
from trader.agent.core.strategy_base import StrategyInterface
from trader.agent.db.ledger import ResearchLedger
from trader.agent.tools.market_bridge import MarketDataProvider

class DirectExitStrategy(StrategyInterface):
    def __init__(self) -> None:
        super().__init__(name="DirectExit", version="1.0.0")
    @property
    def warmup_bars_required(self): return 10
    def compute_indicators(self, bars, params): return {{}}
    def generate_signals(self, bars, indicators, params):
        # Abrupt process termination: skips atexit, finally blocks, and exception handlers
        os._exit(99)

ledger = ResearchLedger(db_path=r"{escaped_db}")
provider = MarketDataProvider(ledger=ledger)
engine = ResearchExecutionEngine(ledger, provider)

req = ControlledExperimentRequest(
    hypothesis_id="{self.hypo_id}",
    prereg_id="{self.prereg_id}",
    strategy=DirectExitStrategy(),
    parameters={{"fast_period": 10.0, "slow_period": 30.0}},
    partition="TRAIN",
    symbol="BTCUSDT",
    experiment_id="EXP-CRASH-001",
)

engine.execute(req)
"""
        # Execute child process
        proc = subprocess.run(
            [sys.executable, "-c", child_script],
            capture_output=True,
            text=True,
            timeout=15,
        )

        # Process must have exited with code 99 (abrupt termination)
        self.assertEqual(proc.returncode, 99)

        # Inspect database before initializing fresh engine
        raw_conn = sqlite3.connect(self.db_path)
        cur = raw_conn.cursor()
        cur.execute(
            "SELECT trial_id, disposition, cumulative_m_total, hypothesis_id, prereg_id FROM trial_ledger WHERE experiment_id = 'EXP-CRASH-001';"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        trial_id, disp, m_total_before, h_id, p_id = row

        # Pre-execution trial registration guarantees STARTED state was committed to disk
        self.assertEqual(disp, "STARTED")
        self.assertEqual(m_total_before, 1)
        self.assertEqual(h_id, self.hypo_id)
        self.assertEqual(p_id, self.prereg_id)
        raw_conn.close()

        # Reopen database in fresh environment and initialize engine
        reopen_ledger = ResearchLedger(db_path=self.db_path)
        reopen_conn = reopen_ledger.conn
        reopen_provider = MarketDataProvider(ledger=reopen_ledger)

        # Engine initialization must run reconcile_interrupted_trials()
        reopen_engine = ResearchExecutionEngine(reopen_ledger, reopen_provider)

        # Verify trial has transitioned to INTERRUPTED
        cur2 = reopen_conn.cursor()
        cur2.execute(
            "SELECT disposition, notes, cumulative_m_total, hypothesis_id, prereg_id FROM trial_ledger WHERE trial_id = ?;",
            (trial_id,),
        )
        row2 = cur2.fetchone()
        self.assertIsNotNone(row2)
        disp_after, notes_after, m_total_after, h_id_after, p_id_after = row2

        self.assertEqual(disp_after, "INTERRUPTED")
        self.assertIn("Reconciled", notes_after)
        # M_total MUST NOT have been incremented again
        self.assertEqual(m_total_after, 1)
        self.assertEqual(h_id_after, self.hypo_id)
        self.assertEqual(p_id_after, self.prereg_id)

        # Confirm global M_total remains 1
        counts = reopen_ledger.get_trial_counts()
        self.assertEqual(counts["M_total"], 1)
        self.assertEqual(counts["M_evaluated"], 0)

        # Reassign self.ledger so tearDown can close cleanly
        self.ledger = reopen_ledger
        self.conn = reopen_conn

    # =========================================================================
    # 2. EXACTLY-ONCE TRIAL ACCOUNTING
    # =========================================================================

    def test_duplicate_trial_id_and_tampering_rejection(self) -> None:
        """Adversarially attempt duplicate trial_id insertion and trigger tampering."""
        cur = self.conn.cursor()
        # Create initial trial
        trial_id = self.ledger.start_trial(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            experiment_id="EXP-TRIAL-01",
            parameters_evaluated={"fast_period": 10.0, "slow_period": 30.0},
        )
        self.assertEqual(trial_id, 1)

        # Attempt to insert an explicit duplicate trial_id
        with self.assertRaises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO trial_ledger (trial_id, timestamp, hypothesis_id, prereg_id, experiment_id, parameters_evaluated_json, disposition, cumulative_m_total)
                VALUES (1, '2026-10-30T00:00:00', 'H-1', 'P-1', 'EXP-DUP', '{}', 'STARTED', 1);
                """
            )
            self.conn.commit()
        self.conn.rollback()

        # Attempt to update trial_id via trigger bypass
        with self.assertRaises(sqlite3.DatabaseError) as cm:
            cur.execute("UPDATE trial_ledger SET trial_id = 999 WHERE trial_id = 1;")
            self.conn.commit()
        self.assertIn("Security violation: Cannot modify trial_id", str(cm.exception))
        self.conn.rollback()

    def test_duplicate_experiment_id_rejected_without_unaccounted_trials(self) -> None:
        """Executing with an already recorded experiment_id is rejected with zero trial inflation."""
        strategy = SyntheticSMAStrategy()
        req1 = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-DUP-CHECK",
        )
        res1 = self.engine.execute(req1)
        self.assertEqual(res1.status, "SUCCESS")
        self.assertEqual(self.ledger.get_trial_counts()["M_total"], 1)

        # Submitting the duplicate experiment_id must raise DuplicateExperimentError
        req2 = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=strategy,
            parameters={"fast_period": 15.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-DUP-CHECK",
        )
        with self.assertRaises(DuplicateExperimentError):
            self.engine.execute(req2)

        # M_total must remain strictly 1 (no dangling or phantom trial started)
        self.assertEqual(self.ledger.get_trial_counts()["M_total"], 1)

    def test_retried_request_accounting_semantics(self) -> None:
        """Verify accounting semantics when retrying failed requests."""
        class FailingStrategy(StrategyInterface):
            def __init__(self) -> None:
                super().__init__(name="Failing", version="1.0.0")
            @property
            def warmup_bars_required(self): return 10
            def compute_indicators(self, bars, params): return {}
            def generate_signals(self, bars, indicators, params):
                raise ValueError("Simulated mathematical failure during signal calculation")

        req_fail = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=FailingStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-FAILED-001",
        )

        with self.assertRaises(SimulationExecutionError):
            self.engine.execute(req_fail)

        # Failed attempt increments M_total to 1
        counts1 = self.ledger.get_trial_counts()
        self.assertEqual(counts1["M_total"], 1)
        self.assertEqual(counts1["M_evaluated"], 0)

        # Immediate retry with SAME experiment_id is rejected
        with self.assertRaises(DuplicateExperimentError):
            self.engine.execute(req_fail)
        self.assertEqual(self.ledger.get_trial_counts()["M_total"], 1)

        # Retry with a clean strategy and a NEW experiment_id
        req_retry = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-RETRY-002",
        )
        res_retry = self.engine.execute(req_retry)
        self.assertEqual(res_retry.status, "SUCCESS")

        # Now M_total is 2, M_evaluated is 1
        counts2 = self.ledger.get_trial_counts()
        self.assertEqual(counts2["M_total"], 2)
        self.assertEqual(counts2["M_evaluated"], 1)

    def test_failure_during_experiment_recording(self) -> None:
        """Failure while recording experiment metrics updates trial to FAILED and preserves M_total."""
        strategy = SyntheticSMAStrategy()
        req = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-REC-FAIL",
        )

        with patch.object(self.ledger, "record_experiment_result", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(SimulationExecutionError) as cm:
                self.engine.execute(req)
            self.assertIn("Failed to record experiment result", str(cm.exception))

        # Trial is finalized as FAILED
        cur = self.conn.cursor()
        cur.execute("SELECT disposition, notes FROM trial_ledger WHERE experiment_id = 'EXP-REC-FAIL';")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "FAILED")
        self.assertIn("Experiment recording failed", row[1])

        # Exactly-once accounting: M_total is 1, M_evaluated is 0
        counts = self.ledger.get_trial_counts()
        self.assertEqual(counts["M_total"], 1)
        self.assertEqual(counts["M_evaluated"], 0)

    def test_failure_during_trial_finalization_reconciled_on_restart(self) -> None:
        """If finalize_trial fails during step 7, the dangling trial is reconciled on engine restart."""
        strategy = SyntheticSMAStrategy()
        req = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-FIN-FAIL",
        )

        with patch.object(self.ledger, "finalize_trial", side_effect=sqlite3.OperationalError("lock contention")):
            with self.assertRaises(SimulationExecutionError) as cm:
                self.engine.execute(req)
            self.assertIn("Failed to finalize trial", str(cm.exception))

        # In DB, the trial is left in STARTED state
        cur = self.conn.cursor()
        cur.execute("SELECT disposition FROM trial_ledger WHERE experiment_id = 'EXP-FIN-FAIL';")
        self.assertEqual(cur.fetchone()[0], "STARTED")

        # Restart engine: instantiate fresh engine on same ledger
        restarted_engine = ResearchExecutionEngine(self.ledger, self.provider)

        # Trial must now be reconciled to INTERRUPTED
        cur.execute("SELECT disposition, notes FROM trial_ledger WHERE experiment_id = 'EXP-FIN-FAIL';")
        row = cur.fetchone()
        self.assertEqual(row[0], "INTERRUPTED")
        self.assertIn("Reconciled", row[1])

        # Exactly-once accounting preserved
        self.assertEqual(self.ledger.get_trial_counts()["M_total"], 1)

    def test_pre_test_rejected_accounting_semantics(self) -> None:
        """Repeated unapproved submissions constitute distinct attempts against multiple-testing family size."""
        strategy = SyntheticSMAStrategy()

        # Submit 3 invalid configurations outside the preregistered grid
        invalid_configs = [
            {"fast_period": 999.0, "slow_period": 30.0},
            {"fast_period": 888.0, "slow_period": 30.0},
            {"fast_period": 777.0, "slow_period": 30.0},
        ]

        for cfg in invalid_configs:
            req = ControlledExperimentRequest(
                hypothesis_id=self.hypo_id,
                prereg_id=self.prereg_id,
                strategy=strategy,
                parameters=cfg,
                partition="TRAIN",
            )
            with self.assertRaises(PreregistrationMismatchError):
                self.engine.execute(req)

        # Verify trial ledger records each rejection
        cur = self.conn.cursor()
        cur.execute("SELECT trial_id, disposition, cumulative_m_total FROM trial_ledger ORDER BY trial_id ASC;")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 3)

        for i, r in enumerate(rows):
            self.assertEqual(r[1], "PRE_TEST_REJECTED")
            self.assertEqual(r[2], i + 1)

        counts = self.ledger.get_trial_counts()
        self.assertEqual(counts["M_total"], 3)
        self.assertEqual(counts["M_rejected"], 3)
        self.assertEqual(counts["M_evaluated"], 0)

    # =========================================================================
    # 3. STATISTICAL RECORD INTEGRITY
    # =========================================================================

    def test_statistical_record_integrity(self) -> None:
        """Verify that failed/interrupted trials cannot inject zero-valued metrics or corrupt DSR."""
        cur = self.conn.cursor()

        # Insert a FAILED experiment
        self.ledger.start_trial(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            experiment_id="EXP-INTEG-FAIL",
            parameters_evaluated={"fast_period": 10.0, "slow_period": 30.0},
        )
        self.ledger.finalize_trial(
            trial_id=1,
            disposition="FAILED",
            experiment_id="EXP-INTEG-FAIL",
        )
        self.ledger.record_failed_experiment(
            experiment_id="EXP-INTEG-FAIL",
            prereg_id=self.prereg_id,
            trial_id=1,
            partition_name="TRAIN",
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            error_message="Simulated error",
        )

        # Verify no entry exists in experiment_metrics for the failed experiment
        cur.execute("SELECT COUNT(*) FROM experiment_metrics WHERE experiment_id = 'EXP-INTEG-FAIL';")
        self.assertEqual(cur.fetchone()[0], 0)

        # Verify LEFT JOIN yields NULLs, preventing silent zero-value coercion
        cur.execute(
            """
            SELECT e.experiment_id, e.status, m.sharpe_ratio, m.net_total_return_pct
            FROM experiments e
            LEFT JOIN experiment_metrics m ON e.experiment_id = m.experiment_id
            WHERE e.experiment_id = 'EXP-INTEG-FAIL';
            """
        )
        row = cur.fetchone()
        self.assertEqual(row[0], "EXP-INTEG-FAIL")
        self.assertEqual(row[1], "FAILED")
        self.assertIsNone(row[2])  # sharpe_ratio is NULL, NOT 0.0
        self.assertIsNone(row[3])  # net_total_return is NULL, NOT 0.0

        # Run one successful experiment
        req_ok = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-INTEG-OK",
        )
        res_ok = self.engine.execute(req_ok)
        self.assertEqual(res_ok.status, "SUCCESS")

        # Verify successful experiment has valid non-null metrics
        cur.execute("SELECT sharpe_ratio FROM experiment_metrics WHERE experiment_id = 'EXP-INTEG-OK';")
        sharpe = cur.fetchone()[0]
        self.assertIsNotNone(sharpe)

        # Verify foreign key constraint linking completed experiment to trial
        cur.execute(
            """
            SELECT e.experiment_id, t.trial_id, t.disposition
            FROM experiments e
            JOIN trial_ledger t ON e.trial_id = t.trial_id
            WHERE e.experiment_id = 'EXP-INTEG-OK';
            """
        )
        link_row = cur.fetchone()
        self.assertEqual(link_row[0], "EXP-INTEG-OK")
        self.assertEqual(link_row[1], res_ok.trial_id)
        self.assertEqual(link_row[2], "COMPLETED")

        # Verify trial counts partition
        counts = self.ledger.get_trial_counts()
        self.assertEqual(counts["M_total"], 2)       # 1 failed + 1 ok
        self.assertEqual(counts["M_evaluated"], 1)   # only 1 completed
        self.assertEqual(counts["M_rejected"], 0)

    # =========================================================================
    # 4. EXECUTION BOUNDARY & BYPASS ROUTE AUDIT
    # =========================================================================

    def test_execution_boundary_and_direct_simulation_isolation(self) -> None:
        """Verify boundary between controlled ResearchExecutionEngine and low-level numerical kernel."""
        # 1. Controlled path enforces preregistration and trial ledger logging
        counts_before = self.ledger.get_trial_counts()
        req = ControlledExperimentRequest(
            hypothesis_id=self.hypo_id,
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id="EXP-BOUNDARY-01",
        )
        self.engine.execute(req)
        counts_after = self.ledger.get_trial_counts()
        self.assertEqual(counts_after["M_total"], counts_before["M_total"] + 1)

        # 2. Direct call to low-level execute_strategy_simulation kernel bypasses ledger
        # This confirms that future agent toolkits MUST NOT expose execute_strategy_simulation directly
        partition_data = self.provider.get_partition_data("TRAIN", warmup_bars=30)
        sim_res = execute_strategy_simulation(
            strategy=SyntheticSMAStrategy(),
            partition_data=partition_data,
            params={"fast_period": 10.0, "slow_period": 30.0},
        )
        self.assertIn("raw_metrics", sim_res)

        # Ledger was not incremented by direct numerical kernel call
        counts_after_direct = self.ledger.get_trial_counts()
        self.assertEqual(counts_after_direct["M_total"], counts_after["M_total"])


if __name__ == "__main__":
    unittest.main()
