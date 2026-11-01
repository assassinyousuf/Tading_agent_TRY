#!/usr/bin/env python3
"""Unit tests for Higher-Timeframe Strategy Research (Standard Library)."""

import unittest
from _load import load

htf = load("trader-timemachine", "research_htf")
wilson_ci = htf.wilson_ci
calc_metrics = htf.calc_metrics
compute_ema = htf.compute_ema
compute_sma = htf.compute_sma
compute_atr = htf.compute_atr
classify_market_regime = htf.classify_market_regime
simulate_htf_strategy = htf.simulate_htf_strategy

class TestHTFResearch(unittest.TestCase):
    def test_wilson_ci(self):
        ci = wilson_ci(15, 30)
        self.assertAlmostEqual(ci[0], 33.15, delta=0.5)
        self.assertAlmostEqual(ci[1], 66.85, delta=0.5)
        self.assertEqual(wilson_ci(0, 0), [0.0, 0.0])

    def test_market_regime_classification(self):
        # Construct strong bull trend: price increasing each candle
        bull_bars = [
            {"t": 1000 + i * 3600, "o": 100.0 + i * 2, "h": 102.0 + i * 2,
             "l": 99.0 + i * 2, "c": 101.5 + i * 2, "v": 100.0}
            for i in range(70)
        ]
        regime_bull = classify_market_regime(bull_bars, 65, ema_period=50)
        self.assertEqual(regime_bull, "BULL_TREND")

        # Construct strong bear trend: price decreasing each candle
        bear_bars = [
            {"t": 1000 + i * 3600, "o": 300.0 - i * 2, "h": 302.0 - i * 2,
             "l": 298.0 - i * 2, "c": 299.0 - i * 2, "v": 100.0}
            for i in range(70)
        ]
        regime_bear = classify_market_regime(bear_bars, 65, ema_period=50)
        self.assertEqual(regime_bear, "BEAR_TREND")

        # Construct flat/chop
        flat_bars = [
            {"t": 1000 + i * 3600, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 100.0}
            for i in range(70)
        ]
        regime_chop = classify_market_regime(flat_bars, 65, ema_period=50)
        self.assertEqual(regime_chop, "RANGE_CHOP")

    def test_atr_stop_and_target_execution(self):
        # 40 baseline bars at 100
        bars = []
        for i in range(40):
            bars.append({
                "t": 10000 + i * 3600,
                "o": 100.0, "h": 102.0, "l": 98.0, "c": 100.0, "v": 1000.0,
                "close_time": 10000 + (i + 1) * 3600 - 1
            })
        # Bar 40: breakout to 105
        bars.append({
            "t": 10000 + 40 * 3600,
            "o": 100.0, "h": 106.0, "l": 99.5, "c": 105.0, "v": 2000.0,
            "close_time": 10000 + 41 * 3600 - 1
        })
        # Bar 41: Target hit on high, close pulls back
        bars.append({
            "t": 10000 + 41 * 3600,
            "o": 105.0, "h": 125.0, "l": 103.0, "c": 104.0, "v": 1500.0,
            "close_time": 10000 + 42 * 3600 - 1
        })

        # Test with ATR Stop
        res = simulate_htf_strategy(bars, lookback=20, stop_type="atr", atr_stop_mult=2.0, target_r=2.0)
        signals = res["signals"]
        trades = res["trades"]

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["decision"], "TAKEN")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["type"], "target")
        self.assertTrue(trades[0]["net_r"] > 1.5)
        self.assertIn("entry_fee", trades[0])
        self.assertIn("exit_fee", trades[0])

    def test_metrics_consistency(self):
        mock_trades = [
            {
                "net_r": 1.8, "gross_r": 2.0, "net_pnl": 18.0, "gross_pnl": 20.0,
                "entry_fee": 1.0, "exit_fee": 1.0, "net_return_pct": 3.6,
                "gross_return_pct": 4.0, "risk": 10.0
            },
            {
                "net_r": -1.2, "gross_r": -1.0, "net_pnl": -12.0, "gross_pnl": -10.0,
                "entry_fee": 1.0, "exit_fee": 1.0, "net_return_pct": -2.4,
                "gross_return_pct": -2.0, "risk": 10.0
            }
        ]
        m = calc_metrics(mock_trades, fee_rate=0.0010, slippage=0.0005)
        self.assertEqual(m["total_trades"], 2)
        self.assertEqual(m["wins"], 1)
        self.assertEqual(m["losses"], 1)
        self.assertEqual(m["win_rate_pct"], 50.0)
        self.assertAlmostEqual(m["gross_expectancy_r"], 0.5)
        self.assertAlmostEqual(m["net_expectancy_r"], 0.3)
        self.assertAlmostEqual(m["profit_factor"], 1.5)


if __name__ == "__main__":
    unittest.main()
