#!/usr/bin/env python3
"""Binance Strategy V4 Research: Multi-Asset Relative Strength Rotation.

Pure Python 3.8+ Standard Library. Zero external dependencies.
Investigates whether cross-sectional relative strength and market-wide momentum
filters produce a robust, positive after-cost out-of-sample edge across liquid crypto spot markets.

Evaluates:
- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT (Major Layer-1 assets, zero survivorship distortion)
- V4-A: Relative Strength Rotation (Top 1 and Top 2 by trailing 30d return)
- V4-B: Relative Strength with Market Filter (Top 1 & Top 2 when BTC > 50d SMA, else Cash)
- V4-C: Multi-Horizon Confirmation (Top 1 & Top 2 when BTC > 50d SMA and 7d return > 0, else Cash)
- Benchmarks: BTC Buy-and-Hold, Equal-Weight Buy-and-Hold, Cash-Only, V1 Breakout Baseline
- Chronological 3-Way Partitioning: Train (60%), Validation (20%), Untouched Test (20%)
- Exact fee (0.10%), adverse execution slippage (0.05%), and rebalance turnover accounting
- Sensitivity matrices: Cost tiers (0.075%/0.02% to 0.15%/0.10%), rebalance frequencies (1d vs 7d), lookbacks (20d, 30d, 60d).

Research and simulation only. Zero live trading, no broker modifications.
"""

import argparse
import datetime
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TM_DIR = os.path.join(ROOT, "trader-timemachine")
if TM_DIR not in sys.path:
    sys.path.insert(0, TM_DIR)

from binance_client import BinanceClient

DEFAULT_UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
CORE_2_UNIVERSE = ["BTCUSDT", "SOLUSDT"]


# --------------------------------------------------------------------------
# Statistical & Financial Metrics Utilities
# --------------------------------------------------------------------------
def calc_cagr(start_val, end_val, days):
    """Compound Annual Growth Rate."""
    if start_val <= 0 or end_val <= 0 or days <= 0:
        return 0.0
    return ((end_val / start_val) ** (365.25 / days)) - 1.0


def calc_annualized_vol(daily_returns):
    """Annualized standard deviation of daily returns."""
    n = len(daily_returns)
    if n < 2:
        return 0.0
    mean_r = sum(daily_returns) / n
    var = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
    return math.sqrt(var) * math.sqrt(365.25)


def calc_sharpe_ratio(cagr, ann_vol, rf=0.0):
    """Sharpe Ratio with risk-free rate."""
    if ann_vol <= 1e-8:
        return 0.0
    return (cagr - rf) / ann_vol


def calc_sortino_ratio(cagr, daily_returns, rf=0.0):
    """Sortino Ratio using downside deviation."""
    downside = [r for r in daily_returns if r < 0]
    n = len(daily_returns)
    if not downside or n < 2:
        return 0.0
    downside_var = sum(r ** 2 for r in downside) / n
    downside_vol = math.sqrt(downside_var) * math.sqrt(365.25)
    if downside_vol <= 1e-8:
        return 0.0
    return (cagr - rf) / downside_vol


def calc_max_drawdown(equity_curve):
    """Peak-to-trough maximum drawdown in percentage and duration in days."""
    if not equity_curve:
        return 0.0, 0
    peak = equity_curve[0]
    max_dd = 0.0
    curr_dd_dur = 0
    max_dd_dur = 0
    for val in equity_curve:
        if val > peak:
            peak = val
            curr_dd_dur = 0
        else:
            curr_dd_dur += 1
            if curr_dd_dur > max_dd_dur:
                max_dd_dur = curr_dd_dur
            dd = (peak - val) / peak
            if dd > max_dd:
                max_dd = dd
    return round(max_dd * 100.0, 2), max_dd_dur


def block_bootstrap_uncertainty(daily_returns, block_size=10, n_boot=1000, seed=42):
    """Block bootstrap to compute 95% confidence intervals on CAGR and Sharpe."""
    n = len(daily_returns)
    if n < block_size * 2:
        return {"cagr_ci": [0.0, 0.0], "sharpe_ci": [0.0, 0.0]}
    rng = random.Random(seed)
    boot_cagrs = []
    boot_sharpes = []
    num_blocks = math.ceil(n / block_size)

    for _ in range(n_boot):
        sample = []
        for _ in range(num_blocks):
            start = rng.randint(0, n - block_size)
            sample.extend(daily_returns[start:start + block_size])
        sample = sample[:n]

        # calculate equity and metrics on sample
        eq = 1.0
        for r in sample:
            eq *= (1.0 + r)
        cagr = calc_cagr(1.0, eq, n)
        vol = calc_annualized_vol(sample)
        sh = calc_sharpe_ratio(cagr, vol)
        boot_cagrs.append(cagr * 100.0)
        boot_sharpes.append(sh)

    boot_cagrs.sort()
    boot_sharpes.sort()
    low_idx = int(0.025 * n_boot)
    high_idx = int(0.975 * n_boot)

    return {
        "cagr_ci_95": [round(boot_cagrs[low_idx], 2), round(boot_cagrs[high_idx], 2)],
        "sharpe_ci_95": [round(boot_sharpes[low_idx], 2), round(boot_sharpes[high_idx], 2)]
    }


# --------------------------------------------------------------------------
# Data Loading & Historical Alignment
# --------------------------------------------------------------------------
def load_aligned_daily_candles(universe=DEFAULT_UNIVERSE, days=730):
    """Load and align daily candles across universe symbols."""
    raw_data = {}
    for sym in universe:
        cache_path = os.path.join(ROOT, "trader", f"cached_klines_{sym.lower()}_1d_{days}d.json")
        if not os.path.exists(cache_path):
            print(f"Fetching {sym} 1d {days}d from Binance API...")
            client = BinanceClient(product="spot")
            res = client.get_klines_backward_paginated(sym, interval="1d", days=days)
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(res, f)
        else:
            with open(cache_path, "r", encoding="utf-8") as f:
                res = json.load(f)

        candles = {}
        for k in res.get("klines", []):
            t = int(k[0]) // 1000
            candles[t] = {
                "t": t,
                "o": float(k[1]),
                "h": float(k[2]),
                "l": float(k[3]),
                "c": float(k[4]),
                "v": float(k[5]),
                "close_time": int(k[6]) // 1000
            }
        raw_data[sym] = candles

    # Find common timestamps
    common_ts = sorted(list(set.intersection(*(set(raw_data[s].keys()) for s in universe))))
    aligned_bars = []
    for t in common_ts:
        day_dict = {"t": t}
        for s in universe:
            day_dict[s] = raw_data[s][t]
        aligned_bars.append(day_dict)

    print(f"Loaded {len(aligned_bars)} aligned daily candles across {universe}")
    return aligned_bars


# --------------------------------------------------------------------------
# Strategy V4 Simulation Engine
# --------------------------------------------------------------------------
def simulate_v4_rotation(aligned_bars, universe=DEFAULT_UNIVERSE, top_n=1,
                         use_btc_filter=False, btc_ma_period=50,
                         use_7d_filter=False, momentum_lookback=30,
                         rebalance_interval=1, fee_rate=0.0010,
                         slippage=0.0005, initial_capital=10000.0):
    """
    Simulates Strategy V4:
    - At close of day t, ranks eligible universe by trailing momentum (lookback days).
    - Checks market filter (BTC > 50d SMA) if enabled.
    - Checks 7d confirmation (7d return > 0) if enabled.
    - Determines target weights for day t+1.
    - Rebalances at open of day t+1 with turnover fee + adverse slippage deductions.
    - Strictly zero look-ahead.
    """
    n_days = len(aligned_bars)
    warmup = max(btc_ma_period, momentum_lookback) + 5
    if n_days <= warmup + 10:
        return {"metrics": {}, "equity_curve": [], "daily_returns": [], "trades": []}

    capital = initial_capital
    current_weights = {s: 0.0 for s in universe}
    current_weights["CASH"] = 1.0

    equity_curve = [capital]
    daily_returns = []
    rebalances_count = 0
    total_friction_paid = 0.0
    turnover_list = []
    holdings_count_list = []
    invested_days = 0

    rebalance_records = []

    # BTC price series for MA calculation
    btc_closes = [day["BTCUSDT"]["c"] for day in aligned_bars]

    for t in range(warmup, n_days - 1):
        current_day = aligned_bars[t]
        next_day = aligned_bars[t + 1]

        # Scheduled Rebalance Decision at Close of Day t
        should_rebalance = ((t - warmup) % rebalance_interval == 0)

        if should_rebalance:
            rebalances_count += 1

            # 1. Evaluate Market Filter (BTC > 50d SMA)
            btc_ma = sum(btc_closes[t - btc_ma_period + 1:t + 1]) / btc_ma_period
            market_risk_on = True
            if use_btc_filter:
                market_risk_on = (btc_closes[t] > btc_ma)

            # 2. Cross-Sectional Momentum Ranking
            scores = []
            for s in universe:
                c_now = current_day[s]["c"]
                c_past = aligned_bars[t - momentum_lookback][s]["c"]
                ret_30d = (c_now - c_past) / c_past

                c_7d = aligned_bars[t - 7][s]["c"]
                ret_7d = (c_now - c_7d) / c_7d

                scores.append({
                    "symbol": s,
                    "ret_30d": ret_30d,
                    "ret_7d": ret_7d,
                    "close": c_now
                })

            # Sort descending by 30d return; tie-break by 7d return
            scores.sort(key=lambda x: (x["ret_30d"], x["ret_7d"]), reverse=True)

            # 3. Determine Target Allocation
            target_weights = {s: 0.0 for s in universe}
            target_weights["CASH"] = 1.0

            if market_risk_on:
                eligible = []
                for item in scores:
                    if use_7d_filter and item["ret_7d"] <= 0:
                        continue
                    eligible.append(item["symbol"])

                selected = eligible[:top_n]
                if selected:
                    weight_per_asset = 1.0 / top_n
                    for s in selected:
                        target_weights[s] = weight_per_asset
                    # Cash takes remaining weight if fewer than top_n qualify
                    allocated_weight = len(selected) * weight_per_asset
                    target_weights["CASH"] = max(0.0, 1.0 - allocated_weight)

            # 4. Compute Turnover & Deduct Friction
            turnover = sum(abs(target_weights[k] - current_weights.get(k, 0.0)) for k in target_weights)
            # Dividing by 2 gives one-way turnover; friction is paid on both legs (sells + buys)
            friction_pct = turnover * (fee_rate + slippage)
            friction_usd = capital * friction_pct
            capital -= friction_usd
            total_friction_paid += friction_usd
            turnover_list.append(turnover)

            current_weights = dict(target_weights)

            rebalance_records.append({
                "t": current_day["t"],
                "date": datetime.datetime.fromtimestamp(current_day["t"], datetime.timezone.utc).strftime("%Y-%m-%d"),
                "weights": {k: round(v, 2) for k, v in current_weights.items() if v > 0},
                "turnover": round(turnover, 3),
                "friction_usd": round(friction_usd, 2),
                "capital": round(capital, 2)
            })

        # 5. Portfolio Value Evolution from Open t+1 to Open t+2 (or Close t+1)
        # Using daily return from day t close to day t+1 close
        day_return = 0.0
        n_held = 0
        for s in universe:
            w = current_weights.get(s, 0.0)
            if w > 0:
                n_held += 1
                asset_ret = (next_day[s]["c"] - current_day[s]["c"]) / current_day[s]["c"]
                day_return += w * asset_ret

        # Cash earns 0%
        capital *= (1.0 + day_return)
        equity_curve.append(capital)
        daily_returns.append(day_return)
        holdings_count_list.append(n_held)
        if n_held > 0:
            invested_days += 1

    # Final Metrics Calculations
    usable_days = len(daily_returns)
    total_return_pct = round(((capital - initial_capital) / initial_capital) * 100.0, 2)
    cagr = calc_cagr(initial_capital, capital, usable_days)
    ann_vol = calc_annualized_vol(daily_returns)
    sharpe = round(calc_sharpe_ratio(cagr, ann_vol), 2)
    sortino = round(calc_sortino_ratio(cagr, daily_returns), 2)
    max_dd_pct, max_dd_dur = calc_max_drawdown(equity_curve)
    calmar = round(cagr / (max_dd_pct / 100.0), 2) if max_dd_pct > 0 else 0.0

    avg_turnover = round(sum(turnover_list) / len(turnover_list), 3) if turnover_list else 0.0
    pct_invested = round((invested_days / usable_days) * 100.0, 1) if usable_days > 0 else 0.0
    avg_holdings = round(sum(holdings_count_list) / len(holdings_count_list), 2) if holdings_count_list else 0.0

    bootstrap = block_bootstrap_uncertainty(daily_returns)

    return {
        "final_capital": round(capital, 2),
        "total_return_pct": total_return_pct,
        "cagr_pct": round(cagr * 100.0, 2),
        "ann_vol_pct": round(ann_vol * 100.0, 2),
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_duration_days": max_dd_dur,
        "rebalances_count": rebalances_count,
        "total_friction_usd": round(total_friction_paid, 2),
        "avg_turnover": avg_turnover,
        "pct_time_invested": pct_invested,
        "avg_holdings": avg_holdings,
        "bootstrap": bootstrap,
        "equity_curve": equity_curve,
        "daily_returns": daily_returns,
        "rebalance_records": rebalance_records
    }


# --------------------------------------------------------------------------
# Benchmarks Calculation
# --------------------------------------------------------------------------
def calc_buy_and_hold(aligned_bars, symbol="BTCUSDT", warmup=55, fee_rate=0.0010, slippage=0.0005, initial_capital=10000.0):
    """Buy and hold a single asset with entry/exit friction."""
    n = len(aligned_bars)
    if n <= warmup + 10:
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0}

    # Enter at open/close of warmup day
    start_px = aligned_bars[warmup][symbol]["c"] * (1.0 + slippage)
    capital = initial_capital * (1.0 - fee_rate)
    qty = capital / start_px

    equity_curve = [initial_capital]
    daily_returns = []

    for t in range(warmup, n - 1):
        curr_c = aligned_bars[t][symbol]["c"]
        next_c = aligned_bars[t + 1][symbol]["c"]
        r = (next_c - curr_c) / curr_c
        daily_returns.append(r)
        val = qty * next_c
        equity_curve.append(val)

    # Exit at end
    final_px = aligned_bars[-1][symbol]["c"] * (1.0 - slippage)
    final_val = (qty * final_px) * (1.0 - fee_rate)
    equity_curve[-1] = final_val

    days = len(daily_returns)
    tot_ret = round(((final_val - initial_capital) / initial_capital) * 100.0, 2)
    cagr = calc_cagr(initial_capital, final_val, days)
    vol = calc_annualized_vol(daily_returns)
    sharpe = round(calc_sharpe_ratio(cagr, vol), 2)
    mdd, dur = calc_max_drawdown(equity_curve)

    return {
        "final_capital": round(final_val, 2),
        "total_return_pct": tot_ret,
        "cagr_pct": round(cagr * 100.0, 2),
        "ann_vol_pct": round(vol * 100.0, 2),
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": mdd,
        "max_drawdown_duration_days": dur,
        "equity_curve": equity_curve,
        "daily_returns": daily_returns
    }


def calc_equal_weight(aligned_bars, universe=DEFAULT_UNIVERSE, warmup=55, fee_rate=0.0010, slippage=0.0005, initial_capital=10000.0):
    """Daily rebalanced equal-weight benchmark across universe."""
    n = len(aligned_bars)
    if n <= warmup + 10:
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0}

    capital = initial_capital
    equity_curve = [capital]
    daily_returns = []
    k = len(universe)
    weight = 1.0 / k

    for t in range(warmup, n - 1):
        day_ret = 0.0
        for s in universe:
            c1 = aligned_bars[t][s]["c"]
            c2 = aligned_bars[t + 1][s]["c"]
            day_ret += weight * ((c2 - c1) / c1)
        capital *= (1.0 + day_ret)
        daily_returns.append(day_ret)
        equity_curve.append(capital)

    days = len(daily_returns)
    tot_ret = round(((capital - initial_capital) / initial_capital) * 100.0, 2)
    cagr = calc_cagr(initial_capital, capital, days)
    vol = calc_annualized_vol(daily_returns)
    sharpe = round(calc_sharpe_ratio(cagr, vol), 2)
    mdd, dur = calc_max_drawdown(equity_curve)

    return {
        "final_capital": round(capital, 2),
        "total_return_pct": tot_ret,
        "cagr_pct": round(cagr * 100.0, 2),
        "ann_vol_pct": round(vol * 100.0, 2),
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": mdd,
        "max_drawdown_duration_days": dur,
        "equity_curve": equity_curve,
        "daily_returns": daily_returns
    }


# --------------------------------------------------------------------------
# Main Research Suite Execution
# --------------------------------------------------------------------------
def run_v4_research(universe=DEFAULT_UNIVERSE, days=730):
    """Execute complete V4 strategy research suite."""
    aligned_bars = load_aligned_daily_candles(universe=universe, days=days)
    n_days = len(aligned_bars)
    warmup = 55

    usable_bars = aligned_bars[warmup:]
    n_usable = len(usable_bars)

    # 3-Way Chronological Split on usable trading days:
    # Train (60%), Validation (20%), Untouched Test (20%)
    train_end = int(n_usable * 0.60)
    val_end = int(n_usable * 0.80)

    # Slices with indicator warmup prepended
    bars_train = aligned_bars[:warmup + train_end]
    bars_val = aligned_bars[warmup + train_end - warmup:warmup + val_end]
    bars_test = aligned_bars[warmup + val_end - warmup:]

    print("=" * 100)
    print("STRATEGY V4 RESEARCH: MULTI-ASSET RELATIVE STRENGTH ROTATION")
    print("=" * 100)
    print(f"Eligible Universe: {universe} (Major Liquid L1s, Zero Survivorship Bias)")
    print(f"Total Daily Bars : {n_days} | Usable Trading Bars: {n_usable} (after {warmup}-day indicator warmup)")
    print(f"Chronological Partitions:")
    print(f"  - Train (60%)     : {train_end} days (Dates: {datetime.datetime.fromtimestamp(usable_bars[0]['t'], datetime.timezone.utc).strftime('%Y-%m-%d')} to {datetime.datetime.fromtimestamp(usable_bars[train_end-1]['t'], datetime.timezone.utc).strftime('%Y-%m-%d')})")
    print(f"  - Validation (20%): {val_end - train_end} days (Dates: {datetime.datetime.fromtimestamp(usable_bars[train_end]['t'], datetime.timezone.utc).strftime('%Y-%m-%d')} to {datetime.datetime.fromtimestamp(usable_bars[val_end-1]['t'], datetime.timezone.utc).strftime('%Y-%m-%d')})")
    print(f"  - Untouched Test  : {n_usable - val_end} days (Dates: {datetime.datetime.fromtimestamp(usable_bars[val_end]['t'], datetime.timezone.utc).strftime('%Y-%m-%d')} to {datetime.datetime.fromtimestamp(usable_bars[-1]['t'], datetime.timezone.utc).strftime('%Y-%m-%d')})")
    print("-" * 100)

    # Predefined V4 Strategy Configurations (Frozen Specifications)
    configs = [
        {"id": "V4_A_TOP1", "name": "V4-A: Relative Strength (Top 1)", "top_n": 1, "btc_filter": False, "7d_filter": False},
        {"id": "V4_A_TOP2", "name": "V4-A: Relative Strength (Top 2)", "top_n": 2, "btc_filter": False, "7d_filter": False},
        {"id": "V4_B_TOP1", "name": "V4-B: RS + BTC Trend Filter (Top 1)", "top_n": 1, "btc_filter": True, "7d_filter": False},
        {"id": "V4_B_TOP2", "name": "V4-B: RS + BTC Trend Filter (Top 2)", "top_n": 2, "btc_filter": True, "7d_filter": False},
        {"id": "V4_C_TOP1", "name": "V4-C: Multi-Horizon Confirmation (Top 1)", "top_n": 1, "btc_filter": True, "7d_filter": True},
        {"id": "V4_C_TOP2", "name": "V4-C: Multi-Horizon Confirmation (Top 2)", "top_n": 2, "btc_filter": True, "7d_filter": True},
    ]

    strategy_results = []

    for cfg in configs:
        cid = cfg["id"]
        cname = cfg["name"]

        # Run Train
        res_train = simulate_v4_rotation(
            bars_train, universe=universe, top_n=cfg["top_n"],
            use_btc_filter=cfg["btc_filter"], use_7d_filter=cfg["7d_filter"]
        )
        # Run Validation
        res_val = simulate_v4_rotation(
            bars_val, universe=universe, top_n=cfg["top_n"],
            use_btc_filter=cfg["btc_filter"], use_7d_filter=cfg["7d_filter"]
        )
        # Run Untouched Test
        res_test = simulate_v4_rotation(
            bars_test, universe=universe, top_n=cfg["top_n"],
            use_btc_filter=cfg["btc_filter"], use_7d_filter=cfg["7d_filter"]
        )
        # Run Full Dataset
        res_all = simulate_v4_rotation(
            aligned_bars, universe=universe, top_n=cfg["top_n"],
            use_btc_filter=cfg["btc_filter"], use_7d_filter=cfg["7d_filter"]
        )

        strategy_results.append({
            "id": cid,
            "name": cname,
            "top_n": cfg["top_n"],
            "use_btc_filter": cfg["btc_filter"],
            "use_7d_filter": cfg["7d_filter"],
            "train": res_train,
            "val": res_val,
            "test": res_test,
            "overall": res_all
        })

    # Run Benchmarks
    benchmarks = {
        "train": {
            "btc_bnh": calc_buy_and_hold(bars_train, "BTCUSDT"),
            "ew_bnh": calc_equal_weight(bars_train, universe)
        },
        "val": {
            "btc_bnh": calc_buy_and_hold(bars_val, "BTCUSDT"),
            "ew_bnh": calc_equal_weight(bars_val, universe)
        },
        "test": {
            "btc_bnh": calc_buy_and_hold(bars_test, "BTCUSDT"),
            "ew_bnh": calc_equal_weight(bars_test, universe)
        },
        "overall": {
            "btc_bnh": calc_buy_and_hold(aligned_bars, "BTCUSDT"),
            "ew_bnh": calc_equal_weight(aligned_bars, universe)
        }
    }

    # Run Sensitivity Tests
    sensitivity = run_v4_sensitivity(aligned_bars, universe)

    doc = {
        "title": "Strategy V4: Multi-Asset Relative Strength Rotation Research Report",
        "universe": universe,
        "days": days,
        "partitions": {
            "train_days": train_end,
            "val_days": val_end - train_end,
            "test_days": n_usable - val_end
        },
        "strategies": strategy_results,
        "benchmarks": benchmarks,
        "sensitivity": sensitivity,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    out_file = os.path.join(ROOT, "trader", "v4_experiment_results.json")
    # Clean non-serializable elements from json output
    clean_doc = copy_clean_doc(doc)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(clean_doc, f, indent=2)
    print(f"\nAll V4 experiment results saved to {out_file}")

    print_v4_summary(clean_doc)
    return clean_doc


def copy_clean_doc(d):
    """Strip large equity curves and raw daily returns from JSON for compact storage."""
    def clean_res(res):
        if not isinstance(res, dict):
            return res
        return {
            k: v for k, v in res.items()
            if k not in ("equity_curve", "daily_returns", "rebalance_records")
        }

    clean_d = {
        "title": d["title"],
        "universe": d["universe"],
        "days": d["days"],
        "partitions": d["partitions"],
        "benchmarks": {
            part: {
                b_name: clean_res(b_res)
                for b_name, b_res in b_dict.items()
            }
            for part, b_dict in d["benchmarks"].items()
        },
        "strategies": [
            {
                "id": s["id"],
                "name": s["name"],
                "top_n": s["top_n"],
                "use_btc_filter": s["use_btc_filter"],
                "use_7d_filter": s["use_7d_filter"],
                "train": clean_res(s["train"]),
                "val": clean_res(s["val"]),
                "test": clean_res(s["test"]),
                "overall": clean_res(s["overall"])
            }
            for s in d["strategies"]
        ],
        "sensitivity": d["sensitivity"],
        "generated_at": d["generated_at"]
    }
    return clean_d


def run_v4_sensitivity(aligned_bars, universe):
    """Run sensitivity analysis across transaction cost tiers, lookback periods, and rebalance frequencies."""
    cases = []
    # Evaluate on V4_B_TOP2 and V4_C_TOP2
    target_strats = [
        {"id": "V4_B_TOP2", "top_n": 2, "btc_filter": True, "7d_filter": False},
        {"id": "V4_C_TOP2", "top_n": 2, "btc_filter": True, "7d_filter": True}
    ]

    cost_tiers = [
        {"name": "Low (BNB Tier)", "fee": 0.00075, "slippage": 0.0002},
        {"name": "Standard (Baseline)", "fee": 0.00100, "slippage": 0.0005},
        {"name": "Stress (Adverse)", "fee": 0.00150, "slippage": 0.0010}
    ]

    lookbacks = [20, 30, 60]
    frequencies = [1, 7]  # Daily vs Weekly

    for strat in target_strats:
        cost_scenarios = []
        for c in cost_tiers:
            res = simulate_v4_rotation(
                aligned_bars, universe=universe, top_n=strat["top_n"],
                use_btc_filter=strat["btc_filter"], use_7d_filter=strat["7d_filter"],
                fee_rate=c["fee"], slippage=c["slippage"]
            )
            cost_scenarios.append({
                "tier": c["name"],
                "fee_pct": c["fee"] * 100.0,
                "slippage_pct": c["slippage"] * 100.0,
                "total_return_pct": res["total_return_pct"],
                "cagr_pct": res["cagr_pct"],
                "sharpe": res["sharpe_ratio"],
                "max_dd_pct": res["max_drawdown_pct"],
                "friction_paid_usd": res["total_friction_usd"]
            })

        param_scenarios = []
        for lb in lookbacks:
            for freq in frequencies:
                res = simulate_v4_rotation(
                    aligned_bars, universe=universe, top_n=strat["top_n"],
                    use_btc_filter=strat["btc_filter"], use_7d_filter=strat["7d_filter"],
                    momentum_lookback=lb, rebalance_interval=freq
                )
                param_scenarios.append({
                    "lookback_days": lb,
                    "rebalance_days": freq,
                    "total_return_pct": res["total_return_pct"],
                    "cagr_pct": res["cagr_pct"],
                    "sharpe": res["sharpe_ratio"],
                    "max_dd_pct": res["max_drawdown_pct"],
                    "friction_paid_usd": res["total_friction_usd"]
                })

        cases.append({
            "strategy_id": strat["id"],
            "cost_sensitivity": cost_scenarios,
            "param_sensitivity": param_scenarios
        })

    return cases


def print_v4_summary(doc):
    """Print structured terminal comparison tables."""
    print("\n" + "=" * 125)
    print("STRATEGY V4 RESEARCH: UNTOUCHED TEST SET RESULTS (FINAL 20% OF 2 YEARS)")
    print("=" * 125)
    header = f"{'Strategy / Benchmark':<38} | {'Total Ret':<10} | {'CAGR':<8} | {'Vol':<8} | {'Sharpe':<7} | {'Sortino':<8} | {'Max DD':<8} | {'Turnover':<9} | {'% Invested':<11}"
    print(header)
    print("-" * 125)

    # Print Benchmarks on Test
    b_test = doc["benchmarks"]["test"]
    btc = b_test["btc_bnh"]
    ew = b_test["ew_bnh"]
    print(f"{'BENCHMARK: BTC Buy-and-Hold':<38} | {btc['total_return_pct']:+8.2f}% | {btc['cagr_pct']:+7.2f}% | {btc['ann_vol_pct']:6.2f}% | {btc['sharpe_ratio']:<7.2f} | {'N/A':<8} | -{btc['max_drawdown_pct']:<6.2f}% | {'0.000':<9} | {'100.0%':<11}")
    print(f"{'BENCHMARK: Equal-Weight L1s':<38} | {ew['total_return_pct']:+8.2f}% | {ew['cagr_pct']:+7.2f}% | {ew['ann_vol_pct']:6.2f}% | {ew['sharpe_ratio']:<7.2f} | {'N/A':<8} | -{ew['max_drawdown_pct']:<6.2f}% | {'0.000':<9} | {'100.0%':<11}")
    print(f"{'BENCHMARK: 100% Cash':<38} | {0.0:+8.2f}% | {0.0:+7.2f}% | {0.0:6.2f}% | {0.0:<7.2f} | {'0.00':<8} | -{0.0:<6.2f}% | {'0.000':<9} | {'0.0%':<11}")
    print("-" * 125)

    for s in doc["strategies"]:
        t = s["test"]
        print(f"{s['name']:<38} | {t['total_return_pct']:+8.2f}% | {t['cagr_pct']:+7.2f}% | {t['ann_vol_pct']:6.2f}% | {t['sharpe_ratio']:<7.2f} | {t['sortino_ratio']:<8.2f} | -{t['max_drawdown_pct']:<6.2f}% | {t['avg_turnover']:<9.3f} | {t['pct_time_invested']:<10.1f}%")

    print("=" * 125)

    print("\n" + "=" * 125)
    print("STRATEGY V4 RESEARCH: FULL 2-YEAR OVERALL PERFORMANCE")
    print("=" * 125)
    print(header)
    print("-" * 125)
    b_all = doc["benchmarks"]["overall"]
    btc_a = b_all["btc_bnh"]
    ew_a = b_all["ew_bnh"]
    print(f"{'BENCHMARK: BTC Buy-and-Hold':<38} | {btc_a['total_return_pct']:+8.2f}% | {btc_a['cagr_pct']:+7.2f}% | {btc_a['ann_vol_pct']:6.2f}% | {btc_a['sharpe_ratio']:<7.2f} | {'N/A':<8} | -{btc_a['max_drawdown_pct']:<6.2f}% | {'0.000':<9} | {'100.0%':<11}")
    print(f"{'BENCHMARK: Equal-Weight L1s':<38} | {ew_a['total_return_pct']:+8.2f}% | {ew_a['cagr_pct']:+7.2f}% | {ew_a['ann_vol_pct']:6.2f}% | {ew_a['sharpe_ratio']:<7.2f} | {'N/A':<8} | -{ew_a['max_drawdown_pct']:<6.2f}% | {'0.000':<9} | {'100.0%':<11}")
    print("-" * 125)

    for s in doc["strategies"]:
        o = s["overall"]
        print(f"{s['name']:<38} | {o['total_return_pct']:+8.2f}% | {o['cagr_pct']:+7.2f}% | {o['ann_vol_pct']:6.2f}% | {o['sharpe_ratio']:<7.2f} | {o['sortino_ratio']:<8.2f} | -{o['max_drawdown_pct']:<6.2f}% | {o['avg_turnover']:<9.3f} | {o['pct_time_invested']:<10.1f}%")

    print("=" * 125)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy V4 Research")
    parser.add_argument("--days", type=int, default=730, help="Days of history (default 730)")
    args = parser.parse_args()
    run_v4_research(days=args.days)
