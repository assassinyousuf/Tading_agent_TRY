# Direction C.4.2 -- Statistical Edge-Case Hardening Report

**Repository:** `E:\projects\binance`  
**Date:** 2026-10-30  
**Status:** Verification & Hardening Complete  
**Final Verdict:** `ACCEPT WITH DOCUMENTED LIMITATIONS`

---

## 1. Executive Summary & Verdict

Following the Direction C.4.1 independent mathematical review, this task implemented defensive and inferential edge-case hardening across the Direction C statistical evaluation framework (`trader-timemachine/benchmark_engine.py`) and its regression test suite (`tests/`).

### Primary Objectives Accomplished:
1. **Finite-Sample Monte Carlo P-Value Convention:**
   - Replaced naive $k/B$ empirical ratio in `paired_block_bootstrap_test` with the standard $(k + 1) / (B + 1)$ convention (Davison & Hinkley 1997; Phipson & Smyth 2010).
   - Eliminated the reporting of literal zero p-values ($p = 0.0000$). For $B=2000$ bootstrap iterations, the minimum possible p-value is strictly bounded at $1 / (B + 1) \approx 0.0004998$ ($0.0005$).
   - Handled boundary ties consistently: test statistics equal to the observed mean count towards the null, ensuring identical series evaluate to $p = 1.0$.
   - Retained raw exceedance ratio $k / B$ (`p_value_raw`), minimum resolution bound (`min_possible_p_value`), and exceedance count (`exceedances_k`) in output dictionaries for full diagnostic transparency.
   - Maintained strict separation between null-centered hypothesis testing and uncentered percentile confidence intervals.

2. **Arithmetic Sharpe Ratio Defensive Validation:**
   - Hardened `calc_arithmetic_sharpe` with explicit input guards:
     * Validates that `annual_factor` is numeric, finite, and strictly positive (`annual_factor > 0`). Zero, negative, NaN, or infinite annualization factors safely return sentinel `0.0`.
     * Validates that `rf_annual` is numeric and finite. Non-numeric, NaN, or infinite risk-free rates safely return sentinel `0.0`.
     * Validates that `daily_returns` is a valid sequence of length $\ge 2$ containing only finite numbers. Empty, single-element, non-numeric, or NaN/inf return sequences safely return sentinel `0.0`.
     * Zero-volatility return series (sample variance $\le 10^{-12}$) safely return sentinel `0.0`.
   - Preserved default `annual_factor = 365.25` and verified exact numerical invariance on valid inputs.

3. **Regression and Integrity Gate:**
   - Ran 479 unit and regression tests across the entire workspace. All 479 tests passed cleanly (`OK`).
   - Strategy V5 remains frozen under its `VERIFIED NO-GO` verdict. Historical registries, ledgers, and audit files remain completely unmodified.
   - All live, paper, testnet, broker, and exchange execution paths remain untouched and disabled.
   - Verified compliance with repository formatting constraints (including zero em-dash policy in `tests/test_repo.py`).

**Final Recommendation:** `ACCEPT WITH DOCUMENTED LIMITATIONS`  
The statistical corrections and defensive hardening are fully verified. However, passing tests and adopting $(k+1)/(B+1)$ do not certify that moving-block bootstrap is universally calibrated across non-stationary crypto returns, nor does it resolve the fundamental unobservability of historical trial counts.

---

## 2. Exact Files and Functions Changed

### A. `trader-timemachine/benchmark_engine.py`

#### 1. Function `calc_arithmetic_sharpe`:
- **Location:** Lines 105--146.
- **Modifications:**
  * Added guard `if not isinstance(rf_annual, (int, float)) or not math.isfinite(rf_annual): return 0.0`.
  * Added guard `if not isinstance(annual_factor, (int, float)) or not math.isfinite(annual_factor) or annual_factor <= 0: return 0.0`.
  * Added guard verifying every element $r$ in `daily_returns` satisfies `isinstance(r, (int, float)) and math.isfinite(r)`.
  * Updated docstring documenting the sentinel `0.0` convention on invalid or non-computable input.

#### 2. Function `paired_block_bootstrap_test`:
- **Location:** Lines 753--888.
- **Modifications:**
  * Adopted $(k + 1) / (B + 1)$ convention for one-sided upper tail, two-sided, and one-sided lower tail p-values.
  * Counted exceedances under $H_0$:
    - Upper tail (`greater`): $k = \sum_{b=1}^B \mathbb{I}(\bar{\widetilde{D}}^*_b \ge \bar{D})$
    - Two-sided (`two-sided`): $k = \sum_{b=1}^B \mathbb{I}(|\bar{\widetilde{D}}^*_b| \ge |\bar{D}|)$
    - Lower tail (`less`): $k = \sum_{b=1}^B \mathbb{I}(\bar{\widetilde{D}}^*_b \le \bar{D})$
  * Computed finite-sample p-value: $p = (k + 1.0) / (B + 1.0)$.
  * Computed and returned `p_value_raw = k / float(B)` alongside finite p-value.
  * Added diagnostic fields to return dictionary:
    - `"min_possible_p_value": round(1.0 / float(n_boot + 1), 6)`
    - `"exceedances_k": k_exceed`
    - `"p_value_convention": "(k + 1) / (B + 1) [Davison & Hinkley 1997]"`
  * Handled sample length $< \text{block\_size}$ defensively with full dictionary fields.

### B. `tests/test_benchmark_engine.py`
- **Location:** Line 196.
- **Modifications:**
  * Updated `test_09_paired_bootstrap_determinism_and_null`: for the strictly winning synthetic case ($k=0$ exceedances out of $B=500$), updated assertion from `assertEqual(ht_win["p_value"], 0.0)` to `assertEqual(ht_win["p_value"], round(1.0 / (500 + 1), 4))` ($0.0020$), and asserted `assertEqual(ht_win["p_value_raw"], 0.0)`.

### C. `tests/test_audit_direction_c3.py`
- **Location:** Lines 89--94.
- **Modifications:**
  * Updated `test_02_production_bootstrap_uncentered_defect_exposed`: verified that `prod_boot["p_value_raw"]` matches the empirical ratio `corr_boot["p_value_centered"]` ($0.1800$), and that finite p-value `prod_boot["p_value"]` ($0.1804$) matches within delta 0.005.

### D. `tests/test_audit_direction_c4.py`
- **Location:** Lines 122--145, 330--420.
- **Modifications:**
  * Updated `test_03_bootstrap_known_positive_and_negative_effects` to assert `res_win["p_value"] == round(1.0 / (500 + 1), 4)` and `res_win["p_value_raw"] == 0.0`.
  * Added `test_12_arithmetic_sharpe_defensive_guards`: verifies that non-positive, NaN, inf, or non-numeric `annual_factor`, non-finite `rf_annual`, and invalid `daily_returns` return sentinel `0.0`, while preserving default `annual_factor=365.25` and valid calculation.
  * Added `test_13_bootstrap_finite_sample_convention_and_bounds`: verifies $(k+1)/(B+1)$ minimum and maximum bounds, exceedance counts, and diagnostic metadata.
  * Added `test_14_bootstrap_ties_and_null_like_data`: verifies tie handling on identical series, consistency across alternative tail directions, and separation of confidence intervals from p-values.

---

## 3. Bootstrap P-Value Convention and Rationale

### A. The Finite Monte Carlo Problem ($k/B$)
In standard empirical bootstrap hypothesis testing with $B$ resamples, researchers frequently compute the p-value as the simple ratio:
$$p_{\text{naive}} = \frac{k}{B} = \frac{1}{B} \sum_{b=1}^B \mathbb{I}(T_b^* \ge T_{\text{obs}})$$
When no bootstrap replicate exceeds the observed test statistic ($k = 0$), this formula outputs $p = 0.0000$.

In financial econometrics and empirical reporting, a reported p-value of $0.0000$ introduces two serious distortions:
1. **False Impossibility:** It falsely implies that the observed result has probability zero under the null hypothesis, confusing finite Monte Carlo simulation limits with infinite-sample mathematical certainty.
2. **Arbitrary Significance Threshold Violations:** In family-wise error rate (FWER) or False Discovery Rate (FDR) adjustments with tiny Bonferroni thresholds (e.g. $\alpha / M = 0.05 / 200 = 0.00025$), a reported $p = 0.0000$ from only $B = 500$ replicates would erroneously claim statistical significance, despite the simulation lacking the resolution to distinguish $p = 0.0002$ from $p = 0.0010$.

### B. The Standard $(k + 1) / (B + 1)$ Convention
To resolve this, we adopted the standard permutation and Monte Carlo testing convention established by Davison & Hinkley (1997, *Bootstrap Methods and Their Application*, Cambridge University Press, p. 148) and formalised by Phipson & Smyth (2010, *Permutation P-values Should Never Be Zero*, Statistical Applications in Genetics and Molecular Biology, 9(1)):
$$p = \frac{k + 1}{B + 1}$$

#### Theoretical Rationale:
- **Reference Set Inclusion:** Under the null hypothesis, the observed sample $T_{\text{obs}}$ is itself a valid realization from the null distribution. Treating the observed sample as one of the $B + 1$ members of the reference set means it always satisfies $T_{\text{obs}} \ge T_{\text{obs}}$ (a tie).
- **Exact Finite-Sample Resolution Lower Bound:** When zero simulated null replicates exceed the observed statistic ($k = 0$), the p-value is strictly bounded:
  $$p_{\min} = \frac{1}{B + 1} > 0$$
  For $B = 2000$, $p_{\min} = \frac{1}{2001} \approx 0.0004998 \approx 0.0005$.
- **Conservative and Uniform Under $H_0$:** Under a continuous null distribution, $(k+1)/(B+1)$ guarantees that $P(P \le \alpha) \le \alpha$ for any significance level $\alpha$, preventing anti-conservative type I error inflation.

### C. Boundary Ties and Tails
- **Null Boundary Ties:** When evaluating identical return series ($D_t = 0.0$), the observed mean is $\bar{D} = 0.0$, and every null-centered replicate has mean $\bar{\widetilde{D}}^* = 0.0$. Because the hypothesis test specification is $H_0: \mu \le 0$ vs $H_1: \mu > 0$, the condition $\bar{\widetilde{D}}^* \ge \bar{D}$ ($0.0 \ge 0.0$) is satisfied for all $B$ replicates ($k = B$). Thus:
  $$p = \frac{B + 1}{B + 1} = 1.0$$
  This correctly rejects the claim of positive excess return.
- **Transparency:** Both `p_value` ($(k+1)/(B+1)$) and `p_value_raw` ($k/B$) are reported in the output dictionary alongside `min_possible_p_value` and `exceedances_k`.

---

## 4. Arithmetic Sharpe Validation and Error Handling

### A. API Contract and Sentinel Convention
In `benchmark_engine.py`, existing performance functions (`calc_cagr`, `calc_max_drawdown`, `calc_sortino_ratio`, `calc_calmar_ratio`) adhere to a sentinel convention: when an input is invalid, non-numeric, or non-computable, they return sentinel `0.0` rather than raising uncaught runtime exceptions. This design allows automated portfolio sweeps across multi-asset catalogs to handle missing or truncated series without crashing.

`calc_arithmetic_sharpe` was hardened to follow this identical sentinel pattern.

### B. Validation Rules Implemented
| Parameter / Field | Valid Input Range | Invalid Condition | Action Taken |
| :--- | :--- | :--- | :--- |
| `annual_factor` | Finite float $> 0$ (default: `365.25`) | $\le 0$, NaN, $+\infty$, $-\infty$, non-numeric | Returns sentinel `0.0` |
| `rf_annual` | Finite float (default: `0.0`) | NaN, $+\infty$, $-\infty$, non-numeric | Returns sentinel `0.0` |
| `daily_returns` | Sequence of floats, length $\ge 2$ | `None`, empty, length $< 2$, non-sequence | Returns sentinel `0.0` |
| Return elements | Finite floats | NaN, $+\infty$, $-\infty$, non-numeric | Returns sentinel `0.0` |
| Sample variance | Sample variance $> 10^{-12}$ | Variance $\le 10^{-12}$ (zero volatility) | Returns sentinel `0.0` |

### C. Numerical Equivalence on Valid Data
For any valid return series and finite parameters, the mathematical calculation remains identical:
$$\bar{E} = \frac{1}{N}\sum_{t=1}^N \left(R_t - \frac{R_{f,\text{ann}}}{\text{annual\_factor}}\right)$$
$$s_E = \sqrt{\frac{1}{N-1}\sum_{t=1}^N (E_t - \bar{E})^2}$$
$$\text{Sharpe}_{\text{arithmetic}} = \frac{\bar{E}}{s_E} \times \sqrt{\text{annual\_factor}}$$

Tests confirm that for `[0.01, 0.02, 0.03]` with `annual_factor=365.25` and `rf_annual=0.0`, the arithmetic Sharpe is exactly $38.2230$, preserved without regression.

---

## 5. Test Commands and Execution Results

All verification commands were executed in the `E:\projects\binance` workspace using Python 3.14.0.

### 1. Focused Benchmark Engine Tests
```powershell
python -m unittest discover -s tests -p "test_benchmark_engine.py"
```
**Result:**
```
............
----------------------------------------------------------------------
Ran 12 tests in 0.057s

OK
```

### 2. Direction C.3 Adversarial Regression Tests
```powershell
python -m unittest discover -s tests -p "test_audit_direction_c3.py"
```
**Result:**
```
........
----------------------------------------------------------------------
Ran 8 tests in 0.657s

OK
```

### 3. Direction C.4 and C.4.2 Production Correction & Hardening Tests
```powershell
python -m unittest discover -s tests -p "test_audit_direction_c4.py"
```
**Result:**
```
..............
----------------------------------------------------------------------
Ran 14 tests in 2.004s

OK
```

### 4. Repository Style and Constraint Checks
```powershell
python -m unittest discover -s tests -p "test_repo.py"
```
**Result:**
```
...........
----------------------------------------------------------------------
Ran 11 tests in 0.240s

OK
```

### 5. Full Workspace Regression Suite
```powershell
python -m unittest discover -s tests -p "test_*.py"
```
**Result:**
```
Ran 479 tests in 23.856s

OK
```
All 479 tests across the entire repository passed with zero failures and zero errors.

---

## 6. Known Statistical Limitations

Implementing defensive input guards and adopting the $(k+1)/(B+1)$ p-value convention resolves specific implementation vulnerabilities, but fundamental econometric limitations remain inherent in offline crypto strategy evaluation:

1. **Finite Bootstrap Resolution vs. Multiple Testing:**
   - Under $B = 2000$, the minimum achievable p-value is $1 / 2001 \approx 0.0005$.
   - If an exploratory research program tests $M = 500$ configurations, a strict Bonferroni-corrected significance threshold is $\alpha / M = 0.05 / 500 = 0.0001$.
   - A finite bootstrap with $B = 2000$ cannot reach $p < 0.0001$. Evaluating extreme tail significance requires dramatically larger $B$ ($B \ge 50,000$) or analytical extreme value approximations.

2. **Block Length Sensitivity in Moving-Block Bootstrap:**
   - The moving-block bootstrap uses a fixed block length ($L = 10$).
   - If return series exhibit long-memory dependence, volatility clustering (GARCH effects), or varying autocorrelation decay rates, a single fixed block length cannot simultaneously minimize bias and variance across all assets.
   - While $L = 10$ is more conservative than naive i.i.d. resampling ($L = 1$), it does not guarantee exact test size in the presence of regime shifts or structural breaks.

3. **Unobservability of Historical Trial Counts ($M$) and Dispersion ($\sigma_M$):**
   - The Deflated Sharpe Ratio (DSR) depends critically on the number of trials $M$ and the variance of trial Sharpe ratios $\sigma_M^2$.
   - In historical research cycles (V1--V5), informal exploratory runs, discarded code branches, and manual parameter tweaks were not exhaustively logged to a single machine-readable ledger.
   - Consequently, $M$ and $\sigma_M$ remain heuristic assumptions. While `calc_dsr_sensitivity_grid` provides a transparent sensitivity matrix across multiple $(M, \sigma_M)$ pairs, it cannot magically reconstruct unrecorded researcher degrees of freedom.

4. **Non-Stationarity and Regime Vulnerability:**
   - Bootstrapping assumes that the underlying historical return generating process is stationary.
   - Crypto markets undergo severe regime transitions (e.g. high-volatility speculative bull runs, prolonged low-volatility bear accumulation, sudden exchange liquidity shocks). A strategy appearing statistically significant over an aggregated sample may fail completely when tested conditionally across individual regimes.

---

## 7. Explicit Confirmation: What Was and Was Not Verified

### What Was Independently Verified:
- **Mathematical Formulations:** The $(k+1)/(B+1)$ formula, exceedance counts, two-sided and one-sided tail inequalities, and minimum p-value bounds were derived and numerically verified.
- **Defensive Error Handling:** Every branch of `calc_arithmetic_sharpe` for non-positive annual factors, non-finite risk-free rates, invalid return sequences, and zero-volatility inputs was tested and verified to return `0.0`.
- **Reproducibility:** Seed determinism and invariance under random state changes were verified across multiple independent test runs.
- **Regression Isolation:** Strategy V5 remains frozen with verdict `VERIFIED NO-GO`. All broker execution code remains disabled.

### What Was NOT Verified (and Must Not Be Claimed):
- **Universal Bootstrap Calibration:** Passing unit tests does *not* prove that moving-block bootstrap achieves exact nominal test size (e.g. 5% false positive rate) across all real-world crypto assets and timeframes.
- **Alpha Existence:** Nothing in this audit demonstrates that any profitable trading strategy exists or that future market inefficiencies can be exploited.
- **Sufficiency of Daily Data:** The framework relies on daily close-to-close returns. Intraday slippage, path-dependent stop executions, order book depth, and liquidity exhaustion cannot be evaluated from daily OHLCV bars alone.

---

## 8. Summary of Hardened Functions

| Function Name | Input Guards Added | Inferential Change | Status |
| :--- | :--- | :--- | :--- |
| `calc_arithmetic_sharpe` | Validates `annual_factor > 0`, finite `rf_annual`, valid shape/elements; returns `0.0`. | None (formula unchanged; guards only). | **Hardened & Verified** |
| `paired_block_bootstrap_test` | Validates $N \ge L$, $L \ge 1$; returns safe dictionary with `min_possible_p_value`. | Adopts $(k+1)/(B+1)$ convention; retains raw ratio $k/B$; handles ties. | **Hardened & Verified** |
| `calc_deflated_sharpe_ratio` | Verified daily/annualized consistency and sensitivity grid. | Retained from C.4 production correction. | **Verified** |
| `calc_minimum_detectable_effect` | Verified analytical formula. | Retained from C.4 production correction. | **Verified** |

---

## 9. Final Decision

**`ACCEPT WITH DOCUMENTED LIMITATIONS`**

The Direction C.4.2 statistical edge-case hardening successfully resolves finite Monte Carlo p-value reporting risks and defensive validation gaps. The offline evaluation engine is suitable for rigorous exploratory benchmarking, provided all documented caveats regarding finite bootstrap resolution, block length selection, and heuristic trial accounting are respected.

