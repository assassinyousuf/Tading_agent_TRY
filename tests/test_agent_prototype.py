"""Unit Test Suite for Autonomous Quant Research Agent Phase 1 Infrastructure.

Verifies:
1. Sealed Test Vault Access & Token Consumption.
2. Preregistration Enforcement & Parameter Tampering Rejection.
3. Partition Leakage Prevention & Indicator Warmup Separation.
4. Independent Initial Portfolio States across Partitions.
5. Append-Only SQLite Ledger Behavior (Trigger Immutability).
6. Global Trial Accounting (Raw M_total vs Diagnostic M_eff).
7. Dataset Hash Provenance and 86400s Timestamp Continuity.
8. Physical Broker Execution Isolation (Zero Broker Imports in Agent Package).
9. SyntheticSMAStrategy Simulation & Benchmark Engine Parity.
"""

import ast
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

from trader.agent.core.strategy_base import StrategyInterface
from trader.agent.core.synthetic_strategy import SyntheticSMAStrategy
from trader.agent.db.ledger import ResearchLedger
from trader.agent.tools.market_bridge import (
    MarketDataProvider,
    SealedPartitionAccessViolation,
    execute_strategy_simulation,
)
from benchmark_engine import (
    calc_sharpe_ratio,
    calc_deflated_sharpe_ratio,
    paired_block_bootstrap_test,
)


class TestAgentPhase1Infrastructure(unittest.TestCase):
    """Verification suite for Phase 1 agent infrastructure and security invariants."""

    def setUp(self) -> None:
        """Initialize in-memory research ledger and market data provider."""
        self.ledger = ResearchLedger(db_path=":memory:")
        self.provider = MarketDataProvider(ledger=self.ledger)

    def tearDown(self) -> None:
        """Clean up ledger connection."""
        self.ledger.close()

    def test_unauthorized_test_access(self) -> None:
        """Attempting to access the sealed TEST partition without a token must raise an error."""
        with self.assertRaises(SealedPartitionAccessViolation):
            self.provider.get_partition_data("TEST", test_token=None)

        with self.assertRaises(SealedPartitionAccessViolation):
            self.provider.get_partition_data("TEST", test_token="")

        with self.assertRaises(SealedPartitionAccessViolation):
            self.provider.get_partition_data("TEST", test_token="   ")

    def test_authorized_test_access_with_single_use_token(self) -> None:
        """Authorized test evaluation requires a valid token which is consumed upon use."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-TEST-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Trend following demo",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-TEST-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 580, "end_bar": 729},
            benchmark_ids=["BTC_NET"],
        )

        token = self.ledger.generate_test_vault_token(
            prereg_id=prereg_id,
            authorized_by="HumanResearcher",
            purpose="Phase 1 Verification Audit",
        )
        self.assertTrue(token.startswith("TEST-TOKEN-"))

        # Access with token must succeed and automatically consume the token
        data = self.provider.get_partition_data("TEST", test_token=token, prereg_id=prereg_id)
        self.assertEqual(data["partition"], "TEST")
        self.assertEqual(len(data["trading_bars"]), 149)

        # Attempting to re-access with the consumed token via provider must fail
        with self.assertRaises(SealedPartitionAccessViolation):
            self.provider.get_partition_data("TEST", test_token=token, prereg_id=prereg_id)

        # Attempting to re-consume via ledger directly must fail
        with self.assertRaises(PermissionError):
            self.ledger.consume_test_vault_token(token, prereg_id)

    def test_token_expiration(self) -> None:
        """Tokens past expiration must be rejected."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-EXP-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Expiration test",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-EXP-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 580, "end_bar": 729},
            benchmark_ids=["BTC_NET"],
        )
        token = self.ledger.generate_test_vault_token(
            prereg_id=prereg_id,
            authorized_by="HumanAuditor",
            purpose="Expiration verification",
            expires_in_seconds=-10,
        )
        with self.assertRaises(PermissionError) as ctx:
            self.ledger.verify_and_consume_test_token(token, prereg_id)
        self.assertIn("expired", str(ctx.exception).lower())

    def test_token_hash_stored_not_raw(self) -> None:
        """Database must only store SHA-256 hash of tokens, never plain text raw secret."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-HASH-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Hash verification",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-HASH-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 580, "end_bar": 729},
            benchmark_ids=["BTC_NET"],
        )
        token = self.ledger.generate_test_vault_token(
            prereg_id=prereg_id,
            authorized_by="SecurityAuditor",
            purpose="Hash verification test",
        )
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT token_hash FROM test_evaluation_authorizations;")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        stored_hash = rows[0][0]
        self.assertNotEqual(stored_hash, token)
        self.assertEqual(len(stored_hash), 64)

        # Check entire database tables for the raw secret token string
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]
        for tbl in tables:
            cur.execute(f"SELECT * FROM {tbl};")
            for row in cur.fetchall():
                for col in row:
                    self.assertNotIn(token, str(col))

    def test_token_binding_and_revocation(self) -> None:
        """Token bound to prereg A cannot access prereg B, and revoked token cannot be consumed."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-BIND-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Binding test",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg1 = self.ledger.preregister_experiment(
            hypothesis_id="H-BIND-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 580, "end_bar": 729},
            benchmark_ids=["BTC_NET"],
        )
        prereg2 = self.ledger.preregister_experiment(
            hypothesis_id="H-BIND-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [20.0], "slow_period": [40.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 580, "end_bar": 729},
            benchmark_ids=["BTC_NET"],
        )
        token = self.ledger.generate_test_vault_token(
            prereg_id=prereg1,
            authorized_by="Admin",
            purpose="Prereg binding test",
        )
        # Attempt consuming for prereg2 must fail
        with self.assertRaises(PermissionError) as ctx:
            self.ledger.verify_and_consume_test_token(token, prereg_id=prereg2)
        self.assertIn("cannot access", str(ctx.exception))

        # Revoke token
        revoked = self.ledger.revoke_test_token(token)
        self.assertTrue(revoked)

        # Attempt consuming revoked token for prereg1 must fail
        with self.assertRaises(PermissionError) as ctx:
            self.ledger.verify_and_consume_test_token(token, prereg_id=prereg1)
        self.assertIn("revoked", str(ctx.exception).lower())

    def test_partition_leakage_and_warmup_separation(self) -> None:
        """Confirm partitions contain zero future bars and warmup strictly precedes trading start."""
        # 1. TRAIN Partition: Bars 30 to 430
        train_data = self.provider.get_partition_data("TRAIN", warmup_bars=30)
        self.assertEqual(len(train_data["warmup_bars"]), 30)
        self.assertEqual(len(train_data["trading_bars"]), 400)
        self.assertEqual(train_data["start_bar"], 30)
        self.assertEqual(train_data["end_bar"], 430)

        # Verify warmup open_time is strictly earlier than trading open_time
        last_warmup_time = train_data["warmup_bars"][-1]["open_time"]
        first_trading_time = train_data["trading_bars"][0]["open_time"]
        self.assertEqual(first_trading_time - last_warmup_time, 86400000)

        # 2. VALIDATION Partition: Bars 430 to 580
        val_data = self.provider.get_partition_data("VALIDATION", warmup_bars=30)
        self.assertEqual(len(val_data["warmup_bars"]), 30)
        self.assertEqual(len(val_data["trading_bars"]), 150)
        self.assertEqual(val_data["start_bar"], 430)
        self.assertEqual(val_data["end_bar"], 580)

    def test_independent_portfolio_initialization(self) -> None:
        """Confirm each partition evaluation provides independent, fresh portfolio state."""
        train_data = self.provider.get_partition_data("TRAIN")
        val_data = self.provider.get_partition_data("VALIDATION")

        train_state = train_data["initial_portfolio_state"]
        val_state = val_data["initial_portfolio_state"]

        self.assertEqual(train_state["initial_capital"], 10000.0)
        self.assertEqual(val_state["initial_capital"], 10000.0)
        self.assertEqual(len(train_state["positions"]), 0)
        self.assertEqual(len(val_state["positions"]), 0)

    def test_preregistration_enforcement(self) -> None:
        """Preregistration gate must verify approved parameter grid and reject tampering."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-PREREG-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Trend following demo",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-PREREG-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [5.0, 10.0], "slow_period": [20.0, 30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 30, "end_bar": 430},
            benchmark_ids=["BTC_NET"],
        )

        # 1. Valid approved parameters
        valid, msg = self.ledger.verify_preregistration(
            prereg_id, {"fast_period": 10.0, "slow_period": 30.0}, "TRAIN", ["BTCUSDT"]
        )
        self.assertTrue(valid, msg)

        # 2. Tampered parameter value not in grid
        valid, msg = self.ledger.verify_preregistration(
            prereg_id, {"fast_period": 15.0, "slow_period": 30.0}, "TRAIN", ["BTCUSDT"]
        )
        self.assertFalse(valid)
        self.assertIn("not in approved grid", msg)

        # 3. Undeclared parameter
        valid, msg = self.ledger.verify_preregistration(
            prereg_id, {"fast_period": 10.0, "slow_period": 30.0, "unauthorized_param": 1.0}, "TRAIN", ["BTCUSDT"]
        )
        self.assertFalse(valid)
        self.assertIn("not declared in preregistration grid", msg)

        # 4. Universe mismatch
        valid, msg = self.ledger.verify_preregistration(
            prereg_id, {"fast_period": 10.0, "slow_period": 30.0}, "TRAIN", ["ETHUSDT"]
        )
        self.assertFalse(valid)
        self.assertIn("Universe mismatch", msg)

    def test_append_only_ledger_behavior(self) -> None:
        """Completed experiment and trial records must be immutable against UPDATE and DELETE."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-IMMUTABLE-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Immutability testing",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-IMMUTABLE-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 30, "end_bar": 430},
            benchmark_ids=["BTC_NET"],
        )

        exp_data = {
            "experiment_id": "EXP-IMMUTABLE-TEST",
            "prereg_id": prereg_id,
            "partition_name": "TRAIN",
            "parameters": {"fast_period": 10.0, "slow_period": 30.0},
        }
        metric_data = {
            "initial_capital": 10000.0,
            "final_capital": 10500.0,
            "net_total_return_pct": 5.0,
            "gross_total_return_pct": 5.5,
            "cagr_pct": 4.5,
            "annualized_vol_pct": 20.0,
            "sharpe_ratio": 0.225,
            "sortino_ratio": 0.310,
            "max_drawdown_pct": -10.0,
            "total_trades": 12,
            "win_rate_pct": 58.33,
            "total_friction_usd": 50.0,
            "compounding_error": 0.0,
            "raw_metrics": {"test": 1},
        }

        self.ledger.record_experiment_result(exp_data, metric_data)

        cur = self.ledger.conn.cursor()

        # Attempt to UPDATE experiments table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE experiments SET status = 'TAMPERED' WHERE experiment_id = 'EXP-IMMUTABLE-TEST';")

        # Attempt to DELETE from experiments table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("DELETE FROM experiments WHERE experiment_id = 'EXP-IMMUTABLE-TEST';")

        # Attempt to UPDATE experiment_metrics table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE experiment_metrics SET sharpe_ratio = 9.99 WHERE experiment_id = 'EXP-IMMUTABLE-TEST';")

        # Attempt to DELETE from experiment_metrics table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("DELETE FROM experiment_metrics WHERE experiment_id = 'EXP-IMMUTABLE-TEST';")

        # Attempt to UPDATE hypotheses table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE hypotheses SET strategy_name = 'HACKED' WHERE hypothesis_id = 'H-IMMUTABLE-1';")

        # Attempt to DELETE from hypotheses table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("DELETE FROM hypotheses WHERE hypothesis_id = 'H-IMMUTABLE-1';")

        # Attempt to UPDATE preregistrations table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("UPDATE preregistrations SET status = 'TAMPERED' WHERE prereg_id = ?;", (prereg_id,))

        # Attempt to DELETE from preregistrations table must abort
        with self.assertRaises(sqlite3.DatabaseError):
            cur.execute("DELETE FROM preregistrations WHERE prereg_id = ?;", (prereg_id,))

    def test_trial_accounting_m_total_and_m_eff(self) -> None:
        """Verify sequential M_total tracking and correlation-based M_eff diagnostic."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-TRIAL-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Trend following premise",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-TRIAL-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [5.0, 10.0], "slow_period": [20.0, 30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 30, "end_bar": 430},
            benchmark_ids=["BTC_NET"],
        )

        # Record normal trial
        m1 = self.ledger.record_trial("H-TRIAL-1", prereg_id, "EXP-1", {"fast": 5.0}, "TRAIN_EVALUATED")
        self.assertEqual(m1, 1)

        # Record rejected pre-test idea
        m2 = self.ledger.record_trial(
            "H-TRIAL-1", prereg_id, None, {"fast": 2.0}, "PRE_TEST_REJECTED", "Too noisy"
        )
        self.assertEqual(m2, 2)

        counts = self.ledger.get_trial_counts()
        self.assertEqual(counts["M_total"], 2)
        self.assertEqual(counts["M_rejected"], 1)

        # Test M_eff calculation:
        # 1. Identical series -> M_eff = 1.0
        ret_identical = [
            [0.01, -0.02, 0.03, -0.01, 0.02],
            [0.01, -0.02, 0.03, -0.01, 0.02],
            [0.01, -0.02, 0.03, -0.01, 0.02],
        ]
        m_eff_ident = ResearchLedger.calculate_effective_trials_diagnostic(ret_identical)
        self.assertAlmostEqual(m_eff_ident, 1.0, places=2)

        # 2. Uncorrelated orthogonal series -> M_eff ~ 3.0
        ret_ortho = [
            [0.05, -0.02, 0.01, 0.03, -0.04, 0.02],
            [-0.01, 0.04, -0.03, 0.02, 0.01, -0.05],
            [0.02, 0.01, 0.04, -0.05, 0.03, 0.01],
        ]
        m_eff_ortho = ResearchLedger.calculate_effective_trials_diagnostic(ret_ortho)
        self.assertGreater(m_eff_ortho, 2.0)
        self.assertLessEqual(m_eff_ortho, 3.0)

    def test_dataset_hash_and_continuity_verification(self) -> None:
        """Verify historical data provenance hashes and 86400s continuous daily cadence."""
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
            bars = self.provider.raw_caches[sym]
            self.assertEqual(len(bars), 729)
            data_hash = self.provider.get_data_hash(sym)
            self.assertEqual(len(data_hash), 64)  # Valid SHA-256 length

            # Verify consecutive 86400s intervals
            for i in range(1, len(bars)):
                interval = bars[i]["open_time"] - bars[i - 1]["open_time"]
                self.assertEqual(interval, 86400000)

    def test_broker_isolation(self) -> None:
        """Verify that agent package has zero imports or references to trader-broker or orders."""
        agent_dir = os.path.join(ROOT, "trader", "agent")
        prohibited_terms = [
            "trader-broker",
            "binance_broker",
            "create_order",
            "execute_bracket_order",
            "cancel_order",
            "orders_journal",
        ]

        for root, _, files in os.walk(agent_dir):
            for file in files:
                if not file.endswith(".py"):
                    continue
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse AST
                tree = ast.parse(content, filename=filepath)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for term in prohibited_terms:
                                self.assertNotIn(term, alias.name, f"Broker import in {filepath}: {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        for term in prohibited_terms:
                            self.assertNotIn(term, mod, f"Broker import in {filepath}: {mod}")

                # Check text content
                for term in ["binance_broker", "orders_journal.json"]:
                    self.assertNotIn(term, content, f"Prohibited broker reference in {filepath}: {term}")

    def test_synthetic_strategy_execution_and_benchmark_engine_parity(self) -> None:
        """Execute SyntheticSMAStrategy on TRAIN partition and connect output to benchmark_engine."""
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

        # 1. Verify compounding error precision < 1e-10
        self.assertLess(results["compounding_error"], 1e-10)

        # 2. Verify return array length equals trading partition length (400)
        daily_returns = results["daily_returns"]
        self.assertEqual(len(daily_returns), 400)
        self.assertEqual(len(results["daily_gross_returns"]), 400)

        # 3. Verify connection to benchmark_engine.py functions
        cagr = results["cagr_pct"]
        ann_vol = results["annualized_vol_pct"]
        sharpe = calc_sharpe_ratio(cagr / 100.0, ann_vol / 100.0)
        self.assertAlmostEqual(sharpe, results["sharpe_ratio"], places=4)

        # 4. Verify paired block bootstrap test executes without error
        synthetic_bench = [0.0002] * 400  # constant benchmark
        boot_res = paired_block_bootstrap_test(
            strategy_returns=daily_returns,
            benchmark_returns=synthetic_bench,
            block_size=10,
            n_boot=100,
            seed=42,
        )
        self.assertIn("p_value", boot_res)
        self.assertTrue(0.0 <= boot_res["p_value"] <= 1.0)
        self.assertIn("ci_95_excess_pp", boot_res)

        # 5. Verify Deflated Sharpe Ratio calculation executes with M_total
        dsr_res = calc_deflated_sharpe_ratio(
            observed_sharpe=sharpe,
            n_obs=400,
            n_trials=10,
            skewness=results["raw_metrics"]["skewness"],
            kurtosis=results["raw_metrics"]["kurtosis"],
            is_annualized=True,
            return_details=True,
        )
        self.assertIn("dsr", dsr_res)
        self.assertTrue(0.0 <= dsr_res["dsr"] <= 1.0)

    def test_schema_versioning(self) -> None:
        """Verify schema metadata and version reporting."""
        version = self.ledger.get_schema_version()
        self.assertGreaterEqual(version, 1)

        cur = self.ledger.conn.cursor()
        cur.execute("SELECT version, description FROM schema_metadata ORDER BY version DESC LIMIT 1;")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row[0], 1)

    def test_audit_logging_and_research_decision(self) -> None:
        """Verify adversarial audit results and decision logging."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-DEC-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Decision testing",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-DEC-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 30, "end_bar": 430},
            benchmark_ids=["BTC_NET"],
        )
        exp_id = "EXP-DEC-1"
        self.ledger.record_experiment_result(
            experiment_data={
                "experiment_id": exp_id,
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
            audit_data={
                "benchmark_id": "BTC_NET",
                "bootstrap_p_value_raw": 0.25,
                "bootstrap_ci_lower": -0.05,
                "bootstrap_ci_upper": 0.07,
                "screening_category": "B",
                "ar2_max_root": 0.22,
                "dsr_value_raw": 0.42,
                "m_total_used": 1,
            },
        )

        dec_id = self.ledger.record_decision(
            hypothesis_id="H-DEC-1",
            decision="NO_GO",
            rationale="Bootstrap p-value 0.25 exceeds threshold; zero outperformance.",
            decided_by="AdversarialReviewer",
            prereg_id=prereg_id,
            experiment_id=exp_id,
        )
        self.assertTrue(dec_id.startswith("DECISION-"))

        # Verify invalid decision raises ValueError
        with self.assertRaises(ValueError):
            self.ledger.record_decision(
                hypothesis_id="H-DEC-1",
                decision="MAYBE",
                rationale="Invalid decision",
                decided_by="Tester",
            )

    def test_sql_injection_resistance(self) -> None:
        """Verify SQL injection payloads in hypothesis/prereg inputs are safely parameterized."""
        malicious_id = "H-INJECT'; DROP TABLE experiments; --"
        self.ledger.record_hypothesis(
            hypothesis_id=malicious_id,
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Testing injection resistance",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        cur = self.ledger.conn.cursor()
        cur.execute("SELECT hypothesis_id FROM hypotheses WHERE hypothesis_id = ?;", (malicious_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], malicious_id)

        # Verify experiments table was not dropped
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='experiments';")
        self.assertIsNotNone(cur.fetchone())

    def test_market_data_provider_with_ledger_integration(self) -> None:
        """MarketDataProvider configured with ledger automatically enforces token validation."""
        provider_with_ledger = MarketDataProvider(ledger=self.ledger)

        self.ledger.record_hypothesis(
            hypothesis_id="H-BRIDGE-1",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Bridge test",
            validation_criteria={"min_sharpe": 0.5, "benchmark": "BTC_NET"},
        )
        prereg_id = self.ledger.preregister_experiment(
            hypothesis_id="H-BRIDGE-1",
            universe=["BTCUSDT"],
            timeframe="1d",
            parameter_grid={"fast_period": [10.0], "slow_period": [30.0]},
            cost_model={"taker_fee_rate": 0.0010, "slippage_rate": 0.0005},
            partition_protocol={"start_bar": 580, "end_bar": 729},
            benchmark_ids=["BTC_NET"],
        )
        token = self.ledger.generate_test_vault_token(
            prereg_id=prereg_id,
            authorized_by="SecurityOfficer",
            purpose="Automated Bridge Enforcement Test",
        )

        # 1. Fetching with valid token and correct prereg_id succeeds
        data = provider_with_ledger.get_partition_data("TEST", test_token=token, prereg_id=prereg_id)
        self.assertEqual(data["partition"], "TEST")
        self.assertEqual(len(data["trading_bars"]), 149)

        # 2. Fetching a second time with consumed token must raise SealedPartitionAccessViolation
        with self.assertRaises(SealedPartitionAccessViolation):
            provider_with_ledger.get_partition_data("TEST", test_token=token, prereg_id=prereg_id)


if __name__ == "__main__":
    unittest.main()
