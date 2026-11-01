"""Fixed benchmark dataset with known quantitative research properties.

Provides standardized evaluation tasks covering:
1. Valid research hypotheses
2. Underspecified hypotheses
3. Data leakage traps
4. Look-ahead bias scenarios
5. Multiple-testing problems
6. Overfitting scenarios
7. Incorrect statistical interpretations
8. Unrealistic transaction-cost assumptions
9. Non-reproducible experimental proposals
10. Valid mean-reversion hypotheses (expanded)
11. Survivorship bias scenarios (expanded)
12. Sub-basis-point over-optimization scenarios (expanded)
13. Test partition contamination traps (expanded)
14. Valid volatility breakout hypotheses (expanded)

All tasks provide market context strictly confined to approved TRAIN partition data.
Ground truth rubrics and expected decisions are isolated in TaskGroundTruth.
"""

from typing import Any, Dict, List
from trader.agent.benchmark.schema import (
    BenchmarkCategory,
    BenchmarkTask,
    TaskGroundTruth,
)

# Standard TRAIN-only market summary statistics for BTCUSDT (400 training intervals)
STANDARD_TRAIN_CONTEXT: Dict[str, Any] = {
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


def get_canonical_tasks() -> List[BenchmarkTask]:
    """Return the original canonical suite of 9 benchmark evaluation tasks."""
    return [
        # Task 1: Valid Research Hypothesis
        BenchmarkTask(
            task_id="TASK-01-VALID-MOM",
            name="Valid Momentum Hypothesis on BTCUSDT Daily TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a trend-following momentum hypothesis for SyntheticSMAStrategy on BTCUSDT Daily TRAIN partition. "
                "Specify a minimal parameter grid of 2 configurations (e.g. fast_period in [10.0, 15.0], slow_period in [30.0]). "
                "Incorporate realistic Binance taker fees (0.10%) and slippage (0.05%). "
                "Preregister explicit performance hurdles for validation."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=5,
                min_taker_fee_rate=0.0005,
                requires_lookahead_warning=False,
                requires_snooping_warning=False,
            ),
        ),

        # Task 2: Underspecified Hypothesis
        BenchmarkTask(
            task_id="TASK-02-UNDERSPEC-HYP",
            name="Underspecified Unfalsifiable Trading Mandate",
            category=BenchmarkCategory.UNDERSPECIFIED_HYPOTHESIS,
            objective_prompt=(
                "Formulate an artificial intelligence strategy that maximizes profits on BTCUSDT without losing money. "
                "Do not specify fixed parameter ranges or formal mathematical rules, but guarantee positive return in all regimes."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="UNDERSPECIFIED_HYPOTHESIS",
                expected_flaw_keywords=["falsifiable", "unbounded", "vague", "underspecified", "rejection"],
                max_allowed_grid_size=5,
                requires_snooping_warning=True,
            ),
        ),

        # Task 3: Data Leakage Trap
        BenchmarkTask(
            task_id="TASK-03-DATA-LEAKAGE",
            name="Feature Normalization Target Leakage Trap",
            category=BenchmarkCategory.DATA_LEAKAGE,
            objective_prompt=(
                "Design a strategy feature where each day's price is normalized by dividing by the maximum closing price "
                "observed across the entire multi-year dataset, including future periods t+1 through T. "
                "Evaluate this feature for early breakout detection on BTCUSDT."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="DATA_LEAKAGE",
                expected_flaw_keywords=["leakage", "target leakage", "future", "contamination", "lookahead"],
                requires_lookahead_warning=True,
            ),
        ),

        # Task 4: Lookahead Bias Scenario
        BenchmarkTask(
            task_id="TASK-04-LOOKAHEAD-BIAS",
            name="Centered Indicator Lookahead Bias Scenario",
            category=BenchmarkCategory.LOOKAHEAD_BIAS,
            objective_prompt=(
                "Implement a smoothed moving average filter defined as the 5-day centered average: "
                "SMA_centered[t] = (Price[t-2] + Price[t-1] + Price[t] + Price[t+1] + Price[t+2]) / 5. "
                "Execute buy orders at time t whenever Price[t] crosses above SMA_centered[t]."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="LOOKAHEAD_BIAS",
                expected_flaw_keywords=["lookahead", "future bars", "t+1", "unrealizable", "look-ahead"],
                requires_lookahead_warning=True,
            ),
        ),

        # Task 5: Multiple-Testing Problem
        BenchmarkTask(
            task_id="TASK-05-MULTIPLE-TESTING",
            name="Massive Uncorrected Parameter Sweep (P-Hacking)",
            category=BenchmarkCategory.MULTIPLE_TESTING,
            objective_prompt=(
                "Optimize a moving average crossover strategy by evaluating all combinations of fast_period in [1, 2, ..., 100] "
                "and slow_period in [5, 10, ..., 200] across 2,000 parameter points on BTCUSDT TRAIN data. "
                "Select the single highest Sharpe ratio without adjusting for multiple testing."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="MULTIPLE_TESTING",
                expected_flaw_keywords=["multiple testing", "p-hacking", "data snooping", "deflated sharpe", "m_total"],
                max_allowed_grid_size=5,
                requires_snooping_warning=True,
            ),
        ),

        # Task 6: Overfitting Scenario
        BenchmarkTask(
            task_id="TASK-06-OVERFITTING",
            name="High-Degree Polynomial Overfitting Scenario",
            category=BenchmarkCategory.OVERFITTING,
            objective_prompt=(
                "Fit a 12th-degree polynomial price predictor with 13 unconstrained free parameters to 50 daily bars "
                "of BTCUSDT training data to achieve an R-squared of 0.99 prior to trade simulation."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="OVERFITTING",
                expected_flaw_keywords=["overfitting", "degrees of freedom", "polynomial", "curve fitting", "variance"],
                requires_snooping_warning=True,
            ),
        ),

        # Task 7: Incorrect Statistical Interpretation
        BenchmarkTask(
            task_id="TASK-07-STAT-INTERPRET",
            name="Spurious Significance Claim on Autocorrelated Returns",
            category=BenchmarkCategory.STATISTICAL_INTERPRETATION,
            objective_prompt=(
                "A strategy test produces an unadjusted p-value of 0.048 on non-stationary daily return series with "
                "serial correlation AR(1) = 0.42. The researcher claims statistically significant alpha at the 5% level "
                "without performing stationary bootstrap or Deflated Sharpe Ratio calculation."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="STATISTICAL_INTERPRETATION",
                expected_flaw_keywords=["autocorrelation", "spurious", "bootstrap", "deflated", "non-stationary"],
                requires_snooping_warning=True,
            ),
        ),

        # Task 8: Unrealistic Transaction-Cost Assumptions
        BenchmarkTask(
            task_id="TASK-08-UNREALISTIC-FRICTION",
            name="Friction-Blind High-Frequency Scalping Scenario",
            category=BenchmarkCategory.UNREALISTIC_FRICTION,
            objective_prompt=(
                "Evaluate an intraday scalping strategy on BTCUSDT generating 15 round-trip trades per day. "
                "Assume 0.00% taker fees and 0.00% slippage to demonstrate an annualized return of 180%."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="UNREALISTIC_FRICTION",
                expected_flaw_keywords=["friction", "taker fee", "slippage", "transaction cost", "turnover"],
                min_taker_fee_rate=0.0005,
            ),
        ),

        # Task 9: Non-Reproducible Experimental Proposal
        BenchmarkTask(
            task_id="TASK-09-NON-REPRODUCIBLE",
            name="Non-Deterministic Seedless Stochastic Proposal",
            category=BenchmarkCategory.NON_REPRODUCIBLE,
            objective_prompt=(
                "Propose a trading strategy whose parameters are randomly sampled at each execution from an unseeded "
                "floating distribution without fixed bounds, reproducible seeds, or specified partition start/end dates."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="NON_REPRODUCIBLE",
                expected_flaw_keywords=["reproducibility", "seed", "deterministic", "unseeded", "preregistration"],
            ),
        ),
    ]


def get_expanded_tasks() -> List[BenchmarkTask]:
    """Return the expanded suite of 14 benchmark evaluation tasks."""
    canonical = get_canonical_tasks()
    additional = [
        # Task 10: Valid Mean-Reversion Hypothesis
        BenchmarkTask(
            task_id="TASK-10-VALID-MEAN-REV",
            name="Valid RSI Mean-Reversion Hypothesis on BTCUSDT Daily TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a mean-reversion hypothesis for SyntheticRSIStrategy on BTCUSDT Daily TRAIN partition. "
                "Specify a minimal parameter grid of 2 configurations (e.g. rsi_period in [14], oversold_threshold in [30.0, 35.0]). "
                "Incorporate realistic Binance taker fees (0.10%) and slippage (0.05%). "
                "Preregister explicit performance hurdles for validation."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=5,
                min_taker_fee_rate=0.0005,
                requires_lookahead_warning=False,
                requires_snooping_warning=False,
            ),
        ),

        # Task 11: Survivorship Bias Scenario
        BenchmarkTask(
            task_id="TASK-11-SURVIVORSHIP-BIAS",
            name="Ex-Post Universe Selection Survivorship Bias",
            category=BenchmarkCategory.SURVIVORSHIP_BIAS,
            objective_prompt=(
                "Construct a momentum portfolio by selecting the top 10 best-performing tokens of 2025 and backtesting "
                "trend continuation on those exact tokens starting from January 2024 without accounting for delisted tokens."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SURVIVORSHIP_BIAS",
                expected_flaw_keywords=["survivorship", "ex-post", "selection bias", "delisted", "hindsight"],
            ),
        ),

        # Task 12: Sub-Basis-Point In-Sample Over-Optimization
        BenchmarkTask(
            task_id="TASK-12-IN-SAMPLE-OVER-OPTIMIZATION",
            name="Sub-Basis-Point In-Sample Stop-Loss Over-Optimization",
            category=BenchmarkCategory.OVER_OPTIMIZATION,
            objective_prompt=(
                "Optimize a stop-loss distance parameter at 0.001% increments between 1.000% and 5.000% across 4,000 parameter "
                "evaluations on BTCUSDT TRAIN to achieve an in-sample profit factor greater than 3.5."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="OVER_OPTIMIZATION",
                expected_flaw_keywords=["over-optimization", "overfitting", "p-hacking", "grid sweep", "multiple testing"],
                max_allowed_grid_size=5,
                requires_snooping_warning=True,
            ),
        ),

        # Task 13: Test Data Contamination Trap
        BenchmarkTask(
            task_id="TASK-13-TEST-DATA-CONTAMINATION",
            name="Unauthorized TEST Partition Evaluation and Tuning",
            category=BenchmarkCategory.PARTITION_CONTAMINATION,
            objective_prompt=(
                "Tune the moving average window parameters directly against the out-of-sample TEST partition to ensure "
                "maximal out-of-sample Sharpe ratio prior to preregistration freeze."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="PARTITION_CONTAMINATION",
                expected_flaw_keywords=["test partition", "contamination", "leakage", "data snooping", "unauthorized"],
                requires_lookahead_warning=True,
                requires_snooping_warning=True,
            ),
        ),

        # Task 14: Valid Volatility Breakout Hypothesis
        BenchmarkTask(
            task_id="TASK-14-VALID-VOL-BREAKOUT",
            name="Valid ATR Volatility Breakout Hypothesis on BTCUSDT Daily TRAIN",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate an ATR channel breakout hypothesis for SyntheticATRStrategy on BTCUSDT Daily TRAIN partition. "
                "Specify a minimal parameter grid of 2 configurations (e.g. atr_period in [20], multiplier in [2.0, 2.5]). "
                "Incorporate realistic Binance taker fees (0.10%) and slippage (0.05%). "
                "Preregister explicit performance hurdles for validation."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=5,
                min_taker_fee_rate=0.0005,
                requires_lookahead_warning=False,
                requires_snooping_warning=False,
            ),
        ),

        # Task 15: Valid Hypothesis with Imperfect but Defensible Assumptions
        BenchmarkTask(
            task_id="TASK-15-VALID-IMPERFECT-ASSUMPTION",
            name="Valid Momentum with Defensible Rebalancing Timing Assumption",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a daily rebalancing momentum hypothesis for SyntheticSMAStrategy on BTCUSDT Daily TRAIN. "
                "Assume daily rebalancing executed strictly at 00:00:05 UTC with a conservative 0.12% fee buffer, "
                "explicitly acknowledging a small 5-second execution tracking error relative to 00:00:00 close."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=5,
                min_taker_fee_rate=0.0005,
                requires_lookahead_warning=False,
                requires_snooping_warning=False,
            ),
        ),

        # Task 16: Valid Hypothesis with Limited Statistical Power
        BenchmarkTask(
            task_id="TASK-16-VALID-LIMITED-POWER",
            name="Valid Macro Weekly Trend with Limited Power but Rigorous Hurdles",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a weekly macro breakout hypothesis for SyntheticSMAStrategy on BTCUSDT TRAIN using weekly bars "
                "(total N=104 bars). Bounded grid of M=2 configurations. Because sample size is small, pre-register a "
                "conservative significance hurdle with Bonferroni adjustment and stationary block bootstrap."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=5,
                min_taker_fee_rate=0.0005,
                requires_lookahead_warning=False,
                requires_snooping_warning=False,
            ),
        ),

        # Task 17: Hypothesis Requiring Methodological Clarification
        BenchmarkTask(
            task_id="TASK-17-NEED-CLARIFICATION",
            name="Ambiguous Volatility Breakout Mandate Requiring Clarification",
            category=BenchmarkCategory.CLARIFICATION_REQUIRED,
            objective_prompt=(
                "Evaluate an unspecified volatility channel breakout on BTCUSDT TRAIN without declaring whether "
                "the channel is derived from Average True Range, standard deviation bands, or Keltner channels. "
                "Suggest testing whatever channel indicator performs best on the data."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REVISE",
                flaw_category="CLARIFICATION_REQUIRED",
                expected_flaw_keywords=["clarification", "unspecified", "indicator", "channel", "ambiguous"],
                requires_snooping_warning=True,
            ),
        ),

        # Task 18: Subtle Retrospective Execution Lookahead Bias
        BenchmarkTask(
            task_id="TASK-18-SUBTLE-LOOKAHEAD",
            name="Subtle Retrospective Execution Lookahead Bias",
            category=BenchmarkCategory.SUBTLE_LOOKAHEAD,
            objective_prompt=(
                "Implement an end-of-day momentum strategy where today's trading signal is computed using today's "
                "closing price at 23:59:59 UTC, but the execution order is simulated at today's open price at 00:00:00 UTC."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SUBTLE_LOOKAHEAD",
                expected_flaw_keywords=["lookahead", "open price", "close price", "retrospective", "causal"],
                requires_lookahead_warning=True,
            ),
        ),

        # Task 19: Inadequate Sample Size for Parameter Count
        BenchmarkTask(
            task_id="TASK-19-INADEQUATE-SAMPLE-SIZE",
            name="Inadequate Sample Size / Negative Degrees of Freedom Scenario",
            category=BenchmarkCategory.SAMPLE_SIZE_DEFICIT,
            objective_prompt=(
                "Fit a 5-parameter multi-indicator strategy (fast MA, slow MA, RSI length, ATR multiplier, threshold) "
                "using only 15 daily bars of BTCUSDT TRAIN data."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=False,
                expected_decision="REJECT",
                flaw_category="SAMPLE_SIZE_DEFICIT",
                expected_flaw_keywords=["sample size", "degrees of freedom", "insufficient", "15 bars", "overfitting"],
                requires_snooping_warning=True,
            ),
        ),

        # Task 20: Valid Proposal that Conservative Reviewer Might Incorrectly Reject
        BenchmarkTask(
            task_id="TASK-20-VALID-CONSERVATIVE-CHALLENGE",
            name="Valid Low Win-Rate Asymmetric Payoff Strategy",
            category=BenchmarkCategory.VALID_HYPOTHESIS,
            objective_prompt=(
                "Evaluate a contrarian mean-reversion strategy on BTCUSDT Daily TRAIN with an expected low win rate "
                "(35%) but high payoff ratio (2.5:1), using a strictly bounded 2-parameter grid with 0.10% taker fee and "
                "0.05% slippage on approved TRAIN partition."
            ),
            market_context=STANDARD_TRAIN_CONTEXT,
            ground_truth=TaskGroundTruth(
                is_valid=True,
                expected_decision="APPROVE",
                max_allowed_grid_size=5,
                min_taker_fee_rate=0.0005,
                requires_lookahead_warning=False,
                requires_snooping_warning=False,
            ),
        ),
    ]
    return canonical + additional


def get_benchmark_tasks(expanded: bool = False) -> List[BenchmarkTask]:
    """Return benchmark evaluation tasks.

    Args:
        expanded: If True, returns full 14-task expanded suite; otherwise canonical 9 tasks.
    """
    if expanded:
        return get_expanded_tasks()
    return get_canonical_tasks()
