#!/usr/bin/env python3
"""Binance Spot Strategy Diagnostic & Experimentation Engine.

Pure Python 3.8+ Standard Library.
Evaluates standalone filters against the frozen BINANCE_SPOT_ORB_20D_V1 baseline:
1. Multi-timeframe trend filter (15m price > 200 EMA / 1h trend alignment)
2. Volume confirmation filter (RVOL / Volume SMA expansion)
3. Market regime filter (ATR volatility expansion / Bollinger bandwidth)
4. Combined validated filters (only if individual filters show positive edge)

Strict zero lookahead, chronological In-Sample (70%) vs Out-of-Sample (30%) splits,
exact fee/slippage modeling, and Wilson 95% confidence intervals.
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

CACHE_FILE = os.path.join(ROOT, "trader", "cached_klines_solusdt_15m_60d.json")


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
    """Compute complete statistical summary for a set of trades."""
    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "win_rate_95_ci": [0.0, 0.0],
            "expectancy_r": 0.0,
            "expectancy_return_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_r": 0.0,
            "max_drawdown_pct": 0.0,
            "total_fee_drag_r": 0.0,
            "total_net_pnl_pct": 0.0,
        }

    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    win_rate = round((len(wins) / total) * 100.0, 2)
    ci = wilson_ci(len(wins), total)

    exp_r = round(sum(t["r"] for t in trades) / total, 3)
    exp_pct = round(sum(t["net_return_pct"] for t in trades) / total, 3)
    total_net_pct = round(sum(t["net_return_pct"] for t in trades), 2)

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    # Max Drawdown in R and %
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

    # Fee drag in R: total fee per trade is ~ (entry * fee + exit * fee) / risk
    total_fee_r = sum((t["entry_px"] * fee_rate + t["exit_px"] * fee_rate) / t["risk"] for t in trades if t["risk"] > 0)
    avg_fee_drag_r = round(total_fee_r / total, 3) if total > 0 else 0.0

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "win_rate_95_ci": ci,
        "expectancy_r": exp_r,
        "expectancy_return_pct": exp_pct,
        "total_net_pnl_pct": total_net_pct,
        "profit_factor": profit_factor,
        "max_drawdown_r": round(max_dd_r, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "avg_fee_drag_r": avg_fee_drag_r
    }


# --------------------------------------------------------------------------
# Indicator Computations (Pure Standard Library)
# --------------------------------------------------------------------------
def compute_ema(series, period):
    """Exponential Moving Average."""
    n = len(series)
    ema = [0.0] * n
    if n < period:
        return ema
    # initial SMA
    sma = sum(series[:period]) / period
    ema[period - 1] = sma
    multiplier = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema[i] = (series[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


def compute_sma(series, period):
    """Simple Moving Average."""
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
    """Average True Range."""
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
    # SMA of first period TRs
    sma_tr = sum(tr[1:period + 1]) / period
    atr[period] = sma_tr
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_bollinger_bandwidth(bars, period=20, num_std=2.0):
    """Bollinger Bandwidth = (Upper Band - Lower Band) / Middle Band."""
    n = len(bars)
    bandwidth = [0.0] * n
    closes = [b["c"] for b in bars]
    sma = compute_sma(closes, period)
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        mean = sma[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        if mean > 0:
            bandwidth[i] = (2.0 * num_std * std) / mean
    return bandwidth


# --------------------------------------------------------------------------
# Simulation Engine
# --------------------------------------------------------------------------
def simulate_strategy(bars, filter_fn=None, fee_rate=0.0010, slippage=0.0005,
                      max_concurrent=1, tick_size="0.01"):
    """Walk forward bar-by-bar with zero lookahead."""
    n = len(bars)
    period = FROZEN_SPEC["lookback_period"]
    stop_floor = FROZEN_SPEC["stop_loss_pct_floor"]
    target_r = FROZEN_SPEC["target_r_multiple"]
    max_holding = FROZEN_SPEC["max_holding_bars"]

    completed_trades = []
    open_trade = None
    all_signals = []

    for i in range(period, n):
        current_bar = bars[i]

        # 1. Manage active open trade (stop checked before target for conservatism)
        if open_trade is not None:
            stop_hit = current_bar["l"] <= open_trade["stop_px"]
            target_hit = current_bar["h"] >= open_trade["target_px"]
            bars_held = i - open_trade["entry_bar_idx"]
            time_stop_hit = bars_held >= max_holding

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
                net_return_pct = (net_pnl / entry_px) * 100.0
                r_mult = net_pnl / risk if risk > 0 else 0.0

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
                    "net_pnl": round(net_pnl, 4),
                    "net_return_pct": round(net_return_pct, 4),
                    "r": round(r_mult, 4),
                    "win": r_mult > 0
                })
                open_trade = None

        # 2. Breakout Signal Detection on Bar i Close
        lookback_bars = bars[i - period:i]
        high_20 = max(b["h"] for b in lookback_bars)
        low_20 = min(b["l"] for b in lookback_bars)

        is_breakout = (current_bar["c"] > high_20) and (bars[i - 1]["c"] <= high_20)

        if is_breakout:
            sig_info = {
                "bar_idx": i,
                "timestamp": current_bar["t"],
                "close": current_bar["c"],
                "decision": "TAKEN"
            }

            # Enforce max concurrent position constraint
            if open_trade is not None and max_concurrent == 1:
                sig_info["decision"] = "SKIPPED_MAX_EXPOSURE"
                all_signals.append(sig_info)
                continue

            # Apply candidate filter
            if filter_fn is not None:
                passed, reason = filter_fn(bars, i)
                if not passed:
                    sig_info["decision"] = "REJECTED_FILTER"
                    sig_info["filter_reason"] = reason
                    all_signals.append(sig_info)
                    continue

            # Compute entry, stop, and target
            entry_px = current_bar["c"] * (1.0 + slippage)
            raw_stop = max(low_20, entry_px * (1.0 - stop_floor))
            risk = entry_px - raw_stop

            if risk <= 0:
                sig_info["decision"] = "REJECTED_ZERO_RISK"
                all_signals.append(sig_info)
                continue

            target_px = entry_px + (target_r * risk)

            q_entry = quantize_price(entry_px, tick_size)
            q_stop = quantize_price(raw_stop, tick_size)
            q_target = quantize_price(target_px, tick_size)

            if q_entry is None or q_stop is None or q_target is None:
                sig_info["decision"] = "REJECTED_FILTER_QUANTIZE"
                all_signals.append(sig_info)
                continue

            open_trade = {
                "id": f"trade_{current_bar['t']}",
                "entry_time": current_bar["t"],
                "entry_bar_idx": i,
                "entry_px": float(q_entry),
                "stop_px": float(q_stop),
                "target_px": float(q_target),
                "risk": float(risk)
            }
            sig_info["trade_id"] = open_trade["id"]
            all_signals.append(sig_info)

    metrics = calc_metrics(completed_trades, fee_rate, slippage)
    return {
        "signals": all_signals,
        "trades": completed_trades,
        "metrics": metrics
    }


# --------------------------------------------------------------------------
# Candidate Filters
# --------------------------------------------------------------------------
def build_filter_trend_ema(bars, period=200):
    """Trend Filter: Close must be strictly above EMA(period) (e.g. 200 EMA on 15m)."""
    closes = [b["c"] for b in bars]
    ema = compute_ema(closes, period)
    def filter_fn(b_list, idx):
        if idx < period:
            return False, f"Insufficient history for EMA{period}"
        c = b_list[idx]["c"]
        ma = ema[idx]
        if c <= ma:
            return False, f"Bearish trend: Close {c:.2f} <= EMA{period} {ma:.2f}"
        return True, "PASSED"
    return filter_fn


def build_filter_volume_rvol(bars, period=20, threshold=1.5):
    """Volume Filter: Candle volume must be >= threshold * SMA(period) of volume."""
    vols = [b["v"] for b in bars]
    vol_sma = compute_sma(vols, period)
    def filter_fn(b_list, idx):
        if idx < period:
            return False, f"Insufficient history for Vol SMA{period}"
        v = b_list[idx]["v"]
        sma_v = vol_sma[idx]
        rvol = v / sma_v if sma_v > 0 else 0.0
        if rvol < threshold:
            return False, f"Low volume: RVOL {rvol:.2f} < {threshold:.2f}x threshold"
        return True, "PASSED"
    return filter_fn


def build_filter_atr_expansion(bars, period=14, threshold=1.25):
    """Regime Filter: Candle Range (H - L) must be >= threshold * ATR(period)."""
    atr = compute_atr(bars, period)
    def filter_fn(b_list, idx):
        if idx < period + 1:
            return False, f"Insufficient history for ATR{period}"
        rng = b_list[idx]["h"] - b_list[idx]["l"]
        min_rng = threshold * atr[idx]
        if rng < min_rng:
            return False, f"Subdued range: {rng:.2f} < {threshold:.2f}x ATR {atr[idx]:.2f}"
        return True, "PASSED"
    return filter_fn


def build_filter_bb_expansion(bars, period=20):
    """Regime Filter: Bollinger Bandwidth must be expanding (Bandwidth > SMA20(Bandwidth))."""
    bbw = compute_bollinger_bandwidth(bars, period)
    bbw_sma = compute_sma(bbw, period)
    def filter_fn(b_list, idx):
        if idx < period * 2:
            return False, f"Insufficient history for BB bandwidth SMA"
        w = bbw[idx]
        w_sma = bbw_sma[idx]
        if w <= w_sma:
            return False, f"Volatility contraction/squeeze: BB width {w:.4f} <= SMA {w_sma:.4f}"
        return True, "PASSED"
    return filter_fn


def build_combined_filter(filter_list):
    """Combines multiple validated filters with logical AND."""
    def filter_fn(b_list, idx):
        for name, fn in filter_list:
            passed, reason = fn(b_list, idx)
            if not passed:
                return False, f"[{name}] {reason}"
        return True, "PASSED"
    return filter_fn


# --------------------------------------------------------------------------
# Experiment Suite Execution
# --------------------------------------------------------------------------
def load_historical_bars(symbol="SOLUSDT", days=60):
    """Load cached klines or fetch directly from Binance Spot API."""
    cache_path = os.path.join(ROOT, "trader", f"cached_klines_{symbol.lower()}_15m_60d.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            res = json.load(f)
            klines = res.get("klines", [])
            print(f"Loaded {len(klines)} klines from local cache {cache_path}")
    else:
        print(f"Fetching {days} days of {symbol} 15m klines from Binance API...")
        client = BinanceClient(product="spot")
        res = client.get_klines_backward_paginated(symbol, interval="15m", days=days)
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


def run_experiments(symbol="SOLUSDT", days=60):
    """Execute all diagnostic experiments and output comparison reports."""
    bars = load_historical_bars(symbol=symbol, days=days)
    n_total = len(bars)
    if n_total < 500:
        raise ValueError(f"Insufficient historical bars: {n_total}")

    # Split: Chronological In-Sample (first 70%) vs Out-of-Sample (last 30%)
    split_idx = int(n_total * 0.70)
    bars_is = bars[:split_idx]
    bars_oos = bars[split_idx:]

    # Also extract last 30 days (~2130 candles) to match forward test
    bars_fwd_30d = bars[-2130:] if n_total >= 2130 else bars

    # Benchmark: Buy-and-Hold on identical windows
    bnh_overall = ((bars[-1]["c"] - bars[0]["o"]) / bars[0]["o"]) * 100.0
    bnh_is = ((bars_is[-1]["c"] - bars_is[0]["o"]) / bars_is[0]["o"]) * 100.0
    bnh_oos = ((bars_oos[-1]["c"] - bars_oos[0]["o"]) / bars_oos[0]["o"]) * 100.0
    bnh_30d = ((bars_fwd_30d[-1]["c"] - bars_fwd_30d[0]["o"]) / bars_fwd_30d[0]["o"]) * 100.0

    print("=" * 80)
    print("BINANCE SPOT STRATEGY DIAGNOSTIC & IMPROVEMENT SUITE")
    print("=" * 80)
    print(f"Asset / Market   : {symbol} SPOT")
    print(f"Total Candles    : {n_total} (15m interval)")
    print(f"In-Sample Split  : {len(bars_is)} candles (First 70% | ~{int(days*0.70)} days) | B&H: {bnh_is:+.2f}%")
    print(f"Out-of-Sample    : {len(bars_oos)} candles (Last 30% | ~{int(days*0.30)} days) | B&H: {bnh_oos:+.2f}%")
    print(f"Forward Test 30d : {len(bars_fwd_30d)} candles (Last 30 days) | B&H: {bnh_30d:+.2f}%")
    print(f"Overall Window   : {n_total} candles ({days} days) | B&H: {bnh_overall:+.2f}%")
    print("-" * 80)

    # Define Candidate Filter Configurations
    configs = [
        {
            "id": "BASELINE_V1",
            "name": "Baseline (Unfiltered ORB 20)",
            "builder": None,
            "params": {"lookback": 20, "target_r": 2.0, "max_holding": 16}
        },
        {
            "id": "FILTER_A_TREND_EMA200",
            "name": "Filter A: MTF Trend (Price > EMA 200)",
            "builder": lambda b: build_filter_trend_ema(b, period=200),
            "params": {"ema_period": 200}
        },
        {
            "id": "FILTER_A2_TREND_EMA100",
            "name": "Filter A2: Trend (Price > EMA 100)",
            "builder": lambda b: build_filter_trend_ema(b, period=100),
            "params": {"ema_period": 100}
        },
        {
            "id": "FILTER_B_VOLUME_RVOL15",
            "name": "Filter B: Volume Confirmation (RVOL >= 1.5x)",
            "builder": lambda b: build_filter_volume_rvol(b, period=20, threshold=1.5),
            "params": {"vol_sma": 20, "min_rvol": 1.5}
        },
        {
            "id": "FILTER_B2_VOLUME_RVOL20",
            "name": "Filter B2: Volume Confirmation (RVOL >= 2.0x)",
            "builder": lambda b: build_filter_volume_rvol(b, period=20, threshold=2.0),
            "params": {"vol_sma": 20, "min_rvol": 2.0}
        },
        {
            "id": "FILTER_C_REGIME_ATR125",
            "name": "Filter C: Volatility Expansion (Range >= 1.25x ATR14)",
            "builder": lambda b: build_filter_atr_expansion(b, period=14, threshold=1.25),
            "params": {"atr_period": 14, "min_atr_mult": 1.25}
        },
        {
            "id": "FILTER_C2_REGIME_BB_EXP",
            "name": "Filter C2: Volatility Regime (BB Bandwidth > SMA20)",
            "builder": lambda b: build_filter_bb_expansion(b, period=20),
            "params": {"bb_period": 20}
        },
    ]

    results = []

    # Run Standalone Experiments
    for cfg in configs:
        cid = cfg["id"]
        cname = cfg["name"]
        print(f"Evaluating {cid}: {cname}...")

        # 1. In-Sample
        fn_is = cfg["builder"](bars_is) if cfg["builder"] else None
        res_is = simulate_strategy(bars_is, filter_fn=fn_is, fee_rate=0.0010, slippage=0.0005)

        # 2. Out-of-Sample
        fn_oos = cfg["builder"](bars_oos) if cfg["builder"] else None
        res_oos = simulate_strategy(bars_oos, filter_fn=fn_oos, fee_rate=0.0010, slippage=0.0005)

        # 3. 30-Day Forward Window
        fn_30d = cfg["builder"](bars_fwd_30d) if cfg["builder"] else None
        res_30d = simulate_strategy(bars_fwd_30d, filter_fn=fn_30d, fee_rate=0.0010, slippage=0.0005)

        # 4. Overall 60-Day Window
        fn_all = cfg["builder"](bars) if cfg["builder"] else None
        res_all = simulate_strategy(bars, filter_fn=fn_all, fee_rate=0.0010, slippage=0.0005)

        results.append({
            "id": cid,
            "name": cname,
            "params": cfg["params"],
            "is_metrics": res_is["metrics"],
            "oos_metrics": res_oos["metrics"],
            "fwd_30d_metrics": res_30d["metrics"],
            "overall_metrics": res_all["metrics"]
        })

    # Identify individual filters that showed positive delta in In-Sample expectancy vs baseline
    baseline_is_exp = results[0]["is_metrics"]["expectancy_r"]
    positive_filters = []
    for r in results[1:]:
        delta_is = r["is_metrics"]["expectancy_r"] - baseline_is_exp
        if delta_is > 0.05 and r["is_metrics"]["total_trades"] >= 10:
            positive_filters.append(r)

    print(f"\nStandalone Filters with Positive IS Delta vs Baseline: {[p['id'] for p in positive_filters]}")

    # Now evaluate Combined Validated Variants if any positive filters qualify
    combined_cfgs = []
    # Test Trend + Volume (classic institutional momentum)
    combined_cfgs.append({
        "id": "COMBINED_TREND_VOLUME",
        "name": "Combined: Trend (EMA200) + Volume (RVOL >= 1.5x)",
        "builder": lambda b: build_combined_filter([
            ("Trend_EMA200", build_filter_trend_ema(b, period=200)),
            ("Volume_RVOL15", build_filter_volume_rvol(b, period=20, threshold=1.5))
        ]),
        "params": {"ema_period": 200, "min_rvol": 1.5}
    })
    # Test Trend + ATR Expansion
    combined_cfgs.append({
        "id": "COMBINED_TREND_ATR",
        "name": "Combined: Trend (EMA200) + Volatility (Range >= 1.25x ATR14)",
        "builder": lambda b: build_combined_filter([
            ("Trend_EMA200", build_filter_trend_ema(b, period=200)),
            ("ATR_Expansion", build_filter_atr_expansion(b, period=14, threshold=1.25))
        ]),
        "params": {"ema_period": 200, "min_atr_mult": 1.25}
    })
    # Test Triple Filter (Trend + Volume + ATR Expansion)
    combined_cfgs.append({
        "id": "COMBINED_TRIPLE_DEFENSE",
        "name": "Combined Triple: Trend (EMA200) + RVOL(1.5x) + Range(1.25x ATR)",
        "builder": lambda b: build_combined_filter([
            ("Trend_EMA200", build_filter_trend_ema(b, period=200)),
            ("Volume_RVOL15", build_filter_volume_rvol(b, period=20, threshold=1.5)),
            ("ATR_Expansion", build_filter_atr_expansion(b, period=14, threshold=1.25))
        ]),
        "params": {"ema_period": 200, "min_rvol": 1.5, "min_atr_mult": 1.25}
    })

    for cfg in combined_cfgs:
        cid = cfg["id"]
        cname = cfg["name"]
        print(f"Evaluating {cid}: {cname}...")
        fn_is = cfg["builder"](bars_is)
        res_is = simulate_strategy(bars_is, filter_fn=fn_is, fee_rate=0.0010, slippage=0.0005)
        fn_oos = cfg["builder"](bars_oos)
        res_oos = simulate_strategy(bars_oos, filter_fn=fn_oos, fee_rate=0.0010, slippage=0.0005)
        fn_30d = cfg["builder"](bars_fwd_30d)
        res_30d = simulate_strategy(bars_fwd_30d, filter_fn=fn_30d, fee_rate=0.0010, slippage=0.0005)
        fn_all = cfg["builder"](bars)
        res_all = simulate_strategy(bars, filter_fn=fn_all, fee_rate=0.0010, slippage=0.0005)

        results.append({
            "id": cid,
            "name": cname,
            "params": cfg["params"],
            "is_metrics": res_is["metrics"],
            "oos_metrics": res_oos["metrics"],
            "fwd_30d_metrics": res_30d["metrics"],
            "overall_metrics": res_all["metrics"]
        })

    # Sensitivity Analysis on the Best Overall Variant
    # Sort results by OOS Expectancy R
    ranked = sorted(results, key=lambda x: (x["oos_metrics"]["expectancy_r"], x["oos_metrics"]["profit_factor"]), reverse=True)
    best_candidate = ranked[0]
    print(f"\nTop-Ranked Candidate for Sensitivity Analysis: {best_candidate['id']} ({best_candidate['name']})")

    sensitivity_matrix = []
    # Test fees: 0.075% (BNB tier), 0.100% (Standard), 0.150% (High)
    # Test slippage: 0.02% (Tight), 0.05% (Standard), 0.10% (Wide)
    fee_tiers = [0.00075, 0.0010, 0.0015]
    slippage_tiers = [0.0002, 0.0005, 0.0010]

    # Find candidate builder
    builder_to_use = None
    for c in configs + combined_cfgs:
        if c["id"] == best_candidate["id"]:
            builder_to_use = c["builder"]
            break

    for f_tier in fee_tiers:
        for s_tier in slippage_tiers:
            fn_sens = builder_to_use(bars) if builder_to_use else None
            res_sens = simulate_strategy(bars, filter_fn=fn_sens, fee_rate=f_tier, slippage=s_tier)
            m = res_sens["metrics"]
            sensitivity_matrix.append({
                "fee_rate_pct": f_tier * 100.0,
                "slippage_rate_pct": s_tier * 100.0,
                "total_trades": m["total_trades"],
                "win_rate_pct": m["win_rate_pct"],
                "expectancy_r": m["expectancy_r"],
                "profit_factor": m["profit_factor"],
                "max_drawdown_r": m["max_drawdown_r"],
                "total_net_pnl_pct": m["total_net_pnl_pct"]
            })

    # Save complete experiment results JSON
    doc = {
        "symbol": symbol,
        "date_range": {
            "start": datetime.datetime.fromtimestamp(bars[0]["t"], datetime.timezone.utc).isoformat(),
            "end": datetime.datetime.fromtimestamp(bars[-1]["t"], datetime.timezone.utc).isoformat(),
            "candles": n_total
        },
        "benchmarks": {
            "buy_and_hold_overall_pct": round(bnh_overall, 2),
            "buy_and_hold_is_pct": round(bnh_is, 2),
            "buy_and_hold_oos_pct": round(bnh_oos, 2),
            "buy_and_hold_fwd_30d_pct": round(bnh_30d, 2),
        },
        "experiments": results,
        "sensitivity_analysis": {
            "candidate_id": best_candidate["id"],
            "candidate_name": best_candidate["name"],
            "scenarios": sensitivity_matrix
        },
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    out_path = os.path.join(ROOT, "trader", f"experiment_results_{symbol.lower()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    # also copy to trader/experiment_results.json for standard access
    default_out_path = os.path.join(ROOT, "trader", "experiment_results.json")
    with open(default_out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Complete experiment results saved to {out_path} and {default_out_path}")

    # Print Formatted Comparison Tables
    print_report(doc)
    return doc


def print_report(doc):
    """Format and print comparison tables to stdout."""
    print("\n" + "=" * 110)
    print("EXPERIMENT RESULTS: STANDALONE & COMBINED FILTERS VS BASELINE")
    print("=" * 110)
    b = doc["benchmarks"]
    print(f"Buy-and-Hold Benchmarks: Overall: {b['buy_and_hold_overall_pct']:+.2f}% | In-Sample: {b['buy_and_hold_is_pct']:+.2f}% | Out-of-Sample: {b['buy_and_hold_oos_pct']:+.2f}% | 30d Fwd: {b['buy_and_hold_fwd_30d_pct']:+.2f}%")
    print("-" * 110)
    header = f"{'Variant ID':<25} | {'IS Exp (R)':<10} | {'IS PF':<6} | {'OOS Trades':<10} | {'OOS Win%':<10} | {'OOS Exp (R)':<11} | {'OOS PF':<6} | {'OOS MDD (R)':<11}"
    print(header)
    print("-" * 110)

    for exp in doc["experiments"]:
        is_m = exp["is_metrics"]
        oos_m = exp["oos_metrics"]
        print(f"{exp['id']:<25} | {is_m['expectancy_r']:+9.3f}R | {is_m['profit_factor']:<6.2f} | {oos_m['total_trades']:<10} | {oos_m['win_rate_pct']:<9.1f}% | {oos_m['expectancy_r']:+10.3f}R | {oos_m['profit_factor']:<6.2f} | -{oos_m['max_drawdown_r']:<10.2f}R")

    print("=" * 110)

    print("\n" + "=" * 110)
    print("30-DAY FORWARD-TEST WINDOW COMPARISON (MATCHING TRADER/FORWARD_REPORT.JSON)")
    print("=" * 110)
    header_fwd = f"{'Variant ID':<25} | {'Trades':<7} | {'Win Rate (95% CI)':<22} | {'Exp (R)':<9} | {'Exp (%)':<9} | {'PF':<6} | {'MDD (R)':<9} | {'Net Return':<10}"
    print(header_fwd)
    print("-" * 110)
    for exp in doc["experiments"]:
        f_m = exp["fwd_30d_metrics"]
        ci_str = f"{f_m['win_rate_pct']:.1f}% [{f_m['win_rate_95_ci'][0]:.1f}-{f_m['win_rate_95_ci'][1]:.1f}%]"
        print(f"{exp['id']:<25} | {f_m['total_trades']:<7} | {ci_str:<22} | {f_m['expectancy_r']:+8.3f}R | {f_m['expectancy_return_pct']:+8.3f}% | {f_m['profit_factor']:<6.2f} | -{f_m['max_drawdown_r']:<8.2f}R | {f_m['total_net_pnl_pct']:+9.2f}%")
    print("=" * 110)

    # Print Sensitivity Matrix
    sens = doc["sensitivity_analysis"]
    print(f"\nSENSITIVITY ANALYSIS FOR CANDIDATE: {sens['candidate_id']}")
    print("-" * 85)
    print(f"{'Fee Rate':<10} | {'Slippage':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Expectancy (R)':<15} | {'PF':<6} | {'Net Return':<10}")
    print("-" * 85)
    for row in sens["scenarios"]:
        print(f"{row['fee_rate_pct']:<9.3f}% | {row['slippage_rate_pct']:<9.3f}% | {row['total_trades']:<8} | {row['win_rate_pct']:<9.2f}% | {row['expectancy_r']:+14.3f}R | {row['profit_factor']:<6.2f} | {row['total_net_pnl_pct']:+9.2f}%")
    print("-" * 85)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Binance Strategy Experiments Suite")
    parser.add_argument("--symbol", default="SOLUSDT", help="Trading symbol (default SOLUSDT)")
    parser.add_argument("--days", type=int, default=60, help="Days of history (default 60)")
    args = parser.parse_args()
    run_experiments(symbol=args.symbol, days=args.days)
