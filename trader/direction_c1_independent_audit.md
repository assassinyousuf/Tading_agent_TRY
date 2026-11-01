# Direction C.1 Independent Statistical Correctness Audit Report

**Audit Target:** Direction C Benchmark & Statistical Evaluation Engine  
**Implementation Source:** `trader-timemachine/benchmark_engine.py`  
**Test Suite:** `tests/test_benchmark_engine.py` & `tests/test_audit_direction_c1.py`  
**Evaluation Dataset:** Binance Daily BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT (729 bars: 2024-10-30 to 2026-10-28)  
**Evaluator:** Independent Statistical & Quantitative Audit  
**Audit Date:** 2026-10-30  

---

## 1. Executive Summary & Verdict

This independent audit conducted a comprehensive statistical, mathematical, and algorithmic review of the Direction C research infrastructure (`benchmark_engine.py`). Direction C established a 5-family benchmark catalog, an automated chronological walk-forward cross-validation splitter, and an inferential testing suite including paired moving-block bootstrap tests, Minimum Detectable Effect (MDE) power analysis, Deflated Sharpe Ratio (DSR) adjustments, and multiple-testing corrections.

### Final Verdict: `VERIFIED FOR RESEARCH USE -- WITH DOCUMENTED LIMITATIONS`

The Direction C benchmark and evaluation engine is mathematically sound, free of future lookahead, strictly enforces trading friction, and provides a reliable baseline for quantitative research. However, this audit uncovered **four material methodological nuances** that must be explicitly understood and documented before evaluating future trading strategies:

1. **Sharpe / Sortino Definition Divergence:** `benchmark_engine.py` implements *Compounded Sharpe/Sortino* (using CAGR in the numerator) rather than standard *Arithmetic Sharpe/Sortino* (using arithmetic mean daily returns). While both are mathematically legitimate, they diverge materially for volatile assets due to variance drag ($-\frac{1}{2}\sigma^2$).
2. **MDE Excess Volatility Proxy:** The Direction C report claimed an MDE of **+77.22%** against BTC. This audit proves that figure holds *only* when evaluating an active strategy against Cash ($\sigma_D = \sigma_{\text{BTC}}$). When evaluating correlated crypto strategies ($\rho \approx 0.85$), the true paired excess volatility is approximately halved, reducing the true MDE to **+38.27%** to **+43.50%**.
3. **Bootstrap Null-Centering vs. Percentile Inversion:** The legacy bootstrap test computed the fraction of raw uncentered replicates $\le 0$, which evaluates Bayesian posterior probability under a flat prior rather than a formal frequentist null distribution ($H_0: \mu_D \le 0$). The centered bootstrap test has been implemented and verified.
4. **Deflated Sharpe Ratio Annual Scaling:** Bailey & Lopez de Prado (2014) DSR operates on per-period returns. Proper annual time scaling ($\sigma / \sqrt{365.25}$) has been verified, demonstrating that across $M=67$ historical project trials, the expected maximum Sharpe ratio from data mining alone is **2.39**, driving the DSR of an observed annual Sharpe of 1.20 down to **5.37%**.

---

## 2. Audit Item 1: Performance Metric Definitions (Compounded vs. Arithmetic)

In portfolio theory and performance attribution, two distinct formulations of the Sharpe ratio exist:
1. **Arithmetic Sharpe Ratio (Standard Conventional):**
   $$\text{SR}_{\text{arith}} = \frac{\bar{R}_d - R_{f,d}}{\sigma_d} \times \sqrt{365.25}$$
   where $\bar{R}_d$ is the arithmetic mean of daily returns, $\sigma_d$ is the sample standard deviation of daily returns, and $R_{f,d} = R_f / 365.25$.
2. **Compounded Sharpe Ratio (CAGR-to-Volatility):**
   $$\text{SR}_{\text{comp}} = \frac{\text{CAGR} - R_f}{\sigma_{\text{ann}}}$$
   where $\text{CAGR} = (V_{\text{final}} / V_{\text{initial}})^{365.25 / N} - 1$, and $\sigma_{\text{ann}} = \sigma_d \times \sqrt{365.25}$.

By Ito's Lemma / log-normal compounding, geometric growth relates to arithmetic drift by variance drag:
$$\text{CAGR} \approx \bar{R}_{\text{ann}} - \frac{1}{2} \sigma_{\text{ann}}^2$$

### Metric Reconciliation Table (698 Return Intervals: 2024-11-29 to 2026-10-28)

| Benchmark ID | Benchmark Name | CAGR (%) | Ann. Mean (%) | Ann. Vol (%) | Compounded SR | Arithmetic SR | Compounded Sortino | Arithmetic Sortino | Divergence Cause |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `BENCH_CASH` | Cash Baseline | +0.00% | +0.00% | 0.00% | 0.0000 | 0.0000 | 0.0000 | 0.0000 | Zero variance |
| `BENCH_BTC_HOLD_GROSS` | BTC Buy & Hold (Gross) | -9.03% | -0.25% | 42.93% | -0.2102 | -0.0059 | -0.3022 | -0.0084 | Variance drag (-9.22 pp) |
| `BENCH_BTC_HOLD_NET` | BTC Buy & Hold (Net) | -9.17% | -0.41% | 42.93% | -0.2136 | -0.0095 | -0.3070 | -0.0137 | Variance drag (-8.76 pp) |
| `BENCH_EW_L1_GROSS` | Equal-Weight L1 (Gross) | -12.88% | -0.68% | 51.13% | -0.2520 | -0.0132 | -0.3524 | -0.0185 | Variance drag (-12.20 pp) |
| `BENCH_EW_L1_NET` | Equal-Weight L1 (Net) | -13.02% | -0.83% | 51.13% | -0.2546 | -0.0163 | -0.3561 | -0.0228 | Variance drag (-12.19 pp) |
| `BENCH_50_50_DRIFT` | 50/50 BTC/Cash (Drift) | -2.04% | +0.25% | 21.52% | -0.0946 | +0.0118 | -0.1368 | +0.0170 | Sign Flip! Mean > 0, CAGR < 0 |
| `BENCH_50_50_MONTHLY` | 50/50 BTC/Cash (Monthly) | -2.90% | -0.66% | 21.37% | -0.1355 | -0.0307 | -0.1940 | -0.0440 | Rebalance friction + drag |
| `BENCH_VOL_TARGET_25` | Vol-Target BTC (25% Vol) | -8.29% | -5.00% | 27.02% | -0.3067 | -0.1850 | -0.4285 | -0.2585 | Realized vol drag + friction |

### Audit Findings on Metrics:
- **Sign Flip Detected:** For `BENCH_50_50_DRIFT`, arithmetic annualized mean return is **+0.25%**, giving an Arithmetic Sharpe of **+0.0118**. However, due to volatility drag and compounding, terminal capital is -2.04% (CAGR = -2.04%), resulting in a Compounded Sharpe of **-0.0946**.
- **Sortino Downside Scaling:** Sortino numerators in `benchmark_engine.py` use CAGR rather than arithmetic excess return. Both metrics correctly penalize downside variance ($\text{TDD} \approx 30.1\%$), but the compounded ratio is significantly more punitive.
- **Rule for Future Use:** Both metrics must be reported simultaneously in strategy tear-sheets, explicitly labeled as `Sharpe_Arithmetic` and `Sharpe_Compounded`.

---

## 3. Audit Item 2: Minimum Detectable Effect (MDE) & Paired Excess Volatility

The Minimum Detectable Effect (MDE) at statistical power $1 - \beta = 0.80$ and significance $\alpha = 0.05$ (one-sided) is governed by:
$$\text{MDE}_{\text{daily}} = (z_{1-\alpha} + z_{1-\beta}) \frac{\sigma_D}{\sqrt{N}} = (1.6449 + 0.8416) \frac{\sigma_D}{\sqrt{N}} = 2.4865 \frac{\sigma_D}{\sqrt{N}}$$
$$\text{MDE}_{\text{annual}} = \text{MDE}_{\text{daily}} \times 365.25 \times 100\%$$

In Direction C, the function `calc_minimum_detectable_effect(n_days=698, excess_vol=0.4293)` yielded **+77.22%**.

### The Paired Excess Volatility Trap
The excess return series is $D_t = R_{\text{strat},t} - R_{\text{bench},t}$. Its variance is:
$$\sigma_D^2 = \sigma_{\text{strat}}^2 + \sigma_{\text{bench}}^2 - 2 \rho \sigma_{\text{strat}} \sigma_{\text{bench}}$$

1. **Active vs. Cash ($R_{\text{bench}} = 0$):**
   $$\sigma_D = \sigma_{\text{strat}} = 42.93\% \implies \text{MDE} = \mathbf{+77.22\%}$$
   *(Matches the Direction C report exactly).*
2. **Active vs. Correlated Benchmark (e.g. EW L1 vs. BTC, $\rho = 0.887$):**
   $$\sigma_D = \sqrt{0.5113^2 + 0.4293^2 - 2(0.887)(0.5113)(0.4293)} = \mathbf{21.28\%}$$
   $$\text{MDE} = 2.4865 \times \frac{0.2128}{\sqrt{698 \times 365.25}} \times 365.25 \times 100\% = \mathbf{+38.27\%}$$
3. **50/50 BTC vs. Cash ($\sigma_D = 21.52\%$):**
   $$\text{MDE} = \mathbf{+38.71\%}$$

### Audit Takeaway on Statistical Power:
Claiming that a strategy must generate +77.22% excess return to beat BTC is true **only if the strategy is completely uncorrelated with BTC ($\rho = 0$) or compared against Cash**. For realistic trend-following and momentum strategies that hold crypto assets and have high beta to BTC ($\rho \ge 0.80$), the paired excess volatility drops to ~21%, meaning an excess return of **+38% to +43%** is statistically detectable. The +77.22% figure must be explicitly documented as the **Active vs. Cash Hurdle**, not the paired excess hurdle.

---

## 4. Audit Item 3: Moving-Block Bootstrap Hypothesis Testing

In Direction C, `paired_block_bootstrap_test` calculated the p-value as the fraction of raw bootstrap replicates with mean excess $\le 0$:
$$p_{\text{legacy}} = \frac{1}{B} \sum_{b=1}^B I\left(\bar{D}_b^* \le 0\right)$$

### The Theoretical Issue
In frequentist hypothesis testing, a p-value is the probability under the **null hypothesis** ($H_0: \mu_D \le 0$) of observing a test statistic as extreme or more extreme than the observed sample mean $\bar{D}$:
$$p = P\left(\bar{D}^* \ge \bar{D} \mid H_0\right)$$
To simulate under $H_0$, the empirical distribution must be centered at the null boundary ($\mu_0 = 0$):
$$\tilde{D}_t = D_t - \bar{D}$$
Then, bootstrap blocks are drawn from $\tilde{D}_t$, and the one-sided p-value is:
$$p_{\text{centered}} = \frac{1}{B} \sum_{b=1}^B I\left(\bar{\tilde{D}}_b^* \ge \bar{D}\right)$$

### Empirical Comparison on Direction C Data (V5 CFG1 vs. BTC Net)

| Metric | Legacy Uncentered Bootstrap | Rigorous Null-Centered Bootstrap | Interpretation |
| :--- | :---: | :---: | :--- |
| **Observed Mean Excess** | -36.17% / year | -36.17% / year | Active strategy lagged BTC Net |
| **P-Value ($H_0: \mu \le 0$)** | **0.8795** | **0.5350** | Both fail to reject null ($p \gg 0.05$) |
| **95% Bootstrap CI** | [-205.75 pp, +37.61 pp] | [-205.75 pp, +37.61 pp] | Contains zero; spans deeply negative |
| **Mathematical Meaning** | Posterior $P(\mu \le 0 \mid \text{data})$ | Frequentist $P(\text{stat} \ge \bar{D} \mid H_0)$ | Legacy is a confidence inversion |

### Audit Resolution:
When $\bar{D} < 0$, both methods yield $p > 0.50$, leading to identical failure-to-reject conclusions. However, if an active strategy generates a positive observed excess ($\bar{D} > 0$), uncentered percentile inversion can suffer from sample-size bias. The null-centered bootstrap is now codified in `tests/test_audit_direction_c1.py` and `audit_direction_c1.py`.

---

## 5. Audit Item 4: Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014)

The Deflated Sharpe Ratio (DSR) computes the probability that an observed Sharpe ratio $\widehat{\text{SR}}$ was generated by skill rather than data snooping across $M$ trials:
$$\text{DSR} = Z\left( \frac{(\widehat{\text{SR}} - \text{SR}^*) \sqrt{N - 1}}{\sqrt{1 - \gamma_3 \widehat{\text{SR}} + \frac{\gamma_4 - 1}{4} \widehat{\text{SR}}^2}} \right)$$
where $\text{SR}^*$ is the expected maximum Sharpe ratio under the null hypothesis of zero skill:
$$\text{SR}^* = \sigma_{\text{trials}} \left[ (1 - \gamma) Z^{-1}\left(1 - \frac{1}{M}\right) + \gamma Z^{-1}\left(1 - \frac{1}{M e}\right) \right]$$
with $\gamma \approx 0.5772156649$ (Euler-Mascheroni constant).

### Time Scaling Reconciliation
Bailey & Lopez de Prado's theorem is derived on per-period returns. When annualizing:
- If per-period Sharpe is $\text{SR}_1$, annual Sharpe is $\text{SR}_{\text{ann}} = \text{SR}_1 \sqrt{365.25}$.
- If standard deviation of annual Sharpe across trials is $\sigma_{\text{trials,ann}} = 1.0$, then daily trial standard deviation is $\sigma_{\text{trials,daily}} = 1.0 / \sqrt{365.25} = 0.0523$.
- In `benchmark_engine.py`, the engine evaluated annualized Sharpe ratios directly with $\sigma_{\text{trials}} = 1.0$.

### Multi-Trial DSR Audit Results ($N = 698$ daily bars, Observed Annual $\text{SR} = 1.20$, Skewness = -0.5, Kurtosis = 2.5)

| Number of Trials ($M$) | Expected Max Annual SR ($\text{SR}^*$) | $Z$-Statistic ($z$) | Deflated Sharpe Ratio (DSR) | Statistical Significance ($\alpha = 0.05$) |
| :---: | :---: | :---: | :---: | :---: |
| **$M = 1$ (No snooping)** | 0.0000 | +1.63 | **0.9483** | Borderline Significant ($p \approx 0.05$) |
| **$M = 5$ (Modest search)** | 1.1926 | +0.01 | **0.5040** | Not Significant ($p = 0.50$) |
| **$M = 67$ (V1--V5 Project Total)** | **2.3863** | **-1.61** | **0.0537** | Completely Insignificant ($p = 0.95$) |
| **$M = 150$ (Extensive backtesting)**| **2.6701** | **-2.00** | **0.0230** | Completely Insignificant ($p = 0.98$) |

### Audit Finding on Snooping:
This reconciliation proves that across the 67 cumulative trials evaluated in this repository across V1 through V5, **an annual Sharpe ratio of 1.20 has an expected probability of only 5.37% of representing genuine skill**. Under 67 trials, an active strategy must demonstrate an annual Sharpe exceeding **2.39** before it can overcome selection bias at the 5% level.

---

## 6. Audit Item 5: Authoritative Walk-Forward Split Boundary Reconciliation

The Direction C implementation plan initially contained an informal placeholder table (Fold 1: bars 30--300, 301--385) that differed from the final balanced rolling splitter. This audit examined the exact index boundaries generated by `WalkForwardSplitter(n_splits=5, warmup_bars=30, mode='rolling', train_bars=300)` over the 729 dataset bars.

### Authoritative Split Boundary Table

| Fold | Warmup Slice (Bars) | Train Slice (Inclusive) | Train Dates (UTC) | Train Bars | Test Slice (Inclusive) | Test Dates (UTC) | Test Bars | Contiguous? | Leakage? |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Fold 1** | [0, 29] (30) | [30, 329] | 2024-11-29 to 2025-09-24 | 300 | [330, 408] | 2025-09-25 to 2025-12-12 | 79 | YES (Start) | ZERO |
| **Fold 2** | [79, 108] (30) | [109, 408] | 2025-02-16 to 2025-12-12 | 300 | [409, 487] | 2025-12-13 to 2026-03-01 | 79 | YES (408+1=409)| ZERO |
| **Fold 3** | [158, 187] (30) | [188, 487] | 2025-05-06 to 2026-03-01 | 300 | [488, 566] | 2026-03-02 to 2026-05-19 | 79 | YES (487+1=488)| ZERO |
| **Fold 4** | [237, 266] (30) | [267, 566] | 2025-07-24 to 2026-05-19 | 300 | [567, 645] | 2026-05-20 to 2026-08-06 | 79 | YES (566+1=567)| ZERO |
| **Fold 5** | [316, 345] (30) | [346, 645] | 2025-10-11 to 2026-08-06 | 300 | [646, 728] | 2026-08-07 to 2026-10-28 | 83 | YES (645+1=646)| ZERO |

### Verification of Invariants:
1. **Contiguity:** Test slices form an unbroken sequence from bar 330 to bar 728 ($79 + 79 + 79 + 79 + 83 = 399$ total test bars).
2. **Anti-Leakage:** For every fold $k$, $\text{Train}_{\text{end}} = \text{Test}_{\text{start}} - 1$. No test bar is ever visible in training.
3. **Warmup Integrity:** Each training window has a 30-bar pre-history window strictly preceding it, satisfying Donchian (30) and ATR (14) indicator initialization.

---

## 7. Audit Item 6: Multiple Testing Adjustments & Benchmark Execution Friction

### Multiple Comparisons Corrections
`benchmark_engine.py` provides Bonferroni, Holm step-down, and Benjamini-Hochberg (BH) FDR corrections via `adjust_multiple_testing_pvalues`.
- **Order Preservation:** The implementation correctly indexes original input positions, sorts for step-up/step-down adjustment, and restores output to original input ordering.
- **Tied P-Values:** Tied input p-values receive identical adjusted p-values and maintain monotonicity ($p_i < p_j \implies \text{adj}_i \le \text{adj}_j$).
- **FWER vs. FDR:** For exploratory research, Benjamini-Hochberg FDR provides necessary statistical power; for final strategy go/no-go decisions, Holm-Bonferroni FWER control at $\alpha = 0.05$ must remain mandatory.

### Benchmark Execution Friction
- **Taker Fee & Slippage:** Net benchmarks accurately charge 10 bps Spot taker fee + 5 bps adverse slippage ($0.15\%$ one-way, $0.30\%$ round-trip).
- **BTC Hold Net:** Paid $15.00 entry drag + $13.83 exit drag = $28.83 on train partition; $15.00 entry + $18.25 exit = $33.25 on test partition.
- **Cash Drag & Rebalancing:** 50/50 drift benchmark correctly models unmanaged passive drift without trading friction; 50/50 monthly rebalanced benchmark incurs friction upon rebalancing, realistically demonstrating cost drag.

---

## 8. Summary of Independent Regression Tests

To permanently prevent regressions and codify the audit findings, 8 dedicated unit tests were implemented in `tests/test_audit_direction_c1.py`:
1. `test_sharpe_arithmetic_vs_compounded`: Verifies variance drag divergence and division-by-zero guards.
2. `test_sortino_arithmetic_vs_compounded`: Verifies downside semi-deviation scaling.
3. `test_mde_excess_volatility_sensitivity`: Verifies that MDE scales with paired excess volatility $\sigma_D$.
4. `test_normal_quantile_accuracy`: Verifies Acklam's inverse normal CDF approximation against known standard quantiles ($\pm 1.0, \pm 1.96, \pm 2.326$).
5. `test_bootstrap_null_centering_vs_uncentered`: Confirms that centered bootstrap properly simulates under $H_0: \mu \le 0$.
6. `test_deflated_sharpe_ratio_properties`: Verifies monotonicity and trial-count sensitivity in DSR.
7. `test_multiple_testing_adjustments_edge_cases`: Verifies Bonferroni, Holm, and FDR handle unsorted lists and tied p-values.
8. `test_authoritative_walk_forward_table_contiguity`: Verifies the 5 walk-forward test folds are strictly contiguous, non-overlapping, and leak-free.

**Workspace Test Suite Execution:** All tests in the test suite pass with zero regressions.

---

## 9. Limitations & Research Guidelines for Future Strategy Evaluation

Future quantitative strategy research in this repository must adhere to the following certified standards:

1. **Dual Sharpe Reporting:** Always report both Arithmetic Sharpe ($\bar{R}/\sigma \times \sqrt{365.25}$) and Compounded Sharpe ($(\text{CAGR} - R_f)/\sigma_{\text{ann}}$). Never cherry-pick the higher metric.
2. **Paired MDE Reporting:** When stating MDE, specify the reference benchmark. Do not quote the +77.22% cash hurdle as the paired hurdle against BTC.
3. **Null-Centered Bootstrap:** Always report the centered bootstrap p-value for $H_0: \mu_D \le 0$ alongside the 95% confidence interval of cumulative excess return.
4. **Cumulative Trial Accounting:** Strategy claims must be evaluated using the cumulative project trial count ($M \ge 67$) in the Deflated Sharpe Ratio calculation to prevent data-snooping contamination.

---

**Audit Sign-off:**  
`VERIFIED FOR RESEARCH USE -- WITH DOCUMENTED LIMITATIONS`

