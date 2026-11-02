"""Sealed, Quarantined Independent Evaluation Benchmark Suite for Phase 2.5.2+.

CRITICAL RESEARCH INTEGRITY NOTICE:
This benchmark suite is strictly sealed, quarantined, and isolated.
It MUST NEVER be imported into:
- trader/agent/coordinator.py
- trader/agent/benchmark/reviewer.py
- Any prompt construction or prompt calibration logic
- Any model training or fine-tuning pipelines

It serves as an independent, unseen post-calibration validation set to measure
generalization performance beyond the calibrated holdout tasks (TASK-21 through TASK-50).

Ground Truth Status:
All ground-truth classifications in this suite are documented with explicit rationale.
Because external independent double-blind human-expert panel review is pending,
all ground truths in this module are formally designated as PROVISIONAL GROUND TRUTH.

Category Breakdown (20 Unseen Tasks):
- SOUND (Valid Quantitative Design): 8 tasks
- FLAWED (Fatal Methodological Defect): 9 tasks
- CLARIFICATION_REQUIRED (Underspecified / Ambiguous): 3 tasks
"""

from typing import Any, Dict, List
from trader.agent.benchmark.schema import (
    BenchmarkCategory,
    BenchmarkTask,
    TaskGroundTruth,
)

# Sealed market contexts strictly confined to approved TRAIN partition data
BTC_TRAIN_SEALED: Dict[str, Any] = {
    "symbol": "BTCUSDT",
    "partition": "TRAIN",
    "total_bars": 720,
    "start_date": "2023-01-01",
    "end_date": "2024-12-31",
    "mean_daily_return_pct": 0.1412,
    "annualized_vol_pct": 51.40,
    "trend_direction": "UPWARD",
    "max_drawdown_train_pct": 21.8,
}

ETH_TRAIN_SEALED: Dict[str, Any] = {
    "symbol": "ETHUSDT",
    "partition": "TRAIN",
    "total_bars": 720,
    "start_date": "2023-01-01",
    "end_date": "2024-12-31",
    "mean_daily_return_pct": 0.1120,
    "annualized_vol_pct": 58.75,
    "trend_direction": "UPWARD",
    "max_drawdown_train_pct": 29.4,
}

SOL_TRAIN_SEALED: Dict[str, Any] = {
    "symbol": "SOLUSDT",
    "partition": "TRAIN",
    "total_bars": 720,
    "start_date": "2023-01-01",
    "end_date": "2024-12-31",
    "mean_daily_return_pct": 0.2240,
    "annualized_vol_pct": 78.10,
    "trend_direction": "UPWARD",
    "max_drawdown_train_pct": 36.2,
}


def get_sealed_benchmark_tasks() -> List[BenchmarkTask]:
    """Return the 20 quarantined sealed benchmark evaluation tasks."""
    return [
        # TASK-51: SOUND - Basis Cash-and-Carry with Explicit Margin Buffer
        BenchmarkTask(
            task_id="TASK-51-SEALED-BASIS-CASH-CARRY",
            name="Quarterly Futures Basis Cash and Carry with Dynamic Margin Buffer",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a market-neutral basis arbitrage hypothesis on BTC quarterly futures on TRAIN data. "
                "Buy spot BTC and short quarterly futures when annualized basis exceeds 12.0%. "
                "Hold until expiration convergence. Maintain an explicit 40% margin buffer to prevent liquidation "
                "during interim basis expansion. Deduct Binance 0.10% spot taker fee and 0.05% futures taker fee. "
                "Sharpe hurdle >= 1.2 on capital allocated."
            ),
            market_context=BTC_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-52: FLAWED - Cointegration Pairs Trading with Full-Sample Eigenvector Leakage
        BenchmarkTask(
            task_id="TASK-52-SEALED-COINT-FULLSAMPLE-LEAKAGE",
            name="Johansen Cointegration Vector Estimated on Full Sample",
            category=BenchmarkCategory.DATA_LEAKAGE,
            objective_prompt=(
                "Evaluate a statistical arbitrage cointegration strategy on ETH and BTC 1-hour TRAIN bars. "
                "Estimate the cointegrating vector beta = [1, -b] by running the Johansen test once over the entire "
                "two-year partition. Trade the resulting spread z = p_ETH - b * p_BTC using Bollinger bands with entry at 2 sigma."
            ),
            market_context=ETH_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="DATA_LEAKAGE",
                expected_flaw_keywords=["johansen", "full sample", "beta", "leakage", "cointegration", "lookahead"],
                requires_lookahead_warning=True,
            ),
        ),

        # TASK-53: FLAWED - Liquidation Cascade Scalp with Unpenalized Microstructural Queue Slippage
        BenchmarkTask(
            task_id="TASK-53-SEALED-LIQUIDATION-ZERO-SLIPPAGE",
            name="Liquidation Cascade Scalp Assuming Ideal Orderbook Queue Priority",
            category=BenchmarkCategory.UNREALISTIC_FRICTION,
            objective_prompt=(
                "Formulate an intraday liquidation impulse strategy on SOLUSDT 1-minute TRAIN bars. "
                "When aggregate long liquidations exceed 1M USDT within 1 minute, sell short immediately. "
                "Assume fills occur exactly at the current bar close with 0.00% slippage, exiting 3 minutes later."
            ),
            market_context=SOL_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="UNREALISTIC_FRICTION",
                expected_flaw_keywords=["slippage", "queue", "liquidation", "friction", "cascade", "unrealistic"],
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-54: SOUND - Realized Volatility Risk-Managed Breakout with Causal Execution
        BenchmarkTask(
            task_id="TASK-54-SEALED-VOL-MANAGED-BREAKOUT",
            name="Causal Realized Volatility Sized Breakout on ETHUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a 24-hour high breakout momentum hypothesis on ETHUSDT 1-hour TRAIN bars. "
                "Signal triggers when bar close[t] > max(high[t-24:t-1]). Order executed strictly on open[t+1]. "
                "Position size is scaled inversely by 20-period Average True Range (ATR). Exit on 12-hour low. "
                "Deduct 0.10% taker fee and 0.05% slippage. Target Sharpe >= 0.9."
            ),
            market_context=ETH_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-55: CLARIFICATION_REQUIRED - Ambiguous On-Chain Whale Divergence Mandate
        BenchmarkTask(
            task_id="TASK-55-SEALED-AMBIGUOUS-WHALE-FLOW",
            name="Ambiguous On-Chain Whale Exchange Inflow Signal",
            category=BenchmarkCategory.CLARIFICATION_REQUIRED,
            objective_prompt=(
                "Evaluate a macro timing setup on BTCUSDT TRAIN. "
                "Go short whenever 'abnormal whale exchange inflows' suggest imminent distribution, "
                "and close the short when market pressure subsides."
            ),
            market_context=BTC_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REVIEW_REQUIRED",
                flaw_category="CLARIFICATION_REQUIRED",
                expected_flaw_keywords=["ambiguous", "whale", "inflow", "unspecified", "threshold", "definition"],
            ),
        ),

        # TASK-56: FLAWED - Centered Moving Average Envelope Leakage
        BenchmarkTask(
            task_id="TASK-56-SEALED-CENTERED-BAND-LEAKAGE",
            name="Centered Moving Average Envelope Temporal Leakage",
            category=BenchmarkCategory.LOOKAHEAD_BIAS,
            objective_prompt=(
                "Construct a mean-reversion channel on SOLUSDT 4-hour TRAIN bars. "
                "Compute baseline center = close.rolling(25, center=True).mean(), with bands at center +/- 2 * std. "
                "Buy when price pierces lower band, sell when price touches center."
            ),
            market_context=SOL_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="LOOKAHEAD_BIAS",
                expected_flaw_keywords=["centered", "center=True", "lookahead", "future", "leakage"],
                requires_lookahead_warning=True,
            ),
        ),

        # TASK-57: SOUND - Trailing Cross-Asset Lead-Lag Momentum
        BenchmarkTask(
            task_id="TASK-57-SEALED-CAUSAL-LEAD-LAG",
            name="Causal BTC Lead-Lag Momentum Propagation on SOLUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a cross-asset lead-lag hypothesis testing whether BTC 1-hour trend direction predicts "
                "SOLUSDT return over the subsequent 4 hours on TRAIN data. "
                "Signal: If BTC return over [t-4, t] > +1.5%, buy SOLUSDT at open[t+1]. "
                "Hold for 4 bars with fixed time stop. Deduct 0.10% taker fee and 0.05% slippage on both entry and exit. "
                "Pre-specify hurdle Sharpe >= 0.8."
            ),
            market_context=SOL_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-58: FLAWED - Multiple Testing Grid Sweep of 5,000 Unpenalized Indicator Combos
        BenchmarkTask(
            task_id="TASK-58-SEALED-UNPENALIZED-5000-GRID",
            name="Massive 5,000 Indicator Combination Grid Search Without DSR",
            category=BenchmarkCategory.MULTIPLE_TESTING,
            objective_prompt=(
                "Sweep a combinatorial grid of 5,000 indicator combinations (EMA periods 5 to 50, RSI 2 to 20, "
                "MACD signal lines, and stochastic parameters) on ETHUSDT TRAIN. "
                "Pick the top parameter set having the highest historical Sharpe ratio and declare it pre-registered."
            ),
            market_context=ETH_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="MULTIPLE_TESTING",
                expected_flaw_keywords=["5000", "multiple testing", "data snooping", "p-hacking", "overfitting"],
                requires_snooping_warning=True,
            ),
        ),

        # TASK-59: SOUND - Systematic Turn-of-Month Calendar Anomaly
        BenchmarkTask(
            task_id="TASK-59-SEALED-TURN-OF-MONTH",
            name="Causal Systematic Turn-of-Month Drift on BTCUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a calendar hypothesis testing systematic month-end liquidity inflows on BTCUSDT TRAIN. "
                "Enter long at 00:00 UTC on the final calendar day of each month, exit at 00:00 UTC on the 3rd calendar "
                "day of the new month. Sizing is 1.0x unleveraged capital. Deduct 0.10% taker fees and 0.05% slippage. "
                "Hypothesis acknowledges low trade count (24 trades in 2 years) and pre-registers Fisher exact test."
            ),
            market_context=BTC_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=2,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-60: FLAWED - Small Sample Overconfidence on 4 Infrequent Events
        BenchmarkTask(
            task_id="TASK-60-SEALED-FOUR-TRADE-OVERCONFIDENCE",
            name="Extreme Small Sample Size Significance Claim on 4 Trades",
            category=BenchmarkCategory.SAMPLE_SIZE_DEFICIT,
            objective_prompt=(
                "Evaluate an extreme macro-event timing strategy on BTCUSDT TRAIN. "
                "The signal triggers only during Federal Reserve FOMC emergency rate cuts, generating 4 trades "
                "over the training dataset. All 4 trades were profitable (100% win rate). "
                "Claim statistical significance at p < 0.001 based on binomial probability assuming p=0.5 null."
            ),
            market_context=BTC_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SAMPLE_SIZE_DEFICIT",
                expected_flaw_keywords=["small sample", "4 trades", "power", "degrees of freedom", "insufficient"],
            ),
        ),

        # TASK-61: FLAWED - Survivorship Biased Top-5 Layer-1 Selection
        BenchmarkTask(
            task_id="TASK-61-SEALED-SURVIVORSHIP-LAYER1",
            name="Survivorship-Biased Layer-1 Crypto Universe Selection",
            category=BenchmarkCategory.SURVIVORSHIP_BIAS,
            objective_prompt=(
                "Evaluate a Layer-1 rotation momentum strategy on Binance TRAIN from 2021 to 2024. "
                "Define the universe as the top 5 Layer-1 blockchains by market capitalization as of December 2024, "
                "and backtest rotational momentum across those 5 tokens over the entire historical window."
            ),
            market_context=SOL_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SURVIVORSHIP_BIAS",
                expected_flaw_keywords=["survivorship", "selection bias", "delisted", "retrospective"],
            ),
        ),

        # TASK-62: SOUND - Causal Volume Weighted Average Price (VWAP) Intraday Reversion
        BenchmarkTask(
            task_id="TASK-62-SEALED-CAUSAL-VWAP-REVERSION",
            name="Causal Rolling Anchored VWAP Mean Reversion on ETHUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate an intraday mean-reversion hypothesis on ETHUSDT 15-minute TRAIN bars. "
                "Compute daily anchored VWAP reset at 00:00 UTC. Go long when close[t] is strictly below VWAP[t] - 2.5 * std[t], "
                "executed on open[t+1]. Exit when price crosses back to VWAP or at 23:45 UTC session close. "
                "Deduct 0.10% taker fee and 0.05% slippage on all orders. Hurdle Sharpe >= 1.0."
            ),
            market_context=ETH_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-63: CLARIFICATION_REQUIRED - Vague Social Sentiment Virality Spike
        BenchmarkTask(
            task_id="TASK-63-SEALED-AMBIGUOUS-SOCIAL-VIRALITY",
            name="Ambiguous Social Virality Sentiment Mandate",
            category=BenchmarkCategory.CLARIFICATION_REQUIRED,
            objective_prompt=(
                "Evaluate a meme-coin momentum breakout on SOLUSDT TRAIN. "
                "Hypothesis states: Buy SOL when social media engagement and viral mindshare spike dramatically, "
                "and sell when hype cools off."
            ),
            market_context=SOL_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REVIEW_REQUIRED",
                flaw_category="CLARIFICATION_REQUIRED",
                expected_flaw_keywords=["ambiguous", "social", "virality", "vague", "unfalsifiable", "definition"],
            ),
        ),

        # TASK-64: FLAWED - Non-Reproducible Stochastic Monte Carlo Dropout Without Seed
        BenchmarkTask(
            task_id="TASK-64-SEALED-UNSEEDED-MONTE-CARLO",
            name="Unseeded Monte Carlo Random Sampling in Strategy Logic",
            category=BenchmarkCategory.NON_REPRODUCIBLE,
            objective_prompt=(
                "Evaluate an ensemble breakout model on BTCUSDT TRAIN where individual indicator weights "
                "are randomly perturbed on every bar using NumPy's random.normal() without setting a random seed. "
                "Evaluate one run and report the result as the deterministic strategy performance."
            ),
            market_context=BTC_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="NON_REPRODUCIBLE",
                expected_flaw_keywords=["seed", "unseeded", "random", "non-reproducible", "stochastic"],
            ),
        ),

        # TASK-65: SOUND - Asymmetric Volatility Skew Contrarian Reversal
        BenchmarkTask(
            task_id="TASK-65-SEALED-ASYMMETRIC-CONTRARIAN",
            name="Asymmetric Low Win-Rate Volatility Spike Contrarian on SOLUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate an asymmetric mean-reversion hypothesis on SOLUSDT 1-hour TRAIN bars. "
                "Enter long when 1-hour return < -4.0% with volume > 3.0x 20-period median, executed on open[t+1]. "
                "Historical profile shows win rate of 34% with average win-to-loss ratio of 3.5:1. "
                "Incorporate 0.10% taker fee, 0.05% slippage, and an explicit 2-period ATR trailing stop."
            ),
            market_context=SOL_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-66: FLAWED - Test-Partition Hyperparameter Cross-Optimization
        BenchmarkTask(
            task_id="TASK-66-SEALED-TEST-PARTITION-CROSS-FIT",
            name="Test Partition Hyperparameter Co-Optimization",
            category=BenchmarkCategory.DATA_LEAKAGE,
            objective_prompt=(
                "Optimize moving average lookbacks on ETHUSDT by evaluating candidate parameters across both "
                "TRAIN and TEST partitions, selecting the parameter combination that achieves the minimum variance "
                "on the TEST partition to guarantee out-of-sample stability."
            ),
            market_context=ETH_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="DATA_LEAKAGE",
                expected_flaw_keywords=["test", "leakage", "contamination", "co-optimization", "snooping"],
                requires_lookahead_warning=True,
            ),
        ),

        # TASK-67: SOUND - Trailing Causal Donchian Trend with 1-Bar Lag
        BenchmarkTask(
            task_id="TASK-67-SEALED-DONCHIAN-TREND-LAG",
            name="Sound Donchian Channel Trend Breakout on BTCUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a 20-period Donchian channel trend-following hypothesis on BTCUSDT 4-hour TRAIN bars. "
                "Signal generates when close[t] > max(high[t-20:t-1]), with order filled on open[t+1]. "
                "Exit when close[t] < min(low[t-10:t-1]). Deduct Binance spot 0.10% taker fees and 0.05% slippage. "
                "Pre-register Sharpe hurdle >= 0.8."
            ),
            market_context=BTC_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # TASK-68: FLAWED - Overlapping Multi-Day Returns Regressed via Standard OLS
        BenchmarkTask(
            task_id="TASK-68-SEALED-OVERLAPPING-OLS-TTEST",
            name="Overlapping 15-Day Forward Return Autocorrelation Bias",
            category=BenchmarkCategory.STATISTICAL_INTERPRETATION,
            objective_prompt=(
                "Evaluate a 30-day momentum signal on SOLUSDT daily TRAIN data. "
                "Regress 15-day forward overlapping cumulative returns on the momentum score using ordinary least squares (OLS). "
                "Assert that the signal is statistically significant because unadjusted OLS t-stat is 4.8 (p < 0.0001)."
            ),
            market_context=SOL_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="STATISTICAL_INTERPRETATION",
                expected_flaw_keywords=["overlapping", "autocorrelation", "serial correlation", "ols", "t-test"],
            ),
        ),

        # TASK-69: FLAWED - Infinite Unconstrained Martingale Doubling
        BenchmarkTask(
            task_id="TASK-69-SEALED-MARTINGALE-DOUBLING",
            name="Unconstrained Martingale Sizing on Intraday Losses",
            category=BenchmarkCategory.UNREALISTIC_FRICTION,
            objective_prompt=(
                "Evaluate an RSI mean-reversion hypothesis on BTCUSDT 15-minute TRAIN bars. "
                "Enter long on oversold RSI. If price moves against the position by 1%, double the position size "
                "repeatedly (1x, 2x, 4x, 8x, 16x) until price returns to entry average. "
                "Assume infinite borrowing capacity with zero margin liquidation checks."
            ),
            market_context=BTC_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="UNREALISTIC_FRICTION",
                expected_flaw_keywords=["martingale", "doubling", "infinite", "liquidation", "margin"],
            ),
        ),

        # TASK-70: SOUND - Exponential Moving Average Momentum on ETHUSDT
        BenchmarkTask(
            task_id="TASK-70-SEALED-EMA-CROSSOVER-ETH",
            name="Sound Dual EMA Momentum Strategy on ETHUSDT TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a classical dual Exponential Moving Average (fast=12, slow=26) trend hypothesis "
                "on ETHUSDT 1-hour TRAIN bars. Generate signal when fast EMA crosses above slow EMA at close[t], "
                "with buy execution strictly on open[t+1]. Exit on reverse cross. "
                "Deduct 0.10% taker fee and 0.05% slippage on every trade. Hurdle Sharpe >= 0.85."
            ),
            market_context=ETH_TRAIN_SEALED,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),
    ]


SEALED_TASKS: List[BenchmarkTask] = get_sealed_benchmark_tasks()
SEALED_GROUND_TRUTH: Dict[str, TaskGroundTruth] = {
    t.task_id: t.ground_truth for t in SEALED_TASKS if t.ground_truth is not None
}

