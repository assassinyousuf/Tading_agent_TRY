#!/usr/bin/env python3
"""Audit Baseline Forward-Test & Backtest Implementations."""

import json
import math
import sys
from decimal import Decimal

def audit():
    with open("trader/forward_log.json", "r", encoding="utf-8") as f:
        log = json.load(f)

    with open("trader/forward_report.json", "r", encoding="utf-8") as f:
        rep = json.load(f)

    signals = log.get("signals", [])
    trades = log.get("trades", [])

    print(f"=== AUDITING BASELINE FORWARD TEST ===")
    print(f"Total Signals Logged: {len(signals)}")
    print(f"Total Trades Logged : {len(trades)}")

    # 1. Verify Signals
    taken_signals = [s for s in signals if s.get("decision") == "TAKEN"]
    skipped_signals = [s for s in signals if s.get("decision") == "SKIPPED_MAX_EXPOSURE"]
    rejected_signals = [s for s in signals if "REJECTED" in s.get("decision", "")]

    print(f"Signals Taken   : {len(taken_signals)} (report says: {rep['signals_taken']})")
    print(f"Signals Skipped : {len(skipped_signals)} (report says: {rep['signals_skipped_max_exposure']})")
    print(f"Signals Rejected: {len(rejected_signals)} (report says: {rep['signals_rejected_filters']})")

    # Check why signals_taken is 38 but trades completed is 37
    print(f"Signals taken ({len(taken_signals)}) vs Trades completed ({len(trades)}):")
    if len(taken_signals) > len(trades):
        last_taken = taken_signals[-1]
        print(f"  Note: Last taken signal {last_taken['trade_id']} at t={last_taken['timestamp']} remained OPEN at end of simulation window!")

    # 2. Overlap Verification
    overlaps = []
    for i in range(len(trades) - 1):
        t_curr = trades[i]
        t_next = trades[i + 1]
        if t_next["entry_time"] < t_curr["exit_time"]:
            overlaps.append((t_curr["id"], t_next["id"], t_curr["exit_time"], t_next["entry_time"]))

    print(f"Overlapping Trades Detected: {len(overlaps)}")
    for ov in overlaps:
        print(f"  Overlap: {ov[0]} exited at {ov[2]} but {ov[1]} entered earlier at {ov[3]}")

    # 3. Check Skipped Signals validity
    invalid_skips = []
    trade_intervals = [(t["entry_time"], t["exit_time"], t["id"]) for t in trades]
    if len(taken_signals) > len(trades):
        # include the unclosed trade interval up to infinity
        trade_intervals.append((taken_signals[-1]["timestamp"], float("inf"), taken_signals[-1]["trade_id"]))

    for s in skipped_signals:
        s_t = s["timestamp"]
        in_interval = any(start <= s_t <= end for start, end, tid in trade_intervals)
        if not in_interval:
            invalid_skips.append(s)

    print(f"Invalid Skipped Signals (skipped when no trade was open): {len(invalid_skips)}")

    # 4. Math verification on each trade
    fee_rate = rep["frozen_spec"]["taker_fee_rate"]
    slippage = rep["frozen_spec"]["slippage_rate"]
    pnl_errors = []
    r_errors = []
    holding_errors = []

    # Map signals to get initial risk
    sig_map = {s["trade_id"]: s for s in taken_signals if "trade_id" in s}

    for idx, t in enumerate(trades):
        t_id = t["id"]
        entry_px = t["entry_px"]
        exit_px = t["exit_px"]
        bars_held = t["bars_held"]
        expected_bars = (t["exit_time"] - t["entry_time"]) / 900  # 15m = 900s
        if bars_held != expected_bars:
            holding_errors.append(f"Trade {t_id}: bars_held logged {bars_held} vs time diff {expected_bars}")

        entry_cost = entry_px * fee_rate
        exit_cost = exit_px * fee_rate
        calc_net_pnl = (exit_px - entry_px) - (entry_cost + exit_cost)
        calc_net_ret = (calc_net_pnl / entry_px) * 100.0

        if abs(t["net_pnl"] - calc_net_pnl) > 0.005:
            pnl_errors.append(f"Trade {t_id}: net_pnl logged {t['net_pnl']} vs calc {calc_net_pnl:.4f}")

        # Check risk and R
        sig = sig_map.get(t_id)
        if sig:
            logged_risk = sig["risk"]
            calc_r = calc_net_pnl / logged_risk if logged_risk > 0 else 0
            if abs(t["r"] - calc_r) > 0.005:
                r_errors.append(f"Trade {t_id}: r logged {t['r']} vs calc {calc_r:.4f}")

    print(f"Holding Duration Inconsistencies: {len(holding_errors)}")
    print(f"Net PnL Calculation Mismatches : {len(pnl_errors)}")
    print(f"R-Multiple Calculation Mismatches: {len(r_errors)}")

    # 5. Aggregate Metrics Recalculation
    n_trades = len(trades)
    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    win_rate = (len(wins) / n_trades) * 100.0 if n_trades > 0 else 0

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

    expectancy_r = sum(t["r"] for t in trades) / n_trades if n_trades > 0 else 0
    expectancy_pct = sum(t["net_return_pct"] for t in trades) / n_trades if n_trades > 0 else 0

    # Max Drawdown calculation in R
    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in trades:
        cum_r += t["r"]
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd

    # Max Drawdown calculation in %
    cum_pct = 0.0
    peak_pct = 0.0
    max_dd_pct = 0.0
    for t in trades:
        cum_pct += t["net_return_pct"]
        if cum_pct > peak_pct:
            peak_pct = cum_pct
        dd = peak_pct - cum_pct
        if dd > max_dd_pct:
            max_dd_pct = dd

    # Wilson 95% CI
    z = 1.95996
    p = len(wins) / n_trades
    denom = 1.0 + (z**2 / n_trades)
    centre = (p + (z**2 / (2.0 * n_trades))) / denom
    spread = z * math.sqrt((p * (1.0 - p) / n_trades) + (z**2 / (4.0 * n_trades**2))) / denom
    ci_low = max(0.0, (centre - spread) * 100.0)
    ci_high = min(100.0, (centre + spread) * 100.0)

    print("\n--- RECALCULATED VS REPORTED COMPARISON ---")
    print(f"Wins / Losses     : Recalc {len(wins)}/{len(losses)} | Reported {rep['performance']['wins']}/{rep['performance']['losses']}")
    print(f"Win Rate          : Recalc {win_rate:.2f}% | Reported {rep['performance']['win_rate_pct']:.2f}%")
    print(f"Win Rate 95% CI   : Recalc [{ci_low:.2f}%, {ci_high:.2f}%] | Reported [{rep['performance']['win_rate_95_ci'][0]:.2f}%, {rep['performance']['win_rate_95_ci'][1]:.2f}%]")
    print(f"Profit Factor     : Recalc {profit_factor:.2f} | Reported {rep['performance']['profit_factor']:.2f}")
    print(f"Net Expectancy (R): Recalc {expectancy_r:+.3f} R | Reported {rep['performance']['expectancy_r']:+.3f} R")
    print(f"Net Expectancy (%): Recalc {expectancy_pct:+.3f}% | Reported {rep['performance']['expectancy_return_pct']:+.3f}%")
    print(f"Max Drawdown (R)  : Recalc -{max_dd_r:.2f} R | Reported {rep['performance']['max_drawdown_r']:.2f} R")
    print(f"Max Drawdown (%)  : Recalc -{max_dd_pct:.2f}%")

if __name__ == "__main__":
    audit()

