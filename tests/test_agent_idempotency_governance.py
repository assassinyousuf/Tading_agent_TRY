"""Unit tests for Phase 2 Idempotency, Budget Gating, and Schema v3 Governance."""

import os
import unittest
import sqlite3

from trader.agent.core.execution import (
    ControlledExperimentRequest,
    ResearchExecutionEngine,
    IdempotencyPayloadConflictError,
    InterruptedTrialRetryError,
    ConcurrentExecutionConflictError,
    TrialBudgetExceededError,
)
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.tools.market_bridge import MarketDataProvider


class TestIdempotencyAndBudgetGovernance(unittest.TestCase):
    """Test idempotency tracking, budget enforcement, and TRAIN partition isolation."""

    def setUp(self) -> None:
        self.ledger = ResearchLedger(db_path=":memory:")
        self.provider = MarketDataProvider(ledger=self.ledger)
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)
        self.strategy = SyntheticSMAStrategy()

        # Record standard test hypothesis with budget of 2 trials
        self.hypothesis_id = "H-IDEMP-01"
        self.ledger.record_hypothesis(
            hypothesis_id=self.hypothesis_id,
            strategy_name=self.strategy.name,
            economic_rationale="Test hypothesis for idempotency and budget governance.",
            validation_criteria={"min_sharpe": 0.5},
            max_evaluation_budget=2,
        )

        # Preregister frozen experiment grid with 2 points
        self.prereg_id = self.ledger.preregister_experiment(
            hypothesis_id=self.hypothesis_id,
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0, 15.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.001, "slippage_rate": 0.0005},
            partition_protocol={"protocol_id": "CHRONOLOGICAL_V1"},
            benchmark_ids=["BTC_NET"],
        )

    def tearDown(self) -> None:
        self.ledger.close()

    def test_idempotent_duplicate_request_returns_cached_result(self) -> None:
        """Verify identical request_id and payload returns cached result without incrementing M_total."""
        counts_before = self.ledger.get_trial_counts()

        req = ControlledExperimentRequest(
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            strategy=self.strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            request_id="REQ-TEST-001",
        )

        # Initial execution
        res1 = self.engine.execute(req)
        self.assertEqual(res1.status, "SUCCESS")
        m_after_first = self.ledger.get_trial_counts()["M_total"]
        self.assertEqual(m_after_first, counts_before["M_total"] + 1)

        # Second execution with exact same request_id and payload
        res2 = self.engine.execute(req)
        self.assertEqual(res2.status, "SUCCESS")
        self.assertEqual(res2.experiment_id, res1.experiment_id)
        self.assertEqual(res2.trial_id, res1.trial_id)

        # M_total must NOT have incremented on duplicate
        m_after_second = self.ledger.get_trial_counts()["M_total"]
        self.assertEqual(m_after_second, m_after_first)

    def test_conflicting_payload_with_same_request_id_raises_error(self) -> None:
        """Verify reusing request_id with differing parameters raises IdempotencyPayloadConflictError."""
        req1 = ControlledExperimentRequest(
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            strategy=self.strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            request_id="REQ-TEST-CONFLICT",
        )
        self.engine.execute(req1)

        req2 = ControlledExperimentRequest(
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            strategy=self.strategy,
            parameters={"fast_period": 15.0, "slow_period": 30.0},  # Different parameter!
            partition="TRAIN",
            request_id="REQ-TEST-CONFLICT",  # Same request_id!
        )
        with self.assertRaises(IdempotencyPayloadConflictError):
            self.engine.execute(req2)

    def test_interrupted_trial_cannot_be_resumed_under_same_request_id(self) -> None:
        """Verify interrupted request requires a fresh request_id for a rerun."""
        # Manually simulate an interrupted execution request
        self.ledger.record_execution_request(
            request_id="REQ-INTERRUPTED-01",
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            payload_hash="dummy_hash",
            status="INTERRUPTED",
        )

        # Attempt to run with same request_id
        # We compute matching payload hash to trigger the status check
        from trader.agent.core.execution import canonical_parameter_json
        import hashlib
        p_str = f"{self.prereg_id}:{canonical_parameter_json({'fast_period': 10.0, 'slow_period': 30.0})}:TRAIN:BTCUSDT"
        p_hash = hashlib.sha256(p_str.encode("utf-8")).hexdigest()

        cur = self.ledger.conn.cursor()
        cur.execute("UPDATE execution_requests SET payload_hash = ? WHERE request_id = 'REQ-INTERRUPTED-01';", (p_hash,))
        self.ledger.conn.commit()

        req = ControlledExperimentRequest(
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            strategy=self.strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            request_id="REQ-INTERRUPTED-01",
        )
        with self.assertRaises(InterruptedTrialRetryError):
            self.engine.execute(req)

    def test_budget_exhaustion_blocks_without_inflating_m_total(self) -> None:
        """Verify budget exhaustion raises TrialBudgetExceededError without adding to trial_ledger."""
        # Budget is 2 trials. Run first trial:
        req1 = ControlledExperimentRequest(
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            strategy=self.strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            request_id="REQ-BUDGET-1",
        )
        self.engine.execute(req1)

        # Run second trial (budget limit reached: 2/2)
        req2 = ControlledExperimentRequest(
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            strategy=self.strategy,
            parameters={"fast_period": 15.0, "slow_period": 30.0},
            partition="TRAIN",
            request_id="REQ-BUDGET-2",
        )
        self.engine.execute(req2)

        counts_at_budget = self.ledger.get_trial_counts()
        self.assertEqual(counts_at_budget["M_total"], 2)

        # Attempt third trial (exceeds budget of 2)
        req3 = ControlledExperimentRequest(
            hypothesis_id=self.hypothesis_id,
            prereg_id=self.prereg_id,
            strategy=self.strategy,
            parameters={"fast_period": 10.0, "slow_period": 30.0},
            partition="TRAIN",
            request_id="REQ-BUDGET-3",
        )
        with self.assertRaises(TrialBudgetExceededError):
            self.engine.execute(req3)

        # CRITICAL TEST (F-03): Repeated budget denials must NOT increment M_total
        counts_after_denial = self.ledger.get_trial_counts()
        self.assertEqual(counts_after_denial["M_total"], 2)

        # Try a fourth time to ensure no loop occurs
        with self.assertRaises(TrialBudgetExceededError):
            self.engine.execute(req3)
        self.assertEqual(self.ledger.get_trial_counts()["M_total"], 2)

    def test_vertical_slice_train_only_enforcement(self) -> None:
        """Verify vertical slice mode strictly blocks non-TRAIN partition queries."""
        os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"
        try:
            req_val = ControlledExperimentRequest(
                hypothesis_id=self.hypothesis_id,
                prereg_id=self.prereg_id,
                strategy=self.strategy,
                parameters={"fast_period": 10.0, "slow_period": 30.0},
                partition="VALIDATION",
                request_id="REQ-VAL-BLOCKED",
            )
            with self.assertRaises(PermissionError) as ctx:
                self.engine.execute(req_val)
            self.assertIn("Vertical slice is strictly restricted to TRAIN partition", str(ctx.exception))
        finally:
            os.environ.pop("PHASE2_VERTICAL_SLICE_TRAIN_ONLY", None)


if __name__ == "__main__":
    unittest.main()

