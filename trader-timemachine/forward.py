#!/usr/bin/env python3
"""Binance Spot Forward-Testing Engine (Pure Python 3.8+ Standard Library).

Strict Zero-Lookahead Bar-by-Bar Forward Simulation & Live Testnet Tracking.
Freezes strategy parameters, logs every signal (taken, skipped, or rejected),
deducts exact Binance taker fees (0.10%) and adverse slippage (0.05%),
and generates comprehensive performance and comparison reports.
"""

import argparse
import datetime
import json
import math
import os
import sys
import time
from decimal import Decimal

# Root setup
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from binance_client import (
    BinanceClient,
    quantize_price,
    quantize_quantity,
    parse_exchange_filters,
    detect_gaps,
    split_windows_at_gaps,
)
from timemachine import _calc_stats

FORWARD_LOG_PATH = "trader/forward_log.json"
FORWARD_REPORT_PATH = "trader/forward_report.json"

# FROZEN STRATEGY PARAMETERS (VERSION: BINANCE_SPOT_ORB_20D_V1)
FROZEN_SPEC = {
    "strategy_id": "BINANCE_SPOT_ORB_20D_V1",
    "version": "1.0.0",
    "market": "spot",
    "interval": "15m",
    "lookback_period": 20,
    "target_r_multiple": 2.0,
    "max_holding_bars": 16,     # ~4 hours for 15m candles
    "stop_loss_pct_floor": 0.02, # max 2% risk from entry if low20 is too close
    "taker_fee_rate": 0.0010,   # 0.10% Binance Spot standard taker fee
    "slippage_rate": 0.0005,    # 0.05% adverse execution slippage per leg
    "max_concurrent_positions": 1,
}


def run_forward_test(symbol="SOLUSDT", days=30, interval="15m", testnet=True,
                     log_path=FORWARD_LOG_PATH, report_path=FORWARD_REPORT_PATH):
    """Execute a strict zero-lookahead chronological forward test on closed candles."""
    client = BinanceClient(product="spot", testnet=testnet)
    print("=" * 70)
    print("BINANCE FORWARD-TESTING ENGINE")
    print("=" * 70)
    print("Symbol / Market : %s (SPOT)" % symbol.upper())
    print("Strategy Spec   : %s (v%s)" % (FROZEN_SPEC["strategy_id"], FROZEN_SPEC["version"]))
    print("Testing Horizon : Last %d days | Interval: %s" % (days, interval))
    print("Cost Model      : Taker fee %.2f%% + Slippage %.2f%% per leg"
          % (FROZEN_SPEC["taker_fee_rate"] * 100, FROZEN_SPEC["slippage_rate"] * 100))
    print("-" * 70)

    # Fetch closed historical klines dynamically backward from now
    res = client.get_klines_backward_paginated(symbol, interval=interval, days=days)
    klines = res.get("klines", [])
    if len(klines) < FROZEN_SPEC["lookback_period"] + 10:
        raise ValueError("Insufficient candle history fetched: %d candles" % len(klines))

    interval_ms = 15 * 60 * 1000 if interval == "15m" else 5 * 60 * 1000
    windows = split_windows_at_gaps(klines, interval_ms)

    # Fetch exchange filters for realistic lot/tick validation
    ex_info = client.get_exchange_info(symbol)
    sym_info = next((s for s in ex_info.get("symbols", []) if s.get("symbol") == symbol.upper()), {})
    filters = parse_exchange_filters(sym_info)

    all_signals = []
    completed_trades = []
    open_trade = None
    period = FROZEN_SPEC["lookback_period"]
    fee_rate = FROZEN_SPEC["taker_fee_rate"]
    slippage = FROZEN_SPEC["slippage_rate"]

    print("Evaluating %d candle(s) across %d continuous data window(s)..." % (len(klines), len(windows)))

    for win in windows:
        if len(win) < period + 5:
            continue
        bars = []
        for k in win:
            bars.append({
                "t": int(k[0]) // 1000,
                "o": float(k[1]),
                "h": float(k[2]),
                "l": float(k[3]),
                "c": float(k[4]),
                "v": float(k[5]),
                "close_time": int(k[6]) // 1000
            })

        w_len = len(bars)
        # Bar-by-bar chronological forward walk (zero lookahead)
        for i in range(period, w_len):
            current_bar = bars[i]

            # 1. Manage existing open trade before evaluating new signals
            if open_trade is not None:
                # Check stop and target within current bar (stop checked first for conservatism)
                stop_hit = current_bar["l"] <= open_trade["stop_px"]
                target_hit = current_bar["h"] >= open_trade["target_px"]
                bars_held = i - open_trade["entry_bar_idx"]
                time_stop_hit = bars_held >= FROZEN_SPEC["max_holding_bars"]

                exit_px = None
                exit_type = None

                if stop_hit:
                    exit_px = open_trade["stop_px"] * (1.0 - slippage)
                    exit_type = "stop"
                elif target_hit:
                    exit_px = open_trade["target_px"] * (1.0 - slippage)
                    exit_type = "target"
                elif time_stop_hit:
                    exit_px = current_bar["c"] * (1.0 - slippage)
                    exit_type = "time_stop"

                if exit_px is not None:
                    # Close trade
                    entry_px = open_trade["entry_px"]
                    risk = open_trade["risk"]
                    entry_fee = entry_px * fee_rate
                    exit_fee = exit_px * fee_rate
                    gross_pnl = exit_px - entry_px
                    net_pnl = gross_pnl - (entry_fee + exit_fee)
                    net_return_pct = (net_pnl / entry_px) * 100.0
                    r_mult = net_pnl / risk if risk > 0 else 0.0

                    trade_record = {
                        "id": open_trade["id"],
                        "entry_time": open_trade["entry_time"],
                        "exit_time": current_bar["t"],
                        "entry_px": round(entry_px, 4),
                        "exit_px": round(exit_px, 4),
                        "stop_px": round(open_trade["stop_px"], 4),
                        "target_px": round(open_trade["target_px"], 4),
                        "type": exit_type,
                        "bars_held": bars_held,
                        "net_pnl": round(net_pnl, 4),
                        "net_return_pct": round(net_return_pct, 4),
                        "r": round(r_mult, 4),
                        "win": r_mult > 0
                    }
                    completed_trades.append(trade_record)
                    open_trade = None

            # 2. Evaluate breakout signal on strictly closed bar i
            lookback_bars = bars[i - period:i]
            high_20 = max(b["h"] for b in lookback_bars)
            low_20 = min(b["l"] for b in lookback_bars)

            # Signal condition: current close > high_20 and prior close <= high_20
            is_breakout = (current_bar["c"] > high_20) and (bars[i - 1]["c"] <= high_20)

            if is_breakout:
                signal_record = {
                    "timestamp": current_bar["t"],
                    "datetime_utc": datetime.datetime.fromtimestamp(current_bar["t"], datetime.timezone.utc).isoformat(),
                    "close": current_bar["c"],
                    "high_20": high_20,
                    "low_20": low_20,
                    "decision": "TAKEN"
                }

                if open_trade is not None:
                    signal_record["decision"] = "SKIPPED_MAX_EXPOSURE"
                    signal_record["reason"] = "Already in active position %s" % open_trade["id"]
                    all_signals.append(signal_record)
                    continue

                # Sizing & parameters
                entry_px = current_bar["c"] * (1.0 + slippage)
                raw_stop = max(low_20, entry_px * (1.0 - FROZEN_SPEC["stop_loss_pct_floor"]))
                risk = entry_px - raw_stop

                if risk <= 0:
                    signal_record["decision"] = "REJECTED_ZERO_RISK"
                    all_signals.append(signal_record)
                    continue

                target_px = entry_px + (FROZEN_SPEC["target_r_multiple"] * risk)

                # Filter checks
                q_entry = quantize_price(entry_px, filters["tick_size"])
                q_stop = quantize_price(raw_stop, filters["tick_size"])
                q_target = quantize_price(target_px, filters["tick_size"])

                if q_entry is None or q_stop is None or q_target is None:
                    signal_record["decision"] = "REJECTED_FILTER"
                    all_signals.append(signal_record)
                    continue

                trade_id = "fwd_%s_%d" % (symbol.lower(), current_bar["t"])
                signal_record["trade_id"] = trade_id
                signal_record["entry_px"] = float(q_entry)
                signal_record["stop_px"] = float(q_stop)
                signal_record["target_px"] = float(q_target)
                signal_record["risk"] = float(risk)
                all_signals.append(signal_record)

                # Open trade
                open_trade = {
                    "id": trade_id,
                    "entry_time": current_bar["t"],
                    "entry_bar_idx": i,
                    "entry_px": float(q_entry),
                    "stop_px": float(q_stop),
                    "target_px": float(q_target),
                    "risk": float(risk)
                }

    # Calculate statistics using standardized audit function
    stats = _calc_stats(completed_trades)

    # Load 60-day backtest baseline for comparison if present
    backtest_comparison = {}
    if os.path.exists("trader/timemachine.json"):
        try:
            with open("trader/timemachine.json", "r", encoding="utf-8") as fh:
                tm_doc = json.load(fh)
                oos = tm_doc.get("out_of_sample", {})
                backtest_comparison = {
                    "backtest_oos_trades": oos.get("total_trades"),
                    "backtest_oos_win_rate": oos.get("win_rate_pct"),
                    "backtest_oos_expectancy_r": oos.get("expectancy_r"),
                    "backtest_oos_profit_factor": oos.get("profit_factor"),
                    "backtest_oos_max_drawdown_r": oos.get("max_drawdown_r")
                }
        except Exception:
            pass

    report_data = {
        "frozen_spec": FROZEN_SPEC,
        "symbol": symbol.upper(),
        "days_evaluated": days,
        "candles_analyzed": len(klines),
        "total_signals_detected": len(all_signals),
        "signals_taken": sum(1 for s in all_signals if s.get("decision") == "TAKEN"),
        "signals_skipped_max_exposure": sum(1 for s in all_signals if s.get("decision") == "SKIPPED_MAX_EXPOSURE"),
        "signals_rejected_filters": sum(1 for s in all_signals if "REJECTED" in s.get("decision", "")),
        "performance": stats,
        "backtest_comparison": backtest_comparison,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    # Write logs
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        json.dump({"signals": all_signals, "trades": completed_trades}, fh, indent=2)

    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report_data, fh, indent=2)

    # Print Terminal Summary Report
    print("\n" + "=" * 70)
    print("FORWARD-TESTING PERFORMANCE REPORT (FROZEN SPEC: %s)" % FROZEN_SPEC["strategy_id"])
    print("=" * 70)
    print("Signals Detected   : %d (Taken: %d | Skipped: %d | Rejected: %d)"
          % (report_data["total_signals_detected"], report_data["signals_taken"],
             report_data["signals_skipped_max_exposure"], report_data["signals_rejected_filters"]))
    print("Completed Trades   : %d" % stats["total_trades"])
    print("Win Rate           : %.2f%% (95%% CI: [%.2f%%, %.2f%%])"
          % (stats["win_rate_pct"], stats["win_rate_95_ci"][0], stats["win_rate_95_ci"][1]))
    print("Net Expectancy (R) : %+.3f R per trade" % stats["expectancy_r"])
    print("Net Expectancy (%%) : %+.3f%% per trade" % stats["expectancy_return_pct"])
    print("Profit Factor      : %.2f" % stats["profit_factor"])
    print("Max Drawdown       : -%.2f R" % stats["max_drawdown_r"])

    if backtest_comparison:
        print("\n" + "-" * 70)
        print("COMPARISON: FORWARD-TEST vs. 60-DAY BACKTEST (OUT-OF-SAMPLE)")
        print("-" * 70)
        print("Metric                Forward Test (30d)    Backtest (OOS)")
        print("%-22s %18.2f%% %17.2f%%" % ("Win Rate", stats["win_rate_pct"], backtest_comparison.get("backtest_oos_win_rate", 0.0)))
        print("%-22s %17.3f R %16.3f R" % ("Net Expectancy", stats["expectancy_r"], backtest_comparison.get("backtest_oos_expectancy_r", 0.0)))
        print("%-22s %18.2f   %17.2f" % ("Profit Factor", stats["profit_factor"], backtest_comparison.get("backtest_oos_profit_factor", 0.0)))
        print("%-22s %17.2f R %16.2f R" % ("Max Drawdown", stats["max_drawdown_r"], backtest_comparison.get("backtest_oos_max_drawdown_r", 0.0)))
        print("-" * 70)

    print("Audit Log Saved    : %s" % log_path)
    print("Report Document    : %s" % report_path)
    print("=" * 70)
    return report_data


def main(argv=None):
    ap = argparse.ArgumentParser(description="Binance Forward-Testing Engine")
    ap.add_argument("--symbol", default="SOLUSDT", help="Trading pair symbol (default: SOLUSDT)")
    ap.add_argument("--days", type=int, default=30, help="Forward testing sample days (default: 30)")
    ap.add_argument("--interval", default="15m", choices=["5m", "15m", "1h"], help="Candle timeframe")
    ap.add_argument("--paper", action="store_true", default=True, help="Paper execution on closed candles")
    ap.add_argument("--log-path", default=FORWARD_LOG_PATH, help="Path for signals & trades JSON")
    ap.add_argument("--report-path", default=FORWARD_REPORT_PATH, help="Path for report summary JSON")
    args = ap.parse_args(argv)

    run_forward_test(symbol=args.symbol, days=args.days, interval=args.interval,
                     testnet=args.paper, log_path=args.log_path, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
