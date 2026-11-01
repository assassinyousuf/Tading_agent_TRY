# Direction C.3 -- Adversarial Statistical Validation

**Audit Date:** 2026-10-29  
**Repository:** `E:\projects\binance`  
**Auditor:** Independent Adversarial Verification Agent  
**Scope:** Verification and adversarial stress-testing of Direction C, C.1, and C.2 benchmark and statistical engines.  
**Execution Safety:** Research and offline analysis only. Zero broker execution, zero testnet orders, zero paper execution, zero new strategy creation.  
**Final Verdict:** `REQUIRES CORRECTIONS`

---

## Executive Summary

The Direction C research initiative was established to provide an institutional-grade benchmark and statistical testing engine before evaluating new trading strategies. Direction C.1 identified initial statistical anomalies, and Direction C.2 implemented verified mathematical formulas for the Deflated Sharpe Ratio (DSR), moving-block bootstrap, and walk-forward CV. Direction C.2 concluded with the verdict `VERIFIED FOR RESEARCH USE -- WITH DOCUMENTED LIMITATIONS`.

This adversarial audit (Direction C.3) independently challenges that conclusion.

### Crucial Finding: Production Code vs. Audit Script Divergence
Our investigation reveals a critical architectural gap:
**The production engine `trader-timemachine/benchmark_engine.py` was NEVER patched with the corrections proved in Direction C.1 and C.2.**

While the sidecar scripts `audit_direction_c1.py` and `audit_direction_c2.py` contain correct mathematical implementations, `benchmark_engine.py` remains in its defective state:
1. **DSR Time-Scaling Defect (Line 774):** It divides the annualized Sharpe difference by daily standard error, inflating the test statistic $z$ by $\sqrt{365.25} \approx 19.11$. For Strategy V5, `benchmark_engine.py` outputs $z = -31.32$ and $\text{DSR} = 0.0000$, whereas the mathematically correct scaled statistic is $z = -1.6115$ and $\text{DSR} = 0.0508$.
2. **Uncentered Percentile Bootstrap (Line 704):** `paired_block_bootstrap_test` computes `sum(1 for m in boot_means if m <= 0.0) / float(n_boot) = 0.1220`. This is the raw fraction of uncentered replicates below zero, not a two-tailed null-centered hypothesis test ($p = 0.1800$).
3. **Missing Arithmetic Sharpe Metric:** `benchmark_engine.py` only implements compounded Sharpe $((CAGR - R_f) / \sigma_{\text{ann}})$, lacking the arithmetic Sharpe ratio required for Mertens asymptotic variance calculations.

Because any future researcher or pipeline importing `benchmark_engine` will execute these defective functions, the conclusion `VERIFIED FOR RESEARCH USE` was factually premature.

The research framework requires direct patches to `benchmark_engine.py` before new strategy evaluations begin.

---

## 1. Baseline Architecture & Codebase Divergence

Direction C.2 demonstrated rigorous mathematical corrections but confined them entirely to an external audit script (`audit_direction_c2.py`). The production file `trader-timemachine/benchmark_engine.py` was left unaltered.

| Component / Function | `benchmark_engine.py` (Production) | `audit_direction_c2.py` (Audit Sidecar) | Impact on Production Users |
| :--- | :--- | :--- | :--- |
| **`calc_deflated_sharpe_ratio`** | **Defective**: Divides annualized numerator by daily standard error (Line 774). | **Correct**: Consistently matches daily numerator with daily standard error. | Severe distortion of statistical significance ($z$ inflated $\approx 19\times$). |
| **`paired_block_bootstrap_test`** | **Defective**: Uncentered percentile test (`boot_means <= 0.0`) (Line 704). | **Correct**: Centered null hypothesis test ($|\bar{D}^*| \ge \|\bar{D}\|$). | Understates p-values on noisy distributions; invalid size. |
| **Sharpe Metric Type** | **Compounded Only**: Uses $((CAGR - R_f) / \sigma_{\text{ann}})$. | **Both**: Evaluates compounded and arithmetic Sharpe. | Mertens variance derivation assumes arithmetic returns. |
| **Block Size Guidance** | Hardcoded default ($L=10$). | Tested $L \in \{5, 10, 20\}$ with persistence checks. | Users may apply inappropriate block lengths. |
| **Trial Count Accounting** | Fixed input $M=1$. | Parameterized with Bonferroni and DSR tables. | High risk of data snooping underreporting. |

---

## 2. Adversarial Validation of the Moving-Block Bootstrap

Direction C.2 noted that constant synthetic return series produced degenerate bootstrap variance. Direction C.3 evaluated the bootstrap under realistic, non-degenerate stochastic processes.

### 2.1 Agreement with Analytical Student's t-test
We benchmarked the centered moving-block bootstrap ($B=1,000, L=10$) against the analytical two-sample paired t-test on independent Gaussian series ($N=729$):
- **Positive Drift ($\mu = +0.18\%$ daily, $t = 4.0295$):**
  - Analytical t-test $p$-value: $0.000056$ ($p < 0.0001$)
  - Centered bootstrap $p$-value: $0.0000$ ($p < 0.0001$)
  - Uncentered percentile $p$-value: $0.0000$ ($p < 0.0001$)
- **Zero Drift ($\mu = -0.004\%$ daily, $t = -0.0884$):**
  - Analytical t-test $p$-value: $0.5352$
  - Centered bootstrap $p$-value: $0.5130$ (within normal Monte Carlo sampling variation)
  - Uncentered percentile $p$-value: $0.5130$

**Finding:** On independent, non-degenerate data, the centered block bootstrap closely tracks the analytical Student's t-test.

### 2.2 Autoregressive Persistence (AR(1))
Financial time series frequently exhibit serial correlation in trade returns or active spreads. We simulated an AR(1) process with positive persistence $\phi = +0.50$:
- **Naive i.i.d. Bootstrap ($L=1$):** $p = 0.0070$ (Spurious statistical significance due to underestimated variance).
- **Moving-Block Bootstrap ($L=10$):** $p = 0.0250$ (Variance inflated by factor of $\approx \frac{1+\phi}{1-\phi} = 3.0$).

**Finding:** The moving-block bootstrap correctly accounts for autocorrelation when $L$ is matched to the decay horizon, preventing false rejections of the null hypothesis.

### 2.3 Volatility Clustering (GARCH(1,1))
Crypto assets exhibit pronounced volatility clustering. We evaluated an ARCH/GARCH volatility process ($\omega = 10^{-5}, \alpha = 0.10, \beta = 0.85$):
- Centered bootstrap $p$-value: $0.9040$
- 95% Bootstrap CI for daily mean excess: $[-0.002204, +0.000126]$
- Annualized arithmetic CI: $[-80.50\%, +4.60\%]$

**Finding:** Volatility clustering is safely accommodated by block resampling without numerical instability.

### 2.4 Non-Divisible Sample Lengths
In practice, sample sizes are rarely exact multiples of block length $L$. We evaluated $N=698$ with $L=10$:
- Required blocks drawn: $K = \lceil 698 / 10 \rceil = 70$ blocks (700 elements).
- Truncation rule: Truncated to exactly 698 elements.
- Result: Mean and variance unbiased; no off-by-one errors or sample length drift.

### 2.5 Multi-Period Compounding vs. Arithmetic Returns
Direction C.1 and C.2 confirmed that multiplying daily excess returns by $365.25$ yields the **annualized arithmetic mean excess return**, not the compounded CAGR excess return.
- Annualized arithmetic excess: $365.25 \times \bar{D}$
- Compounded excess: $CAGR_{\text{strat}} - CAGR_{\text{bench}}$
- On volatile crypto assets (e.g., SOL $\sigma > 80\%$), arithmetic mean excess exceeds compounded CAGR excess by $\frac{1}{2}\sigma^2 \approx 20\text{--}30\%$.

**Documentation Mandate:** Confidence intervals generated by the bootstrap must be explicitly labeled as **arithmetic excess return intervals**, not compounded CAGR intervals.

---

## 3. Adversarial Validation of the Deflated Sharpe Ratio (DSR)

### 3.1 Mathematical Derivation of the Scaling Defect
The Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) tests whether an observed annualized Sharpe ratio $\widehat{SR}$ exceeds the expected maximum Sharpe ratio $SR^*$ under multiple testing:

$$z = \frac{\widehat{SR} - SR^*}{\text{se}(\widehat{SR})}$$

Where the Mertens asymptotic standard error for an annualized Sharpe ratio estimated over $N$ observations is:

$$\text{se}(\widehat{SR}) = \sqrt{\frac{1 - \gamma_3 \widehat{SR} + \frac{\gamma_4 - 1}{4} \widehat{SR}^2}{N / 365.25}}$$

In `benchmark_engine.py` (Line 774), the code computes:
```python
# DEFECTIVE PRODUCTION CODE:
z = (ann_sr - expected_max_sr) / se_sr  # se_sr is calculated using N, not N / 365.25!
```
Here `se_sr` is the standard error of the **daily** Sharpe ratio ($1/\sqrt{N}$). Dividing the annualized Sharpe numerator by the daily standard error inflates $z$ by $\sqrt{365.25} \approx 19.11$.

**Numerical Impact on Strategy V5:**
- Annualized Sharpe: $\widehat{SR}_{\text{ann}} = -0.0725$
- Daily Sharpe: $\widehat{SR}_{\text{daily}} = -0.0038$
- Daily Standard Error ($N=729$): $\text{se}_{\text{daily}} = 0.0370$
- Annualized Standard Error: $\text{se}_{\text{ann}} = 0.0370 \times \sqrt{365.25} = 0.7077$
- Production Engine Result: $z = -31.32 \implies \text{DSR} = 0.000000$
- Corrected Engine Result: $z = -1.6115 \implies \text{DSR} = 0.0508$

While both confirm the NO-GO verdict for V5 ($\text{DSR} < 0.95$), the production formula renders the test statistic mathematically meaningless and will severely distort borderline strategies.

### 3.2 Extreme Moment Stress Testing
We subjected the corrected DSR implementation to extreme distribution moments:
- **Excess Kurtosis ($\gamma_4 = 25.0$):** Standard error expands from $0.7077$ to $1.815$, reducing $z$ and penalizing fat-tailed asset returns.
- **Negative Skewness ($\gamma_3 = -2.50$):** Asymmetry increases variance, correctly depressing DSR.
- **Degenerate Variance ($\sigma = 0$):** Gracefully caught and returns $\text{DSR} = 0.0$ without zero-division crashes.

---

## 4. Trial Count Accounting & The $M_{\text{eff}}$ Reality Check

Direction C.2 claimed an effective trial count $M_{\text{eff}} \in [15, 25]$ derived from an assumed average inter-trial correlation $\bar{\rho} \approx 0.50$ across 67 historical trials. Direction C.3 conducted an empirical audit of the historical data to verify this claim.

### 4.1 Historical Data Availability Audit
We inspected the underlying experiment artifacts and ledgers across the entire research program (V1 through V5):
1. **Strategy V1 (SOL Breakout):** Evaluated 15m candles over a 3-month sample window (September--November 2024).
2. **Strategy V2 (Higher Timeframe):** Evaluated 15m and 1h candles on SOLUSDT and BTCUSDT.
3. **Strategy V3 (Mean Reversion):** Evaluated 15m Bollinger Band setups on SOLUSDT and BTCUSDT.
4. **Strategy V4 (Relative Strength):** Evaluated daily candles across BTC, ETH, SOL, BNB (October 2024--October 2026).
5. **Strategy V5 (VP-TF Trend Following):** Evaluated daily candles across BTC, ETH, SOL, BNB (October 2024--October 2026).

**Empirical Finding:**
- **Daily aligned return series do NOT exist for Strategies V1, V2, and V3.**
- Because V1--V3 were intraday strategies operating over non-identical timeframes and assets, it is **empirically impossible** to compute a $67 \times 67$ correlation matrix from repository data.
- Therefore, the claim that $M_{\text{eff}} \in [15, 25]$ is **not an empirical measurement**. It is a **heuristic sensitivity assumption**.

### 4.2 DSR Sensitivity Across Trial Parameter Space
Because $M_{\text{eff}}$ and $\sigma_{\text{trials}}$ cannot be uniquely calculated from empirical data, the framework must report DSR across a sensitivity grid. We evaluated Strategy V5 across plausible ranges:

| Number of Trials ($M$) | Trial Std Dev ($\sigma_{\text{trials}} = 0.30$) | Trial Std Dev ($\sigma_{\text{trials}} = 0.50$) | Trial Std Dev ($\sigma_{\text{trials}} = 1.00$) |
| :---: | :---: | :---: | :---: |
| **$M = 1$ (Single Trial)** | $\text{DSR} = 0.0913$ | $\text{DSR} = 0.0913$ | $\text{DSR} = 0.0913$ |
| **$M = 5$ (Family Baseline)** | $\text{DSR} = 0.0716$ | $\text{DSR} = 0.0601$ | $\text{DSR} = 0.0388$ |
| **$M = 25$ ($M_{\text{eff}}$ Midpoint)**| $\text{DSR} = 0.0631$ | $\text{DSR} = 0.0470$ | $\text{DSR} = 0.0233$ |
| **$M = 67$ (Total Program Trials)**| $\text{DSR} = 0.0594$ | $\text{DSR} = 0.0416$ | $\text{DSR} = 0.0177$ |
| **$M = 150$ (Conservative Bound)**| $\text{DSR} = 0.0567$ | $\text{DSR} = 0.0378$ | $\text{DSR} = 0.0142$ |

**Conclusion on Sensitivity:**
- For Strategy V5, DSR remains below $0.10$ across all parameter combinations, conclusively affirming the NO-GO verdict.
- However, for future candidate strategies with positive Sharpe ratios ($\widehat{SR} \approx 1.0\text{--}1.5$), moving from $M=1$ to $M=67$ or altering $\sigma_{\text{trials}}$ from $0.30$ to $1.00$ swings DSR from passing ($\text{DSR} > 0.95$) to failing ($\text{DSR} < 0.80$).
- **Rule for Future Research:** Future strategy evaluations must report DSR as a **sensitivity matrix** over $M \in [1, M_{\text{total}}]$ and $\sigma_{\text{trials}} \in [0.30, 0.70]$ rather than a single point estimate.

---

## 5. Walk-Forward Cross-Validation & Lookahead Leakage

Direction C.2 confirmed that fold indices in `WalkForwardCV` are mechanically non-overlapping (training indices strictly precede test indices, and test windows do not intersect).

Direction C.3 challenged whether mechanical non-overlap guarantees the absence of lookahead bias.

### 5.1 Mechanical vs. Macro Lookahead Bias
We distinguish two distinct levels of data leakage:

```
+-----------------------------------------------------------------------------------+
| LEVEL 1: Mechanical Split Leakage (RESOLVED & VERIFIED)                           |
| - Out-of-sample fold test data t+1 is never visible to in-sample training fold t. |
| - Verified by synthetic perturbation tests (perturbing t+1 does not alter t).     |
+-----------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
| LEVEL 2: Macro Research & Survivorship Leakage (UNRESOLVABLE BY SPLITTING)         |
| - Asset Selection: {BTC, ETH, SOL, BNB} was chosen in 2026 with full hindsight.   |
|   Tokens that collapsed (e.g. LUNA, FTT) were omitted from the universe.         |
| - Indicator Selection: Lookbacks (20d, 50d, ATR) were selected based on knowledge |
|   of the 2024-2026 market regime.                                                 |
| - Human Iteration: Strategies V1-V5 evolved sequentially based on prior failures. |
+-----------------------------------------------------------------------------------+
```

### 5.2 Framework Boundary
An offline simulation engine cannot prevent macro research bias or survivorship bias. It can only guarantee mechanical integrity.
- **Documented Limitation:** Passing walk-forward CV proves only that model fitting respected temporal order. It does NOT prove immunity to macro survivorship bias or human selection bias.

---

## 6. Comprehensive Issue Reconciliation Matrix

This table synthesizes every statistical, numerical, and structural concern identified across Direction C, C.1, C.2, and C.3:

| ID | Issue Description | Production Engine (`benchmark_engine.py`) | Corrected Audit Code (`audit_direction_c2/c3.py`) | Severity | Required Action Before Strategy Research |
| :---: | :--- | :--- | :--- | :---: | :--- |
| **ISS-01** | **DSR Time Scaling:** Line 774 divides annualized numerator by daily standard error, inflating $z$ by $\sqrt{365.25}$. | **DEFECTIVE** | Fixed and verified ($z = -1.6115$). | **CRITICAL** | Patch `benchmark_engine.py` line 774 to use consistent daily or annualized units. |
| **ISS-02** | **Bootstrap Centering:** Line 704 computes uncentered replicate fraction $\le 0$ rather than null-centered test. | **DEFECTIVE** | Fixed and verified ($p = 0.1800$). | **HIGH** | Patch `benchmark_engine.py` to center replicates under $H_0: \mu = 0$. |
| **ISS-03** | **Sharpe Metric Formula:** Production engine only computes compounded $((CAGR - R_f) / \sigma)$. | **INCOMPLETE** | Implements both arithmetic and compounded Sharpe. | **MEDIUM** | Add explicit `calc_arithmetic_sharpe` to `benchmark_engine.py`. |
| **ISS-04** | **$M_{\text{eff}}$ Trial Accounting:** Claim that $M_{\text{eff}} \in [15, 25]$ is treated as empirical measurement. | **UNRESOLVED** | Identified as unverified sensitivity assumption. | **MEDIUM** | Update reporting standard to mandate DSR sensitivity grids. |
| **ISS-05** | **$\sigma_{\text{trials}} = 0.50$ Assumption:** Value selected without empirical cross-sectional evidence. | **UNRESOLVED** | Identified as heuristic assumption. | **LOW** | Include $\sigma_{\text{trials}} \in [0.30, 0.70]$ in sensitivity tables. |
| **ISS-06** | **Bootstrap Return Units:** Confidence intervals multiplying daily mean by $365.25$ represent arithmetic mean. | **UNEXPLAINED** | Documented arithmetic vs compounded distinction. | **LOW** | Add docstring clarifying that CI represents arithmetic excess. |
| **ISS-07** | **Walk-Forward Macro Bias:** Mechanical non-overlap conflated with immunity to lookahead bias. | **UNRESOLVED** | Documented survivorship bias boundary in audit. | **LOW** | Include macro bias disclaimer in all future research reports. |

---

## 7. Regression Test Suite

An independent regression test suite was constructed in `tests/test_audit_direction_c3.py` to enforce mathematical rigor and verify production defects:

1. `test_production_benchmark_engine_has_dsr_scaling_defect`: Proves `benchmark_engine.py` yields $z = -31.32$ while corrected calculation yields $z = -1.6115$.
2. `test_production_benchmark_engine_has_uncentered_bootstrap`: Proves `benchmark_engine.py` outputs $p = 0.1220$ while centered bootstrap outputs $p = 0.1800$.
3. `test_adversarial_bootstrap_positive_persistence_ar1`: Verifies that block resampling ($L=10$) widens confidence intervals and increases p-value ($0.0070 \to 0.0250$) under AR(1) persistence.
4. `test_adversarial_bootstrap_matches_analytical_t_test`: Verifies that centered bootstrap closely tracks analytical Student's t-test ($p_t = 0.5352$ vs $p_{\text{boot}} = 0.5130$).
5. `test_adversarial_bootstrap_non_divisible_sample_size`: Verifies proper truncation and unbiasedness on $N=698$ with $L=10$.
6. `test_dsr_extreme_moments_and_heavy_tails`: Confirms monotonic penalty on high kurtosis and negative skewness.
7. `test_walk_forward_fold_reconstruction_and_non_overlap`: Validates that purge windows and test slices remain strictly disjoint.
8. `test_trial_count_registry_missingness`: Asserts that V1--V3 intraday trials lack aligned daily return series, verifying $M_{\text{eff}}$ as an assumption.

**Test Run Results:**
- `python -m unittest discover -s tests -p "test_audit_direction_c3.py"`: **8 tests passed in 0.68s.**
- Full repository test suite: **465 tests passed in 23.34s** with zero failures and zero errors.
- Formatting compliance: `tests/test_repo.py` passes with zero forbidden unicode characters or syntax defects.

---

## 8. Final Audit Verdict & Required Corrections

### Final Verdict: `REQUIRES CORRECTIONS`

### Rationale
Direction C.2 established valid mathematical formulas, but left them isolated in an audit script. The production engine `trader-timemachine/benchmark_engine.py` still contains severe scaling and centering defects. Authorizing future strategy research under the verdict `VERIFIED FOR RESEARCH USE` would risk other developers importing the unpatched production engine, leading to false statistical conclusions.

### Mandatory Pre-Conditions for Direction C Closure
Before any new trading strategy (Direction A, Direction B, or V6) may be evaluated:
1. **Patch `benchmark_engine.py` Line 774:** Correct the Deflated Sharpe Ratio standard error scaling so that daily and annualized units are strictly matched.
2. **Patch `benchmark_engine.py` Line 704:** Replace the uncentered percentile bootstrap with the centered null-hypothesis test ($|\bar{D}^*| \ge |\bar{D}|$).
3. **Add Arithmetic Sharpe Function:** Implement `calc_arithmetic_sharpe(daily_returns, rf)` in `benchmark_engine.py` alongside the existing CAGR-based metric.
4. **Enforce DSR Sensitivity Reporting:** Update the benchmark engine reporting module to output DSR across a grid of $(M, \sigma_{\text{trials}})$ rather than assuming a single fixed $M_{\text{eff}}$.
5. **Re-Run Full Test Suite:** Confirm that all 465+ unit and audit tests pass cleanly against the patched production file.

