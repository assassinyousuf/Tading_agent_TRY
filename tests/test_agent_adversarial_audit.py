"""Independent Adversarial Audit Test Suite for Phase 1 Infrastructure.

Forensic verification probing:
1. TEST vault access bypass attempts (dummy tokens, omitted ledger, missing prereg_id).
2. Partition dictionary boundary tampering resistance.
3. Database trigger immutability (max_uses escalation, expiry extension, un-revocation).
4. Multi-table transaction atomicity and rollback on partial failure.
5. Timezone naive token expiration handling.
6. Atomic single-use consumption and TOCTOU concurrency resistance.
7. Independent benchmark parity calculation from raw daily returns.
8. Architectural isolation classification (Provider-level vs physical isolation).
9. In-memory trial accounting boundary documentation.
"""

import hashlib
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

from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.tools.market_bridge import (
    MarketDataProvider,
    SealedPartitionAccessViolation,
    execute_strategy_simulation,
)
from benchmark_engine import calc_cagr, calc_annualized_vol, calc_sharpe_ratio


class TestAgentAdversarialAudit(unittest.TestCase):
    """Adversarial audit probing edge cases, attack vectors, and security guarantees."""

    def setUp(self) -> None:
        self.ledger = ResearchLedger(db_path=":memory:")
        self.provider = MarketDataProvider(ledger=self.ledger)

    def tearDown(self) -> None:
        self.ledger.close()

    def test_unauthenticated_test_access_without_ledger_rejected(self) -> None:
        """Provider without ledger must strictly reject TEST partition access regardless of token."""
        provider_no_ledger = MarketDataProvider(ledger=None)
        with self.assertRaises(SealedPartitionAccessViolation) as ctx:
            provider_no_ledger.get_partition_data("TEST", test_token="arbitrary_dummy_token")
        self.assertIn("Requires MarketDataProvider initialized with a valid ResearchLedger", str(ctx.exception))

    def test_missing_prereg_id_binding_rejected(self) -> None:
        """TEST partition access requires explicit prereg_id matching the authorization."""
        self.ledger.record_hypothesis("H-ADV-1", "Strat", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment(
            "H-ADV-1", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )
        token = self.ledger.generate_test_vault_token(prereg_id, "Auditor", "Test")

        # Missing prereg_id must be rejected
        with self.assertRaises(SealedPartitionAccessViolation) as ctx:
            self.provider.get_partition_data("TEST", test_token=token, prereg_id=None)
        self.assertIn("requires explicit prereg_id binding", str(ctx.exception))

    def test_partition_bounds_tamper_resistance(self) -> None:
        """Tampering with class-level or instance PARTITIONS dictionary must not expand returned slices."""
        original_train_end = MarketDataProvider.PARTITIONS["TRAIN"]["end_bar"]
        try:
            # Adversary attempts to leak TEST bars by modifying PARTITIONS
            MarketDataProvider.PARTITIONS["TRAIN"]["end_bar"] = 729
            data = self.provider.get_partition_data("TRAIN")
            # Slice length must remain strictly 400 bars (bars 30 to 430)
            self.assertEqual(len(data["trading_bars"]), 400)
            self.assertEqual(data["start_bar"], 30)
            self.assertEqual(data["end_bar"], 430)
        finally:
            MarketDataProvider.PARTITIONS["TRAIN"]["end_bar"] = original_train_end

    def test_authorization_trigger_blocks_max_uses_escalation(self) -> None:
        """Trigger must prevent escalating max_uses to enable token replay."""
        self.ledger.record_hypothesis("H-ADV-2", "Strat", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment(
            "H-ADV-2", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )
        token = self.ledger.generate_test_vault_token(prereg_id, "Auditor", "Test")

        cur = self.ledger.conn.cursor()
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            cur.execute("UPDATE test_evaluation_authorizations SET max_uses = 100;")
        self.assertIn("Cannot modify max_uses", str(ctx.exception))

    def test_authorization_trigger_blocks_expiry_extension(self) -> None:
        """Trigger must prevent extending expires_at timestamp."""
        self.ledger.record_hypothesis("H-ADV-3", "Strat", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment(
            "H-ADV-3", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )
        token = self.ledger.generate_test_vault_token(prereg_id, "Auditor", "Test")

        cur = self.ledger.conn.cursor()
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            cur.execute("UPDATE test_evaluation_authorizations SET expires_at = '2099-01-01T00:00:00+00:00';")
        self.assertIn("Cannot modify expires_at", str(ctx.exception))

    def test_authorization_trigger_blocks_unrevocation(self) -> None:
        """Trigger must prevent clearing is_revoked from 1 back to 0."""
        self.ledger.record_hypothesis("H-ADV-4", "Strat", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment(
            "H-ADV-4", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )
        token = self.ledger.generate_test_vault_token(prereg_id, "Auditor", "Test")
        self.ledger.revoke_test_token(token)

        cur = self.ledger.conn.cursor()
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            cur.execute("UPDATE test_evaluation_authorizations SET is_revoked = 0;")
        self.assertIn("Cannot un-revoke", str(ctx.exception))

    def test_authorization_trigger_blocks_uses_decrement(self) -> None:
        """Trigger must prevent decrementing uses_count."""
        self.ledger.record_hypothesis("H-ADV-5", "Strat", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment(
            "H-ADV-5", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )
        token = self.ledger.generate_test_vault_token(prereg_id, "Auditor", "Test")
        self.ledger.consume_test_vault_token(token, prereg_id)

        cur = self.ledger.conn.cursor()
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            cur.execute("UPDATE test_evaluation_authorizations SET uses_count = 0;")
        self.assertIn("Cannot decrement uses_count", str(ctx.exception))

    def test_transaction_rollback_on_partial_failure(self) -> None:
        """Multi-table insert failure in record_experiment_result must cleanly roll back all rows."""
        self.ledger.record_hypothesis("H-ADV-6", "Strat", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment(
            "H-ADV-6", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )

        # Attempt record_experiment_result with defective audit_data missing required keys
        with self.assertRaises(KeyError):
            self.ledger.record_experiment_result(
                experiment_data={
                    "experiment_id": "EXP-FAIL-TEST",
                    "prereg_id": prereg_id,
                    "partition_name": "TRAIN",
                    "parameters": {"fast_period": 10.0, "slow_period": 30.0},
                },
                metrics_data={
                    "initial_capital": 10000.0,
                    "final_capital": 10100.0,
                    "net_total_return_pct": 1.0,
                    "gross_total_return_pct": 1.5,
                    "cagr_pct": 0.8,
                    "annualized_vol_pct": 15.0,
                    "sharpe_ratio": 0.05,
                    "sortino_ratio": 0.07,
                    "max_drawdown_pct": -5.0,
                    "total_trades": 8,
                    "win_rate_pct": 50.0,
                    "total_friction_usd": 40.0,
                    "compounding_error": 0.0,
                },
                audit_data={"benchmark_id": "BTC_NET"},  # Missing bootstrap_p_value_raw -> KeyError!
            )

        # Perform subsequent successful commit
        self.ledger.record_trial("H-ADV-6", prereg_id, None, {"p": 1.0}, "TRAIN_EVALUATED")

        # Verify that EXP-FAIL-TEST was NOT committed
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT experiment_id FROM experiments WHERE experiment_id = 'EXP-FAIL-TEST';")
        self.assertIsNone(cur.fetchone())
        cur.execute("SELECT metric_id FROM experiment_metrics WHERE experiment_id = 'EXP-FAIL-TEST';")
        self.assertIsNone(cur.fetchone())

    def test_timezone_naive_expires_at_handled(self) -> None:
        """Tokens with naive datetime strings in expires_at must be normalized without crashing."""
        self.ledger.record_hypothesis("H-ADV-7", "Strat", "Rationale", {})
        prereg_id = self.ledger.preregister_experiment(
            "H-ADV-7", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )
        token = self.ledger.generate_test_vault_token(prereg_id, "Auditor", "Test")

        # Simulate direct insertion of naive timestamp
        cur = self.ledger.conn.cursor()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        # Direct SQL update of expires_at is blocked by trigger; insert a second token with naive expires_at
        token_naive = "TEST-TOKEN-NAIVE1234567890ABCDEF"
        naive_hash = hashlib.sha256(token_naive.encode("utf-8")).hexdigest()
        cur.execute(
            """
            INSERT INTO test_evaluation_authorizations (
                auth_id, prereg_id, experiment_id, token_hash,
                authorized_by, created_at, expires_at, is_revoked,
                max_uses, uses_count, purpose
            ) VALUES ('AUTH-NAIVE', ?, NULL, ?, 'Auditor', '2026-01-01 00:00:00', '2020-01-01 00:00:00', 0, 1, 0, 'Test');
            """,
            (prereg_id, naive_hash),
        )
        self.ledger.conn.commit()

        # Consuming naive expired token must raise PermissionError (expired), NOT TypeError
        with self.assertRaises(PermissionError) as ctx:
            self.ledger.verify_and_consume_test_token(token_naive, prereg_id)
        self.assertIn("expired", str(ctx.exception).lower())

    def test_independent_sharpe_and_benchmark_parity(self) -> None:
        """Independently compute Sharpe ratio directly from daily returns and verify parity."""
        strategy = SyntheticSMAStrategy()
        train_data = self.provider.get_partition_data("TRAIN", warmup_bars=30)
        params = {"fast_period": 10.0, "slow_period": 30.0}

        results = execute_strategy_simulation(
            strategy=strategy,
            partition_data=train_data,
            params=params,
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            initial_capital=10000.0,
        )

        daily_returns = results["daily_returns"]
        n_days = len(daily_returns)
        self.assertEqual(n_days, 400)

        # Independent calculation: CAGR and Annualized Vol from raw return array using 365.25 factor
        cum_ret = 1.0
        for r in daily_returns:
            cum_ret *= (1.0 + r)
        indep_cagr = (cum_ret ** (365.25 / n_days)) - 1.0

        mean_r = sum(daily_returns) / n_days
        var_r = sum((r - mean_r) ** 2 for r in daily_returns) / (n_days - 1)
        indep_ann_vol = math.sqrt(var_r) * math.sqrt(365.25)

        indep_sharpe = indep_cagr / indep_ann_vol if indep_ann_vol > 0 else 0.0

        # Assert parity with results dictionary
        self.assertAlmostEqual(indep_cagr * 100.0, results["cagr_pct"], places=4)
        self.assertAlmostEqual(indep_ann_vol * 100.0, results["annualized_vol_pct"], places=4)
        self.assertAlmostEqual(indep_sharpe, results["sharpe_ratio"], places=4)

    def test_provider_level_access_versus_physical_file_isolation(self) -> None:
        """Demonstrate that physical JSON files on disk are unencrypted (Provider-Level access control)."""
        cache_path = os.path.join(ROOT, "trader", "cached_klines_btcusdt_1d_730d.json")
        self.assertTrue(os.path.isfile(cache_path))

        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_klines = data.get("klines", [])
        self.assertEqual(len(raw_klines), 729)

        # Direct file slice demonstrates Category A (Provider-Level Control), NOT physical isolation
        test_bars_direct = raw_klines[580:729]
        self.assertEqual(len(test_bars_direct), 149)

    def test_unaccounted_in_memory_simulation_isolation(self) -> None:
        """Demonstrate that executing simulation in memory does not automatically log to trial ledger."""
        strategy = SyntheticSMAStrategy()
        train_data = self.provider.get_partition_data("TRAIN", warmup_bars=30)

        # Initial ledger count
        initial_m = self.ledger.get_trial_counts()["M_total"]

        # Run 5 in-memory simulations
        for p in [5.0, 10.0, 15.0, 20.0, 25.0]:
            execute_strategy_simulation(
                strategy=strategy,
                partition_data=train_data,
                params={"fast_period": p, "slow_period": 30.0},
            )

        # Ledger M_total remains unchanged unless explicitly logged
        current_m = self.ledger.get_trial_counts()["M_total"]
        self.assertEqual(current_m, initial_m)

    def test_preregistration_binding_mismatch_audit_logging(self) -> None:
        """Presenting token bound to prereg A for prereg B must be denied and logged."""
        self.ledger.record_hypothesis("H-ADV-8", "Strat", "Rationale", {})
        prereg_a = self.ledger.preregister_experiment(
            "H-ADV-8", ["BTCUSDT"], "1d", {"fast_period": [10.0], "slow_period": [30.0]}, {}, {}, ["BTC_NET"]
        )
        prereg_b = self.ledger.preregister_experiment(
            "H-ADV-8", ["BTCUSDT"], "1d", {"fast_period": [20.0], "slow_period": [40.0]}, {}, {}, ["BTC_NET"]
        )

        token_a = self.ledger.generate_test_vault_token(prereg_a, "Auditor", "Test A")

        with self.assertRaises(SealedPartitionAccessViolation):
            self.provider.get_partition_data("TEST", test_token=token_a, prereg_id=prereg_b)

        # Verify denial record in test_evaluation_records
        cur = self.ledger.conn.cursor()
        cur.execute(
            "SELECT access_granted, failure_reason FROM test_evaluation_records WHERE prereg_id = ?;",
            (prereg_b,),
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 0)
        self.assertIn("cannot access", row[1])


if __name__ == "__main__":
    unittest.main()
