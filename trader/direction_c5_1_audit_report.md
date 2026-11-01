# Direction C.5.1 -- Calibration Reconciliation and Low-Alpha Audit Report

**Repository:** `E:\projects\binance`  
**Date:** 2026-10-30  
**Status:** Independent Audit & High-Resolution Calibration Complete  
**Final Verdict:** `ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS`

---

## 1. Executive Summary & Verdict

Following the completion of Direction C.5, this independent audit resolved empirical discrepancies in serial correlation reporting, executed a dedicated high-resolution low-alpha ($\alpha = 0.01$) calibration across 10,000 independent datasets, independently verified Deflated Sharpe Ratio (DSR) mathematics, corrected semantic labeling errors, and established formal calibration restrictions for future offline research.

### Primary Audit Findings:
1. **Reconciliation of AR(1) $\phi=0.50$ Reporting Contradiction:**
   - The discrepancy between Table 4 ($19 / 250 = 7.60\%$) and Table 6 ($24 / 250 = 9.60\%$) in Direction C.5 was traced to independent simulation seed schedules:
     * Table 4 evaluated Batch 1 with `seed_base = 10000` (seeds $10001 \dots 11744$).
     * Table 6 evaluated Batch 2 with `seed_base = 81000` (seeds $81001 \dots 82744$).
   - Both results were reproduced with 100% exact numerical fidelity.
   - The two-sample difference ($\Delta = 2.0\%$) has test statistic $z = 0.7976$ ($p = 0.4251$). It is statistically indistinguishable from random binomial sampling variation between two independent batches of $K=250$.
   - The pooled estimate across all $K=500$ datasets is **$43 / 500 = 8.60\%$** (95% Wilson CI: $[0.0645, 0.1138]$).

2. **Low-Alpha Calibration Audit ($\alpha = 0.01, K=2500, B=1000$):**
   - The C.5 narrative report erred in characterizing Gaussian i.i.d. rejection ($6 / 250 = 2.40\%$, 95% Wilson CI: $[1.10\%, 5.14\%]$) as nominally calibrated. The 95% Wilson interval strictly excluded nominal $1.0\%$.
   - A dedicated high-resolution experiment ($K=2500$ independent series, $B=1000$ bootstrap replicates, $N=500$) resolved this:
     * **Gaussian i.i.d.:** Rejection rate was **$0.0088$** ($22 / 2500$, 95% Wilson CI: $[0.0058, 0.0133]$), successfully enclosing nominal $0.0100$ ($0.88\times$ nominal).
     * **GARCH(1,1):** Rejection rate was **$0.0088$** ($22 / 2500$, 95% Wilson CI: $[0.0058, 0.0133]$), enclosing nominal $0.0100$.
     * **AR(1) $\phi=0.30$:** Rejection rate was **$0.0148$** ($37 / 2500$, 95% Wilson CI: $[0.0108, 0.0203]$), strictly excluding nominal $0.0100$ ($1.48\times$ nominal).
     * **AR(1) $\phi=0.50$:** Rejection rate was **$0.0172$** ($43 / 2500$, 95% Wilson CI: $[0.0128, 0.0231]$), strictly excluding nominal $0.0100$ ($1.72\times$ nominal).
   - **Crucial Inferential Finding:** Lowering the decision threshold to $\alpha = 0.01$ does *not* cure serial correlation distortion. Strong persistence inflates false positives by $48\%$ to $72\%$ even at low alpha.

3. **DSR Interpretation and Mathematical Verification:**
   - Independent pure-math re-implementation verified all DSR calculations, Mertens standard errors, expected maximum Sharpe ratios, and dimensional scaling invariants within machine precision.
   - The C.5 report's semantic labels ("statistically significant", "marginally significant") were audited and rejected as scientifically flawed. DSR is a cumulative normal confidence probability $\Phi(z)$, not a p-value. A DSR of $0.6642$ implies a $33.6\%$ probability that random data snooping generated the result, which is statistically insignificant.

4. **Power and Confidence Interval Integrity:**
   - Power progression across effect sizes $\mu \in \{-0.0005, 0.0, +0.0002, +0.0005, +0.0010\}$ is verified to be strictly monotonic in the empirical sample ($0.0040 \to 0.0480 \to 0.1120 \to 0.3040 \to 0.7520$).
   - Bootstrap uncentered 95% confidence intervals target the population mean difference $\mu$ and achieve mean coverage of **$94.40\%$** across all scenarios.

**Final Verdict:** `ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS`  
The framework is verified for offline comparative evaluation, provided that candidate strategies subject to serial correlation ($\phi \ge 0.30$) are evaluated under mandatory calibration restrictions detailed in Section 7.

---

## 2. Audit 1: Reconciliation of AR(1) Reporting Discrepancy

### A. The Discrepancy Identified in Direction C.5
In `trader/direction_c5_calibration_report.md`, two different tables reported contradictory rejection rates for the exact same data-generating specification ($\text{AR}(1), \phi = 0.50, N = 500, B = 500, L = 10, \alpha = 0.05$):
- **Table 4 (Null Calibration):** Reported $19 / 250 = 7.60\%$ (95% Wilson CI: $[4.92\%, 11.56\%]$).
- **Table 6 (Block Sensitivity):** Reported $24 / 250 = 9.60\%$ (95% Wilson CI: $[6.54\%, 13.89\%]$).

### B. Root Cause Analysis
Inspecting `trader-timemachine/calibration_direction_c5.py` reveals that the two tables were produced by two separate simulation functions with distinct seed schedules:
1. `run_type_1_error_experiment`:
   - Evaluated Batch 1 with `seed_base = 10000`.
   - Data generator RNG: `random.Random(10000 + i * 7 + 1)` (seeds $10001, 10008, \dots, 11744$).
   - Bootstrap RNG: `random.Random(10000 + i * 13 + 3)`.
   - Result: Exactly 19 rejections out of 250 ($7.60\%$).
2. `run_block_length_sensitivity_experiment`:
   - Evaluated Batch 2 with `seed_base = 30000`, using an offset formula: `30000 + int(0.50 * 100) * 1000 + 10 * 100 = 81000`.
   - Data generator RNG: `random.Random(81000 + i * 7 + 1)` (seeds $81001, 81008, \dots, 82744$).
   - Bootstrap RNG: `random.Random(81000 + i * 13 + 3)`.
   - Result: Exactly 24 rejections out of 250 ($9.60\%$).

### C. Statistical Reconciliation Table
| Experiment Batch | Seed Schedule Base | Independent Datasets ($K$) | Rejections ($k$) | Empirical Rate $\hat{\alpha}$ | 95% Wilson CI | Re-run Exact Match |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Batch 1 (Null Table)** | `10000` | $250$ | $19$ | **$7.60\%$** | $[4.92\%, 11.56\%]$ | **Verified (19 / 250)** |
| **Batch 2 (Block Table)** | `81000` | $250$ | $24$ | **$9.60\%$** | $[6.54\%, 13.89\%]$ | **Verified (24 / 250)** |
| **Difference ($\Delta$)** | N/A | N/A | $+5$ | **$+2.00\%$** | N/A | $z = 0.7976, p = 0.4251$ |
| **Pooled Total ($K=500$)** | Combined | $500$ | $43$ | **$8.60\%$** | $[6.45\%, 11.38\%]$ | **Verified (43 / 500)** |

### D. Audit Conclusion on Discrepancy:
- The difference between $7.60\%$ and $9.60\%$ was *not* a computational error, formula discrepancy, or code divergence. It was purely binomial sampling variation between two independent sets of 250 generated time series.
- Because a two-sample test of proportions yields $z = 0.7976$ ($p = 0.4251$), the null hypothesis that both batches share the identical underlying rejection probability cannot be rejected.
- The C.5 narrative report was defective in failing to clarify that Table 4 and Table 6 drew separate batches of datasets. The true underlying rejection rate of moving-block bootstrap ($L=10$) on AR(1) $\phi=0.50$ returns is best represented by the pooled estimate: **$8.60\%$** ($43 / 500$).

---

## 3. Audit 2: High-Resolution Low-Alpha Calibration ($\alpha = 0.01$)

### A. Critique of the Direction C.5 Claim
In Direction C.5, Gaussian i.i.d. returns yielded $6 / 250 = 2.40\%$ rejections at $\alpha = 0.01$, with reported 95% Wilson interval $[1.10\%, 5.14\%]$. The report characterized this as "nominally calibrated" or "modest sample jitter".

This characterization was mathematically flawed:
1. The 95% Wilson interval $[1.10\%, 5.14\%]$ strictly excluded the nominal value $1.00\%$.
2. With only $K=250$ datasets, a binomial standard error for a 1% event is $\sqrt{0.01 \times 0.99 / 250} \approx 0.63\%$, making $K=250$ completely inadequate to test 1% calibration.
3. With $B=500$ bootstrap replicates, the $(k+1)/(B+1)$ convention imposes coarse discrete resolution steps: $(k+1)/501 \le 0.01 \iff k \le 4$. The discrete p-value jumps from $0.00998$ ($k=4$) to $0.01198$ ($k=5$), a step size equal to $20\%$ of the target threshold $\alpha = 0.01$.

### B. High-Resolution Simulation Design
To resolve this rigorously, a dedicated low-alpha calibration was executed:
- **Sample length ($N$):** $500$ daily observations.
- **Block length ($L$):** $10$ days.
- **Bootstrap resamples ($B$):** Increased to **$B = 1000$**, doubling p-value resolution: $(k+1)/1001 \le 0.01 \iff k \le 9$. Step size is refined to $0.000999$ ($10\%$ of $\alpha$).
- **Independent datasets ($K$):** Increased by $10\times$ to **$K = 2500$** per scenario ($10,000$ total datasets). Binomial standard error drops from $0.63\%$ to **$0.20\%$**.
- **Elapsed execution time:** $542.03\text{s}$ (~9.0 minutes) across the 4 scenarios.

### C. Low-Alpha Calibration Results ($\alpha = 0.01, K = 2500, B = 1000$)
| Data Generating Process | Nominal $\alpha$ | Observed Rejections ($k/2500$) | Empirical Size $\hat{\alpha}$ | 95% Wilson CI | Encloses Nominal $\alpha$? | Distortion Ratio ($\hat{\alpha} / 0.01$) | Runtime |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gaussian i.i.d.** | $0.01$ | $22$ | **$0.0088$** | **$[0.0058, 0.0133]$** | **YES (Enclosed)** | $0.88\times$ nominal | $137.0\text{s}$ |
| **GARCH(1,1) (vol clustering)** | $0.01$ | $22$ | **$0.0088$** | **$[0.0058, 0.0133]$** | **YES (Enclosed)** | $0.88\times$ nominal | $135.9\text{s}$ |
| **AR(1) $\phi=0.30$ (moderate)** | $0.01$ | $37$ | **$0.0148$** | **$[0.0108, 0.0203]$** | **NO (Excludes 0.01)** | $1.48\times$ nominal | $135.5\text{s}$ |
| **AR(1) $\phi=0.50$ (high)** | $0.01$ | $43$ | **$0.0172$** | **$[0.0128, 0.0231]$** | **NO (Excludes 0.01)** | **$1.72\times$ nominal** | $133.7\text{s}$ |

### D. Critical Findings on Low-Alpha Behavior:
1. **Gaussian & GARCH Calibration Confirmed:** When evaluated with adequate sample size ($K=2500$) and bootstrap resolution ($B=1000$), both Gaussian i.i.d. and GARCH(1,1) reject at $0.88\%$ ($22 / 2500$). The 95% Wilson interval $[0.58\%, 1.33\%]$ comfortably encloses nominal $1.00\%$. The $2.40\%$ rejection in C.5 was an artifact of small-sample Monte Carlo jitter on $K=250$.
2. **Serial Correlation Distortion Persists at Low Alpha:** AR(1) processes reject at $1.48\%$ ($\phi=0.30$) and $1.72\%$ ($\phi=0.50$). Both confidence intervals strictly exclude $1.00\%$.
3. **Rejection of the "Alpha=0.01 Fix":** Recommending $\alpha = 0.01$ does *not* eliminate size distortion caused by positive serial correlation. At $\phi=0.50$, the false positive rate is still inflated by $+72\%$ above nominal.

---

## 4. Audit 3: Deflated Sharpe Ratio (DSR) Verification and Semantic Audit

### A. Mathematical Verification against Independent Implementation
An independent numerical implementation of Bailey & Lopez de Prado (2014) DSR and Mertens (2002) asymptotic variance was written in `trader-timemachine/audit_direction_c5_1.py`.

Nine diverse parameter cases were cross-checked:
- Observed Sharpe $S \in \{-0.50, 0.00, 1.20, 1.50\}$
- Trial counts $M \in \{1, 5, 25, 67, 150\}$
- Trial dispersion $\sigma_M \in \{0.30, 1.00\}$
- Skewness $\gamma_3 \in \{-0.5, 0.0, 0.5\}$
- Excess kurtosis $\gamma_4 \in \{0.0, 3.0, 6.0\}$

**Result:** In all 9 cases, the production function `calc_deflated_sharpe_ratio` in `benchmark_engine.py` matched the independent reference implementation to within $10^{-6}$ on $z$-statistics and $10^{-4}$ on DSR values. Non-positive Sharpe ratios ($S \le 0$) returned sentinel $\text{DSR} = 0.0$ with 100% compliance.

### B. Audit of Direction C.5 Semantic Labeling Errors
The C.5 report contained improper inferential terminology that confused confidence levels with p-values:
1. **Error 1: Labeling $\text{DSR} = 0.9807$ as "Statistically significant ($p < 0.05$)".**
   - DSR is a cumulative normal probability $\Phi(z)$, where $z = (\hat{S} - E[\max S]) / SE$.
   - A p-value against the data snooping hurdle is $1 - \text{DSR} = 1 - 0.9807 = 0.0193 < 0.05$. While the conclusion happens to align, labeling $\text{DSR} = 0.98$ as a "p-value" reverses the mathematical scale.
2. **Error 2: Labeling $\text{DSR} = 0.6642$ as "Marginally significant".**
   - For $\text{DSR} = 0.6642$, $z = +0.4240$. The observed Sharpe exceeds the expected maximum of noise by barely $0.42$ standard deviations.
   - The probability that random data snooping generated a Sharpe as high or higher is $1 - \Phi(0.4240) = 0.3360$ ($33.6\%$).
   - A result with a $33.6\%$ false positive probability is statistically insignificant by every recognized scientific standard. Calling it "marginally significant" was a serious reporting error.
3. **Corrected DSR Definition:**
   - DSR is a model-based metric measuring the probability that an observed Sharpe ratio exceeds the maximum Sharpe ratio expected from $M$ trials under random noise.
   - To reject the hypothesis of selection bias at the $5\%$ level, the strategy must achieve $\text{DSR} \ge 0.95$ ($z \ge 1.645$).
   - DSR is *not* a universal p-value and does not guarantee out-of-sample profitability.

---

## 5. Audit 4: Power, Coverage, and Monotonicity Verification

### A. Empirical vs. Mathematical Monotonicity
In Direction C.5, the reported power sequence across effect sizes was:
- $\mu = -0.0005 \implies \text{Power}(>0) = 0.0040$
- $\mu = +0.0000 \implies \text{Power}(>0) = 0.0480$
- $\mu = +0.0002 \implies \text{Power}(>0) = 0.1120$
- $\mu = +0.0005 \implies \text{Power}(>0) = 0.3040$
- $\mu = +0.0010 \implies \text{Power}(>0) = 0.7520$

**Verification:**
- In the observed simulation of $K=250$ datasets, the empirical power sequence is strictly monotonically increasing ($0.0040 < 0.0480 < 0.1120 < 0.3040 < 0.7520$).
- **Methodological Distinction:** Empirical monotonicity on a finite sample ($K=250$) is an observed sample realization subject to binomial jitter. In contrast, mathematical monotonicity is a property of the asymptotic power function: for a Gaussian shift alternative, the non-centrality parameter $\delta = \mu \sqrt{N} / \sigma$ is strictly increasing in $\mu$, guaranteeing that true power increases with effect size.

### B. Target Estimand and Confidence Interval Coverage
- **Target Estimand:** The uncentered bootstrap confidence interval (`ci_95_daily_mean`) targets the true population mean daily excess return $\mu = E[D_t]$.
- Across the 5 tested effect sizes, empirical 95% CI coverage was:
  * $\mu = -0.0005 \implies 93.60\%$
  * $\mu = +0.0000 \implies 92.80\%$
  * $\mu = +0.0002 \implies 95.60\%$
  * $\mu = +0.0005 \implies 93.60\%$
  * $\mu = +0.0010 \implies 96.40\%$
- **Mean Coverage:** $94.40\%$, closely bracketing the nominal $95.00\%$ target across all effect sizes.

---

## 6. Test Suite and Workspace Integrity

- **New files created for Direction C.5.1:**
  * [`trader-timemachine/audit_direction_c5_1.py`](file:///E:/projects/binance/trader-timemachine/audit_direction_c5_1.py) (reconciliation and low-alpha harness).
  * [`tests/test_audit_direction_c5_1.py`](file:///E:/projects/binance/tests/test_audit_direction_c5_1.py) (regression unit tests).
  * [`trader/direction_c5_1_audit_results.json`](file:///E:/projects/binance/trader/direction_c5_1_audit_results.json) (structured audit records).
  * [`trader/direction_c5_1_audit_report.md`](file:///E:/projects/binance/trader/direction_c5_1_audit_report.md) (this audit report).
- **Execution of full workspace test suite:**
  `python -m unittest discover -s tests -p "test_*.py"`
  **Result:** **485 tests ran in 28.3s, all passed (`OK`).**
- **Repository constraint check:**
  `python -m unittest discover -s tests -p "test_repo.py"` passed with zero em-dashes.
- **Safety boundaries maintained:** Strategy V5 remains frozen under its `VERIFIED NO-GO` verdict. Zero broker, exchange, testnet, or live trading execution paths were enabled or modified.

---

## 7. Mandatory Calibration Restrictions for Future Research

Because empirical Type I error inflation persists under serial correlation ($8.6\%$ at nominal $5\%$ and $1.72\%$ at nominal $1\%$ for $\phi = 0.50$), the following **Mandatory Calibration Restrictions** are formally instituted for all future offline strategy research:

1. **Mandatory Autocorrelation Diagnostic (Pre-Test Gate):**
   - Before evaluating `paired_block_bootstrap_test`, the sample autocorrelation $\hat{\rho}_1 = \text{Corr}(D_t, D_{t-1})$ of daily excess returns must be computed and logged.
2. **Conservative Alpha Hurdle for Persistent Series:**
   - If $\hat{\rho}_1 \ge 0.30$, a candidate strategy *cannot* claim statistical significance at nominal $\alpha = 0.05$. It must meet a tightened threshold of **$\alpha \le 0.005$** to account for serial covariance distortion.
3. **Mandatory Block-Length Sensitivity Sweep:**
   - Any strategy claiming significance must report p-values across a sensitivity sweep: $L \in \{5, 10, 20\}$. If significance evaporates when $L$ is increased from 10 to 20, the result must be flagged as non-robust serial dependence.
4. **Mandatory Sample Length Floor:**
   - Hypotheses must not be tested on partitions shorter than **$N = 500$** return intervals. Samples shorter than 500 daily bars suffer elevated Monte Carlo dispersion in the presence of fat tails.
5. **DSR Significance Threshold and Sensitivity Reporting:**
   - DSR results must strictly require **$\text{DSR} \ge 0.95$** to claim confidence against multiple testing noise.
   - Point estimates of DSR are prohibited; results must be presented across the standard $(M, \sigma_M)$ sensitivity grid ($M \in [5, 150], \sigma_M \in [0.30, 1.00]$).

---

## 8. Final Decision

**`ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS`**

The evaluation engine is mathematically sound, reproducible, and nominally calibrated for independent, fat-tailed, and volatility-clustered returns. However, because serial correlation introduces persistent Type I error inflation ($1.5\times$ to $1.7\times$ nominal rates), the framework is approved for offline comparative research **only** under the mandatory calibration restrictions specified in Section 7.

