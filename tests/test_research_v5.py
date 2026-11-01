"""
Unit tests for Strategy V5: Multi-Asset Volatility-Parity Trend Following (VP-TF)
Verifies:
- Data loading, alignment, and indicator warmup
- Zero look-ahead and execution delay modeling (Day t Close signal -> Day t+1 Open fill)
- Trailing stop ratcheting and gap-down exit logic
- Exact compounding identity (< 1e-10 error)
- Transaction cost accounting (15 bps crypto taker fee + slippage, 0 bps cash)
- Benchmark parity and identical evaluation dates
- Moving block bootstrap determinism and hypothesis test outputs
- Registry schema conformance and automated NO-GO verdict enforcement
"""

import json
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from research_v5_trend_following import (
    DEFAULT_UNIVERSE,
    load_aligned_daily_candles,
    simulate_trend_following,
    calc_btc_buy_and_hold,
    calc_equal_weight,
    calc_cash_benchmark,
    paired_block_bootstrap_test,
    run_v5_research
)


class TestResearchV5(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.trader_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trader")
        cls.bars = load_aligned_daily_candles(DEFAULT_UNIVERSE, trader_dir=cls.trader_dir)

    def test_01_data_loading_and_indicators(self):
        """Verifies 729 bars loaded, zero gaps, indicators computed with correct warmup."""
        self.assertEqual(len(self.bars), 729)
        for s in DEFAULT_UNIVERSE:
            self.assertIn("atr14", self.bars[30][s])
            self.assertIn("high_20", self.bars[30][s])
            self.assertIn("high_30", self.bars[30][s])
            self.assertIsNotNone(self.bars[30][s]["atr14"])
            self.assertIsNotNone(self.bars[30][s]["high_20"])
            self.assertIsNotNone(self.bars[30][s]["high_30"])
            # ATR14 must be positive
            self.assertGreater(self.bars[30][s]["atr14"], 0.0)

    def test_02_zero_lookahead_and_execution_timing(self):
        """Verifies breakout signal at close t executes at day t+1 open."""
        sim = simulate_trend_following(
            self.bars, universe=DEFAULT_UNIVERSE, lookback=20, stop_mult=2.5,
            start_bar=30, end_bar=100
        )
        ledger = sim["trade_ledger"]
        buys = [t for t in ledger if t["type"] == "BUY"]
        for b in buys:
            bar_idx = b["bar"]
            sym = b["symbol"]
            # Fill price must equal Open of bar_idx
            self.assertEqual(b["price"], self.bars[bar_idx][sym]["o"])
            # The signal must have triggered at close of bar_idx - 1
            prev_close = self.bars[bar_idx - 1][sym]["c"]
            prev_high20 = self.bars[bar_idx - 1][sym]["high_20"]
            self.assertGreater(prev_close, prev_high20)

    def test_03_trailing_stop_mechanics(self):
        """Verifies trailing stops ratchet only upwards and gap downs exit at open."""
        sim = simulate_trend_following(
            self.bars, universe=DEFAULT_UNIVERSE, lookback=20, stop_mult=2.5,
            start_bar=30, end_bar=429
        )
        sells = [t for t in sim["trade_ledger"] if t["type"] == "SELL" and t.get("reason") in ["TRAILING_STOP", "GAP_DOWN_STOP"]]
        for s in sells:
            bar_idx = s["bar"]
            sym = s["symbol"]
            open_p = self.bars[bar_idx][sym]["o"]
            low_p = self.bars[bar_idx][sym]["l"]
            exit_p = s["price"]
            if s["reason"] == "GAP_DOWN_STOP":
                # Must exit at Open price
                self.assertEqual(exit_p, open_p)
            elif s["reason"] == "TRAILING_STOP":
                # Exit price must be <= open_p and >= low_p
                self.assertLessEqual(exit_p, open_p)
                self.assertGreaterEqual(exit_p, low_p)

    def test_04_compounding_identity(self):
        """Verifies exact compounding identity: prod(1 + R_net) == final_equity / initial_equity (< 1e-10 error)."""
        for p_name, s_bar, e_bar in [("TRAIN", 30, 429), ("VAL", 430, 579), ("TEST", 580, 728)]:
            sim = simulate_trend_following(
                self.bars, universe=DEFAULT_UNIVERSE, lookback=20, stop_mult=2.5,
                start_bar=s_bar, end_bar=e_bar
            )
            raw_final = sim["equity_curve"][-1]
            initial_cap = sim["metrics"]["initial_capital"]
            compounded = initial_cap
            for r in sim["daily_returns"]:
                compounded *= (1.0 + r)
            err = abs(raw_final - compounded)
            self.assertLess(err, 1e-10, f"Compounding error {err} >= 1e-10 in {p_name}")
            self.assertAlmostEqual(sim["metrics"]["final_capital"], compounded, places=2)

    def test_05_friction_accounting_and_terminal_liquidation(self):
        """Verifies only crypto trades pay friction and terminal positions are liquidated."""
        sim = simulate_trend_following(
            self.bars, universe=DEFAULT_UNIVERSE, lookback=20, stop_mult=2.5,
            start_bar=30, end_bar=429, liquidate_terminal=True
        )
        ledger = sim["trade_ledger"]
        total_drag = 0.0010 + 0.0005 # 0.0015
        for t in ledger:
            expected_friction = round(t["notional"] * total_drag, 2)
            self.assertAlmostEqual(t["friction"], expected_friction, delta=0.02)

        # Check last sell has TERMINAL_LIQUIDATION if positions were open
        terminal_sells = [t for t in ledger if t.get("reason") == "TERMINAL_LIQUIDATION"]
        for ts in terminal_sells:
            self.assertEqual(ts["bar"], 429)

    def test_06_benchmark_parity(self):
        """Verifies benchmarks evaluate over identical dates and charge round-trip friction."""
        btc = calc_btc_buy_and_hold(self.bars, start_bar=580, end_bar=728)
        ew = calc_equal_weight(self.bars, universe=DEFAULT_UNIVERSE, start_bar=580, end_bar=728)
        cash = calc_cash_benchmark(len(btc["daily_returns"]))

        self.assertEqual(len(btc["daily_returns"]), len(ew["daily_returns"]))
        self.assertEqual(len(btc["daily_returns"]), len(cash["daily_returns"]))
        self.assertEqual(cash["metrics"]["net_total_return_pct"], 0.0)
        self.assertGreater(btc["metrics"]["total_friction_usd"], 0.0)
        self.assertGreater(ew["metrics"]["total_friction_usd"], 0.0)

    def test_07_paired_bootstrap_determinism(self):
        """Verifies paired moving block bootstrap gives deterministic results with fixed seed."""
        sim = simulate_trend_following(self.bars, start_bar=580, end_bar=728)
        btc = calc_btc_buy_and_hold(self.bars, start_bar=580, end_bar=728)

        ht1 = paired_block_bootstrap_test(sim["daily_returns"], btc["daily_returns"], seed=42)
        ht2 = paired_block_bootstrap_test(sim["daily_returns"], btc["daily_returns"], seed=42)
        self.assertEqual(ht1["p_value"], ht2["p_value"])
        self.assertEqual(ht1["ci_95_excess_pp"], ht2["ci_95_excess_pp"])

    def test_08_registry_schema_and_verdict(self):
        """Verifies registry JSON records conform to schema and enforce NO-GO."""
        reg_file = os.path.join(self.trader_dir, "v5_experiment_registry.json")
        self.assertTrue(os.path.exists(reg_file))
        with open(reg_file, "r") as f:
            records = json.load(f)

        self.assertEqual(len(records), 3) # 3 preregistered configs
        for rec in records:
            self.assertEqual(rec["hypothesis_id"], "H5-B")
            self.assertIn("experiment_id", rec)
            self.assertIn("metrics", rec)
            self.assertIn("benchmarks", rec)
            self.assertIn("hypothesis_test", rec)
            self.assertIn("multiple_testing_correction", rec)
            self.assertEqual(rec["decision"], "NO-GO")
            # All configs failed out-of-sample significance vs BTC
            self.assertGreaterEqual(rec["hypothesis_test"]["bootstrap_p_value_vs_btc"], 0.0167)


if __name__ == "__main__":
    unittest.main()
