---
name: trader-timemachine
description: >-
  Binance 24/7 Time Machine & Honest Backtesting Engine. Replays planned trades
  across historical Binance klines with dynamic backward pagination and strict
  gap policy. Evaluates chronological In-Sample (first 70%) vs Out-of-Sample (last 30%)
  windows, deducts exact Binance taker fees (0.10% Spot / 0.05% Futures) and slippage,
  and runs 10,000 Monte Carlo bootstrap resamples. Computes win rate, profit factor,
  net expectancy (in R and %), median and 95th-percentile max drawdown, and 95%
  confidence intervals. Use when the user asks "run timemachine", "simulate SOLUSDT",
  "replay trade", "backtest crypto setup", "what are the odds", or after /trader-exit.
argument-hint: "--symbol <SYMBOL> --interval <INTERVAL> --days <DAYS> [--market spot|futures]"
---

# trader-timemachine

The Binance 24/7 Time Machine sits between the plan and the money. Before capital is
risked on Binance Spot or USD(S)-M Futures, this skill performs dynamic historical
pagination, detects data continuity gaps, and executes an honest statistical simulation
with Monte Carlo uncertainty modeling.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
# Direct CLI invocation with backward pagination and honest statistical report
python3 "${CLAUDE_SKILL_DIR}/scripts/timemachine.py" --symbol SOLUSDT --interval 15m --days 60

# Specify market and simulation count
python3 "${CLAUDE_SKILL_DIR}/scripts/timemachine.py" --symbol BTCUSDT --interval 5m --days 30 --market futures --simulations 10000

# JSON output mode
python3 "${CLAUDE_SKILL_DIR}/scripts/timemachine.py" --symbol ETHUSDT --interval 15m --days 60 --json
```

## How It Works

1. **Dynamic Backward Historical Pagination**:
   - Calculates exact required candles from interval (e.g., 60 days on 15m = 5,760 candles).
   - Paginates backward using `endTime = earliest_open_time - 1` until the requested start time is reached.
   - Deduplicates and sorts candles in strictly ascending order, dropping the currently unclosed candle.

2. **Market-Data Gap Policy**:
   - Verifies timestamp continuity (`t[i] - t[i-1] == interval_ms`).
   - Never fabricates, interpolates, or ignores missing candles.
   - Records every gap and splits simulation evaluation windows cleanly at gap boundaries so simulated trades never bridge across missing data.
   - Prints a complete data integrity summary.

3. **Honest Statistical Output**:
   - Deducts configurable Binance taker fees (0.10% Spot / 0.05% Futures per leg) and slippage (0.05%) from every trade.
   - Splits data chronologically into In-Sample (first 70%) and Out-of-Sample (last 30%).
   - Runs 10,000 bootstrap / Monte Carlo resamples to derive the distribution of drawdowns (median and 95th-percentile max drawdown) and 95% confidence intervals.
   - Displays clear disclaimer that historical simulations do not guarantee future live profitability.

## What it reads and writes

| file | what |
| --- | --- |
| Binance REST API | `/api/v3/klines` or `/fapi/v1/klines` dynamically paginated backward |
| `trader/plan.json` | plan parameters from trader-exit |
| `trader/timemachine.json` | full backtest report with data integrity, in-sample, out-of-sample, and Monte Carlo stats |

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Orders require explicit user yes confirmation before execution in trader-broker.
- Monte Carlo simulations estimate past uncertainty; they do not guarantee future profits.
