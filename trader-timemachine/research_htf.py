#!/usr/bin/env python3
"""Binance Higher-Timeframe (1h & 4h) Strategy Research Suite (V2).

Pure Python 3.8+ Standard Library.
Investigates 1h and 4h breakout strategies on BTCUSDT and SOLUSDT.
Implements:
1. Frozen baseline (15m ORB 20) comparison
2. 20-bar breakout with trend confirmation (EMA 50)
3. 20-bar breakout with ATR-based stop-loss and a fixed 2R target
4. Breakout with trend confirmation plus volume confirmation (EMA 50 + RVOL >= 1.5x)
5. Chronological 3-way partition: Train (60%), Validation (20%), Untouched Test (20%)
6. Market regime breakdown (Bullish Trend, Bearish Trend, Range/Chop)
7. Full sensitivity tests across transaction costs and parameter variations.

Research & simulation only. Zero live trading, no broker modifications.
"""

import argparse
import datetime
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from binance_client import (
    BinanceClient,
    quantize_price,
    detect_gaps,
    split_windows_at_gaps,
)

FROZEN_BASELINE_15M = {
    "strategy_id": "BINANCE_SPOT_ORB_20D_V1",
    "version": "1.0.0",
    "timeframe": "15m",
    "lookback_period": 20,
    "target_r": 2.0,
    "max_holding_bars": 16,
    "stop_loss_pct_floor": 0.02,
    "fee_rate": 0.0010,
    "slippage": 0.0005,
    "max_concurrent": 1
}


# --------------------------------------------------------------------------
# Statistical Utilities
# --------------------------------------------------------------------------
def wilson_ci(wins, total, z=1.95996):
    """Wilson score confidence interval for binomial proportion."""
    if total <= 0:
        return [0.0, 0.0]
    p = wins / total
    denom = 1.0 + (z**2 / total)
    centre = (p + (z**2 / (2.0 * total))) / denom
    spread = z * math.sqrt((p * (1.0 - p) / total) + (z**2 / (4.0 * total**2))) / denom
    ci_low = max(0.0, (centre - spread) * 100.0)
    ci_high = min(100.0, (centre + spread) * 100.0)
    return [round(ci_low, 2), round(ci_high, 2)]


def calc_metrics(trades, fee_rate, slippage):
    """Calculate comprehensive performance metrics from completed trade records."""
    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "win_rate_95_ci": [0.0, 0.0],
            "gross_expectancy_r": 0.0,
            "net_expectancy_r": 0.0,
            "gross_expectancy_pct": 0.0,
            "net_expectancy_pct": 0.0,
            "total_gross_pnl_pct": 0.0,
            "total_net_pnl_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_r": 0.0,
            "max_drawdown_pct": 0.0,
            "total_fee_drag_r": 0.0,
            "avg_fee_drag_r": 0.0
        }

    wins = [t for t in trades if t["net_r"] > 0]
    losses = [t for t in trades if t["net_r"] <= 0]
    win_rate = round((len(wins) / total) * 100.0, 2)
    ci = wilson_ci(len(wins), total)

    gross_exp_r = round(sum(t["gross_r"] for t in trades) / total, 3)
    net_exp_r = round(sum(t["net_r"] for t in trades) / total, 3)
    gross_exp_pct = round(sum(t["gross_return_pct"] for t in trades) / total, 3)
    net_exp_pct = round(sum(t["net_return_pct"] for t in trades) / total, 3)

    tot_gross_pct = round(sum(t["gross_return_pct"] for t in trades), 2)
    tot_net_pct = round(sum(t["net_return_pct"] for t in trades), 2)

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    # Max Drawdown in R and %
    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in trades:
        cum_r += t["net_r"]
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd

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

    fee_drag_r = sum((t["entry_fee"] + t["exit_fee"]) / t["risk"] for t in trades if t["risk"] > 0)
    avg_fee_drag_r = round(fee_drag_r / total, 3) if total > 0 else 0.0

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "win_rate_95_ci": ci,
        "gross_expectancy_r": gross_exp_r,
        "net_expectancy_r": net_exp_r,
        "gross_expectancy_pct": gross_exp_pct,
        "net_expectancy_pct": net_exp_pct,
        "total_gross_pnl_pct": tot_gross_pct,
        "total_net_pnl_pct": tot_net_pct,
        "profit_factor": profit_factor,
        "max_drawdown_r": round(max_dd_r, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "total_fee_drag_r": round(fee_drag_r, 2),
        "avg_fee_drag_r": avg_fee_drag_r
    }


# --------------------------------------------------------------------------
# Indicators (Pure Standard Library)
# --------------------------------------------------------------------------
def compute_ema(series, period):
    n = len(series)
    ema = [0.0] * n
    if n < period:
        return ema
    sma = sum(series[:period]) / period
    ema[period - 1] = sma
    multiplier = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema[i] = (series[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


def compute_sma(series, period):
    n = len(series)
    sma = [0.0] * n
    if n < period:
        return sma
    running_sum = sum(series[:period])
    sma[period - 1] = running_sum / period
    for i in range(period, n):
        running_sum += series[i] - series[i - period]
        sma[i] = running_sum / period
    return sma


def compute_atr(bars, period=14):
    n = len(bars)
    atr = [0.0] * n
    if n < period + 1:
        return atr
    tr = [0.0] * n
    for i in range(1, n):
        h = bars[i]["h"]
        l = bars[i]["l"]
        prev_c = bars[i - 1]["c"]
        tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
    sma_tr = sum(tr[1:period + 1]) / period
    atr[period] = sma_tr
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def classify_market_regime(bars, idx, ema_period=50):
    """Classify regime at bar idx into BULL_TREND, BEAR_TREND, or RANGE_CHOP."""
    if idx < ema_period + 5:
        return "UNKNOWN"
    closes = [b["c"] for b in bars[:idx + 1]]
    ema = compute_ema(closes, ema_period)
    curr_c = bars[idx]["c"]
    curr_ema = ema[idx]
    prev_ema = ema[idx - 5]
    slope = (curr_ema - prev_ema) / prev_ema if prev_ema > 0 else 0.0

    if curr_c > curr_ema and slope > 0.002:
        return "BULL_TREND"
    elif curr_c < curr_ema and slope < -0.002:
        return "BEAR_TREND"
    else:
        return "RANGE_CHOP"


# --------------------------------------------------------------------------
# Simulation Engine
# --------------------------------------------------------------------------
def simulate_htf_strategy(bars, lookback=20, stop_type="range_low", atr_stop_mult=2.0,
                          target_r=2.0, max_holding_bars=24, use_trend=False,
                          use_volume=False, min_rvol=1.5, fee_rate=0.0010,
                          slippage=0.0005, tick_size="0.01"):
    """Walk forward bar-by-bar with zero lookahead on completed candles."""
    n = len(bars)
    if n < lookback + 10:
        return {"signals": [], "trades": [], "metrics": calc_metrics([], fee_rate, slippage), "regimes": {}}

    closes = [b["c"] for b in bars]
    vols = [b["v"] for b in bars]
    ema_50 = compute_ema(closes, 50) if use_trend else None
    vol_sma = compute_sma(vols, 20) if use_volume else None
    atr_14 = compute_atr(bars, 14)

    completed_trades = []
    open_trade = None
    all_signals = []

    for i in range(max(lookback, 50 if use_trend else 20), n):
        current_bar = bars[i]

        # 1. Manage active position (stop evaluated before target for conservatism)
        if open_trade is not None:
            stop_hit = current_bar["l"] <= open_trade["stop_px"]
            target_hit = current_bar["h"] >= open_trade["target_px"]
            bars_held = i - open_trade["entry_bar_idx"]
            time_stop_hit = bars_held >= max_holding_bars

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
                entry_px = open_trade["entry_px"]
                risk = open_trade["risk"]
                entry_fee = entry_px * fee_rate
                exit_fee = exit_px * fee_rate
                gross_pnl = exit_px - entry_px
                net_pnl = gross_pnl - (entry_fee + exit_fee)
                gross_return_pct = (gross_pnl / entry_px) * 100.0
                net_return_pct = (net_pnl / entry_px) * 100.0
                gross_r = gross_pnl / risk if risk > 0 else 0.0
                net_r = net_pnl / risk if risk > 0 else 0.0

                completed_trades.append({
                    "id": open_trade["id"],
                    "entry_time": open_trade["entry_time"],
                    "exit_time": current_bar["t"],
                    "entry_px": round(entry_px, 4),
                    "exit_px": round(exit_px, 4),
                    "stop_px": round(open_trade["stop_px"], 4),
                    "target_px": round(open_trade["target_px"], 4),
                    "risk": round(risk, 4),
                    "type": exit_type,
                    "bars_held": bars_held,
                    "entry_fee": round(entry_fee, 4),
                    "exit_fee": round(exit_fee, 4),
                    "gross_pnl": round(gross_pnl, 4),
                    "net_pnl": round(net_pnl, 4),
                    "gross_return_pct": round(gross_return_pct, 4),
                    "net_return_pct": round(net_return_pct, 4),
                    "gross_r": round(gross_r, 4),
                    "net_r": round(net_r, 4),
                    "regime": open_trade["regime"]
                })
                open_trade = None

        # 2. Breakout Signal Detection on Bar i Close
        lookback_bars = bars[i - lookback:i]
        high_n = max(b["h"] for b in lookback_bars)
        low_n = min(b["l"] for b in lookback_bars)

        is_breakout = (current_bar["c"] > high_n) and (bars[i - 1]["c"] <= high_n)

        if is_breakout:
            sig = {
                "bar_idx": i,
                "timestamp": current_bar["t"],
                "close": current_bar["c"],
                "decision": "TAKEN"
            }

            # Position concurrency limit: 1
            if open_trade is not None:
                sig["decision"] = "SKIPPED_MAX_EXPOSURE"
                all_signals.append(sig)
                continue

            # Filter checks
            if use_trend:
                if current_bar["c"] <= ema_50[i]:
                    sig["decision"] = "REJECTED_TREND_FILTER"
                    all_signals.append(sig)
                    continue

            if use_volume:
                sma_v = vol_sma[i]
                rvol = current_bar["v"] / sma_v if sma_v > 0 else 0.0
                if rvol < min_rvol:
                    sig["decision"] = "REJECTED_VOLUME_FILTER"
                    all_signals.append(sig)
                    continue

            entry_px = current_bar["c"] * (1.0 + slippage)

            # Determine Stop Loss Price
            if stop_type == "atr":
                atr_val = atr_14[i]
                raw_stop = entry_px - (atr_stop_mult * atr_val)
            else:
                # range low with 2% floor
                raw_stop = max(low_n, entry_px * 0.98)

            risk = entry_px - raw_stop
            if risk <= 0:
                sig["decision"] = "REJECTED_ZERO_RISK"
                all_signals.append(sig)
                continue

            target_px = entry_px + (target_r * risk)

            q_entry = quantize_price(entry_px, tick_size)
            q_stop = quantize_price(raw_stop, tick_size)
            q_target = quantize_price(target_px, tick_size)

            if q_entry is None or q_stop is None or q_target is None:
                sig["decision"] = "REJECTED_QUANTIZE"
                all_signals.append(sig)
                continue

            regime = classify_market_regime(bars, i)

            open_trade = {
                "id": f"htf_{current_bar['t']}",
                "entry_time": current_bar["t"],
                "entry_bar_idx": i,
                "entry_px": float(q_entry),
                "stop_px": float(q_stop),
                "target_px": float(q_target),
                "risk": float(risk),
                "regime": regime
            }
            sig["trade_id"] = open_trade["id"]
            sig["regime"] = regime
            all_signals.append(sig)

    metrics = calc_metrics(completed_trades, fee_rate, slippage)

    # Regime breakdown
    regimes = {}
    for r_name in ["BULL_TREND", "BEAR_TREND", "RANGE_CHOP"]:
        sub_trades = [t for t in completed_trades if t.get("regime") == r_name]
        regimes[r_name] = calc_metrics(sub_trades, fee_rate, slippage)

    return {
        "signals": all_signals,
        "trades": completed_trades,
        "metrics": metrics,
        "regimes": regimes
    }


# --------------------------------------------------------------------------
# Data Loading & Partitioning
# --------------------------------------------------------------------------
def load_bars(symbol="BTCUSDT", interval="1h", days=180):
    """Load cached klines or fetch directly from Binance API."""
    cache_path = os.path.join(ROOT, "trader", f"cached_klines_{symbol.lower()}_{interval}_{days}d.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            res = json.load(f)
            klines = res.get("klines", [])
            print(f"Loaded {len(klines)} klines from local cache {cache_path}")
    else:
        print(f"Fetching {days} days of {symbol} {interval} klines from Binance API...")
        client = BinanceClient(product="spot")
        res = client.get_klines_backward_paginated(symbol, interval=interval, days=days)
        klines = res.get("klines", [])
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(res, f)
        print(f"Cached {len(klines)} klines to {cache_path}")

    bars = []
    for k in klines:
        bars.append({
            "t": int(k[0]) // 1000,
            "o": float(k[1]),
            "h": float(k[2]),
            "l": float(k[3]),
            "c": float(k[4]),
            "v": float(k[5]),
            "close_time": int(k[6]) // 1000
        })
    return bars


def run_htf_research(symbols=("BTCUSDT", "SOLUSDT"), timeframes=("1h", "4h"), days=180):
    """Execute complete HTF research suite across symbols, timeframes, and variants."""
    all_experiments = []

    # Predefined Configurations (as specified in milestone)
    configs = [
        {
            "id": "BASELINE_RAW_20",
            "name": "Raw 20-Bar Breakout (Unfiltered)",
            "params": {"lookback": 20, "stop_type": "range_low", "target_r": 2.0, "use_trend": False, "use_volume": False}
        },
        {
            "id": "V2_TREND_EMA50",
            "name": "20-Bar Breakout + Trend Confirmation (Close > EMA 50)",
            "params": {"lookback": 20, "stop_type": "range_low", "target_r": 2.0, "use_trend": True, "use_volume": False}
        },
        {
            "id": "V2_ATR_STOP_2R",
            "name": "20-Bar Breakout + ATR Stop (2.0x ATR14) + Fixed 2R Target",
            "params": {"lookback": 20, "stop_type": "atr", "atr_stop_mult": 2.0, "target_r": 2.0, "use_trend": False, "use_volume": False}
        },
        {
            "id": "V2_TREND_PLUS_VOLUME",
            "name": "Breakout + Trend (EMA 50) + Volume Confirmation (RVOL >= 1.5x)",
            "params": {"lookback": 20, "stop_type": "range_low", "target_r": 2.0, "use_trend": True, "use_volume": True, "min_rvol": 1.5}
        },
        {
            "id": "V2_TREND_ATR_VOLUME",
            "name": "Combined: Trend (EMA 50) + ATR Stop (2.0x) + RVOL (1.5x)",
            "params": {"lookback": 20, "stop_type": "atr", "atr_stop_mult": 2.0, "target_r": 2.0, "use_trend": True, "use_volume": True, "min_rvol": 1.5}
        }
    ]

    for sym in symbols:
        for tf in timeframes:
            print("\n" + "=" * 90)
            print(f"RUNNING HTF EXPERIMENTS FOR {sym} ({tf}) OVER {days} DAYS")
            print("=" * 90)

            bars = load_bars(symbol=sym, interval=tf, days=days)
            n_bars = len(bars)
            if n_bars < 200:
                print(f"Skipping {sym} {tf}: Insufficient candles ({n_bars})")
                continue

            # Chronological 3-way partition: Train (60%), Validation (20%), Untouched Test (20%)
            idx_train = int(n_bars * 0.60)
            idx_val = int(n_bars * 0.80)

            bars_train = bars[:idx_train]
            bars_val = bars[idx_train:idx_val]
            bars_test = bars[idx_val:]  # Untouched Out-of-Sample

            bnh_train = ((bars_train[-1]["c"] - bars_train[0]["o"]) / bars_train[0]["o"]) * 100.0
            bnh_val = ((bars_val[-1]["c"] - bars_val[0]["o"]) / bars_val[0]["o"]) * 100.0
            bnh_test = ((bars_test[-1]["c"] - bars_test[0]["o"]) / bars_test[0]["o"]) * 100.0
            bnh_overall = ((bars[-1]["c"] - bars[0]["o"]) / bars[0]["o"]) * 100.0

            print(f"Data Split: Train (60%): {len(bars_train)} bars (B&H: {bnh_train:+.2f}%) | Val (20%): {len(bars_val)} bars (B&H: {bnh_val:+.2f}%) | Test (20%): {len(bars_test)} bars (B&H: {bnh_test:+.2f}%)")
            print(f"Overall Buy-and-Hold ({days}d): {bnh_overall:+.2f}%")
            print("-" * 90)

            holding_bars = 24 if tf == "1h" else 18

            for cfg in configs:
                cid = cfg["id"]
                p = cfg["params"]

                # 1. Train
                res_train = simulate_htf_strategy(
                    bars_train, lookback=p["lookback"], stop_type=p["stop_type"],
                    atr_stop_mult=p.get("atr_stop_mult", 2.0), target_r=p["target_r"],
                    max_holding_bars=holding_bars, use_trend=p["use_trend"],
                    use_volume=p["use_volume"], min_rvol=p.get("min_rvol", 1.5)
                )

                # 2. Validation
                res_val = simulate_htf_strategy(
                    bars_val, lookback=p["lookback"], stop_type=p["stop_type"],
                    atr_stop_mult=p.get("atr_stop_mult", 2.0), target_r=p["target_r"],
                    max_holding_bars=holding_bars, use_trend=p["use_trend"],
                    use_volume=p["use_volume"], min_rvol=p.get("min_rvol", 1.5)
                )

                # 3. Untouched Test (Out-of-Sample)
                res_test = simulate_htf_strategy(
                    bars_test, lookback=p["lookback"], stop_type=p["stop_type"],
                    atr_stop_mult=p.get("atr_stop_mult", 2.0), target_r=p["target_r"],
                    max_holding_bars=holding_bars, use_trend=p["use_trend"],
                    use_volume=p["use_volume"], min_rvol=p.get("min_rvol", 1.5)
                )

                # 4. Overall Window
                res_overall = simulate_htf_strategy(
                    bars, lookback=p["lookback"], stop_type=p["stop_type"],
                    atr_stop_mult=p.get("atr_stop_mult", 2.0), target_r=p["target_r"],
                    max_holding_bars=holding_bars, use_trend=p["use_trend"],
                    use_volume=p["use_volume"], min_rvol=p.get("min_rvol", 1.5)
                )

                all_experiments.append({
                    "symbol": sym,
                    "timeframe": tf,
                    "variant_id": cid,
                    "variant_name": cfg["name"],
                    "params": p,
                    "benchmarks": {
                        "bnh_train_pct": round(bnh_train, 2),
                        "bnh_val_pct": round(bnh_val, 2),
                        "bnh_test_pct": round(bnh_test, 2),
                        "bnh_overall_pct": round(bnh_overall, 2),
                    },
                    "train_metrics": res_train["metrics"],
                    "val_metrics": res_val["metrics"],
                    "test_metrics": res_test["metrics"],
                    "overall_metrics": res_overall["metrics"],
                    "regimes": res_overall["regimes"]
                })

    # Sensitivity Analysis on Selected Top Candidates
    sensitivity_results = run_sensitivity_tests(symbols, timeframes, days)

    doc = {
        "title": "Binance Higher-Timeframe Strategy Research Report (V2)",
        "symbols": list(symbols),
        "timeframes": list(timeframes),
        "evaluation_days": days,
        "frozen_baseline_15m": FROZEN_BASELINE_15M,
        "experiments": all_experiments,
        "sensitivity": sensitivity_results,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    out_file = os.path.join(ROOT, "trader", "htf_experiment_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"\nAll HTF experiment results saved to {out_file}")

    print_htf_summary(doc)
    return doc


def run_sensitivity_tests(symbols, timeframes, days):
    """Run parameter and transaction cost sensitivity tests."""
    sensitivity_cases = []
    # Test on BTCUSDT 1h and SOLUSDT 1h for the top trend + ATR strategy
    fee_tiers = [0.00075, 0.0010, 0.0015]
    slippage_tiers = [0.0002, 0.0005, 0.0010]
    lookbacks = [15, 20, 25]
    atr_mults = [1.5, 2.0, 2.5]
    target_rs = [1.5, 2.0, 2.5]

    for sym in ["BTCUSDT", "SOLUSDT"]:
        bars = load_bars(symbol=sym, interval="1h", days=days)
        if len(bars) < 200:
            continue

        # 1. Cost Sensitivity (holding params constant at lookback=20, atr=2.0, target=2.0)
        cost_scenarios = []
        for f in fee_tiers:
            for s in slippage_tiers:
                res = simulate_htf_strategy(
                    bars, lookback=20, stop_type="atr", atr_stop_mult=2.0, target_r=2.0,
                    use_trend=True, use_volume=True, fee_rate=f, slippage=s
                )
                m = res["metrics"]
                cost_scenarios.append({
                    "fee_pct": f * 100.0,
                    "slippage_pct": s * 100.0,
                    "trades": m["total_trades"],
                    "win_rate": m["win_rate_pct"],
                    "net_exp_r": m["net_expectancy_r"],
                    "pf": m["profit_factor"],
                    "net_ret_pct": m["total_net_pnl_pct"]
                })

        # 2. Parameter Sensitivity (holding costs at standard 0.10% / 0.05%)
        param_scenarios = []
        for lb in lookbacks:
            for atr_m in atr_mults:
                for tr in target_rs:
                    res = simulate_htf_strategy(
                        bars, lookback=lb, stop_type="atr", atr_stop_mult=atr_m, target_r=tr,
                        use_trend=True, use_volume=True, fee_rate=0.0010, slippage=0.0005
                    )
                    m = res["metrics"]
                    param_scenarios.append({
                        "lookback": lb,
                        "atr_mult": atr_m,
                        "target_r": tr,
                        "trades": m["total_trades"],
                        "win_rate": m["win_rate_pct"],
                        "net_exp_r": m["net_expectancy_r"],
                        "pf": m["profit_factor"],
                        "max_dd_r": m["max_drawdown_r"],
                        "net_ret_pct": m["total_net_pnl_pct"]
                    })

        sensitivity_cases.append({
            "symbol": sym,
            "timeframe": "1h",
            "cost_matrix": cost_scenarios,
            "parameter_matrix": param_scenarios
        })

    return sensitivity_cases


def print_htf_summary(doc):
    """Print structured terminal comparison tables."""
    print("\n" + "=" * 120)
    print("HIGHER-TIMEFRAME (1H & 4H) STRATEGY RESEARCH: UNTOUCHED TEST SET RESULTS")
    print("=" * 120)
    header = f"{'Symbol':<8} | {'TF':<4} | {'Variant':<25} | {'Test N':<6} | {'Test Win%':<9} | {'Gross R':<8} | {'Net R':<8} | {'PF':<5} | {'MDD (R)':<8} | {'Net Ret':<8} | {'B&H':<8}"
    print(header)
    print("-" * 120)

    for exp in doc["experiments"]:
        t = exp["test_metrics"]
        b = exp["benchmarks"]
        ci_str = f"{t['win_rate_pct']:.1f}%"
        print(f"{exp['symbol']:<8} | {exp['timeframe']:<4} | {exp['variant_id']:<25} | {t['total_trades']:<6} | {ci_str:<9} | {t['gross_expectancy_r']:+7.3f}R | {t['net_expectancy_r']:+7.3f}R | {t['profit_factor']:<5.2f} | -{t['max_drawdown_r']:<7.2f}R | {t['total_net_pnl_pct']:+7.2f}% | {b['bnh_test_pct']:+7.2f}%")

    print("=" * 120)

    print("\n" + "=" * 120)
    print("FULL 180-DAY OVERALL PERFORMANCE & REGIME BREAKDOWN")
    print("=" * 120)
    header_all = f"{'Symbol':<8} | {'TF':<4} | {'Variant':<25} | {'Trades':<6} | {'Win% (95% CI)':<20} | {'Gross R':<8} | {'Net R':<8} | {'PF':<5} | {'MDD (R)':<8} | {'Bull R':<8} | {'Bear R':<8} | {'Chop R':<8}"
    print(header_all)
    print("-" * 120)

    for exp in doc["experiments"]:
        o = exp["overall_metrics"]
        regs = exp["regimes"]
        ci_str = f"{o['win_rate_pct']:.1f}% [{o['win_rate_95_ci'][0]:.1f}-{o['win_rate_95_ci'][1]:.1f}%]"
        bull_r = regs.get("BULL_TREND", {}).get("net_expectancy_r", 0.0)
        bear_r = regs.get("BEAR_TREND", {}).get("net_expectancy_r", 0.0)
        chop_r = regs.get("RANGE_CHOP", {}).get("net_expectancy_r", 0.0)
        print(f"{exp['symbol']:<8} | {exp['timeframe']:<4} | {exp['variant_id']:<25} | {o['total_trades']:<6} | {ci_str:<20} | {o['gross_expectancy_r']:+7.3f}R | {o['net_expectancy_r']:+7.3f}R | {o['profit_factor']:<5.2f} | -{o['max_drawdown_r']:<7.2f}R | {bull_r:+7.3f}R | {bear_r:+7.3f}R | {chop_r:+7.3f}R")

    print("=" * 120)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTF Strategy Research")
    parser.add_argument("--days", type=int, default=180, help="Days of history (default 180)")
    args = parser.parse_args()
    run_htf_research(days=args.days)

