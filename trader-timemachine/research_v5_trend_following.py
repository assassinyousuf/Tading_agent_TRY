"""
Strategy V5: Multi-Asset Volatility-Parity Trend Following with ATR Trailing Stops (VP-TF)
Preregistered Offline Research & Experiment Engine

Conforms strictly to the V5 Research Framework and Experiment Registry Schema.
- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT (729 daily bars, 2024-10-30 to 2026-10-28)
- Partitions:
  * Warmup: 30 bars (bars 0 to 29)
  * Train: bars 30 to 429 (400 trading bars, 2024-11-29 to 2026-01-02)
  * Validation: bars 430 to 579 (150 trading bars, 2026-01-03 to 2026-06-01)
  * Untouched Test: bars 580 to 728 (149 trading bars, 2026-06-02 to 2026-10-28)
- Preregistered Configurations (M = 3):
  * CFG1 (Baseline): lookback=20, stop_mult=2.5, risk_budget=0.02
  * CFG2 (Tight):    lookback=20, stop_mult=2.0, risk_budget=0.015
  * CFG3 (Wide):     lookback=30, stop_mult=3.0, risk_budget=0.025
- Cost Model: 0.10% fee + 0.05% slippage = 0.15% per traded crypto notional. Cash pays 0.00%.
- Execution Timing: Signals evaluated at Day t Close; orders execute at Day t+1 Open.
- Trailing Stop: Ratchets upward only; models opening gap downs and intraday breaches.
- Authoritative Net Return: Compounding identity verified to < 1e-10.
- Benchmarks: BTC Buy & Hold, Equal-Weight L1 Basket, Cash (evaluated over exact identical dates).
- Hypothesis Testing: Paired Moving-Block Bootstrap (B=2000, block_size=10, seed=42).
- Bonferroni Adjustment: alpha_threshold = 0.05 / 3 = 0.0167.

Offline simulation only. Zero live trading, zero testnet orders.
"""

import json
import math
import os
import random
import datetime

DEFAULT_UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


# ==============================================================================
# 1. Performance Metric Utilities
# ==============================================================================

def calc_cagr(start_val, end_val, days):
    if days <= 0 or start_val <= 0 or end_val <= 0:
        return 0.0
    return (end_val / start_val) ** (365.25 / days) - 1.0


def calc_annualized_vol(daily_returns):
    if len(daily_returns) < 2:
        return 0.0
    n = len(daily_returns)
    mean_r = sum(daily_returns) / n
    var = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
    return math.sqrt(max(0.0, var)) * math.sqrt(365.25)


def calc_sharpe_ratio(cagr, ann_vol, rf=0.0):
    if ann_vol <= 1e-9:
        return 0.0
    return (cagr - rf) / ann_vol


def calc_sortino_ratio(cagr, daily_returns, rf=0.0):
    if len(daily_returns) < 2:
        return 0.0
    neg_rets = [min(0.0, r) for r in daily_returns]
    downside_var = sum(r ** 2 for r in neg_rets) / len(daily_returns)
    downside_dev = math.sqrt(downside_var) * math.sqrt(365.25)
    if downside_dev <= 1e-9:
        return 0.0
    return (cagr - rf) / downside_dev


def calc_max_drawdown(equity_curve):
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        if peak > 0:
            dd = (val - peak) / peak
            if dd < max_dd:
                max_dd = dd
    return max_dd


# ==============================================================================
# 2. Data Loading & Indicator Calculations
# ==============================================================================

def load_aligned_daily_candles(universe=DEFAULT_UNIVERSE, trader_dir="trader"):
    raw = {}
    for sym in universe:
        fn = os.path.join(trader_dir, f"cached_klines_{sym.lower()}_1d_730d.json")
        if not os.path.exists(fn):
            raise FileNotFoundError(f"Missing cached klines: {fn}")
        with open(fn, "r") as f:
            data = json.load(f)
        raw[sym] = data.get("klines", [])

    n_bars = len(raw[universe[0]])
    for sym in universe:
        if len(raw[sym]) != n_bars:
            raise ValueError(f"Length mismatch for {sym}: {len(raw[sym])} vs {n_bars}")

    bars = []
    for i in range(n_bars):
        day_t = raw[universe[0]][i][0]
        day_dict = {"t": day_t}
        for sym in universe:
            k = raw[sym][i]
            if k[0] != day_t:
                raise ValueError(f"Timestamp mismatch at bar {i} for {sym}: {k[0]} vs {day_t}")
            day_dict[sym] = {
                "o": float(k[1]),
                "h": float(k[2]),
                "l": float(k[3]),
                "c": float(k[4]),
                "v": float(k[5])
            }
        bars.append(day_dict)

    # Compute True Range, ATR(14), and Donchian Highs for lookbacks 20 and 30
    for sym in universe:
        tr = []
        for t in range(n_bars):
            h = bars[t][sym]["h"]
            l = bars[t][sym]["l"]
            if t == 0:
                tr.append(h - l)
            else:
                c_prev = bars[t - 1][sym]["c"]
                tr.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))

        for t in range(n_bars):
            bars[t][sym]["tr"] = tr[t]
            # ATR14: rolling simple 14-day average of TR
            window_start = max(0, t - 13)
            bars[t][sym]["atr14"] = sum(tr[window_start:t + 1]) / float(t - window_start + 1)

            # Donchian Highs of completed previous bars (excluding today)
            for lb in [20, 30]:
                if t >= lb:
                    bars[t][sym][f"high_{lb}"] = max(bars[j][sym]["h"] for j in range(t - lb, t))
                else:
                    bars[t][sym][f"high_{lb}"] = None

    return bars


# ==============================================================================
# 3. Simulation Engine: Multi-Asset Trend Following (VP-TF)
# ==============================================================================

def simulate_trend_following(bars, universe=DEFAULT_UNIVERSE, lookback=20,
                              stop_mult=2.5, risk_budget=0.02, max_asset_weight=0.40,
                              fee_rate=0.0010, slippage=0.0005, initial_capital=10000.0,
                              start_bar=30, end_bar=None, liquidate_terminal=True,
                              fill_delay="NEXT_OPEN"):
    """
    Simulates Multi-Asset Volatility-Parity Trend Following with ATR Trailing Stops:
    - At close of day t, checks if Close_t > High_{lookback}(t).
    - If triggered and not already holding, queues an entry for day t+1.
    - Day t+1:
      * If fill_delay == 'NEXT_OPEN', orders fill at Open_{t+1}.
      * Sizing is governed by volatility parity:
        target_notional = min(equity * (risk_budget / (stop_dist / fill_price)), equity * max_asset_weight)
      * If total required cash > available cash, scales down allocations proportionally.
      * Deducts entry friction (fee_rate + slippage) directly from Cash.
      * Positions held from prior days ratchet trailing stops upward:
        trailing_stop = max(trailing_stop, peak_high - stop_mult * atr_{t})
      * Trailing stop exit checks:
        - If Open_{t+1} <= trailing_stop: exits at Open_{t+1} (gap down).
        - Else if Low_{t+1} <= trailing_stop: exits at trailing_stop (intraday stop).
        - Else: position remains open, peak_high updated to max(peak_high, High_{t+1}).
      * When stopped out, deducts exit friction and returns proceeds to Cash.
      * End of day t+1: total portfolio equity evaluated, daily net return recorded.
    - Terminal Liquidation:
      * Open positions on the final bar are liquidated at final Close price, paying 15 bps friction.
    - Exact compounding identity: prod(1 + R_t) == final_equity / initial_equity (< 1e-10 error).
    - Strictly zero look-ahead.
    """
    if end_bar is None:
        end_bar = len(bars) - 1

    total_drag = fee_rate + slippage
    cash = initial_capital
    positions = {}  # sym -> dict(qty, entry_price, peak_high, trailing_stop, entry_bar, is_new)

    equity_curve = [initial_capital]
    daily_returns = []
    trade_ledger = []
    rebalance_records = []
    total_friction = 0.0
    total_traded_notional = 0.0
    trades_count = 0

    for t in range(start_bar, end_bar):
        curr_day = bars[t]
        next_day = bars[t + 1]
        start_equity = cash + sum(positions[s]["qty"] * curr_day[s]["c"] for s in positions)

        # 1. Breakout Signal Evaluation at Day t Close
        pending_entries = []
        for s in universe:
            high_lb = curr_day[s].get(f"high_{lookback}")
            if high_lb is not None and s not in positions:
                if curr_day[s]["c"] > high_lb:
                    atr = curr_day[s]["atr14"]
                    stop_dist = stop_mult * atr
                    pending_entries.append((s, stop_dist, atr))

        # 2. Execute Pending Entries at Day t+1
        day_turnover_notional = 0.0
        if pending_entries:
            allocations = []
            for s, stop_dist, atr in pending_entries:
                fill_p = next_day[s]["o"] if fill_delay == "NEXT_OPEN" else curr_day[s]["c"]
                dist_pct = max(0.001, stop_dist / fill_p)
                target_notional = min(start_equity * (risk_budget / dist_pct), start_equity * max_asset_weight)
                allocations.append((s, target_notional, stop_dist, atr, fill_p))

            total_req_cash = sum(a[1] * (1.0 + total_drag) for a in allocations)
            scale = 1.0
            if total_req_cash > cash and total_req_cash > 0:
                scale = cash / total_req_cash

            for s, target_notional, stop_dist, atr, fill_p in allocations:
                actual_notional = target_notional * scale
                if actual_notional > 10.0:  # Minimum order notional threshold
                    friction = actual_notional * total_drag
                    total_friction += friction
                    total_traded_notional += actual_notional
                    day_turnover_notional += actual_notional
                    cash -= (actual_notional + friction)
                    qty = actual_notional / fill_p
                    initial_stop = fill_p - stop_dist
                    positions[s] = {
                        "qty": qty,
                        "entry_price": fill_p,
                        "peak_high": max(fill_p, next_day[s]["h"]),
                        "trailing_stop": initial_stop,
                        "entry_bar": t + 1,
                        "entry_date": datetime.datetime.fromtimestamp(next_day["t"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d"),
                        "is_new": True
                    }
                    trades_count += 1
                    trade_ledger.append({
                        "type": "BUY",
                        "symbol": s,
                        "bar": t + 1,
                        "date": datetime.datetime.fromtimestamp(next_day["t"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d"),
                        "price": fill_p,
                        "qty": qty,
                        "notional": round(actual_notional, 2),
                        "friction": round(friction, 2),
                        "stop_price": round(initial_stop, 2),
                        "reason": f"BREAKOUT_{lookback}D"
                    })

        # 3. Trailing Stop Ratchet & Exits during Day t+1
        closed_symbols = []
        for s in list(positions.keys()):
            pos = positions[s]
            open_p = next_day[s]["o"]
            low_p = next_day[s]["l"]
            high_p = next_day[s]["h"]

            # Positions opened prior to day t+1 ratchet their trailing stops using day t ATR and peak high
            if not pos.get("is_new", False):
                atr_t = curr_day[s]["atr14"]
                new_stop = pos["peak_high"] - stop_mult * atr_t
                pos["trailing_stop"] = max(pos["trailing_stop"], new_stop)

            stop_price = pos["trailing_stop"]

            if open_p <= stop_price:
                # Opening gap-down below stop
                closed_symbols.append((s, open_p, "GAP_DOWN_STOP"))
            elif low_p <= stop_price:
                # Intraday breach of trailing stop
                closed_symbols.append((s, stop_price, "TRAILING_STOP"))
            else:
                # Position survived: update peak high
                pos["peak_high"] = max(pos["peak_high"], high_p)
                pos["is_new"] = False

        # Process closed positions
        for s, exit_price, reason in closed_symbols:
            pos = positions.pop(s)
            exit_notional = pos["qty"] * exit_price
            friction = exit_notional * total_drag
            total_friction += friction
            total_traded_notional += exit_notional
            day_turnover_notional += exit_notional
            cash += (exit_notional - friction)
            trades_count += 1
            pnl_usd = exit_notional - (pos["qty"] * pos["entry_price"]) - friction
            pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0
            trade_ledger.append({
                "type": "SELL",
                "symbol": s,
                "bar": t + 1,
                "date": datetime.datetime.fromtimestamp(next_day["t"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d"),
                "price": exit_price,
                "qty": pos["qty"],
                "notional": round(exit_notional, 2),
                "friction": round(friction, 2),
                "entry_price": pos["entry_price"],
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "hold_days": (t + 1) - pos["entry_bar"],
                "reason": reason
            })

        # 4. Terminal Liquidation at End of Partition
        terminal_friction = 0.0
        if t == end_bar - 1 and liquidate_terminal:
            for s in list(positions.keys()):
                pos = positions.pop(s)
                exit_price = next_day[s]["c"]
                exit_notional = pos["qty"] * exit_price
                friction = exit_notional * total_drag
                total_friction += friction
                total_traded_notional += exit_notional
                day_turnover_notional += exit_notional
                terminal_friction += friction
                cash += (exit_notional - friction)
                trades_count += 1
                pnl_usd = exit_notional - (pos["qty"] * pos["entry_price"]) - friction
                pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0
                trade_ledger.append({
                    "type": "SELL",
                    "symbol": s,
                    "bar": t + 1,
                    "date": datetime.datetime.fromtimestamp(next_day["t"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d"),
                    "price": exit_price,
                    "qty": pos["qty"],
                    "notional": round(exit_notional, 2),
                    "friction": round(friction, 2),
                    "entry_price": pos["entry_price"],
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "hold_days": (t + 1) - pos["entry_bar"],
                    "reason": "TERMINAL_LIQUIDATION"
                })

        # 5. Daily Portfolio Valuation & Authoritative Net Return
        end_equity = cash + sum(positions[s]["qty"] * next_day[s]["c"] for s in positions)
        net_daily_return = (end_equity / start_equity) - 1.0
        daily_returns.append(net_daily_return)
        equity_curve.append(end_equity)

        one_way_turnover = (day_turnover_notional / start_equity) * 0.5 if start_equity > 0 else 0.0
        rebalance_records.append({
            "t": next_day["t"],
            "date": datetime.datetime.fromtimestamp(next_day["t"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d"),
            "starting_equity": round(start_equity, 2),
            "ending_equity": round(end_equity, 2),
            "net_return_pct": round(net_daily_return * 100.0, 4),
            "turnover_usd": round(day_turnover_notional, 2),
            "one_way_turnover": round(one_way_turnover, 4),
            "friction_usd": round(day_turnover_notional * total_drag, 2),
            "cash_usd": round(cash, 2),
            "active_positions": list(positions.keys())
        })

    # Exact Compounding Verification
    compounded_equity = initial_capital
    for r in daily_returns:
        compounded_equity *= (1.0 + r)
    compounding_error = abs(equity_curve[-1] - compounded_equity)

    n_days = len(daily_returns)
    cagr = calc_cagr(initial_capital, equity_curve[-1], n_days)
    ann_vol = calc_annualized_vol(daily_returns)
    sharpe = calc_sharpe_ratio(cagr, ann_vol)
    sortino = calc_sortino_ratio(cagr, daily_returns)
    max_dd = calc_max_drawdown(equity_curve)
    net_total_ret = (equity_curve[-1] / initial_capital - 1.0) * 100.0
    avg_turnover = sum(r["one_way_turnover"] for r in rebalance_records) / max(1, len(rebalance_records))

    metrics = {
        "initial_capital": round(initial_capital, 2),
        "final_capital": round(equity_curve[-1], 2),
        "net_total_return_pct": round(net_total_ret, 2),
        "cagr_pct": round(cagr * 100.0, 2),
        "annualized_vol_pct": round(ann_vol * 100.0, 2),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "avg_one_way_turnover": round(avg_turnover, 4),
        "total_friction_usd": round(total_friction, 2),
        "total_traded_notional_usd": round(total_traded_notional, 2),
        "trades_count": trades_count,
        "n_trading_days": n_days,
        "compounding_error_usd": compounding_error
    }

    return {
        "metrics": metrics,
        "equity_curve": equity_curve,
        "daily_returns": daily_returns,
        "trade_ledger": trade_ledger,
        "rebalance_records": rebalance_records
    }


# ==============================================================================
# 4. Benchmark Calculations (Exact Alignment & Parity)
# ==============================================================================

def calc_btc_buy_and_hold(bars, start_bar=30, end_bar=None, fee_rate=0.0010,
                          slippage=0.0005, initial_capital=10000.0):
    if end_bar is None:
        end_bar = len(bars) - 1

    total_drag = fee_rate + slippage
    # Enter at Open of bar start_bar + 1
    entry_p = bars[start_bar + 1]["BTCUSDT"]["o"]
    entry_friction = initial_capital * total_drag
    invested_equity = initial_capital - entry_friction
    btc_qty = invested_equity / entry_p

    equity_curve = [initial_capital]
    daily_returns = []

    # First day: from start_bar + 1 Open to start_bar + 1 Close
    c_first = bars[start_bar + 1]["BTCUSDT"]["c"]
    end_first = btc_qty * c_first
    ret_first = (end_first / initial_capital) - 1.0
    daily_returns.append(ret_first)
    equity_curve.append(end_first)

    # Subsequent days
    for t in range(start_bar + 1, end_bar):
        curr_day = bars[t]
        next_day = bars[t + 1]
        start_eq = equity_curve[-1]
        end_eq = btc_qty * next_day["BTCUSDT"]["c"]
        ret = (end_eq / start_eq) - 1.0
        daily_returns.append(ret)
        equity_curve.append(end_eq)

    # Terminal liquidation
    terminal_notional = equity_curve[-1]
    terminal_friction = terminal_notional * total_drag
    final_capital = terminal_notional - terminal_friction
    equity_curve[-1] = final_capital
    total_friction = entry_friction + terminal_friction

    # Update last daily return for terminal friction
    daily_returns[-1] = (final_capital / equity_curve[-2]) - 1.0

    n_days = len(daily_returns)
    cagr = calc_cagr(initial_capital, final_capital, n_days)
    ann_vol = calc_annualized_vol(daily_returns)
    sharpe = calc_sharpe_ratio(cagr, ann_vol)
    sortino = calc_sortino_ratio(cagr, daily_returns)
    max_dd = calc_max_drawdown(equity_curve)
    net_total_ret = (final_capital / initial_capital - 1.0) * 100.0

    return {
        "metrics": {
            "initial_capital": round(initial_capital, 2),
            "final_capital": round(final_capital, 2),
            "net_total_return_pct": round(net_total_ret, 2),
            "cagr_pct": round(cagr * 100.0, 2),
            "annualized_vol_pct": round(ann_vol * 100.0, 2),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "max_drawdown_pct": round(max_dd * 100.0, 2),
            "total_friction_usd": round(total_friction, 2)
        },
        "daily_returns": daily_returns,
        "equity_curve": equity_curve
    }


def calc_equal_weight(bars, universe=DEFAULT_UNIVERSE, start_bar=30, end_bar=None,
                       fee_rate=0.0010, slippage=0.0005, initial_capital=10000.0):
    if end_bar is None:
        end_bar = len(bars) - 1

    total_drag = fee_rate + slippage
    n_assets = len(universe)
    weight_per_asset = 1.0 / n_assets

    entry_friction = initial_capital * total_drag
    invested_equity = initial_capital - entry_friction
    notional_per_asset = invested_equity * weight_per_asset

    quantities = {}
    for s in universe:
        entry_p = bars[start_bar + 1][s]["o"]
        quantities[s] = notional_per_asset / entry_p

    equity_curve = [initial_capital]
    daily_returns = []

    # First day: start_bar + 1 Open to Close
    end_first = sum(quantities[s] * bars[start_bar + 1][s]["c"] for s in universe)
    ret_first = (end_first / initial_capital) - 1.0
    daily_returns.append(ret_first)
    equity_curve.append(end_first)

    for t in range(start_bar + 1, end_bar):
        next_day = bars[t + 1]
        start_eq = equity_curve[-1]
        end_eq = sum(quantities[s] * next_day[s]["c"] for s in universe)
        ret = (end_eq / start_eq) - 1.0
        daily_returns.append(ret)
        equity_curve.append(end_eq)

    terminal_notional = equity_curve[-1]
    terminal_friction = terminal_notional * total_drag
    final_capital = terminal_notional - terminal_friction
    equity_curve[-1] = final_capital
    total_friction = entry_friction + terminal_friction
    daily_returns[-1] = (final_capital / equity_curve[-2]) - 1.0

    n_days = len(daily_returns)
    cagr = calc_cagr(initial_capital, final_capital, n_days)
    ann_vol = calc_annualized_vol(daily_returns)
    sharpe = calc_sharpe_ratio(cagr, ann_vol)
    sortino = calc_sortino_ratio(cagr, daily_returns)
    max_dd = calc_max_drawdown(equity_curve)
    net_total_ret = (final_capital / initial_capital - 1.0) * 100.0

    return {
        "metrics": {
            "initial_capital": round(initial_capital, 2),
            "final_capital": round(final_capital, 2),
            "net_total_return_pct": round(net_total_ret, 2),
            "cagr_pct": round(cagr * 100.0, 2),
            "annualized_vol_pct": round(ann_vol * 100.0, 2),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "max_drawdown_pct": round(max_dd * 100.0, 2),
            "total_friction_usd": round(total_friction, 2)
        },
        "daily_returns": daily_returns,
        "equity_curve": equity_curve
    }


def calc_cash_benchmark(daily_returns_len, initial_capital=10000.0):
    return {
        "metrics": {
            "initial_capital": round(initial_capital, 2),
            "final_capital": round(initial_capital, 2),
            "net_total_return_pct": 0.0,
            "cagr_pct": 0.0,
            "annualized_vol_pct": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "total_friction_usd": 0.0
        },
        "daily_returns": [0.0] * daily_returns_len,
        "equity_curve": [initial_capital] * (daily_returns_len + 1)
    }


# ==============================================================================
# 5. Non-Parametric Paired Hypothesis Testing (Moving Block Bootstrap)
# ==============================================================================

def paired_block_bootstrap_test(strategy_returns, benchmark_returns, block_size=10,
                                n_boot=2000, seed=42):
    """
    Non-parametric paired moving block bootstrap test for excess returns.
    H0: mu(strategy - benchmark) <= 0
    H1: mu(strategy - benchmark) > 0
    """
    n = min(len(strategy_returns), len(benchmark_returns))
    strat = strategy_returns[:n]
    bench = benchmark_returns[:n]

    excess = [s - b for s, b in zip(strat, bench)]
    mean_excess = sum(excess) / n if n > 0 else 0.0

    if n <= block_size:
        return {
            "p_value": 1.0,
            "empirical_mean_excess_daily": mean_excess,
            "excess_return_total_pp": (sum(strat) - sum(bench)) * 100.0,
            "ci_95": [0.0, 0.0],
            "iterations": n_boot
        }

    random.seed(seed)
    n_blocks = int(math.ceil(n / float(block_size)))
    boot_means = []

    for _ in range(n_boot):
        sample = []
        for _ in range(n_blocks):
            idx = random.randint(0, n - block_size)
            sample.extend(excess[idx:idx + block_size])
        sample = sample[:n]
        boot_means.append(sum(sample) / float(n))

    boot_means.sort()
    # One-sided p-value: fraction of bootstrap replicates with mean excess <= 0
    p_val = sum(1 for m in boot_means if m <= 0.0) / float(n_boot)

    ci_low = boot_means[int(0.025 * n_boot)] * n * 100.0
    ci_high = boot_means[int(0.975 * n_boot)] * n * 100.0

    # Total cumulative return difference
    cum_strat = 1.0
    cum_bench = 1.0
    for s, b in zip(strat, bench):
        cum_strat *= (1.0 + s)
        cum_bench *= (1.0 + b)
    total_excess_pp = (cum_strat - cum_bench) * 100.0

    return {
        "p_value": round(p_val, 4),
        "empirical_mean_excess_daily": mean_excess,
        "total_excess_return_pp": round(total_excess_pp, 2),
        "ci_95_excess_pp": [round(ci_low, 2), round(ci_high, 2)],
        "iterations": n_boot,
        "block_size": block_size,
        "seed": seed
    }


# ==============================================================================
# 6. Full Preregistered V5 Research Protocol
# ==============================================================================

def run_v5_research(trader_dir="trader"):
    print("=" * 80)
    print("V5 QUANTITATIVE STRATEGY RESEARCH: VOLATILITY-PARITY TREND FOLLOWING")
    print("=" * 80)

    bars = load_aligned_daily_candles(DEFAULT_UNIVERSE, trader_dir=trader_dir)
    print(f"Loaded {len(bars)} aligned daily bars across {DEFAULT_UNIVERSE}")

    # Partition definitions
    warmup_bars = 30
    partitions = {
        "TRAIN": {"start_bar": 30, "end_bar": 429, "name": "In-Sample Training (400 bars)"},
        "VAL":   {"start_bar": 430, "end_bar": 579, "name": "Validation (150 bars)"},
        "TEST":  {"start_bar": 580, "end_bar": 728, "name": "Untouched Out-of-Sample (149 bars)"},
        "OVERALL": {"start_bar": 30, "end_bar": 728, "name": "Continuous 2-Year Horizon (699 bars)"}
    }

    # Preregistered Configurations (M = 3)
    configs = {
        "H5-B_CFG1": {
            "name": "Baseline (20d High, 2.5 ATR, 2.0% Risk)",
            "params": {"lookback": 20, "stop_mult": 2.5, "risk_budget": 0.02, "max_asset_weight": 0.40}
        },
        "H5-B_CFG2": {
            "name": "Tight (20d High, 2.0 ATR, 1.5% Risk)",
            "params": {"lookback": 20, "stop_mult": 2.0, "risk_budget": 0.015, "max_asset_weight": 0.40}
        },
        "H5-B_CFG3": {
            "name": "Wide (30d High, 3.0 ATR, 2.5% Risk)",
            "params": {"lookback": 30, "stop_mult": 3.0, "risk_budget": 0.025, "max_asset_weight": 0.40}
        }
    }

    results = {}
    registry_records = []
    m_hypotheses = len(configs)
    bonferroni_alpha = 0.05 / float(m_hypotheses)

    # 1. Evaluate Benchmarks for each partition
    benchmarks_by_partition = {}
    for p_key, p_info in partitions.items():
        s_bar, e_bar = p_info["start_bar"], p_info["end_bar"]
        btc_res = calc_btc_buy_and_hold(bars, start_bar=s_bar, end_bar=e_bar)
        ew_res = calc_equal_weight(bars, universe=DEFAULT_UNIVERSE, start_bar=s_bar, end_bar=e_bar)
        cash_res = calc_cash_benchmark(len(btc_res["daily_returns"]))
        benchmarks_by_partition[p_key] = {
            "BTC": btc_res,
            "EW": ew_res,
            "CASH": cash_res
        }

    # 2. Run All Preregistered Configurations across Partitions
    for cfg_id, cfg_data in configs.items():
        print(f"\n--- Running {cfg_id}: {cfg_data['name']} ---")
        cfg_results = {"info": cfg_data, "partitions": {}}

        for p_key, p_info in partitions.items():
            s_bar, e_bar = p_info["start_bar"], p_info["end_bar"]
            sim_res = simulate_trend_following(
                bars, universe=DEFAULT_UNIVERSE,
                start_bar=s_bar, end_bar=e_bar,
                **cfg_data["params"]
            )

            # Hypothesis tests vs benchmarks
            btc_b = benchmarks_by_partition[p_key]["BTC"]
            ew_b = benchmarks_by_partition[p_key]["EW"]

            ht_btc = paired_block_bootstrap_test(sim_res["daily_returns"], btc_b["daily_returns"], seed=42)
            ht_ew = paired_block_bootstrap_test(sim_res["daily_returns"], ew_b["daily_returns"], seed=42)

            partition_doc = {
                "metrics": sim_res["metrics"],
                "benchmarks": {
                    "btc_net_return_pct": btc_b["metrics"]["net_total_return_pct"],
                    "btc_sharpe": btc_b["metrics"]["sharpe_ratio"],
                    "btc_max_dd_pct": btc_b["metrics"]["max_drawdown_pct"],
                    "btc_friction_usd": btc_b["metrics"]["total_friction_usd"],
                    "ew_net_return_pct": ew_b["metrics"]["net_total_return_pct"],
                    "ew_sharpe": ew_b["metrics"]["sharpe_ratio"],
                    "ew_max_dd_pct": ew_b["metrics"]["max_drawdown_pct"],
                    "ew_friction_usd": ew_b["metrics"]["total_friction_usd"],
                    "cash_net_return_pct": 0.0
                },
                "hypothesis_test": {
                    "vs_btc": ht_btc,
                    "vs_ew": ht_ew
                },
                "trade_count": len(sim_res["trade_ledger"])
            }
            cfg_results["partitions"][p_key] = partition_doc

            # Decision criteria for Test partition
            if p_key == "TEST":
                # Must beat BTC net return with adjusted p-value < bonferroni_alpha
                passed_test = (
                    sim_res["metrics"]["net_total_return_pct"] > btc_b["metrics"]["net_total_return_pct"] and
                    ht_btc["p_value"] < bonferroni_alpha
                )
                decision = "PASS" if passed_test else "NO-GO"

                registry_record = {
                    "experiment_id": f"EXP_V5_{cfg_id}_TEST",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "hypothesis_id": "H5-B",
                    "strategy_name": f"VP-TF_{cfg_id}",
                    "universe": DEFAULT_UNIVERSE,
                    "timeframe": "1d",
                    "partition_protocol": {
                        "type": "CHRONOLOGICAL_SPLIT",
                        "partition": "TEST",
                        "start_bar": s_bar,
                        "end_bar": e_bar,
                        "warmup_bars": warmup_bars
                    },
                    "parameters": cfg_data["params"],
                    "cost_model": {
                        "fee_rate": 0.0010,
                        "slippage_rate": 0.0005,
                        "fill_timing": "NEXT_OPEN",
                        "terminal_liquidation": True
                    },
                    "metrics": sim_res["metrics"],
                    "benchmarks": {
                        "btc_buy_and_hold_net_return_pct": btc_b["metrics"]["net_total_return_pct"],
                        "equal_weight_net_return_pct": ew_b["metrics"]["net_total_return_pct"],
                        "cash_return_pct": 0.0
                    },
                    "hypothesis_test": {
                        "excess_return_vs_btc_pp": ht_btc["total_excess_return_pp"],
                        "excess_return_vs_ew_pp": ht_ew["total_excess_return_pp"],
                        "bootstrap_p_value_vs_btc": ht_btc["p_value"],
                        "bootstrap_p_value_vs_ew": ht_ew["p_value"],
                        "bootstrap_iterations": ht_btc["iterations"],
                        "block_size": ht_btc["block_size"],
                        "seed": ht_btc["seed"]
                    },
                    "multiple_testing_correction": {
                        "total_hypotheses_tested": m_hypotheses,
                        "bonferroni_alpha_threshold": round(bonferroni_alpha, 4),
                        "adjusted_significance_achieved": ht_btc["p_value"] < bonferroni_alpha
                    },
                    "decision": decision
                }
                registry_records.append(registry_record)

            m = sim_res["metrics"]
            b_btc = btc_b["metrics"]
            print(f"  [{p_key:7s}] Net: {m['net_total_return_pct']:+6.2f}% (BTC: {b_btc['net_total_return_pct']:+6.2f}%) | "
                  f"MaxDD: {m['max_drawdown_pct']:6.2f}% | Friction: ${m['total_friction_usd']:6.2f} | "
                  f"Trades: {m['trades_count']} | p_vs_btc: {ht_btc['p_value']:.4f}")

        results[cfg_id] = cfg_results

    # 3. Sensitivity Analysis on CFG1
    print("\n--- Running Sensitivity Analysis on Baseline (CFG1) ---")
    s_bar, e_bar = partitions["TEST"]["start_bar"], partitions["TEST"]["end_bar"]
    # Double friction test (fee 0.20% + slippage 0.10% = 30 bps)
    double_friction_res = simulate_trend_following(
        bars, universe=DEFAULT_UNIVERSE, start_bar=s_bar, end_bar=e_bar,
        fee_rate=0.0020, slippage=0.0010, **configs["H5-B_CFG1"]["params"]
    )
    # Fill at close instead of next open
    close_fill_res = simulate_trend_following(
        bars, universe=DEFAULT_UNIVERSE, start_bar=s_bar, end_bar=e_bar,
        fill_delay="SAME_CLOSE", **configs["H5-B_CFG1"]["params"]
    )

    sensitivity = {
        "double_friction_30bps": {
            "net_total_return_pct": double_friction_res["metrics"]["net_total_return_pct"],
            "final_capital": double_friction_res["metrics"]["final_capital"],
            "friction_usd": double_friction_res["metrics"]["total_friction_usd"],
            "trades": double_friction_res["metrics"]["trades_count"]
        },
        "same_close_fill": {
            "net_total_return_pct": close_fill_res["metrics"]["net_total_return_pct"],
            "final_capital": close_fill_res["metrics"]["final_capital"],
            "friction_usd": close_fill_res["metrics"]["total_friction_usd"],
            "trades": close_fill_res["metrics"]["trades_count"]
        }
    }
    print(f"  Double Friction (30 bps) on Test: Net Return={sensitivity['double_friction_30bps']['net_total_return_pct']:+.2f}%, Friction=${sensitivity['double_friction_30bps']['friction_usd']:.2f}")
    print(f"  Same Close Fill on Test        : Net Return={sensitivity['same_close_fill']['net_total_return_pct']:+.2f}%, Friction=${sensitivity['same_close_fill']['friction_usd']:.2f}")

    # 4. Save Outputs
    registry_path = os.path.join(trader_dir, "v5_experiment_registry.json")
    with open(registry_path, "w") as f:
        json.dump(registry_records, f, indent=2)
    print(f"\nSaved {len(registry_records)} records to {registry_path}")

    results_doc = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "hypothesis": "H5-B_VP-TF",
        "multiple_testing": {
            "m_hypotheses": m_hypotheses,
            "bonferroni_alpha": round(bonferroni_alpha, 4)
        },
        "partitions": {k: v["name"] for k, v in partitions.items()},
        "configurations": results,
        "sensitivity": sensitivity
    }
    results_path = os.path.join(trader_dir, "v5_experiment_results.json")
    with open(results_path, "w") as f:
        json.dump(results_doc, f, indent=2)
    print(f"Saved full experiment results to {results_path}")

    # Final Verdict Summary
    print("\n" + "=" * 80)
    print("V5 EXPERIMENT VERDICT SUMMARY")
    print("=" * 80)
    for rec in registry_records:
        print(f"Strategy: {rec['strategy_name']} | Decision: {rec['decision']} | "
              f"Test Net Ret: {rec['metrics']['net_total_return_pct']:+6.2f}% vs BTC: {rec['benchmarks']['btc_buy_and_hold_net_return_pct']:+6.2f}% | "
              f"p-val: {rec['hypothesis_test']['bootstrap_p_value_vs_btc']:.4f} (threshold: {rec['multiple_testing_correction']['bonferroni_alpha_threshold']:.4f})")

    return results_doc


if __name__ == "__main__":
    run_v5_research()

