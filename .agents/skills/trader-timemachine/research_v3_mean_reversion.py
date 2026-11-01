#!/usr/bin/env python3
"""Binance Strategy V3: Mean-Reversion & Liquidity Sweep Research Suite.

Pure Python 3.8+ Standard Library.
Investigates whether mean-reversion and liquidity-sweep reversal strategies
offer a robust after-cost edge over the baseline BINANCE_SPOT_ORB_20D_V1 breakout strategy.

Evaluates:
- Strategy A: Liquidity Sweep and Reclaim (Swing low sweep, wick absorption, ATR buffer stop, 1.5R/2R/Range High targets)
- Strategy B: Range Mean-Reversion (ADX/EMA regime filter, range lower-bound deviation, reversal confirmation, invalidation stop, midpoint/fixed R targets)
- Assets: BTCUSDT and SOLUSDT
- Timeframes: 15m and 1h
- 3-Way Chronological Partition: Train (60%), Validation (20%), Untouched Test (20%)
- Full Regime Breakdown: Bullish Trend, Bearish Trend, Range/Chop
- Sensitivity Matrix: Cost tiers and parameter perturbations.

Research and simulation only. Zero live trading, no broker modifications.
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

FROZEN_BASELINE_ORB = {
    "strategy_id": "BINANCE_SPOT_ORB_20D_V1",
    "version": "1.0.0",
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
# Indicator Library (Pure Standard Library)
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


def compute_rsi(closes, period=14):
    n = len(closes)
    rsi = [50.0] * n
    if n < period + 1:
        return rsi
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains[i] = diff
        else:
            losses[i] = -diff
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_adx(bars, period=14):
    n = len(bars)
    adx = [0.0] * n
    if n < period * 2 + 1:
        return adx
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        h = bars[i]["h"]
        l = bars[i]["l"]
        prev_h = bars[i - 1]["h"]
        prev_l = bars[i - 1]["l"]
        prev_c = bars[i - 1]["c"]
        tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
        up_move = h - prev_h
        down_move = prev_l - l
        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    smooth_tr = sum(tr[1:period + 1])
    smooth_pdm = sum(plus_dm[1:period + 1])
    smooth_mdm = sum(minus_dm[1:period + 1])

    dx = [0.0] * n
    pdi = 100.0 * (smooth_pdm / smooth_tr) if smooth_tr > 0 else 0.0
    mdi = 100.0 * (smooth_mdm / smooth_tr) if smooth_tr > 0 else 0.0
    dx[period] = 100.0 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0

    for i in range(period + 1, n):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr[i]
        smooth_pdm = smooth_pdm - (smooth_pdm / period) + plus_dm[i]
        smooth_mdm = smooth_mdm - (smooth_mdm / period) + minus_dm[i]
        pdi = 100.0 * (smooth_pdm / smooth_tr) if smooth_tr > 0 else 0.0
        mdi = 100.0 * (smooth_mdm / smooth_tr) if smooth_tr > 0 else 0.0
        dx[i] = 100.0 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0

    adx[period * 2 - 1] = sum(dx[period:period * 2]) / period
    for i in range(period * 2, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


def compute_bollinger_bands(closes, period=20, num_std=2.0):
    n = len(closes)
    mid = compute_sma(closes, period)
    upper = [0.0] * n
    lower = [0.0] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        m = mid[i]
        variance = sum((x - m) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = m + (num_std * std)
        lower[i] = m - (num_std * std)
    return upper, mid, lower


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
# Strategy A: Liquidity Sweep & Reclaim Simulation
# --------------------------------------------------------------------------
def simulate_liquidity_sweep(bars, p_left=5, p_right=2, lookback_window=30,
                             min_wick_ratio=0.40, buffer_atr_mult=0.5,
                             target_type="2.0r", max_holding_bars=16,
                             fee_rate=0.0010, slippage=0.0005, tick_size="0.01"):
    """
    Simulates Strategy A: Liquidity Sweep and Reclaim.
    1. Identifies rolling swing lows confirmed at least p_right bars ago (zero lookahead).
    2. Sweeps below swing low (Low_i < Swing_Low) and reclaims (Close_i > Swing_Low).
    3. Confirms with lower wick >= min_wick_ratio of candle range (absorption).
    4. Stop loss placed below sweep low with buffer: Low_i - (buffer * ATR14).
    5. Target: 1.5R, 2.0R, or opposing range high (High of prior 20 bars).
    """
    n = len(bars)
    atr_14 = compute_atr(bars, 14)

    completed_trades = []
    open_trade = None
    all_signals = []

    # Swing low tracking: confirmed pivots
    # A bar s is a confirmed pivot low at bar i if s <= i - 1 - p_right and
    # Low_s < Low_{s-k} (k=1..p_left) and Low_s < Low_{s+k} (k=1..p_right).

    start_idx = max(lookback_window + 10, 20)

    for i in range(start_idx, n):
        current_bar = bars[i]

        # 1. Manage active trade
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

        # 2. Identify confirmed swing low in lookback window [i - lookback_window, i - 1 - p_right]
        max_pivot_idx = i - 1 - p_right
        min_pivot_idx = max(p_left, i - lookback_window)
        recent_swing_low = None

        for s in range(max_pivot_idx, min_pivot_idx - 1, -1):
            is_pivot = True
            low_s = bars[s]["l"]
            for left in range(1, p_left + 1):
                if bars[s - left]["l"] <= low_s:
                    is_pivot = False
                    break
            if not is_pivot:
                continue
            for right in range(1, p_right + 1):
                if bars[s + right]["l"] <= low_s:
                    is_pivot = False
                    break
            if is_pivot:
                recent_swing_low = low_s
                break

        if recent_swing_low is None:
            continue

        # 3. Detect Sweep and Reclaim
        # Intrabar low swept below swing low, but candle closed back above it
        swept = (current_bar["l"] < recent_swing_low)
        reclaimed = (current_bar["c"] > recent_swing_low)

        if swept and reclaimed:
            sig = {
                "bar_idx": i,
                "timestamp": current_bar["t"],
                "close": current_bar["c"],
                "swing_low": recent_swing_low,
                "decision": "TAKEN"
            }

            if open_trade is not None:
                sig["decision"] = "SKIPPED_MAX_EXPOSURE"
                all_signals.append(sig)
                continue

            # Check Confirmation: Lower Wick Absorption
            bar_range = current_bar["h"] - current_bar["l"]
            if bar_range <= 0:
                sig["decision"] = "REJECTED_ZERO_RANGE"
                all_signals.append(sig)
                continue

            lower_wick = min(current_bar["o"], current_bar["c"]) - current_bar["l"]
            wick_ratio = lower_wick / bar_range
            if wick_ratio < min_wick_ratio:
                sig["decision"] = "REJECTED_WICK_RATIO"
                sig["wick_ratio"] = round(wick_ratio, 2)
                all_signals.append(sig)
                continue

            # Determine Stop Loss (below sweep low with ATR buffer)
            atr_val = atr_14[i]
            entry_px = current_bar["c"] * (1.0 + slippage)
            raw_stop = current_bar["l"] - (buffer_atr_mult * atr_val)
            risk = entry_px - raw_stop

            if risk <= 0:
                sig["decision"] = "REJECTED_ZERO_RISK"
                all_signals.append(sig)
                continue

            # Determine Target Price
            if target_type == "1.5r":
                target_px = entry_px + (1.5 * risk)
            elif target_type == "2.0r":
                target_px = entry_px + (2.0 * risk)
            elif target_type == "range_high":
                # Opposing 20-bar range high
                range_high = max(b["h"] for b in bars[i - 20:i])
                target_px = max(range_high, entry_px + (1.0 * risk))
            else:
                target_px = entry_px + (2.0 * risk)

            q_entry = quantize_price(entry_px, tick_size)
            q_stop = quantize_price(raw_stop, tick_size)
            q_target = quantize_price(target_px, tick_size)

            if q_entry is None or q_stop is None or q_target is None:
                sig["decision"] = "REJECTED_QUANTIZE"
                all_signals.append(sig)
                continue

            regime = classify_market_regime(bars, i)
            open_trade = {
                "id": f"sweep_{current_bar['t']}",
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
# Strategy B: Range Mean-Reversion Simulation
# --------------------------------------------------------------------------
def simulate_range_mean_reversion(bars, range_lookback=20, max_adx=25.0,
                                  entry_zone_pct=0.20, target_type="midpoint",
                                  buffer_atr_mult=0.5, max_holding_bars=16,
                                  fee_rate=0.0010, slippage=0.0005, tick_size="0.01"):
    """
    Simulates Strategy B: Range Mean-Reversion.
    1. Detects range-bound market using ADX < max_adx and flat EMA50 slope.
    2. Identifies price deviation in bottom entry_zone_pct of 20-bar range or BB lower touch.
    3. Reversal confirmation: Bullish candle closing above midpoint of prior bearish bar.
    4. Stop loss placed below range low with ATR buffer: Low_range - (buffer * ATR14).
    5. Target: Range Midpoint or Fixed R multiple.
    6. Disabled on confirmed range breakdown (Close < Low_range).
    """
    n = len(bars)
    closes = [b["c"] for b in bars]
    adx_14 = compute_adx(bars, 14)
    atr_14 = compute_atr(bars, 14)
    rsi_14 = compute_rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = compute_bollinger_bands(closes, 20, 2.0)

    completed_trades = []
    open_trade = None
    all_signals = []

    start_idx = max(range_lookback + 10, 40)

    for i in range(start_idx, n):
        current_bar = bars[i]

        # 1. Manage active trade
        if open_trade is not None:
            stop_hit = current_bar["l"] <= open_trade["stop_px"]
            target_hit = current_bar["h"] >= open_trade["target_px"]
            bars_held = i - open_trade["entry_bar_idx"]
            time_stop_hit = bars_held >= max_holding_bars

            # Range breakdown invalidation: if candle closes below range low
            invalidation_hit = current_bar["c"] < open_trade["range_low"]

            exit_px = None
            exit_type = None

            if stop_hit:
                exit_px = open_trade["stop_px"] * (1.0 - slippage)
                exit_type = "stop"
            elif target_hit:
                exit_px = open_trade["target_px"] * (1.0 - slippage)
                exit_type = "target"
            elif invalidation_hit:
                exit_px = current_bar["c"] * (1.0 - slippage)
                exit_type = "range_invalidation"
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

        # 2. Range-bound condition check
        curr_adx = adx_14[i]
        if curr_adx >= max_adx:
            continue

        # 3. Calculate recent range
        lookback_bars = bars[i - range_lookback:i]
        high_range = max(b["h"] for b in lookback_bars)
        low_range = min(b["l"] for b in lookback_bars)
        range_height = high_range - low_range

        if range_height <= 0:
            continue

        # Disable entry if candle closed below range low (breakdown in progress)
        if current_bar["c"] <= low_range:
            continue

        # Check lower end of range deviation
        rel_pos = (current_bar["c"] - low_range) / range_height
        touched_bb = (current_bar["l"] <= bb_lower[i])

        if rel_pos > entry_zone_pct and not touched_bb:
            continue

        # 4. Reversal confirmation:
        # Candle i is bullish, closes above midpoint of prior candle i-1
        prev_bar = bars[i - 1]
        prior_mid = (prev_bar["o"] + prev_bar["c"]) / 2.0
        is_bullish = current_bar["c"] > current_bar["o"]
        pierced_mid = current_bar["c"] > prior_mid
        rsi_uptick = (rsi_14[i] > rsi_14[i - 1]) and (rsi_14[i - 1] < 45.0)

        if not (is_bullish and pierced_mid and rsi_uptick):
            continue

        sig = {
            "bar_idx": i,
            "timestamp": current_bar["t"],
            "close": current_bar["c"],
            "range_high": high_range,
            "range_low": low_range,
            "decision": "TAKEN"
        }

        if open_trade is not None:
            sig["decision"] = "SKIPPED_MAX_EXPOSURE"
            all_signals.append(sig)
            continue

        entry_px = current_bar["c"] * (1.0 + slippage)
        atr_val = atr_14[i]
        raw_stop = low_range - (buffer_atr_mult * atr_val)
        risk = entry_px - raw_stop

        if risk <= 0:
            sig["decision"] = "REJECTED_ZERO_RISK"
            all_signals.append(sig)
            continue

        # Target selection
        if target_type == "midpoint":
            midpoint = (high_range + low_range) / 2.0
            target_px = max(midpoint, entry_px + (1.0 * risk))
        elif target_type == "1.5r":
            target_px = entry_px + (1.5 * risk)
        elif target_type == "2.0r":
            target_px = entry_px + (2.0 * risk)
        else:
            target_px = (high_range + low_range) / 2.0

        q_entry = quantize_price(entry_px, tick_size)
        q_stop = quantize_price(raw_stop, tick_size)
        q_target = quantize_price(target_px, tick_size)

        if q_entry is None or q_stop is None or q_target is None:
            sig["decision"] = "REJECTED_QUANTIZE"
            all_signals.append(sig)
            continue

        regime = classify_market_regime(bars, i)
        open_trade = {
            "id": f"range_{current_bar['t']}",
            "entry_time": current_bar["t"],
            "entry_bar_idx": i,
            "entry_px": float(q_entry),
            "stop_px": float(q_stop),
            "target_px": float(q_target),
            "risk": float(risk),
            "range_low": low_range,
            "regime": regime
        }
        sig["trade_id"] = open_trade["id"]
        sig["regime"] = regime
        all_signals.append(sig)

    metrics = calc_metrics(completed_trades, fee_rate, slippage)
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
# Frozen Baseline Simulator (for side-by-side benchmarking)
# --------------------------------------------------------------------------
def simulate_baseline_orb(bars, fee_rate=0.0010, slippage=0.0005, tick_size="0.01"):
    """Evaluates frozen BINANCE_SPOT_ORB_20D_V1 on the exact same dataset."""
    n = len(bars)
    period = 20
    stop_floor = 0.02
    target_r = 2.0
    max_holding = 16

    completed_trades = []
    open_trade = None
    all_signals = []

    for i in range(period, n):
        current_bar = bars[i]

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

        lookback_bars = bars[i - period:i]
        high_20 = max(b["h"] for b in lookback_bars)
        low_20 = min(b["l"] for b in lookback_bars)

        if current_bar["c"] > high_20 and bars[i - 1]["c"] <= high_20:
            sig = {
                "bar_idx": i,
                "timestamp": current_bar["t"],
                "close": current_bar["c"],
                "decision": "TAKEN"
            }
            if open_trade is not None:
                sig["decision"] = "SKIPPED_MAX_EXPOSURE"
                all_signals.append(sig)
                continue

            entry_px = current_bar["c"] * (1.0 + slippage)
            raw_stop = max(low_20, entry_px * (1.0 - stop_floor))
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
                "id": f"orb_{current_bar['t']}",
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
def load_cached_bars(symbol="BTCUSDT", interval="15m"):
    """Load cached klines for 15m (60d) or 1h (180d)."""
    days = 180 if interval == "1h" else 60
    cache_path = os.path.join(ROOT, "trader", f"cached_klines_{symbol.lower()}_{interval}_{days}d.json")
    if not os.path.exists(cache_path):
        # fallback to standard naming
        cache_path = os.path.join(ROOT, "trader", f"cached_klines_{symbol.lower()}_{interval}_60d.json")

    if not os.path.exists(cache_path):
        print(f"Cache not found at {cache_path}. Fetching from Binance API...")
        client = BinanceClient(product="spot")
        res = client.get_klines_backward_paginated(symbol, interval=interval, days=days)
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(res, f)
    else:
        with open(cache_path, "r", encoding="utf-8") as f:
            res = json.load(f)

    klines = res.get("klines", [])
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
    print(f"Loaded {len(bars)} {interval} candles for {symbol} ({cache_path})")
    return bars


# --------------------------------------------------------------------------
# Main Research Runner
# --------------------------------------------------------------------------
def run_v3_research(symbols=("BTCUSDT", "SOLUSDT"), timeframes=("15m", "1h")):
    """Run full V3 research suite across assets, timeframes, and strategies."""
    results = []

    strategies = [
        {
            "id": "BASELINE_ORB_V1",
            "name": "Frozen Baseline (ORB 20-bar Breakout)",
            "runner": lambda b: simulate_baseline_orb(b)
        },
        {
            "id": "STRAT_A1_SWEEP_15R",
            "name": "Strategy A1: Liquidity Sweep & Reclaim (Target 1.5R)",
            "runner": lambda b: simulate_liquidity_sweep(b, target_type="1.5r", buffer_atr_mult=0.5)
        },
        {
            "id": "STRAT_A2_SWEEP_20R",
            "name": "Strategy A2: Liquidity Sweep & Reclaim (Target 2.0R)",
            "runner": lambda b: simulate_liquidity_sweep(b, target_type="2.0r", buffer_atr_mult=0.5)
        },
        {
            "id": "STRAT_A3_SWEEP_RANGE_HIGH",
            "name": "Strategy A3: Liquidity Sweep & Reclaim (Target Opposing Range High)",
            "runner": lambda b: simulate_liquidity_sweep(b, target_type="range_high", buffer_atr_mult=0.5)
        },
        {
            "id": "STRAT_B1_RANGE_MIDPOINT",
            "name": "Strategy B1: Range Mean-Reversion (Target Range Midpoint)",
            "runner": lambda b: simulate_range_mean_reversion(b, target_type="midpoint", max_adx=25.0)
        },
        {
            "id": "STRAT_B2_RANGE_15R",
            "name": "Strategy B2: Range Mean-Reversion (Target Fixed 1.5R)",
            "runner": lambda b: simulate_range_mean_reversion(b, target_type="1.5r", max_adx=25.0)
        },
        {
            "id": "STRAT_B3_RANGE_20R",
            "name": "Strategy B3: Range Mean-Reversion (Target Fixed 2.0R)",
            "runner": lambda b: simulate_range_mean_reversion(b, target_type="2.0r", max_adx=25.0)
        }
    ]

    for sym in symbols:
        for tf in timeframes:
            print("\n" + "=" * 90)
            print(f"EVALUATING STRATEGY V3: {sym} ({tf})")
            print("=" * 90)
            bars = load_cached_bars(symbol=sym, interval=tf)
            n_total = len(bars)
            if n_total < 200:
                print(f"Skipping {sym} {tf}: Insufficient candles ({n_total})")
                continue

            # 3-Way Chronological Partition: Train (60%), Validation (20%), Untouched Test (20%)
            idx_train = int(n_total * 0.60)
            idx_val = int(n_total * 0.80)

            bars_train = bars[:idx_train]
            bars_val = bars[idx_train:idx_val]
            bars_test = bars[idx_val:]

            bnh_train = ((bars_train[-1]["c"] - bars_train[0]["o"]) / bars_train[0]["o"]) * 100.0
            bnh_val = ((bars_val[-1]["c"] - bars_val[0]["o"]) / bars_val[0]["o"]) * 100.0
            bnh_test = ((bars_test[-1]["c"] - bars_test[0]["o"]) / bars_test[0]["o"]) * 100.0
            bnh_overall = ((bars[-1]["c"] - bars[0]["o"]) / bars[0]["o"]) * 100.0

            print(f"Partitions: Train (60%): {len(bars_train)} bars (B&H: {bnh_train:+.2f}%) | Val (20%): {len(bars_val)} bars (B&H: {bnh_val:+.2f}%) | Test (20%): {len(bars_test)} bars (B&H: {bnh_test:+.2f}%)")
            print(f"Overall Buy-and-Hold: {bnh_overall:+.2f}%")
            print("-" * 90)

            for strat in strategies:
                sid = strat["id"]
                sname = strat["name"]

                res_train = strat["runner"](bars_train)
                res_val = strat["runner"](bars_val)
                res_test = strat["runner"](bars_test)
                res_all = strat["runner"](bars)

                results.append({
                    "symbol": sym,
                    "timeframe": tf,
                    "strategy_id": sid,
                    "strategy_name": sname,
                    "benchmarks": {
                        "bnh_train_pct": round(bnh_train, 2),
                        "bnh_val_pct": round(bnh_val, 2),
                        "bnh_test_pct": round(bnh_test, 2),
                        "bnh_overall_pct": round(bnh_overall, 2),
                    },
                    "train_metrics": res_train["metrics"],
                    "val_metrics": res_val["metrics"],
                    "test_metrics": res_test["metrics"],
                    "overall_metrics": res_all["metrics"],
                    "regimes": res_all["regimes"]
                })

    # Sensitivity Analysis on Strategy A and Strategy B
    sens_matrix = run_v3_sensitivity(symbols, timeframes)

    doc = {
        "title": "Strategy V3: Mean-Reversion and Liquidity Sweep Research Report",
        "symbols": list(symbols),
        "timeframes": list(timeframes),
        "frozen_baseline_orb": FROZEN_BASELINE_ORB,
        "results": results,
        "sensitivity": sens_matrix,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    out_file = os.path.join(ROOT, "trader", "v3_experiment_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"\nAll V3 experiment results saved to {out_file}")

    print_v3_summary(doc)
    return doc


def run_v3_sensitivity(symbols, timeframes):
    """Run sensitivity analysis across transaction costs and parameter perturbations."""
    cases = []
    fee_tiers = [0.00075, 0.0010, 0.0015]
    slippage_tiers = [0.0002, 0.0005, 0.0010]

    for sym in ["BTCUSDT", "SOLUSDT"]:
        bars = load_cached_bars(symbol=sym, interval="1h")

        # 1. Cost Sensitivity on Strategy A2 (Sweep 2.0R)
        cost_scenarios_a = []
        for f in fee_tiers:
            for s in slippage_tiers:
                res = simulate_liquidity_sweep(bars, target_type="2.0r", fee_rate=f, slippage=s)
                m = res["metrics"]
                cost_scenarios_a.append({
                    "strategy": "STRAT_A2_SWEEP_20R",
                    "fee_pct": f * 100.0,
                    "slippage_pct": s * 100.0,
                    "trades": m["total_trades"],
                    "win_rate": m["win_rate_pct"],
                    "net_exp_r": m["net_expectancy_r"],
                    "pf": m["profit_factor"],
                    "net_ret_pct": m["total_net_pnl_pct"]
                })

        # 2. Parameter Perturbations on Strategy A (Sweep Buffer ATR)
        param_scenarios_a = []
        for buf in [0.3, 0.5, 0.8]:
            for wick in [0.30, 0.40, 0.50]:
                for tr in ["1.5r", "2.0r", "range_high"]:
                    res = simulate_liquidity_sweep(bars, buffer_atr_mult=buf, min_wick_ratio=wick, target_type=tr)
                    m = res["metrics"]
                    param_scenarios_a.append({
                        "buffer_atr": buf,
                        "min_wick": wick,
                        "target_type": tr,
                        "trades": m["total_trades"],
                        "win_rate": m["win_rate_pct"],
                        "net_exp_r": m["net_expectancy_r"],
                        "pf": m["profit_factor"],
                        "max_dd_r": m["max_drawdown_r"],
                        "net_ret_pct": m["total_net_pnl_pct"]
                    })

        # 3. Cost Sensitivity on Strategy B1 (Range Midpoint)
        cost_scenarios_b = []
        for f in fee_tiers:
            for s in slippage_tiers:
                res = simulate_range_mean_reversion(bars, target_type="midpoint", fee_rate=f, slippage=s)
                m = res["metrics"]
                cost_scenarios_b.append({
                    "strategy": "STRAT_B1_RANGE_MIDPOINT",
                    "fee_pct": f * 100.0,
                    "slippage_pct": s * 100.0,
                    "trades": m["total_trades"],
                    "win_rate": m["win_rate_pct"],
                    "net_exp_r": m["net_expectancy_r"],
                    "pf": m["profit_factor"],
                    "net_ret_pct": m["total_net_pnl_pct"]
                })

        # 4. Parameter Perturbations on Strategy B (ADX & Entry Zone)
        param_scenarios_b = []
        for adx_lim in [20.0, 25.0, 30.0]:
            for zone in [0.15, 0.20, 0.25]:
                for tr in ["midpoint", "1.5r", "2.0r"]:
                    res = simulate_range_mean_reversion(bars, max_adx=adx_lim, entry_zone_pct=zone, target_type=tr)
                    m = res["metrics"]
                    param_scenarios_b.append({
                        "max_adx": adx_lim,
                        "entry_zone": zone,
                        "target_type": tr,
                        "trades": m["total_trades"],
                        "win_rate": m["win_rate_pct"],
                        "net_exp_r": m["net_expectancy_r"],
                        "pf": m["profit_factor"],
                        "max_dd_r": m["max_drawdown_r"],
                        "net_ret_pct": m["total_net_pnl_pct"]
                    })

        cases.append({
            "symbol": sym,
            "cost_sensitivity_a": cost_scenarios_a,
            "param_sensitivity_a": param_scenarios_a,
            "cost_sensitivity_b": cost_scenarios_b,
            "param_sensitivity_b": param_scenarios_b
        })
    return cases


def print_v3_summary(doc):
    """Print clean summary tables to terminal."""
    print("\n" + "=" * 125)
    print("STRATEGY V3 RESEARCH: UNTOUCHED OUT-OF-SAMPLE TEST SET RESULTS (FINAL 20%)")
    print("=" * 125)
    header = f"{'Symbol':<8} | {'TF':<4} | {'Strategy ID':<26} | {'Test N':<6} | {'Test Win%':<10} | {'Gross R':<8} | {'Net R':<8} | {'PF':<5} | {'MDD (R)':<8} | {'Net Ret':<8} | {'B&H':<8}"
    print(header)
    print("-" * 125)

    for item in doc["results"]:
        t = item["test_metrics"]
        b = item["benchmarks"]
        ci_str = f"{t['win_rate_pct']:.1f}%"
        print(f"{item['symbol']:<8} | {item['timeframe']:<4} | {item['strategy_id']:<26} | {t['total_trades']:<6} | {ci_str:<10} | {t['gross_expectancy_r']:+7.3f}R | {t['net_expectancy_r']:+7.3f}R | {t['profit_factor']:<5.2f} | -{t['max_drawdown_r']:<7.2f}R | {t['total_net_pnl_pct']:+7.2f}% | {b['bnh_test_pct']:+7.2f}%")

    print("=" * 125)

    print("\n" + "=" * 125)
    print("FULL DATASET OVERALL PERFORMANCE & REGIME BREAKDOWN")
    print("=" * 125)
    header_all = f"{'Symbol':<8} | {'TF':<4} | {'Strategy ID':<26} | {'Trades':<6} | {'Win% (95% CI)':<20} | {'Gross R':<8} | {'Net R':<8} | {'PF':<5} | {'MDD (R)':<8} | {'Bull R':<8} | {'Bear R':<8} | {'Chop R':<8}"
    print(header_all)
    print("-" * 125)

    for item in doc["results"]:
        o = item["overall_metrics"]
        regs = item["regimes"]
        ci_str = f"{o['win_rate_pct']:.1f}% [{o['win_rate_95_ci'][0]:.1f}-{o['win_rate_95_ci'][1]:.1f}%]"
        bull_r = regs.get("BULL_TREND", {}).get("net_expectancy_r", 0.0)
        bear_r = regs.get("BEAR_TREND", {}).get("net_expectancy_r", 0.0)
        chop_r = regs.get("RANGE_CHOP", {}).get("net_expectancy_r", 0.0)
        print(f"{item['symbol']:<8} | {item['timeframe']:<4} | {item['strategy_id']:<26} | {o['total_trades']:<6} | {ci_str:<20} | {o['gross_expectancy_r']:+7.3f}R | {o['net_expectancy_r']:+7.3f}R | {o['profit_factor']:<5.2f} | -{o['max_drawdown_r']:<7.2f}R | {bull_r:+7.3f}R | {bear_r:+7.3f}R | {chop_r:+7.3f}R")

    print("=" * 125)


if __name__ == "__main__":
    run_v3_research()
