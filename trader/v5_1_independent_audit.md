# Strategy V5.1 Independent Audit & Metric Reconciliation Report

**Audit Date:** 2026-10-30  
**Target:** Strategy V5 Multi-Asset Volatility-Parity Trend Following with ATR Trailing Stops (VP-TF)  
**Evaluator:** Independent Quantitative Auditor  
**Audit Decision:** **`VERIFIED NO-GO`**  
**Safety Gate:** All live trading, testnet connectivity, and forward paper orders remain strictly disabled.

---

## Executive Audit Summary

This audit independently reproduced the mathematical, statistical, and operational claims of the Strategy V5 VP-TF research engine (`trader-timemachine/research_v5_trend_following.py`). The audit recomputed all performance metrics directly from the raw daily klines of `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, and `BNBUSDT` without relying on derived summaries.

### Audit Verdict: **`VERIFIED NO-GO`**
The empirical decision of **NO-GO** reached in V5 is fully confirmed and verified:
1. **Mathematical Reproducibility**: All returns, drawdowns, equity curves, and friction costs match the underlying simulation ledger with a numerical compounding error of **$7.28 \times 10^{-12}$ USD** (< 0.01 cents).
2. **Failure of Out-of-Sample Alpha**: In formal paired moving-block bootstrap tests against BTC Buy & Hold on the untouched out-of-sample test partition, all three preregistered configurations produced $p$-values exceeding **0.90** (CFG1 $p = 0.9570$, CFG2 $p = 0.9580$, CFG3 $p = 0.9265$). Under the Bonferroni threshold ($\alpha = 0.0167$), the strategy overwhelmingly fails to reject the null hypothesis of zero excess return.
3. **Confirmed Defects**: Two non-fatal reporting defects were identified and corrected (a markdown transcription typo in the sensitivity table and a semantic misinterpretation of bootstrap $p$-values in the report's text).

---

## 1. Resolution of Specific Audit Inquiries

### Question A: Data Accounting (729 Raw Candles vs 699 Continuous Active Bars)
- **Verified Facts**:
  - Raw dataset contains exactly **729 continuous daily candles** spanning 2024-10-30T00:00:00Z to 2026-10-28T00:00:00Z (0 gaps, 0 duplicates).
  - The first **30 bars (bars 0 to 29)**, spanning 2024-10-30 to 2024-11-28, are the **preregistered indicator warmup window**.
  - **Reason for Exclusion**: Indicator computation requires historical depth. Calculating the 14-day ATR requires 14 bars; calculating the 30-day Donchian High ($H_{30}$) requires 30 preceding completed bars.
  - The first bar eligible for trade signal evaluation is **bar 30** (2024-11-29).
  - The continuous active trading horizon spans **bars 30 to 728**, which equals:
    $$728 - 30 + 1 = \mathbf{699\text{ active bars}}$$
  - These 699 bars generate exactly **698 daily return intervals** ($R_t = \text{Equity}_{t+1} / \text{Equity}_t - 1.0$).
  - **Reconciliation**: $30\text{ warmup bars} + 699\text{ active bars} = \mathbf{729\text{ total bars}}$. Every excluded bar is fully accounted for.

---

### Question B: Execution Timing Discrepancy (NEXT_OPEN vs SAME_CLOSE)
- **Report Claim**: The V5 report Table 4 displayed identical returns (+1.08%) and identical final equity ($10,107.96) for both `NEXT_OPEN` and `SAME_CLOSE`.
- **Audit Findings**:
  - Re-running the simulation engine with unrounded floating-point precision yields:
    - **`NEXT_OPEN` Final Equity**: **\$10,107.9603** (Net return: **+1.0796%**)
    - **`SAME_CLOSE` Final Equity**: **\$10,108.2834** (Net return: **+1.0828%**)
    - **Unrounded Difference**: **+\$0.3231 USD** (32.3 cents).
  - **Defect Identified**: The simulation JSON (`v5_experiment_results.json`) correctly recorded `$10,108.28` for `SAME_CLOSE`. A manual transcription error occurred when drafting the markdown table in `strategy_v5_trend_following_report.md`, copying `$10,107.96` into both rows.
  - Both returns round to **+1.08%** when formatted to 2 decimal places.
  - **Physical Mechanism**: In 24/7 Binance crypto spot trading, the 00:00:00 UTC candle close price of Day $t$ and the 00:00:00 UTC candle open price of Day $t+1$ are adjacent ticks in a continuous order book. Across all 729 daily candles, the maximum gap between $c_t$ and $o_{t+1}$ was only $1.87 on an $80k BTC price (0.002%). Consequently, opening fills and closing fills in daily crypto data differ by fractions of a cent per share.

---

### Question C: Bootstrap Inference & Semantic Correction of P-Values
- **Statistical Model Inspected**:
  - **Paired Statistic**: $D_t = R_{\text{strat}, t} - R_{\text{bench}, t}$ (Daily net excess return).
  - **Null Hypothesis $H_0$**: $\mu(D_t) \le 0$ (The strategy delivers zero or negative excess returns over passive holding).
  - **Alternative Hypothesis $H_1$**: $\mu(D_t) > 0$ (The strategy delivers true positive excess return).
  - **Resampling Procedure**: Moving block bootstrap with $B = 2,000$ iterations, block length = 10 days, seed = 42.
  - **Empirical Sample Mean Excess**: **$-0.1387\%$ per day** (Cumulative test excess return: **$-20.40$ percentage points** vs BTC).
  - **Empirical $p$-value**: **$p = 0.9570$**.
  - **95% Bootstrap CI of Cumulative Excess**: `[-65.01 pp, +4.18 pp]`.
- **Semantic Defect Corrected**:
  - The V5 report stated: *"out-of-sample passive holding outperformed trend following with > 90% probability"*.
  - **Correction**: This statement commits the classical Bayesian/frequentist conflation. A $p$-value is **not** the posterior probability that a hypothesis or benchmark wins.
  - The correct frequentist interpretation: Given data where the strategy underperformed by $-20.40$ percentage points, the one-sided $p$-value of $0.9570$ signifies that under the null hypothesis of no edge ($\mu \le 0$), there is a 95.7% probability of observing an excess return as high or higher than the observed empirical deficit. There is **zero evidence to reject $H_0$**.

---

### Question D: Multiple Testing Family & Selection Bias
- **Preregistered Family**: $M = 3$ parameter configurations (CFG1 Baseline, CFG2 Tight, CFG3 Wide).
  $$\alpha_{\text{Bonferroni}} = \frac{0.05}{3} = \mathbf{0.0167}$$
- **Cumulative Repository Search Space**:
  - Across the full research campaign (V1 15m ORB, V2 HTF breakouts, V3 mean reversion, V4 rotation variants, and V5 trend following), approximately **22 distinct strategy models** have been backtested.
  - Cumulative project-wide threshold: $\alpha_{\text{project}} = \frac{0.05}{22} \approx \mathbf{0.0023}$.
- **Impact on Decision**: **None**. Because all three test $p$-values exceed $0.90$ ($p \gg 0.05$), the null hypothesis cannot be rejected under any threshold ($\alpha = 0.05$, $0.0167$, or $0.0023$).

---

### Question E: Benchmark Friction Breakdown to the Exact Cent
The report's benchmark friction figures were independently reconciled across all partitions:

| Partition | BTC Initial Entry (15 bps) | BTC Terminal Exit (15 bps) | BTC Total Friction | EW Initial Entry (15 bps) | EW Terminal Exit (15 bps) | EW Total Friction |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Train** | \$15.00 | \$13.83 | **\$28.83** | \$15.00 | \$13.79 | **\$28.79** |
| **Val** | \$15.00 | \$11.80 | **\$26.80** | \$15.00 | \$10.59 | **\$25.59** |
| **Test** | \$15.00 | \$18.25 | **\$33.25** | \$15.00 | \$18.85 | **\$33.85** |
| **Overall** | \$15.00 | \$12.50 | **\$27.50** | \$15.00 | \$11.51 | **\$26.51** |

- **Why Benchmark Friction Varies**:
  - Initial entry is always fixed: $\$10,000 \times 0.0015 = \$15.00$.
  - Terminal liquidation friction is charged against the **drifted market value** of the held tokens at the end of the partition:
    $$\text{Terminal Friction} = \text{Terminal Equity}_{\text{gross}} \times 0.0015$$
  - When the market appreciates (e.g. Test partition, where BTC rises $+21.5\%$ to \$12,148), terminal liquidation friction rises to **\$18.25** ($\$15.00 + \$18.25 = \mathbf{\$33.25}$).
  - When the market declines (e.g. Val partition, where BTC drops to \$7,856), terminal liquidation friction falls to **\$11.80** ($\$15.00 + \$11.80 = \mathbf{\$26.80}$).

---

### Question F: Genuine Like-for-Like V4 vs V5 Friction Comparison
- **Original Claim**: "Friction Slashed by up to 83% (V4 \$1,663.13 vs V5 \$280.53 – \$386.76)".
- **Audit Date Alignment**:
  - V4 used a 55-bar warmup, running over **673 return intervals** (2024-12-25 to 2026-10-28).
  - V5 used a 30-bar warmup, running over **698 return intervals** (2024-11-29 to 2026-10-28).
  - The original comparison spanned slightly different calendar horizons (V5 had 25 additional trading days).
- **Exact Like-for-Like Recomputation (Identical Dates, Capital, and Universe)**:
  - **Overall Horizon (Exact 673 return intervals)**:
    - V4 Relative Strength Friction: **\$1,663.13** (97 rebalances)
    - V5 Trend Following Friction: **\$383.97** (110 trades)
    - **Genuine Overall Cost Reduction: 76.91%**
  - **Out-of-Sample Test (Exact 134 return intervals: 2026-06-17 to 2026-10-28)**:
    - V4 Test Friction: **\$372.86** (21 rebalances)
    - V5 Test Friction: **\$89.72** (26 trades)
    - **Genuine Test Cost Reduction: 75.94%**
- **Conclusion**: The claim that event-driven trend following slashed transaction costs by $> 75\%$ is mathematically validated on identical calendar dates. The "83%" figure was based on the widest configuration (CFG3: \$280.53).

---

### Question G: Portfolio Risk Constraints & Sizing Verification
- **40% Notional Cap**: Audited all 114 trade executions across the continuous horizon.
  - Maximum observed position weight at entry was exactly **40.00%**. No trade ever breached the 40% single-asset cap.
- **Aggregate Cash Constraints**: When multiple breakouts triggered simultaneously, allocation requests were scaled down proportionally by available USDT cash. No leverage, debt, or short positions were created.
- **Compounding Precision**: Conservation of capital verified across all daily steps:
  $$\left|\text{Equity}_N - \text{Equity}_0 \prod_{t=1}^N (1 + R_t)\right| = \mathbf{7.28 \times 10^{-12}\text{ USD}}$$

---

## 2. Confirmed Defects and Impact on Decision

| Defect Description | Classification | Resolution / Correction | Impact on Verdict |
| :--- | :--- | :--- | :--- |
| **Table 4 Typo in Report** | Minor Presentation Defect | Transcribed `$10,107.96` instead of `$10,108.28` for `SAME_CLOSE` | **Zero Impact**: Both round to +1.08% return. |
| **P-Value Semantic Description** | Statistical Interpretation Defect | Described p-value as "probability passive holding wins" | **Zero Impact**: The null hypothesis of zero excess return remains firmly unrejected ($p = 0.9570$). |
| **V4/V5 Date Misalignment in Text** | Accounting Comparison Defect | 83% comparison evaluated 698 vs 673 days | **Zero Impact**: Like-for-like reduction is verified at **76.91%** on identical dates. |

---

## 3. Unresolved Uncertainties

1. **Intraday Liquidity & Microstructure**: Our historical database consists of 1-day OHLCV bars. While 15 bps total drag (10 bps taker fee + 5 bps slippage) is realistic for large-cap spot on Binance, execution during flash volatility spikes cannot be modeled without order book tick data.
2. **Universe Beta Collinearity**: The 4 assets (`BTC`, `ETH`, `SOL`, `BNB`) exhibit average pairwise correlation $\bar{\rho} \approx 0.77$. When macro crypto sentiment turns, all assets break out or crash in unison, preventing cross-sectional risk diversification.

---

## 4. Final Audit Verdict

### **`VERIFIED NO-GO`**

The V5 evaluation is mathematically sound, reproducible to machine precision, and the **NO-GO** decision is empirically incontrovertible. Strategy V5 VP-TF must not be deployed to paper trading or live execution.

- **Reconciliation JSON**: [`trader/v5_1_audit_reconciliation.json`](file:///e:/projects/binance/trader/v5_1_audit_reconciliation.json)
- **Regression Test Suite**: [`tests/test_audit_v5_1.py`](file:///e:/projects/binance/tests/test_audit_v5_1.py) (7 tests, passing in 0.156s)

