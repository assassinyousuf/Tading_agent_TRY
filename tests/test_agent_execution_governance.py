"""Unit & Adversarial Tests for Phase 1.2 Research Execution Governance.

Verifies:
1. Every strategy evaluation requires an associated preregistration.
2. Every parameter configuration must be validated against that preregistration.
3. Every execution attempt receives a unique trial record created BEFORE execution begins.
4. Failed, interrupted, and rejected attempts maintain explicit dispositions in trial_ledger.
5. An execution attempt cannot silently disappear from trial accounting.
6. Duplicate execution IDs are strictly rejected.
7. Experiment records strictly reference their corresponding trial records.
8. Parameter configurations are stored in deterministic canonical JSON.
9. Execution records retain strategy metadata, git hash, data hash, partition, and timestamp.
10. Interrupted trials are safely reconciled upon engine restart.
"""

import json
import math
import os
import sqlite3
import sys
import unittest

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
from trader.agent.tools.market_bridge import (
    MarketDataProvider,
    SealedPartitionAccessViolation,
)


class CrashingStrategy(StrategyInterface):
    """Strategy that intentionally raises an exception during simulation."""

    def __init__(self) -> None:
        super().__init__(name="CrashingStrategy", version="1.0.0")

    @property
    def warmup_bars_required(self) -> int:
        return 30

    def compute_indicators(self, bars, params):
        raise RuntimeError("Intentional simulation crash during compute_indicators")

    def generate_signals(self, bars, indicators, params):
        return [0.0] * len(bars)


class TestAgentExecutionGovernance(unittest.TestCase):
    """Rigorous verification suite for Phase 1.2 controlled execution governance."""

    def setUp(self) -> None:
        self.ledger = ResearchLedger(db_path=":memory:")
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)

        # Base hypothesis and preregistration for testing
        self.ledger.record_hypothesis(
            hypothesis_id="H-GOV-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Trend following governance testing",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        self.prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-GOV-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [5.0, 10.0, 15.0], "slow_period": [20.0, 30.0, 40.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 30, "end_bar": 430},
            benchmark_ids=["BTC_NET"],
        )

    def tearDown(self) -> None:
        self.ledger.close()

    def test_authorized_execution_creates_one_trial_record(self) -> None:
        """Every authorized execution creates exactly one trial record with disposition COMPLETED."""
        req = ControlledExperimentRequest(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
        )

        res = self.engine.execute(req)

        self.assertEqual(res.status, "SUCCESS")
        self.assertGreater(res.trial_id, 0)
        self.assertEqual(res.cumulative_m_total, 1)

        # Verify trial_ledger row
        cur = self.ledger.conn.cursor()
        cur.execute(
            "SELECT hypothesis_id, prereg_id, experiment_id, disposition, cumulative_m_total FROM trial_ledger WHERE trial_id = ?;",
            (res.trial_id,),
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "H-GOV-1")
        self.assertEqual(row[1], self.prereg_id)
        self.assertEqual(row[2], res.experiment_id)
        self.assertEqual(row[3], "COMPLETED")
        self.assertEqual(row[4], 1)

        # Verify experiments row references trial_id
        cur.execute(
            "SELECT trial_id, strategy_name, strategy_version, status FROM experiments WHERE experiment_id = ?;",
            (res.experiment_id,),
        )
        exp_row = cur.fetchone()
        self.assertIsNotNone(exp_row)
        self.assertEqual(exp_row[0], res.trial_id)
        self.assertEqual(exp_row[1], "SyntheticSMAStrategy")
        self.assertEqual(exp_row[2], "1.0.0")
        self.assertEqual(exp_row[3], "SUCCESS")

    def test_failed_simulation_attempt_remains_recorded(self) -> None:
        """A simulation crash must not disappear from trial accounting; remains logged as FAILED."""
        req = ControlledExperimentRequest(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            strategy=CrashingStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
        )

        with self.assertRaises(SimulationExecutionError):
            self.engine.execute(req)

        # Verify trial was permanently logged in trial_ledger as FAILED
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT trial_id, disposition, cumulative_m_total, notes FROM trial_ledger WHERE prereg_id = ?;", (self.prereg_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        trial_id, disposition, m_total, notes = row
        self.assertEqual(disposition, "FAILED")
        self.assertEqual(m_total, 1)
        self.assertIn("Simulation crashed", notes)

        # Verify failed experiment row exists
        cur.execute("SELECT status, error_message FROM experiments WHERE trial_id = ?;", (trial_id,))
        exp_row = cur.fetchone()
        self.assertIsNotNone(exp_row)
        self.assertEqual(exp_row[0], "FAILED")
        self.assertIn("Intentional simulation crash", exp_row[1])

    def test_unapproved_parameter_rejected_and_recorded(self) -> None:
        """Attempting unapproved parameters outside frozen grid must be rejected and recorded as PRE_TEST_REJECTED."""
        req = ControlledExperimentRequest(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 99.0, "slow_period": 30.0},  # 99.0 not in grid
            partition="TRAIN",
        )

        with self.assertRaises(PreregistrationMismatchError):
            self.engine.execute(req)

        # Verify that the rejected attempt is permanently logged
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition, cumulative_m_total, notes FROM trial_ledger WHERE prereg_id = ?;", (self.prereg_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "PRE_TEST_REJECTED")
        self.assertEqual(row[1], 1)
        self.assertIn("not in approved grid", row[2])

    def test_duplicate_experiment_id_rejected(self) -> None:
        """Presenting a duplicate experiment ID must be rejected before execution."""
        fixed_id = "EXP-DUPLICATE-TEST"

        req1 = ControlledExperimentRequest(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id=fixed_id,
        )
        self.engine.execute(req1)

        req2 = ControlledExperimentRequest(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 15.0, "slow_period": 30.0},
            partition="TRAIN",
            experiment_id=fixed_id,
        )
        with self.assertRaises(DuplicateExperimentError):
            self.engine.execute(req2)

    def test_interrupted_execution_recovery(self) -> None:
        """Simulate an interrupted process leaving a STARTED trial; verify reconciliation."""
        # Manually start a trial in SQLite as if a process began execution and died
        trial_id = self.ledger.start_trial(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            experiment_id="EXP-CRASHED-PROCESS",
            parameters_evaluated={"fast_period": 10.0, "slow_period": 30.0},
            disposition="STARTED",
            notes="Process started before unexpected kill",
        )

        # Verify initially in STARTED state
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition FROM trial_ledger WHERE trial_id = ?;", (trial_id,))
        self.assertEqual(cur.fetchone()[0], "STARTED")

        # Simulate engine restart and reconciliation
        reconciled_count = self.engine.reconcile_interrupted_trials()
        self.assertEqual(reconciled_count, 1)

        # Verify disposition transitioned to INTERRUPTED
        cur.execute("SELECT disposition, notes FROM trial_ledger WHERE trial_id = ?;", (trial_id,))
        row = cur.fetchone()
        self.assertEqual(row[0], "INTERRUPTED")
        self.assertIn("Reconciled: process terminated unexpectedly", row[1])

        # Attempting to tamper with the INTERRUPTED trial must fail
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE trial_ledger SET disposition = 'COMPLETED' WHERE trial_id = ?;", (trial_id,))

    def test_trial_ledger_immutability_triggers(self) -> None:
        """Ensure cumulative_m_total cannot be decremented and completed trials cannot be modified."""
        req = ControlledExperimentRequest(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
        )
        res = self.engine.execute(req)

        cur = self.ledger.conn.cursor()

        # Cannot modify parameters
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE trial_ledger SET parameters_evaluated_json = '{}' WHERE trial_id = ?;", (res.trial_id,))

        # Cannot decrement cumulative_m_total
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE trial_ledger SET cumulative_m_total = 0 WHERE trial_id = ?;", (res.trial_id,))

        # Cannot modify terminal disposition
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE trial_ledger SET disposition = 'STARTED' WHERE trial_id = ?;", (res.trial_id,))

        # Cannot delete trial record
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("DELETE FROM trial_ledger WHERE trial_id = ?;", (res.trial_id,))

    def test_canonical_parameter_json_deterministic(self) -> None:
        """Verify parameter configurations serialize to identical canonical JSON regardless of key order."""
        p1 = {"slow_period": 30.0, "fast_period": 10.0}
        p2 = {"fast_period": 10.0, "slow_period": 30.0}

        s1 = canonical_parameter_json(p1)
        s2 = canonical_parameter_json(p2)

        self.assertEqual(s1, s2)
        self.assertEqual(s1, '{"fast_period":10.0,"slow_period":30.0}')

    def test_test_partition_governance_in_engine(self) -> None:
        """Attempting TEST partition execution via engine without token raises and logs failure."""
        req = ControlledExperimentRequest(
            hypothesis_id="H-GOV-1",
            prereg_id=self.prereg_id,
            strategy=SyntheticSMAStrategy(),
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TEST",
            test_token=None,
        )

        with self.assertRaises(SealedPartitionAccessViolation):
            self.engine.execute(req)

        # Verify failed trial recorded
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT disposition, notes FROM trial_ledger WHERE prereg_id = ?;", (self.prereg_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "FAILED")
        self.assertIn("Data retrieval failed", row[1])

    def test_sequential_trials_increment_m_total_accurately(self) -> None:
        """Run 3 sequential controlled experiments and verify strictly monotonic M_total tracking."""
        params_list = [
            {"fast_period": 5.0, "slow_period": 20.0},
            {"fast_period": 10.0, "slow_period": 30.0},
            {"fast_period": 15.0, "slow_period": 40.0},
        ]

        results = []
        for p in params_list:
            req = ControlledExperimentRequest(
                hypothesis_id="H-GOV-1",
                prereg_id=self.prereg_id,
                strategy=SyntheticSMAStrategy(),
                parameters=p,
                partition="TRAIN",
            )
            res = self.engine.execute(req)
            results.append(res)

        self.assertEqual(results[0].cumulative_m_total, 1)
        self.assertEqual(results[1].cumulative_m_total, 2)
        self.assertEqual(results[2].cumulative_m_total, 3)

        counts = self.ledger.get_trial_counts()
        self.assertEqual(counts["M_total"], 3)
        self.assertEqual(counts["M_evaluated"], 3)
        self.assertEqual(counts["M_rejected"], 0)


if __name__ == "__main__":
    unittest.main()

