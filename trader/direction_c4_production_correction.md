# Direction C.4 -- Production Statistical Corrections and Regression Gate

**Audit Date:** 2026-10-29  
**Repository:** `E:\projects\binance`  
**Auditor:** Quantitative Audit & Verification Engine  
**Scope:** Direct production corrections to `trader-timemachine/benchmark_engine.py`, regression test coverage, and full test suite verification.  
**Execution Safety:** Research infrastructure only. Zero live trading, zero testnet orders, zero paper execution, zero broker/exchange connectivity, zero new strategy research.  
**Final Verdict:** `CORRECTIONS VERIFIED -- RESEARCH FRAMEWORK ONLY`

---

## Executive Summary

Direction C.3 identified that while previous audit scripts proved the mathematics of the Deflated Sharpe Ratio (DSR) and centered bootstrap, the production engine `trader-timemachine/benchmark_engine.py` was left unpatched.

In Direction C.4, we completed all direct production-path corrections within `benchmark_engine.py`, added dedicated unit and regression test coverage under `tests/test_audit_direction_c4.py`, and verified the entire workspace test suite.

### Key Production Outcomes
1. **DSR Dimensional Scaling Fixed (`calc_deflated_sharpe_ratio`):**
   The standard error scaling now strictly matches the numerator units. For inputs ($SR=1.20, N=698, M=67, \sigma_{\text{trials}}=1.0$), the production function now outputs $z = -1.6372$ and $\text{DSR} = 0.0508$ (matching the audit derivation), rather than collapsing to $z = -31.32$ and $\text{DSR} = 0.0000$.
2. **Moving-Block Bootstrap Hypothesis Test Specified & Centered (`paired_block_bootstrap_test`):**
   Resampling is now performed on the null-centered distribution $\widetilde{D}_t = D_t - \bar{D}$ under $H_0: \mu \le 0$, yielding a valid finite-bootstrap p-value ($p = 0.1800$ on benchmark noise rather than uncentered $p = 0.1220$). Random state isolation via `random.Random(seed)` prevents global state pollution.
3. **Conventional Arithmetic Sharpe Implemented (`calc_arithmetic_sharpe`):**
   Added an explicit arithmetic Sharpe ratio metric based on daily excess returns and sample standard deviation ($N-1$ degrees of freedom), scaled by $\sqrt{365.25}$, kept strictly distinct from CAGR-based compounded Sharpe.
4. **DSR Sensitivity Grid Reporting Added (`calc_dsr_sensitivity_grid`):**
   Production catalog reporting now evaluates DSR across a structured grid ($M \in [1, 150]$, $\sigma_{\text{trials}} \in [0.30, 1.00]$) with explicit documentation warnings that historical V1--V3 aligned daily return series do not exist, and that $M_{\text{eff}}$ and $\sigma_{\text{trials}}$ are heuristic assumptions.
5. **Full Test Suite Gate Passed:**
   476 out of 476 tests passed in 29.17 seconds with zero failures and zero errors.

---

## 1. Task 1: Production DSR Implementation Corrections

### 1.1 Mathematical Derivation & Dimensional Consistency
In Bailey & Lopez de Prado (2014), the Deflated Sharpe Ratio tests whether an observed Sharpe ratio $\widehat{SR}$ statistically exceeds the expected maximum Sharpe ratio $SR^*$ under $M$ multiple tests:

$$z = \frac{\widehat{SR} - SR^*}{\text{se}(\widehat{SR})}$$

Where the Mertens (2002) asymptotic variance for a per-period (daily) Sharpe ratio estimated over $N$ observations is:

$$\text{Var}(\widehat{SR}_{\text{daily}}) = \frac{1 - \gamma_3 \widehat{SR}_{\text{daily}} + \frac{\kappa + 2}{4} \widehat{SR}_{\text{daily}}^2}{N - 1}$$

Here $\gamma_3$ is skewness, and $\kappa = \gamma_4 - 3$ is excess kurtosis (normal = 0.0).

**The Legacy Production Defect:**
In the unpatched `benchmark_engine.py` (Line 774):
```python
# DEFECTIVE LEGACY PRODUCTION CODE:
sr_var = (1.0 - skewness * observed_sharpe + ((kurtosis + 2.0) / 4.0) * (observed_sharpe ** 2)) / (n_obs - 1)
sr_std = math.sqrt(sr_var)
z_stat = (observed_sharpe - expected_max_sr) / sr_std
```
When `observed_sharpe` was passed in annualized units (e.g. 1.20):
- The numerator `observed_sharpe - expected_max_sr` was in annualized units ($\approx -1.1863$).
- But `sr_std` was computed using daily formula without multiplying by $\sqrt{365.25}$, yielding $\text{se}_{\text{daily}} \approx 0.0379$.
- Dividing an annualized difference by a daily standard error inflated the test statistic $z$ by a factor of $\sqrt{365.25} \approx 19.11$:
  $$z_{\text{legacy}} = \frac{-1.1863}{0.0379} = -31.32 \implies \text{DSR} = 0.0000$$

**The Corrected Production Implementation:**
The production function `calc_deflated_sharpe_ratio` in `benchmark_engine.py` now enforces consistent dimensional scaling:
- Daily units:
  $$z = \frac{\widehat{SR}_{\text{daily}} - SR^*_{\text{daily}}}{\text{se}(\widehat{SR}_{\text{daily}})}$$
- Annualized units:
  $$\text{se}(\widehat{SR}_{\text{ann}}) = \sqrt{365.25} \cdot \text{se}(\widehat{SR}_{\text{daily}})$$
  $$z = \frac{\widehat{SR}_{\text{ann}} - SR^*_{\text{ann}}}{\text{se}(\widehat{SR}_{\text{ann}})} = \frac{\sqrt{365.25}(\widehat{SR}_{\text{daily}} - SR^*_{\text{daily}})}{\sqrt{365.25} \cdot \text{se}(\widehat{SR}_{\text{daily}})} = z_{\text{daily}}$$

### 1.2 Daily vs. Annual Equivalence Verification
We verified that `calc_deflated_sharpe_ratio` yields mathematically identical results whether inputs are passed annualized or daily:
- Annualized input: `calc_deflated_sharpe_ratio(observed_sharpe=1.20, n_obs=698, n_trials=67, trial_sharpe_std=1.0, is_annualized=True)`:
  - $z = -1.6372$, $\text{DSR} = 0.0508$
- Daily input: `calc_deflated_sharpe_ratio(observed_sharpe=1.20/sqrt(365.25), n_obs=698, n_trials=67, trial_sharpe_std=1.0/sqrt(365.25), is_annualized=False)`:
  - $z = -1.6372$, $\text{DSR} = 0.0508$
- Difference: $|z_{\text{ann}} - z_{\text{daily}}| < 10^{-12}$ (machine precision).

### 1.3 Edge Cases & Defensive Guards
The production function now includes comprehensive guards:
1. `observed_sharpe <= 0`: Returns `0.0`.
2. `n_obs <= 10`: Returns `0.0`.
3. Non-finite inputs (`NaN`, `inf` in Sharpe, skewness, kurtosis, or benchmark Sharpe): Returns `0.0`.
4. Zero volatility / zero standard error ($\text{Var} \le 10^{-12}$): Returns `0.0`.
5. Single trial ($M=1$): Evaluates against `benchmark_sharpe` ($SR^* = 0.0$).
6. Extreme moments: Verified with $\gamma_3 = -2.0$ and $\kappa = 20.0$; correctly expands $\text{se}_{\text{ann}}$ from $0.7246$ to $1.815$, monotonically lowering DSR.

---

## 2. Task 2: Production Bootstrap Hypothesis Test Corrections

### 2.1 Formal Statistical Specification
Before modifying `paired_block_bootstrap_test`, the hypothesis test was explicitly specified:

| Property | Specification |
| :--- | :--- |
| **Null Hypothesis ($H_0$)** | $H_0: \mu(D_t) \le 0$ (The strategy generates zero or negative mean excess return over benchmark after friction). |
| **Alternative Hypothesis ($H_1$)**| $H_1: \mu(D_t) > 0$ (The strategy generates true positive excess return after friction). |
| **Tail Direction** | One-sided upper tail (default for trading alpha tests). Optional `'two-sided'` ($H_0: \mu = 0$) and `'less'` ($H_0: \mu \ge 0$) supported. |
| **Paired Excess Series** | $D_t = R_{\text{strat},t} - R_{\text{bench},t}$ for aligned daily return periods. |
| **Null Distribution Centering** | $\widetilde{D}_t = D_t - \bar{D}$. Enforces $\sum \widetilde{D}_t = 0.0$ under the null hypothesis (Hall & Wilson, 1991). |
| **Resampling Mechanics** | Moving overlapping blocks of length $L$ drawn uniformly with replacement: $K = \lceil N / L \rceil$ blocks concatenated and truncated to length $N$. |
| **Random State Isolation** | Dedicated `random.Random(seed)` instance; zero mutation of Python's global random state. |
| **Finite-Bootstrap P-Value** | $p = \frac{1}{B} \sum_{b=1}^B \mathbb{I}(\bar{\widetilde{D}}^{*(b)} \ge \bar{D})$. |
| **Confidence Intervals** | Formed from uncentered bootstrap replicate means $\bar{D}^{*(b)}$ at percentiles $[0.025, 0.975]$. |

### 2.2 Verification Against Analytical Reference & Stress Scenarios
1. **Deterministic Extreme Cases:**
   - Strictly positive outperformance ($D_t = +0.40\%$ daily): $p = 0.0000$.
   - Strictly negative underperformance ($D_t = -0.40\%$ daily): $p = 1.0000$.
2. **Analytical Student's t-test Agreement:**
   On independent Gaussian return series ($N=700$):
   - Analytical paired t-test: $p_t = 0.5352$
   - Null-centered moving-block bootstrap ($L=1$): $p_{\text{boot}} = 0.5130$
   - Absolute divergence: $< 0.023$ (well within standard Monte Carlo error $\approx 1/\sqrt{2000} \approx 0.022$).
3. **Autoregressive Persistence (AR(1)):**
   On serially correlated returns ($\phi = +0.50$):
   - Naive i.i.d. bootstrap ($L=1$): underestimates variance ($p = 0.0070$, spurious significance).
   - Moving block bootstrap ($L=10$): inflates variance by $\approx \frac{1+\phi}{1-\phi} = 3.0$ and yields $p = 0.0250$, properly preventing false alpha discoveries.
4. **Non-Divisible Sample Length:**
   Tested $N=698$ with $L=10$:
   - Required blocks: $K = \lceil 698 / 10 \rceil = 70$ blocks (700 elements drawn).
   - Truncated cleanly to 698 observations without bias or indexing errors.
5. **Multiple Block Lengths:**
   Evaluated $L \in \{5, 10, 20\}$; all produce valid, properly scaled confidence intervals.

---

## 3. Task 3: Conventional Arithmetic Sharpe Ratio

### 3.1 Implementation in Production Engine
Implemented `calc_arithmetic_sharpe(daily_returns, rf_annual=0.0, annual_factor=365.25)` in Section 1 of `benchmark_engine.py`:

```python
def calc_arithmetic_sharpe(daily_returns, rf_annual=0.0, annual_factor=365.25):
    """
    Conventional Arithmetic Sharpe Ratio:
    Arithmetic mean excess daily return divided by daily sample standard deviation (N-1 dof),
    annualized by multiplying by sqrt(annual_factor).
    """
    if not daily_returns or len(daily_returns) < 2:
        return 0.0
    for r in daily_returns:
        if not math.isfinite(r):
            return 0.0
    rf_daily = rf_annual / float(annual_factor)
    excess_rets = [r - rf_daily for r in daily_returns]
    n = len(excess_rets)
    mean_excess = sum(excess_rets) / float(n)
    var = sum((r - mean_excess) ** 2 for r in excess_rets) / float(n - 1)
    if var <= 1e-12:
        return 0.0
    return (mean_excess / math.sqrt(var)) * math.sqrt(annual_factor)
```

### 3.2 Hand-Calculated Verification Cases
- **Case 1 ($r_f = 0.0$):**
  - Daily returns: $[0.01, 0.02, 0.03]$ ($N=3$)
  - Mean: $0.02$, Sample variance: $0.0001$, Sample std: $0.01$
  - Daily Sharpe: $0.02 / 0.01 = 2.0$
  - Annualized arithmetic Sharpe: $2.0 \times \sqrt{365.25} = 38.2230297$
  - Verified exact match: `abs(actual - expected) < 1e-9`.
- **Case 2 Nonzero Risk-Free Rate ($r_{f,\text{ann}} = 3.6525\%$):**
  - Daily $r_f = 0.036525 / 365.25 = 0.0001$
  - Excess returns: $[0.0099, 0.0199, 0.0299]$
  - Mean excess: $0.0199$, Sample std: $0.01$
  - Daily Sharpe: $1.99$
  - Annualized arithmetic Sharpe: $1.99 \times \sqrt{365.25} = 38.0319146$
  - Verified exact match: `abs(actual - expected) < 1e-9`.
- **Case 3 Distinction from Compounded Sharpe:**
  - Tested on multi-period volatile returns ($[+5\%, -4\%, +6\%, -5\%]$).
  - Confirmed `calc_sharpe_ratio` (CAGR-based) and `calc_arithmetic_sharpe` evaluate distinct mathematical quantities and do not collide.

---

## 4. Task 4: Explicit DSR Sensitivity Reporting

### 4.1 Production Sensitivity Grid (`calc_dsr_sensitivity_grid`)
Implemented `calc_dsr_sensitivity_grid` in `benchmark_engine.py` and integrated it directly into `run_benchmark_engine()` and `trader/direction_c_benchmark_catalog.json`.

The grid evaluates DSR across all combinations of:
- Trial counts: $M \in \{1, 5, 15, 25, 67, 150\}$
- Trial Sharpe dispersion: $\sigma_{\text{trials}} \in \{0.30, 0.50, 0.70, 1.00\}$

### 4.2 Tabular Sensitivity Output for Candidate Strategy ($\widehat{SR}_{\text{ann}} = 1.20, N=698$)

| Trial Count ($M$) | $\sigma_{\text{trials}} = 0.30$ | $\sigma_{\text{trials}} = 0.50$ | $\sigma_{\text{trials}} = 0.70$ | $\sigma_{\text{trials}} = 1.00$ |
| :---: | :---: | :---: | :---: | :---: |
| **$M = 1$ (Single Trial)** | $\text{DSR} = 0.9511$ | $\text{DSR} = 0.9511$ | $\text{DSR} = 0.9511$ | $\text{DSR} = 0.9511$ |
| **$M = 5$ (Family Baseline)** | $\text{DSR} = 0.8774$ | $\text{DSR} = 0.7976$ | $\text{DSR} = 0.6929$ | $\text{DSR} = 0.5041$ |
| **$M = 25$ ($M_{\text{eff}}$ Midpoint)** | $\text{DSR} = 0.7965$ | $\text{DSR} = 0.6095$ | $\text{DSR} = 0.3923$ | $\text{DSR} = 0.1356$ |
| **$M = 67$ (Recovered Program Trials)** | $\text{DSR} = 0.7480$ | $\text{DSR} = 0.5038$ | $\text{DSR} = 0.2581$ | $\text{DSR} = 0.0508$ |
| **$M = 150$ (Project Bound)** | $\text{DSR} = 0.7090$ | $\text{DSR} = 0.4261$ | $\text{DSR} = 0.1779$ | $\text{DSR} = 0.0212$ |

### 4.3 Mandatory Documentation Warnings
The catalog and function docstrings now record explicit caveats:
> [!WARNING]
> 1. **Data Availability Reality:** Historical effective trial count ($M_{\text{eff}}$) cannot be empirically reconstructed because aligned daily return series for Strategies V1--V3 do not exist in the repository (V1--V3 evaluated intraday 15m/1h bars on non-identical horizons).
> 2. **Assumptions vs. Facts:** Values such as $M_{\text{eff}} \in [15, 25]$ and $\sigma_{\text{trials}} = 0.50$ are heuristic sensitivity assumptions, not empirical measurements.
> 3. **Selection Bias Boundary:** Reporting across a sensitivity grid does not eliminate macro research-selection bias or survivorship bias in universe construction.

---

## 5. Task 5: Regression & Integration Test Execution

### 5.1 Test Suites Executed

| Test Command | Module / File | Tests | Failures | Errors | Result |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `python -m unittest discover -s tests -p "test_benchmark_engine.py"` | Benchmark Suite & Splitter | 12 | 0 | 0 | **PASS (0.035s)** |
| `python -m unittest discover -s tests -p "test_audit_direction_c3.py"` | C.3 Adversarial Validation | 8 | 0 | 0 | **PASS (0.594s)** |
| `python -m unittest discover -s tests -p "test_audit_direction_c4.py"` | C.4 Production Corrections | 11 | 0 | 0 | **PASS (2.081s)** |
| `python -m unittest discover -s tests -p "test_repo.py"` | Repo Formatting & No Em-Dash | 11 | 0 | 0 | **PASS (4.426s)** |
| `python -m unittest discover -s tests -p "test_*.py"` | **Full Workspace Test Suite** | **476** | **0** | **0** | **PASS (29.170s)** |

- Total tests executed: **476**
- Failures: **0**
- Errors: **0**
- Skipped: **0**
- Execution time: **29.170 seconds**

### 5.2 Before vs. After Production Function Outputs

| Function / Input Case | Pre-Correction Legacy Output | Post-Correction Production Output | Impact / Verdict |
| :--- | :--- | :--- | :--- |
| **`calc_deflated_sharpe_ratio(1.20, 698, 67, 1.0)`** | $z = -31.32$, $\text{DSR} = 0.0000$ | $z = -1.6372$, $\text{DSR} = 0.0508$ | Scaling defect resolved; units dimensionally invariant. |
| **`paired_block_bootstrap_test(s_noisy, b_noisy)`** | $p = 0.1220$ (uncentered replicate fraction $\le 0$) | $p = 0.1800$ (null-centered $P(\bar{\widetilde{D}}^* \ge \bar{D})$) | Hypothesis test correctly specified under $H_0: \mu \le 0$. |
| **`calc_arithmetic_sharpe([0.01, 0.02, 0.03])`** | *Function did not exist* | $SR_{\text{ann}} = 38.2230$ | Verified against analytical hand calculation. |
| **`calc_dsr_sensitivity_grid(1.20, 698)`** | *Function did not exist* | 20-cell grid across $M \in [1, 150]$ and $\sigma \in [0.30, 1.00]$ | Full sensitivity reporting integrated into catalog. |

---

## 6. Task 6: Final Source & Git Audit

### 6.1 Git Status & File Inventory
- **Modified Tracked Production File:**
  - `trader-timemachine/benchmark_engine.py`: Core production file containing the verified mathematical corrections.
- **Created Audit & Reconciliation Files:**
  - `tests/test_audit_direction_c4.py`: 11 dedicated regression tests for C.4 production corrections.
  - `trader/direction_c4_production_correction.md`: This comprehensive audit report.
  - `trader/direction_c4_reconciliation.json`: Machine-readable reconciliation artifact.
  - `tests/test_audit_direction_c3.py`: Updated to assert both legacy defect demonstration and post-correction production behavior.
  - `trader/direction_c_benchmark_catalog.json`: Updated via `run_benchmark_engine()` with DSR sensitivity grid.
- **Preserved Historical Artifacts:**
  - All Direction C, C.1, C.2, and C.3 reports and reconciliation JSON files remain completely intact and unrewritten.
  - Strategy V5 files and results remain frozen as `VERIFIED NO-GO`.

### 6.2 Safety Invariants Re-Verification
- `binance_broker.py`, exchange connectivity, and order execution code were **NOT modified**.
- Zero live trading, zero testnet orders, zero paper execution, zero broker requests.
- No strategy optimization or parameter tuning was conducted.
- Python standard library only was used (`math`, `random`, `json`, `os`, `datetime`).

---

## 7. Final Verdict

### Final Verdict: `CORRECTIONS VERIFIED -- RESEARCH FRAMEWORK ONLY`

### Conditions for Authorized Research Use
1. The Direction C benchmark and statistical framework is now mathematically verified and structurally sound for evaluating future offline quantitative strategies.
2. Strategy V5 remains frozen under its existing verdict: `VERIFIED NO-GO`.
3. New strategy research (Direction A, Direction B, or V6) must strictly evaluate candidate strategies using the corrected `benchmark_engine.py` functions:
   - Must report paired null-centered bootstrap p-values against both Cash and BTC benchmarks.
   - Must report conventional arithmetic Sharpe ratios alongside CAGR metrics.
   - Must report Deflated Sharpe Ratios as a sensitivity matrix across $(M, \sigma_{\text{trials}})$ rather than relying on point assumptions.
4. Live, testnet, paper, and broker execution paths remain strictly disabled.

