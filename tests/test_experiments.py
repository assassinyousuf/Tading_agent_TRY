#!/usr/bin/env python3
"""Unit tests for Strategy Diagnostic & Experiments Suite (Standard Library)."""

import unittest
import math
from _load import load

exp = load("trader-timemachine", "experiments")
wilson_ci = exp.wilson_ci
calc_metrics = exp.calc_metrics
compute_ema = exp.compute_ema
compute_sma = exp.compute_sma
compute_atr = exp.compute_atr
compute_bollinger_bandwidth = exp.compute_bollinger_bandwidth
build_filter_trend_ema = exp.build_filter_trend_ema
build_filter_volume_rvol = exp.build_filter_volume_rvol
build_filter_atr_expansion = exp.build_filter_atr_expansion
build_filter_bb_expansion = exp.build_filter_bb_expansion
build_combined_filter = exp.build_combined_filter
simulate_strategy = exp.simulate_strategy
FROZEN_SPEC = exp.FROZEN_SPEC

class TestExperimentsSuite(unittest.TestCase):
    def test_wilson_ci_bounds(self):
        # 10 wins out of 37 trades
        ci = wilson_ci(10, 37)
        self.assertAlmostEqual(ci[0], 15.40, delta=0.1)
        self.assertAlmostEqual(ci[1], 42.98, delta=0.1)

        # Zero trades edge case
        self.assertEqual(wilson_ci(0, 0), [0.0, 0.0])

        # 100% win rate
        ci_100 = wilson_ci(20, 20)
        self.assertGreater(ci_100[0], 80.0)
        self.assertEqual(ci_100[1], 100.0)

    def test_indicator_math(self):
        # Test SMA
        series = [10.0, 20.0, 30.0, 40.0, 50.0]
        sma3 = compute_sma(series, 3)
        self.assertEqual(sma3[:2], [0.0, 0.0])
        self.assertAlmostEqual(sma3[2], 20.0)
        self.assertAlmostEqual(sma3[3], 30.0)
        self.assertAlmostEqual(sma3[4], 40.0)

        # Test EMA
        ema3 = compute_ema(series, 3)
        self.assertAlmostEqual(ema3[2], 20.0)
        # alpha = 2/(3+1) = 0.5; ema[3] = 40*0.5 + 20*0.5 = 30.0
        self.assertAlmostEqual(ema3[3], 30.0)
        # ema[4] = 50*0.5 + 30*0.5 = 40.0
        self.assertAlmostEqual(ema3[4], 40.0)

        # Test ATR on synthetic bars
        bars = [
            {"t": 1000 + i * 900, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 500.0}
            for i in range(20)
        ]
        atr14 = compute_atr(bars, 14)
        self.assertAlmostEqual(atr14[14], 10.0)

        # Test BB Bandwidth
        bbw = compute_bollinger_bandwidth(bars, 5)
        # On flat range bars, variance is zero
        self.assertAlmostEqual(bbw[-1], 0.0)

    def test_filter_evaluation(self):
        bars = [
            {"t": 1000 + i * 900, "o": 100.0 + i, "h": 102.0 + i, "l": 98.0 + i, "c": 101.0 + i, "v": 1000.0}
            for i in range(25)
        ]
        # EMA trend filter
        trend_fn = build_filter_trend_ema(bars, period=5)
        passed, reason = trend_fn(bars, 10)
        self.assertTrue(passed)

        # Volume filter
        vol_fn = build_filter_volume_rvol(bars, period=5, threshold=1.5)
        # Normal volume is 1000.0 -> RVOL = 1.0 < 1.5 -> should fail
        passed, reason = vol_fn(bars, 10)
        self.assertFalse(passed)
        self.assertIn("RVOL", reason)

        # Increase volume on bar 15
        bars[15]["v"] = 2500.0
        vol_fn2 = build_filter_volume_rvol(bars, period=5, threshold=1.5)
        passed, reason = vol_fn2(bars, 15)
        self.assertTrue(passed)

        # Combined filter
        combined = build_combined_filter([("Trend", trend_fn), ("Volume", vol_fn2)])
        passed, reason = combined(bars, 15)
        self.assertTrue(passed)

    def test_simulation_concurrency_and_execution(self):
        # Create synthetic breakout candles
        bars = []
        base_px = 100.0
        for i in range(50):
            o_px = base_px
            h_px = base_px + 1.0
            l_px = base_px - 1.0
            c_px = base_px
            if i == 25:
                # breakout candle: high_20 was ~101.0, close is 105.0
                o_px = 101.0
                h_px = 106.0
                l_px = 100.5
                c_px = 105.0
            elif i == 26:
                # continuation breakout while open trade active
                o_px = 105.0
                h_px = 107.5
                l_px = 104.5
                c_px = 107.0
            elif i == 27:
                # target reached on high, close pulls back below high_20
                o_px = 107.0
                h_px = 115.0
                l_px = 103.0
                c_px = 104.0
            bars.append({
                "t": 10000 + i * 900,
                "o": o_px,
                "h": h_px,
                "l": l_px,
                "c": c_px,
                "v": 1000.0,
                "close_time": 10000 + (i + 1) * 900 - 1
            })

        res = simulate_strategy(bars, max_concurrent=1)
        signals = res["signals"]
        trades = res["trades"]

        # First breakout at bar 25 should be taken
        taken = [s for s in signals if s["decision"] == "TAKEN"]
        self.assertEqual(len(taken), 1)

        # Second breakout at bar 26 should be skipped due to max exposure
        skipped = [s for s in signals if s["decision"] == "SKIPPED_MAX_EXPOSURE"]
        self.assertEqual(len(skipped), 1)

        # Trade should complete at target
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["type"], "target")
        self.assertTrue(trades[0]["win"])
        self.assertGreater(trades[0]["r"], 1.5)

    def test_calc_metrics_zero_trades(self):
        m = calc_metrics([], fee_rate=0.0010, slippage=0.0005)
        self.assertEqual(m["total_trades"], 0)
        self.assertEqual(m["expectancy_r"], 0.0)


if __name__ == "__main__":
    unittest.main()
