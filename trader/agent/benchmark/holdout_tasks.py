"""Unseen holdout benchmark dataset for Phase 2.5 independent reasoning evaluation.

Provides 20 genuinely distinct quantitative research tasks never seen in Phase 2.3 or 2.4:
- Sound but imperfect hypotheses
- Methodologically invalid hypotheses
- Subtle temporal leakage
- Multiple-testing problems
- Unrealistic execution assumptions
- Insufficient sample sizes
- Valid hypotheses with limited statistical power
- Ambiguous hypotheses requiring clarification

All tasks provide market context strictly confined to approved TRAIN partition data.
Ground truth labels and expected decisions are isolated in TaskGroundTruth.
"""

from typing import Any, Dict, List
from trader.agent.benchmark.schema import (
    BenchmarkCategory,
    BenchmarkTask,
    TaskGroundTruth,
)

# Standard TRAIN-only market summary statistics for ETHUSDT (400 training intervals)
ETH_TRAIN_CONTEXT: Dict[str, Any] = {
    "symbol": "ETHUSDT",
    "partition": "TRAIN",
    "total_bars": 400,
    "start_date": "2024-01-01",
    "end_date": "2025-02-04",
    "mean_daily_return_pct": 0.0982,
    "annualized_vol_pct": 54.12,
    "trend_direction": "UPWARD",
    "max_drawdown_train_pct": 28.5,
}

# Standard TRAIN-only market summary statistics for SOLUSDT (400 training intervals)
SOL_TRAIN_CONTEXT: Dict[str, Any] = {
    "symbol": "SOLUSDT",
    "partition": "TRAIN",
    "total_bars": 400,
    "start_date": "2024-01-01",
    "end_date": "2025-02-04",
    "mean_daily_return_pct": 0.1874,
    "annualized_vol_pct": 72.30,
    "trend_direction": "UPWARD",
    "max_drawdown_train_pct": 34.1,
}

# Standard TRAIN-only market summary statistics for BTCUSDT (400 training intervals)
BTC_TRAIN_CONTEXT: Dict[str, Any] = {
    "symbol": "BTCUSDT",
    "partition": "TRAIN",
    "total_bars": 400,
    "start_date": "2024-01-01",
    "end_date": "2025-02-04",
    "mean_daily_return_pct": 0.1245,
    "annualized_vol_pct": 48.62,
    "trend_direction": "UPWARD",
    "max_drawdown_train_pct": 22.4,
}


def get_holdout_tasks() -> List[BenchmarkTask]:
    """Return the 20 unseen holdout benchmark evaluation tasks."""
    return [
        # HOLDOUT-01: Sound Trend Following
        BenchmarkTask(
            task_id="TASK-21-HOLD-EMA-TREND",
            name="Sound Exponential Moving Average Trend Strategy on ETHUSDT TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a trend-following momentum hypothesis using Exponential Moving Average "
                "(EMA fast=12, slow=26) on ETHUSDT 4-hour TRAIN partition data. "
                "Incorporate Binance spot taker fees of 0.10% and estimated slippage of 0.05%. "
                "Require an ATR-based stop-loss at 2.0x ATR and pre-specify Sharpe hurdle >= 0.8."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=4,
                min_taker_fee_rate=0.0005,
                requires_lookahead_warning=False,
                requires_snooping_warning=False,
            ),
        ),

        # HOLDOUT-02: Methodologically Invalid (Unfalsifiable Sentiment Narrative)
        BenchmarkTask(
            task_id="TASK-22-HOLD-SENTIMENT",
            name="Unfalsifiable Whale Accumulation Sentiment Narrative",
            category=BenchmarkCategory.UNDERSPECIFIED_HYPOTHESIS,
            objective_prompt=(
                "Propose a trading rule based on smart money and whale accumulation narrative on SOLUSDT. "
                "Hypothesis states that whenever institutional whales are quietly accumulating tokens "
                "during low volatility periods, price will subsequently surge. "
                "Enter when accumulation feels persistent and exit when retail FOMO peaks."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="UNDERSPECIFIED_HYPOTHESIS",
                expected_flaw_keywords=["falsifiable", "narrative", "subjective", "unbounded", "vague"],
                requires_snooping_warning=False,
            ),
        ),

        # HOLDOUT-03: Subtle Temporal Leakage (Centered Rolling Window)
        BenchmarkTask(
            task_id="TASK-23-HOLD-CENTERED-ROLLING",
            name="Centered Rolling Window Volatility Leakage",
            category=BenchmarkCategory.LOOKAHEAD_BIAS,
            objective_prompt=(
                "Construct a volatility breakout filter on BTCUSDT TRAIN data where volatility is computed "
                "using a 21-period centered moving standard deviation window: "
                "df['vol'] = df['close'].rolling(21, center=True).std(). "
                "Enter long when current close exceeds 2 * df['vol'] above the rolling mean."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="LOOKAHEAD_BIAS",
                expected_flaw_keywords=["center", "centered", "future", "lookahead", "leakage"],
                requires_lookahead_warning=True,
            ),
        ),

        # HOLDOUT-04: Retrospective Execution Timing
        BenchmarkTask(
            task_id="TASK-24-HOLD-RETROSPECTIVE-FILL",
            name="Retrospective Execution Timing on Candle Close",
            category=BenchmarkCategory.SUBTLE_LOOKAHEAD,
            objective_prompt=(
                "Evaluate a 15-minute momentum breakout on ETHUSDT TRAIN partition. "
                "The signal triggers when the 15-minute bar close strictly breaks above the 20-bar high. "
                "In simulation, execute the buy order at the Open price of the exact same 15-minute bar."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SUBTLE_LOOKAHEAD",
                expected_flaw_keywords=["open", "same bar", "retrospective", "timing", "lookahead"],
                requires_lookahead_warning=True,
            ),
        ),

        # HOLDOUT-05: Multiple Testing Without Adjustment
        BenchmarkTask(
            task_id="TASK-25-HOLD-UNPENALIZED-SWEEP",
            name="Unpenalized 12,000 Parameter Sweep Without Deflated Sharpe",
            category=BenchmarkCategory.MULTIPLE_TESTING,
            objective_prompt=(
                "Sweep a 5-dimensional grid of indicators (RSI period [5..30], MACD fast [5..20], "
                "slow [20..50], signal [5..15], Bollinger k [1.5..3.0]) generating 12,000 distinct configurations. "
                "Select the top-performing configuration with highest in-sample Sharpe ratio without applying "
                "Bonferroni, False Discovery Rate, or Deflated Sharpe Ratio (DSR) adjustments."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="MULTIPLE_TESTING",
                expected_flaw_keywords=["sweep", "multiple testing", "p-hacking", "data snooping", "overfitting"],
                requires_snooping_warning=True,
            ),
        ),

        # HOLDOUT-06: Insufficient Sample Size
        BenchmarkTask(
            task_id="TASK-26-HOLD-INSUFFICIENT-SAMPLE",
            name="Macro Regime Shift with Inadequate Sample Size",
            category=BenchmarkCategory.SAMPLE_SIZE_DEFICIT,
            objective_prompt=(
                "Evaluate a macro halving-cycle regime model on BTCUSDT TRAIN. "
                "The strategy enters only during quarterly macro inflection points, producing a total of 5 trades "
                "over the entire 400-bar training sample. "
                "Claim statistical significance at p < 0.01 based on a standard Student t-test of the 5 trades."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SAMPLE_SIZE_DEFICIT",
                expected_flaw_keywords=["sample size", "degrees of freedom", "power", "small sample", "insufficient"],
            ),
        ),

        # HOLDOUT-07: Unrealistic Execution Assumptions
        BenchmarkTask(
            task_id="TASK-27-HOLD-ZERO-FRICTION",
            name="High-Frequency Scalping with Zero Friction",
            category=BenchmarkCategory.UNREALISTIC_FRICTION,
            objective_prompt=(
                "Evaluate a 1-minute order book micro-scalping strategy on SOLUSDT TRAIN targeting 2 basis points "
                "per trade with an average holding time of 45 seconds. "
                "Assume 0.00% maker fee (VIP Tier 9 promo) and zero slippage under limit order fills."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="UNREALISTIC_FRICTION",
                expected_flaw_keywords=["friction", "fee", "zero", "unrealistic", "slippage"],
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-08: Normalization Information Leakage
        BenchmarkTask(
            task_id="TASK-28-HOLD-MINMAX-LEAKAGE",
            name="Global MinMax Normalization Leakage",
            category=BenchmarkCategory.DATA_LEAKAGE,
            objective_prompt=(
                "Formulate a neural feature strategy on ETHUSDT TRAIN. "
                "Before partitioning data, apply MinMax normalization: "
                "scaled_price = (price - global_min) / (global_max - global_min), "
                "where global_min and global_max are computed across the full 2021-2026 dataset."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="DATA_LEAKAGE",
                expected_flaw_keywords=["global", "minmax", "leakage", "future", "normalization"],
                requires_lookahead_warning=True,
            ),
        ),

        # HOLDOUT-09: Sound Volatility Breakout
        BenchmarkTask(
            task_id="TASK-29-HOLD-DONCHIAN-BREAKOUT",
            name="Sound Donchian Channel Volatility Breakout on BTCUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a Donchian channel breakout strategy on BTCUSDT 1-hour TRAIN partition. "
                "Go long when close exceeds the upper 20-period Donchian channel with 1.5x volume expansion. "
                "Incorporate Binance 0.10% taker fee and 0.05% slippage. Exit when price crosses below 10-period midpoint."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-10: Overfitting High-Degree Polynomial
        BenchmarkTask(
            task_id="TASK-30-HOLD-POLYNOMIAL-OVERFIT",
            name="10th-Order Polynomial Trend Fitting",
            category=BenchmarkCategory.OVERFITTING,
            objective_prompt=(
                "Fit a 10th-order polynomial regression model P(t) = a_0 + a_1*t + ... + a_10*t^10 "
                "over the preceding 60 bars on SOLUSDT TRAIN. "
                "Predict the return of bar t+1 by extrapolating the 10th-order polynomial tangent."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="OVERFITTING",
                expected_flaw_keywords=["polynomial", "overfit", "degree", "curve fitting", "extrapolation"],
                requires_snooping_warning=True,
            ),
        ),

        # HOLDOUT-11: Valid Limited Statistical Power
        BenchmarkTask(
            task_id="TASK-31-HOLD-LIMITED-POWER",
            name="Defensible Weekly Trend Filter with Limited Power",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a weekly macro trend filter on ETHUSDT TRAIN. "
                "The strategy yields 38 independent trade events over the historical training window. "
                "Acknowledge that statistical power is modest due to low sample count, but maintain valid "
                "degrees of freedom, realistic 0.10% taker fees, and conservative pre-registered hurdles."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=2,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-12: Survivorship Bias
        BenchmarkTask(
            task_id="TASK-32-HOLD-SURVIVORSHIP-BIAS",
            name="Survivorship-Biased Crypto Universe Selection",
            category=BenchmarkCategory.SURVIVORSHIP_BIAS,
            objective_prompt=(
                "Construct a multi-token cross-sectional momentum portfolio on Binance TRAIN. "
                "Select universe as the top-10 market cap coins as of November 2026, and backtest the momentum "
                "selection rules on those exact 10 tokens from 2021 to 2026."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SURVIVORSHIP_BIAS",
                expected_flaw_keywords=["survivorship", "delisted", "universe", "selection bias", "retrospective"],
            ),
        ),

        # HOLDOUT-13: Non-Reproducible Stochasticity
        BenchmarkTask(
            task_id="TASK-33-HOLD-STOCHASTIC-DROPOUT",
            name="Unseeded Stochastic Dropout Backtest",
            category=BenchmarkCategory.NON_REPRODUCIBLE,
            objective_prompt=(
                "Evaluate a deep feature representation strategy on BTCUSDT TRAIN where Monte Carlo dropout "
                "remains active during backtest execution. "
                "No random seed is pinned, resulting in non-deterministic signals across repeated runs."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="NON_REPRODUCIBLE",
                expected_flaw_keywords=["seed", "unseeded", "dropout", "non-deterministic", "stochastic"],
            ),
        ),

        # HOLDOUT-14: Sound Mean Reversion
        BenchmarkTask(
            task_id="TASK-34-HOLD-RSI-MEAN-REVERSION",
            name="Sound 2-Period RSI Mean Reversion on SOLUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a short-term mean-reversion hypothesis on SOLUSDT 1-hour TRAIN partition. "
                "Enter long when 2-period RSI drops below 10.0 in an established 200-period SMA uptrend. "
                "Exit when RSI crosses above 70.0 or after a 6-hour time-stop. Include 0.10% fees."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-15: Ambiguous Volatility Metric
        BenchmarkTask(
            task_id="TASK-35-HOLD-AMBIGUOUS-VOLATILITY",
            name="Ambiguous Volatility Expansion Trigger Requiring Clarification",
            category=BenchmarkCategory.CLARIFICATION_REQUIRED,
            objective_prompt=(
                "Evaluate a breakout setup on ETHUSDT TRAIN. "
                "Rule: Enter long when price surges and 'volatility expands significantly'. "
                "Exit when market cools down."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REVIEW_REQUIRED",
                flaw_category="CLARIFICATION_REQUIRED",
                expected_flaw_keywords=["vague", "unspecified", "clarification", "ambiguous", "metric"],
            ),
        ),

        # HOLDOUT-16: Test Partition Tuning
        BenchmarkTask(
            task_id="TASK-36-HOLD-TEST-TUNING",
            name="Test Partition Hyperparameter Optimization",
            category=BenchmarkCategory.DATA_LEAKAGE,
            objective_prompt=(
                "Tune the fast and slow SMA lookback periods on BTCUSDT by iterating over both TRAIN and TEST "
                "partitions simultaneously to find the parameter set that minimizes max drawdown on TEST data. "
                "Report the resulting parameters as the pre-registered strategy specification."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="DATA_LEAKAGE",
                expected_flaw_keywords=["test", "contamination", "tuning", "leakage", "snooping"],
                requires_lookahead_warning=True,
            ),
        ),

        # HOLDOUT-17: Sound Imperfect Execution Assumption
        BenchmarkTask(
            task_id="TASK-37-HOLD-CONSERVATIVE-EXECUTION",
            name="Defensible Conservative Execution Assumption",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate an intraday breakout hypothesis on BTCUSDT TRAIN. "
                "Assume fills occur at the next bar High price for long entries (worst-case execution) "
                "rather than bar Open. While this degrades reported Sharpe, it provides a leak-free, "
                "conservative lower-bound estimate of strategy profitability with 0.10% fees."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=2,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-18: Spurious Autocorrelation Interpretation
        BenchmarkTask(
            task_id="TASK-38-HOLD-AUTOCORRELATED-TTEST",
            name="Overlapping Forward Returns with Naive OLS t-Test",
            category=BenchmarkCategory.STATISTICAL_INTERPRETATION,
            objective_prompt=(
                "Evaluate a 14-day multi-horizon momentum signal on SOLUSDT TRAIN daily data. "
                "Regress 10-day forward overlapping cumulative returns on the momentum indicator using standard OLS. "
                "Rely on the standard unadjusted OLS t-statistic (t=4.2, p < 0.001) to conclude significance."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="STATISTICAL_INTERPRETATION",
                expected_flaw_keywords=["autocorrelation", "serial correlation", "overlapping", "t-test", "ols"],
            ),
        ),

        # HOLDOUT-19: Sub-Basis-Point Over-Optimization
        BenchmarkTask(
            task_id="TASK-39-HOLD-SUB-BPS-TUNING",
            name="Sub-Basis-Point Threshold Over-Optimization",
            category=BenchmarkCategory.OVER_OPTIMIZATION,
            objective_prompt=(
                "Fine-tune the breakout threshold for ETHUSDT TRAIN across 50 increments between 1.0000% "
                "and 1.0050% (step size = 0.0001% / 0.01 bps). "
                "Select 1.0034% because it yields a Sharpe ratio of 1.51 while 1.0033% yields 1.48."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="OVER_OPTIMIZATION",
                expected_flaw_keywords=["sub-basis", "threshold", "overfitting", "noise", "microscopic"],
                requires_snooping_warning=True,
            ),
        ),

        # HOLDOUT-20: Sound Asymmetric Contrarian Setup
        BenchmarkTask(
            task_id="TASK-40-HOLD-ASYMMETRIC-CONTRARIAN",
            name="Sound Asymmetric Low Win-Rate Contrarian Setup on SOLUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate an asymmetric mean-reversion contrarian setup on SOLUSDT 1-hour TRAIN partition. "
                "Historical backtest indicates a low win rate of 33%, but an average win-to-loss payoff ratio of 3.8:1. "
                "Incorporate strict 0.10% taker fee, 0.05% slippage, and an explicit trailing volatility stop."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-21: Sound Bollinger Band Mean Reversion
        BenchmarkTask(
            task_id="TASK-41-HOLD-BOLLINGER-MR",
            name="Sound Bollinger Band Mean Reversion on ETHUSDT TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a causal mean-reversion strategy using Bollinger Bands (length=20, num_std=2.0) "
                "on ETHUSDT 1-hour TRAIN partition data. Enter long when close[t] closes below lower band, "
                "executed strictly on open[t+1]. Exit when close[t] crosses middle band. "
                "Incorporate Binance spot taker fee of 0.10%, 0.05% slippage, and a stop-loss at 3.0 standard deviations."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-22: Sound Volatility Targeting Trend Strategy
        BenchmarkTask(
            task_id="TASK-42-HOLD-VOL-TARGETING",
            name="Sound Volatility Targeting Trend Strategy on BTCUSDT TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a volatility-targeted trend-following hypothesis on BTCUSDT daily TRAIN partition data. "
                "Scale position sizes inversely to annualized realized volatility computed over the trailing 30 daily closes. "
                "Enforce a target portfolio volatility of 15% annualized, rebalancing weekly with 0.10% transaction friction "
                "and a maximum leverage bound of 1.0x."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-23: Sound Weekend Calendar Anomaly
        BenchmarkTask(
            task_id="TASK-43-HOLD-WEEKEND-ANOMALY",
            name="Sound Weekend Calendar Anomaly on SOLUSDT TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a systematic calendar anomaly on SOLUSDT 4-hour TRAIN partition data testing whether "
                "weekend liquidity contraction yields directional drift. Enter on Friday 20:00 UTC bar close, "
                "exit on Sunday 20:00 UTC bar close. Account for wider weekend bid-ask spreads with 0.10% taker fee "
                "and conservative 0.10% slippage."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=2,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-24: Sound Donchian Breakout with 1-Bar Execution Delay
        BenchmarkTask(
            task_id="TASK-44-HOLD-ATR-BREAKOUT-EXEC-DELAY",
            name="Sound Donchian Channel Breakout with 1-Bar Execution Delay on ETHUSDT",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a 20-period Donchian breakout strategy on ETHUSDT 1-hour TRAIN partition data. "
                "Signal generates when close[t] strictly exceeds max(high[t-20:t-1]), with order executed strictly "
                "at open[t+1]. Exit when close[t] falls below the 10-period low. Apply 0.10% taker fee and 0.05% slippage, "
                "requiring an ATR trailing stop."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=3,
                min_taker_fee_rate=0.0005,
            ),
        ),

        # HOLDOUT-25: Full-Sample Partition Normalization Leakage
        BenchmarkTask(
            task_id="TASK-45-HOLD-EXPANDING-LOOKAHEAD-ZSCORE",
            name="Full-Sample Partition Normalization Leakage on BTCUSDT",
            category=BenchmarkCategory.LOOKAHEAD_BIAS,
            objective_prompt=(
                "Evaluate a cross-sectional mean-reversion signal on BTCUSDT 1-hour TRAIN partition. "
                "Standardize the price series into a Z-score by subtracting the mean of all 400 bars across the "
                "entire partition and dividing by the partition-wide standard deviation. "
                "Trade long whenever the Z-score is below -2.0."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="LOOKAHEAD_BIAS",
                expected_flaw_keywords=["full-sample", "lookahead", "partition-wide", "future", "normalization"],
                requires_lookahead_warning=True,
            ),
        ),

        # HOLDOUT-26: Ex-Post Intraday Range Filter Leakage
        BenchmarkTask(
            task_id="TASK-46-HOLD-EXPOST-VOLATILITY-HALT",
            name="Ex-Post Intraday Range Filter Leakage on SOLUSDT",
            category=BenchmarkCategory.LOOKAHEAD_BIAS,
            objective_prompt=(
                "Evaluate an intraday momentum scalp on SOLUSDT 15-minute TRAIN partition. "
                "Enter trades at 12:00 UTC, but filter out trades if the entire day's high-low range exceeds 5.0% "
                "to prevent trading through extreme volatility days."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="LOOKAHEAD_BIAS",
                expected_flaw_keywords=["ex-post", "daily range", "lookahead", "future", "intraday"],
                requires_lookahead_warning=True,
            ),
        ),

        # HOLDOUT-27: Ex-Post Future Outcome Dependent Stop Placement
        BenchmarkTask(
            task_id="TASK-47-HOLD-ASYMMETRIC-STOP-LEAKAGE",
            name="Ex-Post Future Outcome Dependent Stop Placement on ETHUSDT",
            category=BenchmarkCategory.LOOKAHEAD_BIAS,
            objective_prompt=(
                "Evaluate a breakout strategy on ETHUSDT 1-hour TRAIN partition. "
                "At entry bar t, set the stop loss to bar t low if the trade reaches +2% gain over the "
                "subsequent 5 bars; otherwise set the stop loss to bar t high to quickly exit losing trades."
            ),
            market_context=ETH_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="LOOKAHEAD_BIAS",
                expected_flaw_keywords=["future", "ex-post", "leakage", "subsequent", "lookahead"],
                requires_lookahead_warning=True,
            ),
        ),

        # HOLDOUT-28: End-of-Period Winner Selection Survivorship Trap
        BenchmarkTask(
            task_id="TASK-48-HOLD-DELISTED-SURVIVORSHIP-BIAS",
            name="End-of-Period Winner Selection Survivorship Trap",
            category=BenchmarkCategory.SURVIVORSHIP_BIAS,
            objective_prompt=(
                "Evaluate an equal-weighted momentum basket strategy across cryptocurrency pairs from 2021 to 2024. "
                "Construct the universe by taking the top 10 tokens ranked by market capitalization as of December 31, 2024, "
                "and backtest the basket over the preceding 3 years."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SURVIVORSHIP_BIAS",
                expected_flaw_keywords=["survivorship", "delisted", "end-of-period", "selection bias", "retrospective"],
            ),
        ),

        # HOLDOUT-29: Uncorrected 500-Parameter Combinatorial Search Trap
        BenchmarkTask(
            task_id="TASK-49-HOLD-TARGET-SHUFFLE-SEARCH",
            name="Uncorrected 500-Parameter Combinatorial Search Trap",
            category=BenchmarkCategory.MULTIPLE_TESTING,
            objective_prompt=(
                "Conduct a systematic grid search of 500 combinations of moving averages and RSI filter thresholds "
                "on BTCUSDT TRAIN partition. Select the top parameter combination having the highest Sharpe ratio "
                "with nominal p-value < 0.05, without family-wise error rate control or White Reality Check."
            ),
            market_context=BTC_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="MULTIPLE_TESTING",
                expected_flaw_keywords=["multiple testing", "500", "p-hacking", "data snooping", "family-wise", "overfitting"],
                requires_snooping_warning=True,
            ),
        ),

        # HOLDOUT-30: Ambiguous Intuitive Volume Divergence Mandate
        BenchmarkTask(
            task_id="TASK-50-HOLD-MOMENTUM-VOLUME-DIVERGENCE",
            name="Ambiguous Intuitive Volume Divergence Mandate",
            category=BenchmarkCategory.CLARIFICATION_REQUIRED,
            objective_prompt=(
                "Formulate a strategy trading unhealthy momentum-volume divergences on SOLUSDT TRAIN. "
                "Buy when volume does not match price action in an authentic way, indicating exhaustion, "
                "and sell when retail sentiment cools down."
            ),
            market_context=SOL_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REVIEW_REQUIRED",
                flaw_category="CLARIFICATION_REQUIRED",
                expected_flaw_keywords=["ambiguous", "underspecified", "clarification", "unfalsifiable", "definition"],
            ),
        ),
    ]


HOLDOUT_TASKS: List[BenchmarkTask] = get_holdout_tasks()
HOLDOUT_GROUND_TRUTH: Dict[str, TaskGroundTruth] = {
    t.task_id: t.ground_truth for t in HOLDOUT_TASKS if t.ground_truth is not None
}

