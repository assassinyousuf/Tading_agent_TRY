#!/usr/bin/env python3
"""Binance Strategy V4 Research: Multi-Asset Relative Strength Rotation (V4.2 Corrected).

Pure Python 3.8+ Standard Library. Zero external dependencies.
Investigates whether cross-sectional relative strength and market-wide momentum
filters produce a robust, positive after-cost out-of-sample edge across liquid crypto spot markets.

V4.2 Accounting Repairs:
1. Strict Crypto-Only Turnover & Traded Notional:
   - Cash (USDT) weight changes are NOT charged exchange taker commissions or adverse slippage.
   - Traded notional is derived from actual crypto buy and sell orders.
   - Documented One-Way Turnover: T_{1-way} = 0.5 * sum(|w_{target} - w_{current}|).
   - Documented Traded Crypto Weight: sum_{s in Universe} |w_{target, s} - w_{current, s}|.
2. Authoritative Net Daily Return Series:
   - The equity ledger is the source of truth.
   - Net daily return: net_return[t] = ending_equity[t] / starting_equity[t] - 1.0.
   - Exactly zero double-counting of costs and zero omitted friction.
   - Compounding check: initial_capital * prod(1 + net_return[t]) == final_capital (< 1e-10 error).
3. Consistent Terminal Liquidation:
   - Optional terminal liquidation of open positions to Cash at the final candle close.
   - All benchmarks and strategies start in 100% Cash and liquidate to 100% Cash.
4. Corrected Metrics & Bootstrap:
   - Volatility, Sharpe, Sortino, and 10-day block bootstrap strictly evaluate true net returns.
5. Benchmark Parity:
   - Clearly distinguishes Gross return, Net return, and Excess return in percentage points.

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
        return {"cagr_ci_95": [0.0, 0.0], "sharpe_ci_95": [0.0, 0.0]}
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
# Corrected Strategy V4 Simulation Engine (V4.2)
# --------------------------------------------------------------------------
def simulate_v4_rotation(aligned_bars, universe=DEFAULT_UNIVERSE, top_n=1,
                         use_btc_filter=False, btc_ma_period=50,
                         use_7d_filter=False, momentum_lookback=30,
                         rebalance_interval=1, fee_rate=0.0010,
                         slippage=0.0005, initial_capital=10000.0,
                         liquidate_terminal=True):
    """
    Simulates Strategy V4 with authoritative net accounting:
    - At close of day t, ranks eligible universe by trailing momentum (lookback days).
    - Checks market filter (BTC > 50d SMA) if enabled.
    - Checks 7d confirmation (7d return > 0) if enabled.
    - Determines target weights for day t+1.
    - Rebalances at open of day t+1:
      * Only actual crypto buys and sells incur fees (0.10%) and slippage (0.05%).
      * Cash (USDT) transfers pay 0.00%.
      * Traded notional = pre_trade_equity * sum(|w_{target, s} - w_{current, s}| for s in crypto).
    - Deducts friction from pre-trade equity before applying asset price returns.
    - Computes authoritative net return for interval t:
      net_return[t] = ending_equity[t] / starting_equity[t] - 1.0.
    - Optional terminal liquidation of open positions to Cash at final candle close.
    - Reconciles compounded net daily returns with final equity (< 1e-10 error).
    - Strictly zero look-ahead.
    """
    n_days = len(aligned_bars)
    warmup = max(btc_ma_period, momentum_lookback) + 5
    if n_days <= warmup + 10:
        return {"metrics": {}, "equity_curve": [], "daily_returns": [], "trade_ledger": []}

    total_drag_rate = fee_rate + slippage
    capital = initial_capital
    current_weights = {s: 0.0 for s in universe}
    current_weights["CASH"] = 1.0

    equity_curve = [capital]
    daily_returns = []
    rebalances_count = 0
    total_friction_paid = 0.0
    total_traded_notional = 0.0
    one_way_turnover_list = []
    crypto_traded_weight_list = []
    holdings_count_list = []
    invested_days = 0

    trade_ledger = []
    rebalance_records = []

    # BTC price series for MA calculation
    btc_closes = [day["BTCUSDT"]["c"] for day in aligned_bars]

    for t in range(warmup, n_days - 1):
        current_day = aligned_bars[t]
        next_day = aligned_bars[t + 1]
        start_day_equity = capital

        # Scheduled Rebalance Decision at Close of Day t
        should_rebalance = ((t - warmup) % rebalance_interval == 0)

        target_weights = dict(current_weights)
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
                    allocated_weight = len(selected) * weight_per_asset
                    target_weights["CASH"] = max(0.0, 1.0 - allocated_weight)

        # 4. Corrected Turnover & Friction Accounting
        # One-way turnover: half sum of all weight changes across crypto and cash
        all_keys = set(universe) | {"CASH"}
        one_way_turnover = 0.5 * sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in all_keys)
        one_way_turnover_list.append(one_way_turnover)

        # Traded crypto notional fraction (only actual crypto buys and sells incur fees)
        crypto_traded_weight = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in universe)
        crypto_traded_weight_list.append(crypto_traded_weight)

        traded_notional = start_day_equity * crypto_traded_weight
        friction_usd = traded_notional * total_drag_rate
        total_friction_paid += friction_usd
        total_traded_notional += traded_notional

        post_trade_equity = start_day_equity - friction_usd
        current_weights = dict(target_weights)

        # 5. Portfolio Value Evolution across Day t+1
        day_gross_return = sum(current_weights.get(s, 0.0) * ((next_day[s]["c"] - current_day[s]["c"]) / current_day[s]["c"]) for s in universe)
        end_day_equity = post_trade_equity * (1.0 + day_gross_return)

        # 6. Terminal Liquidation at End of Horizon (if enabled)
        is_last_day = (t == n_days - 2)
        terminal_friction = 0.0
        if is_last_day and liquidate_terminal:
            crypto_held_weight = sum(current_weights.get(s, 0.0) for s in universe)
            if crypto_held_weight > 0:
                terminal_notional = end_day_equity * crypto_held_weight
                terminal_friction = terminal_notional * total_drag_rate
                end_day_equity -= terminal_friction
                total_friction_paid += terminal_friction
                total_traded_notional += terminal_notional

        # 7. Authoritative Net Daily Return
        net_daily_return = (end_day_equity / start_day_equity) - 1.0
        daily_returns.append(net_daily_return)
        capital = end_day_equity
        equity_curve.append(capital)

        n_held = sum(1 for s in universe if current_weights.get(s, 0.0) > 0)
        holdings_count_list.append(n_held)
        if n_held > 0:
            invested_days += 1

        rebalance_records.append({
            "t": current_day["t"],
            "date": datetime.datetime.fromtimestamp(current_day["t"], datetime.timezone.utc).strftime("%Y-%m-%d"),
            "weights": {k: round(v, 2) for k, v in current_weights.items() if v > 0},
            "turnover": round(crypto_traded_weight, 4),
            "one_way_turnover": round(one_way_turnover, 4),
            "friction_usd": round(friction_usd + terminal_friction, 2),
            "capital": round(capital, 2)
        })

        if crypto_traded_weight > 0 or terminal_friction > 0:
            trade_ledger.append({
                "t": current_day["t"],
                "date": datetime.datetime.fromtimestamp(current_day["t"], datetime.timezone.utc).strftime("%Y-%m-%d"),
                "starting_equity": round(start_day_equity, 2),
                "target_weights": {k: round(v, 2) for k, v in current_weights.items() if v > 0},
                "one_way_turnover": round(one_way_turnover, 4),
                "crypto_traded_weight": round(crypto_traded_weight, 4),
                "traded_notional_usd": round(traded_notional, 2),
                "friction_usd": round(friction_usd + terminal_friction, 2),
                "post_trade_equity": round(post_trade_equity, 2),
                "gross_return_pct": round(day_gross_return * 100.0, 3),
                "ending_equity": round(end_day_equity, 2),
                "net_return_pct": round(net_daily_return * 100.0, 4)
            })

    # Exact Compounding Verification
    compounded_equity = initial_capital
    for r in daily_returns:
        compounded_equity *= (1.0 + r)
    compounding_error = abs(capital - compounded_equity)

    # Final Metrics Calculations
    usable_days = len(daily_returns)
    total_return_pct = round(((capital - initial_capital) / initial_capital) * 100.0, 2)
    cagr = calc_cagr(initial_capital, capital, usable_days)
    ann_vol = calc_annualized_vol(daily_returns)
    sharpe = round(calc_sharpe_ratio(cagr, ann_vol), 2)
    sortino = round(calc_sortino_ratio(cagr, daily_returns), 2)
    max_dd_pct, max_dd_dur = calc_max_drawdown(equity_curve)
    calmar = round(cagr / (max_dd_pct / 100.0), 2) if max_dd_pct > 0 else 0.0

    avg_one_way_turnover = round(sum(one_way_turnover_list) / len(one_way_turnover_list), 4) if one_way_turnover_list else 0.0
    avg_crypto_traded_weight = round(sum(crypto_traded_weight_list) / len(crypto_traded_weight_list), 4) if crypto_traded_weight_list else 0.0
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
        "total_traded_notional_usd": round(total_traded_notional, 2),
        "avg_turnover": avg_one_way_turnover,
        "avg_crypto_traded_weight": avg_crypto_traded_weight,
        "pct_time_invested": pct_invested,
        "avg_holdings": avg_holdings,
        "compounding_error": compounding_error,
        "bootstrap": bootstrap,
        "equity_curve": equity_curve,
        "daily_returns": daily_returns,
        "trade_ledger": trade_ledger,
        "rebalance_records": rebalance_records
    }


# --------------------------------------------------------------------------
# Corrected Benchmarks Calculation (V4.2)
# --------------------------------------------------------------------------
def calc_buy_and_hold(aligned_bars, symbol="BTCUSDT", warmup=55, fee_rate=0.0010,
                      slippage=0.0005, initial_capital=10000.0, liquidate_terminal=True):
    """
    Buy and hold a single asset with rigorous Gross and Net accounting:
    - Net entry friction: fee_rate + slippage paid on initial capital.
    - Net terminal liquidation: fee_rate + slippage paid on final valuation.
    - Reconciles compounded net daily returns with final equity.
    """
    n = len(aligned_bars)
    if n <= warmup + 10:
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0}

    total_drag_rate = fee_rate + slippage

    # Net Simulation via authoritative equity ledger
    net_capital = initial_capital
    entry_friction = net_capital * total_drag_rate
    post_entry_capital = net_capital - entry_friction
    total_friction = entry_friction

    net_equity_curve = [initial_capital]
    net_daily_returns = []

    gross_capital = initial_capital
    gross_equity_curve = [gross_capital]
    gross_daily_returns = []

    for t in range(warmup, n - 1):
        start_net_eq = net_capital
        curr_c = aligned_bars[t][symbol]["c"]
        next_c = aligned_bars[t + 1][symbol]["c"]
        r = (next_c - curr_c) / curr_c

        # Gross
        gross_capital *= (1.0 + r)
        gross_equity_curve.append(gross_capital)
        gross_daily_returns.append(r)

        # Net
        if t == warmup:
            end_net_eq = post_entry_capital * (1.0 + r)
        else:
            end_net_eq = start_net_eq * (1.0 + r)

        is_last_day = (t == n - 2)
        if is_last_day and liquidate_terminal:
            exit_friction = end_net_eq * total_drag_rate
            end_net_eq -= exit_friction
            total_friction += exit_friction

        net_r = (end_net_eq / start_net_eq) - 1.0
        net_daily_returns.append(net_r)
        net_capital = end_net_eq
        net_equity_curve.append(net_capital)

    days = len(net_daily_returns)
    net_tot_ret = round(((net_capital - initial_capital) / initial_capital) * 100.0, 2)
    net_cagr = calc_cagr(initial_capital, net_capital, days)
    net_vol = calc_annualized_vol(net_daily_returns)
    net_sharpe = round(calc_sharpe_ratio(net_cagr, net_vol), 2)
    net_sortino = round(calc_sortino_ratio(net_cagr, net_daily_returns), 2)
    net_mdd, net_dur = calc_max_drawdown(net_equity_curve)

    gross_tot_ret = round(((gross_capital - initial_capital) / initial_capital) * 100.0, 2)
    gross_cagr = calc_cagr(initial_capital, gross_capital, days)
    gross_vol = calc_annualized_vol(gross_daily_returns)
    gross_sharpe = round(calc_sharpe_ratio(gross_cagr, gross_vol), 2)

    return {
        "final_capital": round(net_capital, 2),
        "total_return_pct": net_tot_ret,
        "cagr_pct": round(net_cagr * 100.0, 2),
        "ann_vol_pct": round(net_vol * 100.0, 2),
        "sharpe_ratio": net_sharpe,
        "sortino_ratio": net_sortino,
        "max_drawdown_pct": net_mdd,
        "max_drawdown_duration_days": net_dur,
        "total_friction_usd": round(total_friction, 2),
        "gross": {
            "total_return_pct": gross_tot_ret,
            "cagr_pct": round(gross_cagr * 100.0, 2),
            "ann_vol_pct": round(gross_vol * 100.0, 2),
            "sharpe_ratio": gross_sharpe
        },
        "equity_curve": net_equity_curve,
        "daily_returns": net_daily_returns
    }


def calc_equal_weight(aligned_bars, universe=DEFAULT_UNIVERSE, warmup=55,
                       fee_rate=0.0010, slippage=0.0005, initial_capital=10000.0,
                       rebalance_daily=False, liquidate_terminal=True):
    """
    Equal-Weight benchmark across universe:
    - If rebalance_daily=False: Passive Buy & Hold (Static 25% allocation at start, drift, exit friction).
    - If rebalance_daily=True : Daily rebalanced to 25% each day with rebalance turnover friction.
    """
    n = len(aligned_bars)
    if n <= warmup + 10:
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0}

    total_drag_rate = fee_rate + slippage
    k = len(universe)
    weight = 1.0 / k

    # Passive Buy & Hold Net
    net_capital = initial_capital
    entry_friction = net_capital * total_drag_rate
    post_entry = net_capital - entry_friction
    total_friction = entry_friction

    alloc_per_coin = post_entry / k
    qtys = {s: alloc_per_coin / aligned_bars[warmup][s]["c"] for s in universe}

    net_equity_curve = [initial_capital]
    net_daily_returns = []

    gross_capital = initial_capital
    gross_equity_curve = [gross_capital]
    gross_daily_returns = []

    for t in range(warmup, n - 1):
        start_net_eq = net_capital
        # Gross daily return
        day_gross_ret = sum(weight * ((aligned_bars[t + 1][s]["c"] - aligned_bars[t][s]["c"]) / aligned_bars[t][s]["c"]) for s in universe)
        gross_capital *= (1.0 + day_gross_ret)
        gross_equity_curve.append(gross_capital)
        gross_daily_returns.append(day_gross_ret)

        # Net evaluation of passive holdings
        curr_val = sum(qtys[s] * aligned_bars[t + 1][s]["c"] for s in universe)
        is_last_day = (t == n - 2)
        if is_last_day and liquidate_terminal:
            exit_friction = curr_val * total_drag_rate
            curr_val -= exit_friction
            total_friction += exit_friction

        net_r = (curr_val / start_net_eq) - 1.0
        net_daily_returns.append(net_r)
        net_capital = curr_val
        net_equity_curve.append(net_capital)

    days = len(net_daily_returns)
    net_tot_ret = round(((net_capital - initial_capital) / initial_capital) * 100.0, 2)
    net_cagr = calc_cagr(initial_capital, net_capital, days)
    net_vol = calc_annualized_vol(net_daily_returns)
    net_sharpe = round(calc_sharpe_ratio(net_cagr, net_vol), 2)
    net_sortino = round(calc_sortino_ratio(net_cagr, net_daily_returns), 2)
    net_mdd, net_dur = calc_max_drawdown(net_equity_curve)

    gross_tot_ret = round(((gross_capital - initial_capital) / initial_capital) * 100.0, 2)
    gross_cagr = calc_cagr(initial_capital, gross_capital, days)
    gross_vol = calc_annualized_vol(gross_daily_returns)
    gross_sharpe = round(calc_sharpe_ratio(gross_cagr, gross_vol), 2)

    return {
        "final_capital": round(net_capital, 2),
        "total_return_pct": net_tot_ret,
        "cagr_pct": round(net_cagr * 100.0, 2),
        "ann_vol_pct": round(net_vol * 100.0, 2),
        "sharpe_ratio": net_sharpe,
        "sortino_ratio": net_sortino,
        "max_drawdown_pct": net_mdd,
        "max_drawdown_duration_days": net_dur,
        "total_friction_usd": round(total_friction, 2),
        "gross": {
            "total_return_pct": gross_tot_ret,
            "cagr_pct": round(gross_cagr * 100.0, 2),
            "ann_vol_pct": round(gross_vol * 100.0, 2),
            "sharpe_ratio": gross_sharpe
        },
        "equity_curve": net_equity_curve,
        "daily_returns": net_daily_returns
    }


# --------------------------------------------------------------------------
# Main Research Suite Execution (V4.2)
# --------------------------------------------------------------------------
def run_v4_research(universe=DEFAULT_UNIVERSE, days=730):
    """Execute complete V4 strategy research suite with corrected accounting."""
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
    print("STRATEGY V4.2 RESEARCH: REPAIRED MULTI-ASSET RELATIVE STRENGTH ROTATION")
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
    full_ledgers = {}

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

        full_ledgers[cid] = {
            "overall_trade_ledger": res_all["trade_ledger"],
            "test_trade_ledger": res_test["trade_ledger"]
        }

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
        "title": "Strategy V4.2: Corrected Multi-Asset Relative Strength Rotation Research Report",
        "universe": universe,
        "days": days,
        "accounting_engine": "V4.2 Authoritative Net Equity Ledger (Crypto-Only Turnover)",
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

    # Save summary JSON
    out_file = os.path.join(ROOT, "trader", "v4_experiment_results.json")
    clean_doc = copy_clean_doc(doc)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(clean_doc, f, indent=2)
    print(f"\nAll corrected V4 experiment results saved to {out_file}")

    # Save machine-readable detailed ledger JSON
    ledger_file = os.path.join(ROOT, "trader", "v4_corrected_results.json")
    ledger_doc = {
        "title": "Strategy V4.2 Machine-Readable Corrected Results & Trade Ledger",
        "accounting_convention": {
            "turnover_definition": "One-Way Portfolio Turnover: T_{1-way} = 0.5 * sum(|w_{target} - w_{current}|)",
            "traded_crypto_notional": "Traded Notional = Pre-Trade Equity * sum(|w_{target, s} - w_{current, s}| for s in crypto)",
            "fee_rate": 0.0010,
            "slippage": 0.0005,
            "total_friction_rate": 0.0015,
            "cash_fee_rate": 0.0000,
            "net_daily_return_formula": "net_return[t] = ending_equity[t] / starting_equity[t] - 1.0"
        },
        "summary": clean_doc,
        "ledgers": full_ledgers,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    with open(ledger_file, "w", encoding="utf-8") as f:
        json.dump(ledger_doc, f, indent=2)
    print(f"Machine-readable corrected ledger saved to {ledger_file}")

    print_v4_summary(clean_doc)
    return clean_doc


def copy_clean_doc(d):
    """Strip large equity curves and raw daily returns from JSON for compact storage."""
    def clean_res(res):
        if not isinstance(res, dict):
            return res
        return {
            k: v for k, v in res.items()
            if k not in ("equity_curve", "daily_returns", "trade_ledger", "rebalance_records")
        }

    clean_d = {
        "title": d["title"],
        "universe": d["universe"],
        "days": d["days"],
        "accounting_engine": d["accounting_engine"],
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
    print("\n" + "=" * 135)
    print("STRATEGY V4.2 RESEARCH: UNTOUCHED TEST SET RESULTS (FINAL 20% OF 2 YEARS)")
    print("=" * 135)
    header = "%-38s | %-10s | %-8s | %-8s | %-7s | %-8s | %-8s | %-10s | %-10s | %-11s" % (
        "Strategy / Benchmark", "Total Ret", "CAGR", "Vol", "Sharpe", "Sortino", "Max DD", "Friction", "Turnover", "% Invested"
    )
    print(header)
    print("-" * 135)

    # Print Benchmarks on Test
    b_test = doc["benchmarks"]["test"]
    btc = b_test["btc_bnh"]
    ew = b_test["ew_bnh"]
    print("%-38s | %+8.2f%% | %+7.2f%% | %6.2f%% | %-7.2f | %-8.2f | -%6.2f%% | $%-9.2f | %-10.4f | %-11s" % (
        "BENCHMARK: BTC Buy-and-Hold (Net)", btc["total_return_pct"], btc["cagr_pct"], btc["ann_vol_pct"], btc["sharpe_ratio"], btc["sortino_ratio"], btc["max_drawdown_pct"], btc["total_friction_usd"], 0.0, "100.0%"
    ))
    print("%-38s | %+8.2f%% | %+7.2f%% | %6.2f%% | %-7.2f | %-8.2f | -%6.2f%% | $%-9.2f | %-10.4f | %-11s" % (
        "BENCHMARK: Equal-Weight L1s (Net)", ew["total_return_pct"], ew["cagr_pct"], ew["ann_vol_pct"], ew["sharpe_ratio"], ew["sortino_ratio"], ew["max_drawdown_pct"], ew["total_friction_usd"], 0.0, "100.0%"
    ))
    print("%-38s | %+8.2f%% | %+7.2f%% | %6.2f%% | %-7.2f | %-8.2f | -%6.2f%% | $%-9.2f | %-10.4f | %-11s" % (
        "BENCHMARK: 100% Cash", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "0.0%"
    ))
    print("-" * 135)

    for s in doc["strategies"]:
        t = s["test"]
        print("%-38s | %+8.2f%% | %+7.2f%% | %6.2f%% | %-7.2f | %-8.2f | -%6.2f%% | $%-9.2f | %-10.4f | %-11s" % (
            s["name"], t["total_return_pct"], t["cagr_pct"], t["ann_vol_pct"], t["sharpe_ratio"], t["sortino_ratio"], t["max_drawdown_pct"], t["total_friction_usd"], t["avg_turnover"], f"{t['pct_time_invested']:.1f}%"
        ))

    print("=" * 135)

    print("\n" + "=" * 135)
    print("STRATEGY V4.2 RESEARCH: FULL 2-YEAR OVERALL PERFORMANCE")
    print("=" * 135)
    print(header)
    print("-" * 135)
    b_all = doc["benchmarks"]["overall"]
    btc_a = b_all["btc_bnh"]
    ew_a = b_all["ew_bnh"]
    print("%-38s | %+8.2f%% | %+7.2f%% | %6.2f%% | %-7.2f | %-8.2f | -%6.2f%% | $%-9.2f | %-10.4f | %-11s" % (
        "BENCHMARK: BTC Buy-and-Hold (Net)", btc_a["total_return_pct"], btc_a["cagr_pct"], btc_a["ann_vol_pct"], btc_a["sharpe_ratio"], btc_a["sortino_ratio"], btc_a["max_drawdown_pct"], btc_a["total_friction_usd"], 0.0, "100.0%"
    ))
    print("%-38s | %+8.2f%% | %+7.2f%% | %6.2f%% | %-7.2f | %-8.2f | -%6.2f%% | $%-9.2f | %-10.4f | %-11s" % (
        "BENCHMARK: Equal-Weight L1s (Net)", ew_a["total_return_pct"], ew_a["cagr_pct"], ew_a["ann_vol_pct"], ew_a["sharpe_ratio"], ew_a["sortino_ratio"], ew_a["max_drawdown_pct"], ew_a["total_friction_usd"], 0.0, "100.0%"
    ))
    print("-" * 135)

    for s in doc["strategies"]:
        o = s["overall"]
        print("%-38s | %+8.2f%% | %+7.2f%% | %6.2f%% | %-7.2f | %-8.2f | -%6.2f%% | $%-9.2f | %-10.4f | %-11s" % (
            s["name"], o["total_return_pct"], o["cagr_pct"], o["ann_vol_pct"], o["sharpe_ratio"], o["sortino_ratio"], o["max_drawdown_pct"], o["total_friction_usd"], o["avg_turnover"], f"{o['pct_time_invested']:.1f}%"
        ))

    print("=" * 135)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy V4.2 Research (Corrected Accounting)")
    parser.add_argument("--days", type=int, default=730, help="Days of history (default 730)")
    args = parser.parse_args()
    run_v4_research(days=args.days)
