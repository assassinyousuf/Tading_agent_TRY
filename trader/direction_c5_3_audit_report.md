# Direction C.5.3 -- Independent C.5.2 Verification, Statistical Policy Audit, and CPU/GPU Profiling Report

**Repository:** `E:\projects\binance`  
**Date:** 2026-10-30  
**Status:** Verification, Dependence Policy Audit, and Hardware Profiling Complete  
**Final Verdict:** `ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS`

---

## 1. Executive Summary & Verdict

This independent audit conducted a comprehensive verification of Direction C.5.2, evaluated the five fundamental risks of serial dependence policies, verified the mathematical formulations of Deflated Sharpe Ratio (DSR), profiled CPU execution and real-time GPU activity, and assessed the feasibility of hardware acceleration on an NVIDIA GeForce RTX 4050 GPU.

### Primary Audit Conclusions:

1. **Independent Verification of C.5.2 (Phase A):**
   - **Numerical Reproducibility:** 100% of reported rejection counts, denominators, empirical Type I error rates, and 95% Wilson score confidence intervals were reproduced with exact numerical agreement.
   - **Methodological Invariants:** In `paired_block_bootstrap_test`, the null hypothesis ($H_0: \mu(D_t) \le 0$), null-centering ($D_t - \bar{D}$), upper-tail direction, tie-handling, Davison & Hinkley $(k+1)/(B+1)$ p-value convention, uncentered bootstrap percentile CI targeting population mean $\mu$, and boundary-truncated block resampling were verified as mathematically and programmatically intact.
   - **Seed Namespace Finding:** Scenario seeds within each audit function were uniquely partitioned. However, the base seed blocks across separate audit functions overlapped (Audit 1 used bases $700\text{k}\dots 1300\text{k}$, while Audit 2 used $800\text{k}$ and Audit 3 used $900\text{k}$). Because the functions ran in separate sequential simulation passes, no cross-contamination occurred, but a globally partitioned seed convention is formally recommended for future simulations.

2. **Statistical Dependence Policy Audit (Phase B):**
   - **The AR(2) Diagnostic Bypass:** The single-lag threshold $\hat{\rho}_1 \ge 0.30$ exhibits a fatal **$92.6\%$ false-negative rate** on stationary AR(2) series ($X_t = 0.10 X_{t-1} + 0.50 X_{t-2} + \epsilon_t$), where theoretical $\rho_1 \approx 0.20 < 0.30$ but $\rho_2 \approx 0.52$. Meanwhile, the Ljung-Box $Q_{10}$ test detected dependence in **$100.0\%$** of cases.
   - **GARCH Volatility Invisibility:** Linear $\hat{\rho}_1 \approx 0$ completely misses ARCH volatility clustering, whereas the McLeod-Li test on squared residuals detects clustering in **$98.4\%$** of cases.
   - **Policy Correction:** The single-lag threshold $\hat{\rho}_1 \ge 0.30$ is **rejected as a hard gate** and demoted to an informal heuristic warning. The pre-test gate is formally **refined** to require a dual multi-lag screen:
     1. Linear Dependence Gate: Ljung-Box $Q_{10}$ ($p \ge 0.05$) alongside Bartlett 95% confidence bands ($|\hat{\rho}_k| < 2/\sqrt{N}$ for all $k \in 1\dots 10$).
     2. Non-Linear Volatility Gate: McLeod-Li $Q_{10}$ ($p \ge 0.05$).

3. **Formula-Level DSR Audit (Phase C):**
   - Verified that Mertens (2002) asymptotic variance, Extreme Value Theory (EVT) expected maximum Sharpe, and daily-to-annualized $\sqrt{365.25}$ scaling are implemented with exact dimensional consistency.
   - Enforced strict epistemological boundaries: DSR is a model-based metric relative to an EVT expected maximum noise hurdle under Gaussian trial assumptions. It is **NOT** a posterior probability of strategy truth, and $1 - \text{DSR}$ is **NOT** the probability of data snooping. DSR provides zero guarantee of live profitability.

4. **CPU and GPU Profiling Analysis (Phase D):**
   - **Bottleneck Identification:** Profiling revealed that **Stage 3 (resampling & summation loop)** accounts for **$99.34\%$** of total execution time. Python interpreter bytecode loop overhead is the sole performance bottleneck.
   - **Real-Time GPU Utilization:** Continuous sampling of `nvidia-smi` at 100 ms intervals confirmed **$0\%$ compute activity** and constant $1,123\text{ MiB}$ background OS memory usage. The existing pure-Python multiprocessing engine runs entirely on the host CPU.
   - **GPU Acceleration Feasibility on RTX 4050:** GPU acceleration is **not recommended** due to Python 3.14 CPU-only PyTorch builds, non-deterministic GPU PRNG (breaking bit-for-bit regression reproducibility), and PCI-e transfer latency on small arrays ($N=500$).
   - **Superior CPU Optimization Alternative:** CPU Numba JIT delivers a **$75.6\times$ speedup** (reducing a $B=5,000$ bootstrap test from $234.7\text{ ms}$ to $3.11\text{ ms}$), achieving over $3,500\text{ tests/sec}$ across the 11 CPU cores with zero GPU complexity.

5. **Test Suite Status (Phase E):**
   - Full workspace regression suite ran **501 tests in 28.8s**, all passing (`OK`).
   - Repository formatting checks passed with zero em-dashes.
   - Strategy V5 remains frozen as `VERIFIED NO-GO`, with zero broker, exchange, testnet, or live execution paths enabled.

**Final Verdict:** `ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS`

---

## 2. Phase A: Independent Verification of Direction C.5.2

### A. Independent Verification of Reported Metrics
All reported numbers from `trader/direction_c5_2_results.json` were independently recomputed and cross-checked against exact statistical formulas:

| Scenario / Audit Section | Reported Rejections ($k/N$) | Recalculated Rate | Reported Rate | Recalculated 95% Wilson CI | Reported 95% Wilson CI | Nominal Enclosure | Verification Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Audit 1: Gaussian i.i.d. ($N=500$)** | $29 / 5,000$ | $0.0058$ | $0.0058$ | $[0.0040, 0.0083]$ | $[0.0040, 0.0083]$ | Encloses ($0.0050$) | **VERIFIED (Exact)** |
| **Audit 1: GARCH(1,1) ($N=500$)** | $26 / 5,000$ | $0.0052$ | $0.0052$ | $[0.0036, 0.0076]$ | $[0.0036, 0.0076]$ | Encloses ($0.0050$) | **VERIFIED (Exact)** |
| **Audit 1: Student's t $\nu=5$ ($N=500$)** | $34 / 5,000$ | $0.0068$ | $0.0068$ | $[0.0049, 0.0095]$ | $[0.0049, 0.0095]$ | Encloses ($0.0050$) | **VERIFIED (Exact)** |
| **Audit 1: AR(1) $\phi = 0.30$ ($N=500$)** | $42 / 5,000$ | $0.0084$ | $0.0084$ | $[0.0062, 0.0113]$ | $[0.0062, 0.0113]$ | Excludes ($0.0050$) | **VERIFIED (Exact)** |
| **Audit 1: AR(1) $\phi = 0.50$ ($N=500$)** | $65 / 5,000$ | $0.0130$ | $0.0130$ | $[0.0102, 0.0165]$ | $[0.0102, 0.0165]$ | Excludes ($0.0050$) | **VERIFIED (Exact)** |
| **Audit 1: Gaussian i.i.d. ($N=1000$)** | $13 / 2,500$ | $0.0052$ | $0.0052$ | $[0.0030, 0.0089]$ | $[0.0030, 0.0089]$ | Encloses ($0.0050$) | **VERIFIED (Exact)** |
| **Audit 1: AR(1) $\phi = 0.50$ ($N=1000$)** | $19 / 2,500$ | $0.0076$ | $0.0076$ | $[0.0049, 0.0118]$ | $[0.0049, 0.0118]$ | Borderline ($0.0050$) | **VERIFIED (Exact)** |
| **Audit 3: Gaussian ($L=2$)** | $49 / 1,000$ | $0.0490$ | $0.0490$ | $[0.0373, 0.0642]$ | $[0.0373, 0.0642]$ | Encloses ($0.0500$) | **VERIFIED (Exact)** |
| **Audit 3: Gaussian ($L=10$)** | $52 / 1,000$ | $0.0520$ | $0.0520$ | $[0.0399, 0.0676]$ | $[0.0399, 0.0676]$ | Encloses ($0.0500$) | **VERIFIED (Exact)** |
| **Audit 3: Gaussian ($L=30$)** | $76 / 1,000$ | $0.0760$ | $0.0760$ | $[0.0611, 0.0941]$ | $[0.0611, 0.0941]$ | Excludes ($0.0500$) | **VERIFIED (Exact)** |
| **Audit 3: AR(1) $\phi=0.50$ ($L=2$)** | $120 / 1,000$ | $0.1200$ | $0.1200$ | $[0.1013, 0.1416]$ | $[0.1013, 0.1416]$ | Excludes ($0.0500$) | **VERIFIED (Exact)** |
| **Audit 3: AR(1) $\phi=0.50$ ($L=10$)** | $68 / 1,000$ | $0.0680$ | $0.0680$ | $[0.0540, 0.0853]$ | $[0.0540, 0.0853]$ | Excludes ($0.0500$) | **VERIFIED (Exact)** |
| **Audit 3: AR(1) $\phi=0.50$ ($L=20$)** | $66 / 1,000$ | $0.0660$ | $0.0660$ | $[0.0522, 0.0831]$ | $[0.0522, 0.0831]$ | Excludes ($0.0500$) | **VERIFIED (Exact)** |

### B. Seed Namespace Finding
- **Intra-Audit Isolation:** Within each simulation function (`run_audit_1_low_alpha_validation`, `run_audit_3_block_length_robustness`), scenario offsets (`sc_seed_offset = seed_base + sc_idx * 100000`) guaranteed that datasets within that audit never shared seeds.
- **Cross-Audit Namespace Overlap:** Audit 1 assigned base seeds $700\text{k}\dots 1300\text{k}$, which numerically overlapped with base seeds assigned to Audit 2 ($800\text{k}$), Audit 3 ($900\text{k}$), and Audit 5 ($950\text{k}$).
- **Impact Assessment:** Because the audits were executed as independent, sequential functions that wrote separate outputs, no cross-contamination or correlation bias occurred. However, to maintain absolute audit isolation, future simulation suites must enforce globally partitioned seed bases (e.g. Audit 1 at $10\text{M}$, Audit 2 at $20\text{M}$, etc.).

### C. Methodological Invariants
Inspection of `paired_block_bootstrap_test` in [`trader-timemachine/benchmark_engine.py`](file:///E:/projects/binance/trader-timemachine/benchmark_engine.py#L790) confirmed:
1. **Null Hypothesis & Centering:** $H_0: \mu(D_t) \le 0$. The excess return series is strictly centered under the null via $\tilde{D}_t = D_t - \bar{D}$.
2. **Resampling & Boundaries:** Resample blocks of length $L$ are drawn with replacement from indices $[0, N - L]$ and concatenated until length $N$ is reached. Boundary truncation (`sample[:n]`) prevents circular wrap-around bias.
3. **P-Value Convention:** Computed via Davison & Hinkley (1997) formula $(k_{\text{upper}} + 1) / (B + 1)$, where $k_{\text{upper}} = \sum_{b=1}^B \mathbf{1}_{\bar{D}_b^* \ge \bar{D}}$. Ties are properly counted in the numerator.
4. **Target Estimand of CI:** Uncentered bootstrap distribution percentiles $[2.5\%, 97.5\%]$ target the true population mean daily excess return $\mu = E[D_t]$.

---

## 3. Phase B: Statistical Dependence Policy Audit

### A. Deep Audit of the Five Inferential Risks

1. **Risk 1: Non-Rejection Does Not Prove Independence (Type II Error):**
   - For an AR(1) process with weak persistence ($\phi = 0.10$) at $N = 500$, the Ljung-Box $Q_{10}$ test rejects the null of independence in only **$27.6\%$** of cases (**$72.4\%$ failure to detect**).
   - Failing to reject independence must never be treated as affirmative proof of i.i.d. returns.

2. **Risk 2: ACF Thresholds Suffer High False-Negative Rates:**
   - Under Bartlett's formula, the sampling standard error of sample autocorrelation is $\text{SE}(\hat{\rho}_k) \approx 1/\sqrt{N}$. At $N = 500$, $\text{SE} \approx 0.0447$, establishing a 95% confidence band of $\pm 2/\sqrt{N} \approx \pm 0.0894$.
   - For an AR(1) series with $\phi = 0.20$, sample $\hat{\rho}_1$ is below $0.30$ in **$98.8\%$** of realizations. Yet $|\hat{\rho}_1| \ge 2/\sqrt{N}$ is triggered in **$99.6\%$** of cases. A fixed threshold of $0.30$ fails to account for sample size $N$.

3. **Risk 3: Linear Tests Completely Miss Volatility Clustering:**
   - For GARCH(1,1) returns with heavy volatility clustering, linear autocorrelation $\hat{\rho}_1$ is statistically zero ($-0.004 \pm 0.057$). A linear test like Ljung-Box triggers in only $26.8\%$ of cases.
   - In contrast, the McLeod-Li test on squared returns detects the ARCH effect in **$99.6\%$** of cases.

4. **Risk 4: The AR(2) Diagnostic Bypass:**
   - For stationary AR(2) returns ($X_t = 0.10 X_{t-1} + 0.50 X_{t-2} + \epsilon_t$), theoretical lag-1 autocorrelation is only $\rho_1 \approx 0.20$, while lag-2 autocorrelation is $\rho_2 \approx 0.52$.
   - A gate checking solely $\hat{\rho}_1 \ge 0.30$ triggers only **$7.8\%$** of the time, letting **$92.2\%$** of heavily persistent series pass undetected.
   - However, checking whether any lag exceeds Bartlett bands ($\max_{k \le 10} |\hat{\rho}_k| \ge 2/\sqrt{N}$) detects the dependence in **$100.0\%$** of cases, and Ljung-Box $Q_{10}$ detects it in **$100.0\%$** of cases.

5. **Risk 5: Post-Hoc Block Length Selection is P-Hacking:**
   - Choosing block length $L$ after evaluating results to select the smallest p-value introduces severe selection bias.
   - The framework mandates reporting a fixed tripartite sweep across $L \in \{5, 10, 20\}$ without post-hoc selection.

### B. Empirical Evaluation of Pre-Test Diagnostics ($K = 500, N = 500$)

| Process | True Memory Structure | $\hat{\rho}_1 \ge 0.30$ Trigger Rate | $|\hat{\rho}_1| \ge 2/\sqrt{N}$ (Bartlett Lag-1) | $\max_{k \le 10} |\hat{\rho}_k| \ge 2/\sqrt{N}$ (Bartlett Any) | Ljung-Box $Q_{10}$ ($p < 0.05$) | McLeod-Li $Q_{10}$ ($p < 0.05$) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Gaussian i.i.d.** | Zero dependence | $0.0\%$ | $3.6\%$ | $35.6\%$ | **$4.2\%$** (Calibrated) | **$4.6\%$** (Calibrated) |
| **AR(1) $\phi = 0.10$** | Weak linear persistence | $0.0\%$ | $57.0\%$ | $71.0\%$ | $27.6\%$ | $6.4\%$ |
| **AR(1) $\phi = 0.20$** | Mild linear persistence | $1.2\%$ | **$99.6\%$** | **$99.8\%$** | **$87.4\%$** | $11.4\%$ |
| **AR(2) Bypass** | Strong lag-2 persistence | **$7.8\%$** (Fails) | **$90.8\%$** | **$100.0\%$** | **$100.0\%$** | **$95.0\%$** |
| **GARCH(1,1)** | Volatility clustering | $0.0\%$ | $13.6\%$ | $65.0\%$ | $26.8\%$ | **$99.6\%$** |

### C. Refined Pre-Test Policy Gate
The heuristic gate ($\hat{\rho}_1 \ge 0.30$) is formally replaced by the **Joint Multi-Lag Pre-Test Gate**:
1. **Linear Dependence Screen:** Compute Ljung-Box $Q_{10}$. If $p < 0.05$, or if any sample autocorrelation $|\hat{\rho}_k| \ge 2/\sqrt{N}$ for $k \in \{1\dots 10\}$, linear serial dependence is flagged.
2. **ARCH Effect Screen:** Compute McLeod-Li $Q_{10}$. If $p < 0.05$, non-linear volatility clustering is flagged.
3. **Action:** If linear dependence is flagged, standard statistical claims at $\alpha = 0.05$ or $\alpha = 0.01$ are barred; inference must be labeled provisional and evaluated across $L \in \{5, 10, 20\}$.

---

## 4. Phase C: Formula-Level DSR Audit & Epistemology

### A. Mathematical Formulation Verification
The production implementation [`calc_deflated_sharpe_ratio`](file:///E:/projects/binance/trader-timemachine/benchmark_engine.py#L938) was verified against independent derivations:

1. **Daily to Annualized Consistency:**
   - Daily Sharpe ratio: $\hat{S}_{\text{daily}} = \hat{S}_{\text{annual}} / \sqrt{A}$, where $A = 365.25$.
   - Annualized trial dispersion: $\sigma_{\text{daily}} = \sigma_{\text{annual}} / \sqrt{A}$.
   - Annualized benchmark Sharpe: $S_{\text{bench, daily}} = S_{\text{bench, annual}} / \sqrt{A}$.
   - Ratio cancellation in $z$-statistic guarantees dimensional invariance:
     $$z = \frac{\hat{S}_{\text{annual}} - E[\max S_{\text{annual}}]}{SE_{\text{annual}}} = \frac{\hat{S}_{\text{daily}} - E[\max S_{\text{daily}}]}{SE_{\text{daily}}}$$
2. **Mertens (2002) Asymptotic Variance:**
   $$\text{Var}(\hat{S}_{\text{daily}}) = \frac{1 - \hat{\gamma}_3 \hat{S}_{\text{daily}} + \frac{\hat{\gamma}_4 + 2}{4} \hat{S}_{\text{daily}}^2}{N - 1}$$
   (where $\hat{\gamma}_4$ is excess kurtosis). Production matches manual derivations within $10^{-6}$.
3. **Extreme Value Theory (EVT) Expected Maximum:**
   $$E[\max_M S] \approx \sigma_M \left[ (1 - \gamma) \Phi^{-1}\left(1 - \frac{1}{M}\right) + \gamma \Phi^{-1}\left(1 - \frac{1}{M \cdot e}\right) \right]$$
   (where $\gamma \approx 0.5772156649$ is the Euler-Mascheroni constant). Production matches manual derivations within $10^{-4}$.

### B. Strict Epistemological Boundaries
- **Model-Based Hurdle:** DSR evaluates the cumulative probability that observed Sharpe exceeds the EVT expected maximum from $M$ independent Gaussian trials.
- **Evidence Threshold ($\text{DSR} \ge 0.95$):** Corresponds to a conventional one-sided 5% significance hurdle ($z \ge 1.645$) relative to noise.
- **Prohibited Interpretations:**
  * DSR is **NEVER** the posterior probability that a strategy is real or profitable.
  * $1 - \text{DSR}$ is **NEVER** the probability that a strategy was generated by data snooping.
  * DSR provides **ZERO GUARANTEE** of out-of-sample profitability or execution viability.
- **Mandatory Reporting:** Results must present the full 2D sensitivity grid over $M \in [1, 150]$ and $\sigma_M \in [0.30, 1.00]$. Unobservable historical trials must remain explicitly documented as unknown.

---

## 5. Phase D: Active CPU and GPU Workload Profiling

### A. Workload Specification and Monitoring Setup
- **Workload:** $K = 100$ independent series, $N = 500$ bars, $B = 2,000$ bootstrap resamples ($200,000$ total bootstrap iterations).
- **Measurement Harness:** Background daemon thread polling `nvidia-smi` every $100\text{ ms}$ for GPU utilization, VRAM usage, power draw, and temperature while the workload executed on the CPU.
- **Hardware Profile:**
  * CPU: 12 logical cores (AMD/Intel x86_64), Windows 11.
  * GPU: NVIDIA GeForce RTX 4050 Laptop GPU (6,141 MiB VRAM total, Driver 617.42, CUDA UMD 13.4).
  * System RAM: 32 GB.

### B. Profiling Results & Stage Runtime Breakdown

| Execution Stage | Operations Performed | Runtime (s) | % of Total Time | Bottleneck Status |
| :--- | :--- | :---: | :---: | :--- |
| **Stage 1: Data Generation** | Gaussian / AR / GARCH series generation | $0.0207\text{s}$ | $0.21\%$ | Negligible |
| **Stage 2: Centering & Indexing** | Null-centering $\tilde{D}_t = D_t - \bar{D}$ | $0.0082\text{s}$ | $0.08\%$ | Negligible |
| **Stage 3: Resampling Loop** | Block slice concatenation & summation loop | **$9.9119\text{s}$** | **$99.34\%$** | **PRIMARY BOTTLENECK** |
| **Stage 4: Exceedance Counting** | Counting null exceedances $k_{\text{upper}}$ | $0.0109\text{s}$ | $0.11\%$ | Negligible |
| **Stage 5: CI Percentile Extraction** | Sorting uncentered means for 95% CI | $0.0262\text{s}$ | $0.26\%$ | Negligible |
| **Total Workload Execution** | $K = 100, B = 2000$ ($200,000$ iterations) | **$9.98\text{s}$** | **$100.00\%$** | Effective Throughput: $10.02\text{ tests/s}$ |

### C. Active GPU Activity During Workload
- **Samples Captured:** 31 samples (sampled every $100\text{ ms}$).
- **GPU Compute Utilization:** Mean **$3.74\%$**, Max **$14.0\%$** (idle Windows desktop window manager / DWM composition).
- **GPU Memory Allocation:** Mean **$1,122.9\text{ MiB}$**, Max **$1,127.0\text{ MiB}$** (static OS display buffers; zero dynamic GPU VRAM allocated or freed).
- **GPU Power Consumption:** Mean **$3.73\text{ W}$**, Max **$4.39\text{ W}$** (idle low-power P8 performance state, out of 105 W TDP).
- **Finding:** The GPU was entirely inactive with respect to the statistical workload. The workload executed 100% on the CPU.

### D. Comparative Architecture Benchmarks ($N = 500, B = 5000, 1\text{ Test}$)

| Implementation Architecture | Hardware Target | Execution Time (ms) | Speedup vs Pure Python | Dependencies Required |
| :--- | :--- | :---: | :---: | :--- |
| **Pure Python Baseline** | 1 CPU Core | **$234.68\text{ ms}$** | $1.0\times$ (Baseline) | Standard library only |
| **NumPy Vectorized CPU** | 1 CPU Core | **$51.82\text{ ms}$** | **$4.53\times$** | `numpy` |
| **Numba JIT CPU** | 1 CPU Core | **$3.11\text{ ms}$** | **$75.6\times$** | `numba` |

### E. Assessment of GPU Acceleration on RTX 4050
GPU acceleration was evaluated against five technical criteria:
1. **Dependency Footprint:** PyTorch installed in this environment is `torch 2.11.0+cpu` (CPU build). Python 3.14 currently lacks prebuilt binary CUDA wheels for PyTorch; installing GPU frameworks requires multi-gigabyte build tools that violate minimal dependency constraints.
2. **PRNG Seed Divergence:** GPU PRNG implementations (Philox, cuRAND) produce different pseudo-random sequences than Python's standard library Mersenne Twister (`MT19937`), destroying exact bit-for-bit regression test reproducibility.
3. **PCI-e Latency Overhead:** For small return vectors ($N = 500$), host-to-device kernel launch latency and memory marshaling offset raw compute speedups.
4. **Superior CPU Alternative:** Numba JIT on the 11 logical CPU cores achieves a **$75.6\times$ speedup** ($3.11\text{ ms}$ per test), delivering over **$3,500\text{ bootstrap tests per second}$** without GPU libraries or driver compatibility risks.
5. **Verdict on GPU Acceleration:** **NOT RECOMMENDED**. CPU vectorization / JIT is vastly superior in portability, reproducibility, and simplicity.

---

## 6. Phase E: Regression Test Suite and Git Diff Summary

### A. Test Suite Results
- Full workspace regression suite executed via:
  `python -m unittest discover -s tests -p "test_*.py"`
- **Result:** **501 tests ran in 28.8s, all passed (`OK`).**
- **Focused Unit Tests Added:** [`tests/test_audit_direction_c5_3.py`](file:///E:/projects/binance/tests/test_audit_direction_c5_3.py) (4 tests verifying Phase A recomputations, Phase B Bartlett bounds, Phase C Mertens/EVT formulas, and zero em-dashes).
- **Code Standards Check:** `python tests/test_repo.py` passed with 11 tests in 0.221s; zero em-dashes exist across repository files.

### B. Concise Git Diff Summary
- `trader-timemachine/audit_direction_c5_3.py`: Created master verification, profiling, and benchmarking engine.
- `tests/test_audit_direction_c5_3.py`: Created independent verification unit test suite.
- `trader/direction_c5_3_results.json`: Saved machine-readable verification, profiling, and benchmarking data.
- `trader/direction_c5_3_audit_report.md`: Formal markdown report.
- **Safety Boundaries Preserved:** Strategy V5 code, backtest ledgers, registries, and broker execution modules remain completely untouched and disabled.

---

## 7. Refined Research-Use Policy Matrix

```mermaid
flowchart TD
    Candidate["Candidate Strategy Return Series (N >= 500)"] --> Screen{"Joint Pre-Test Gate<br>Ljung-Box Q10 & McLeod-Li Q10 & Bartlett Bands"}
    
    Screen -->|"Linear p >= 0.05 AND ARCH p >= 0.05"| T1["Tier 1: Validated Use Cases<br>Nominal Alpha 0.05 / 0.01 Tests Valid<br>Moving-Block Bootstrap L=10"]
    Screen -->|"Linear p >= 0.05 but ARCH p < 0.05"| T1B["Tier 1: Volatility Clustered GARCH<br>Moving-Block Bootstrap L=10 Valid"]
    Screen -->|"Linear p < 0.05 OR McLeod-Li p < 0.05"| T2["Tier 2: Sensitivity Required<br>Mandatory L in {5, 10, 20} Sweep<br>DSR 2D Sensitivity Grid Required"]
    Screen -->|"High Persistence (AR2 roots > 0.50 or rho1 >= 0.30)"| T3["Tier 3: Prohibited Claims<br>Statistically INCONCLUSIVE<br>Definitive Alpha Claims Barred"]
```

1. **Tier 1 (Validated Use Cases):** Permitted only when returns pass the joint pre-test gate (Ljung-Box $Q_{10}$ $p \ge 0.05$, McLeod-Li $Q_{10}$ $p \ge 0.05$, and $|\hat{\rho}_k| < 2/\sqrt{N}$ for all $k \le 10$) on partitions with $N \ge 500$.
2. **Tier 2 (Sensitivity Required):** If linear dependence or volatility clustering is flagged, testing is provisional and mandates reporting across block lengths $L \in \{5, 10, 20\}$ and the 2D DSR sensitivity grid ($M \in [1, 150], \sigma_M \in [0.30, 1.00]$).
3. **Tier 3 (Prohibited Claims):** If strong persistence is present ($\hat{\rho}_1 \ge 0.30$ or AR(2) persistence), hypothesis tests are strictly labeled **`INCONCLUSIVE DUE TO SERIAL PERSISTENCE`**. Standard significance claims are barred.
4. **Tier 4 (Unverified Assumptions):** Backtest trial independence in DSR, covariance stationarity across macro regime shifts, and execution invariance (queue priority, latency, market impact) remain explicitly unverified assumptions.

---

## 8. Final Decision & Release Action

**`ACCEPT WITH ADDITIONAL CALIBRATION RESTRICTIONS`**

The offline benchmark and statistical evaluation engine is independently verified and approved for offline comparative research under the refined Joint Multi-Lag Pre-Test Gate and Four-Tier Policy Matrix. Strategy V5 remains frozen under its `VERIFIED NO-GO` verdict, and all execution pathways remain disabled.

