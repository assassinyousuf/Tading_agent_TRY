#!/usr/bin/env python3
"""Unit tests for Strategy V3 Mean-Reversion & Liquidity Sweep (Standard Library)."""

import unittest
from _load import load

v3 = load("trader-timemachine", "research_v3_mean_reversion")
wilson_ci = v3.wilson_ci
calc_metrics = v3.calc_metrics
compute_rsi = v3.compute_rsi
compute_adx = v3.compute_adx
compute_bollinger_bands = v3.compute_bollinger_bands
simulate_liquidity_sweep = v3.simulate_liquidity_sweep
simulate_range_mean_reversion = v3.simulate_range_mean_reversion

class TestStrategyV3(unittest.TestCase):
    def test_rsi_monotonic(self):
        rising = [100.0 + i for i in range(30)]
        r_up = compute_rsi(rising)
        self.assertEqual(r_up[-1], 100.0)

        falling = [200.0 - i for i in range(30)]
        r_down = compute_rsi(falling)
        self.assertEqual(r_down[-1], 0.0)

    def test_bollinger_bands(self):
        closes = [100.0] * 30
        upper, mid, lower = compute_bollinger_bands(closes, period=20)
        self.assertAlmostEqual(upper[-1], 100.0)
        self.assertAlmostEqual(mid[-1], 100.0)
        self.assertAlmostEqual(lower[-1], 100.0)

    def test_liquidity_sweep_execution(self):
        bars = []
        for i in range(40):
            bars.append({
                "t": 10000 + i * 900, "o": 100.0, "h": 102.0, "l": 98.0, "c": 100.0, "v": 1000.0,
                "close_time": 10000 + (i + 1) * 900 - 1
            })
        # Bar 20 had swing low at 90.0
        bars[20]["l"] = 90.0
        bars[20]["c"] = 92.0
        # Bar 35: sweeps 90.0 with l=88.0 and closes back at 95.0 with wick
        bars[35]["o"] = 92.0
        bars[35]["l"] = 88.0
        bars[35]["c"] = 95.0
        bars[35]["h"] = 96.0
        bars[35]["v"] = 3000.0
        # Bar 36: Target hit
        bars[36]["h"] = 120.0
        bars[36]["l"] = 94.0
        bars[36]["c"] = 98.0

        res = simulate_liquidity_sweep(bars, p_left=3, p_right=2, lookback_window=20, min_wick_ratio=0.40, target_type="1.5r")
        signals = res["signals"]
        trades = res["trades"]

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["decision"], "TAKEN")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["type"], "target")
        self.assertTrue(trades[0]["net_r"] > 1.0)

    def test_range_mean_reversion_execution(self):
        bars = []
        for i in range(50):
            bars.append({
                "t": 10000 + i * 900, "o": 100.0, "h": 105.0, "l": 95.0, "c": 100.0, "v": 500.0,
                "close_time": 10000 + (i + 1) * 900 - 1
            })
        bars[45] = {
            "t": 10000 + 45 * 900, "o": 100.0, "h": 100.0, "l": 95.5, "c": 96.0, "v": 800.0,
            "close_time": 10000 + 46 * 900 - 1
        }
        bars[46] = {
            "t": 10000 + 46 * 900, "o": 96.0, "h": 99.0, "l": 95.5, "c": 98.5, "v": 1200.0,
            "close_time": 10000 + 47 * 900 - 1
        }
        bars[47] = {
            "t": 10000 + 47 * 900, "o": 98.5, "h": 110.0, "l": 98.0, "c": 99.0, "v": 1000.0,
            "close_time": 10000 + 48 * 900 - 1
        }

        res = simulate_range_mean_reversion(bars, range_lookback=20, max_adx=30.0, target_type="midpoint")
        signals = res["signals"]
        trades = res["trades"]

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["decision"], "TAKEN")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["type"], "target")


if __name__ == "__main__":
    unittest.main()
