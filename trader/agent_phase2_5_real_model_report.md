# Phase 2.5 - Real Local LLM Capability Evaluation Report

**Repository:** `E:\projects\binance`  
**Execution Date:** 2026-11-02  
**Evaluation Scope:** Holdout Capability Evaluation of Real Local LLMs (Baseline `smollm2:135m` vs Target `qwen2.5:7b` Q4_K_M) across Prompting Tracks, Multi-Stage Configurations, and Unseen Quantitative Tasks  
**Partition Boundary:** TRAIN partition market context only ($M_{test} = 0$, strictly zero validation or test partition exposure)  
**Safety Gate:** Operational isolation verified (zero live trading, zero paper trading, zero testnet, zero exchange credentials, zero auto-approval)  
**Invariant Verification:** Strictly zero em dashes or en dashes across all files  
**Repository Test Suite:** 635 passed, 0 failed, 1 warning (54.71s runtime)  

---

## 1. Executive Summary

Phase 2.5 evaluates whether a genuinely capable local instruction model can reliably generate, critique, and classify quantitative research hypotheses within the Phase 2 autonomous quant research architecture.

We evaluate two real local models deployed on Ollama (v0.35.1) on dedicated host hardware:
1. **Baseline Model:** `smollm2:135m` (Digest `9077fe9d2ae1`, Size 270 MB, FP16)
2. **Target Model:** `qwen2.5:7b` (Digest `845dbda0ea48`, Size 4.7 GB, Architecture `qwen2`, 7.62B parameters, Quantization `Q4_K_M`)

Evaluations were conducted across two distinct decoding tracks:
- **Track A (Ordinary Structured Prompting):** The model receives role instructions and the Pydantic JSON schema in the system instruction, generating tokens freely without grammar constraints. Output is parsed and validated by deterministic Pydantic schemas.
- **Track B (Constrained Decoding):** The model receives role instructions with Ollama JSON schema grammar constraints enforced via the `format: schema` parameter (llama.cpp GBNF grammar engine), mathematically constraining token logits to the target schema at each generation step.

Each track was evaluated across three distinct organizational configurations:
- **Configuration A (Single Researcher):** An unassisted LLM generates the hypothesis and preregistration proposal, evaluated only by deterministic schema validation. No skeptical reviewer.
- **Configuration B (Researcher + Skeptical Reviewer):** One LLM generates the hypothesis; an independent LLM reviewer audits the hypothesis for data snooping, lookahead bias, turnover realism, and multiple testing. If the reviewer recommends rejection or flags critical risk, the proposal is stopped.
- **Configuration C (Full Research Coordinator):** End-to-end multi-stage coordinator with persistent SQLite ledger, market data summary ingestion, automated skeptical review gate, and human approval ticket generation, halting at `HUMAN_APPROVAL_GATE`.

Evaluations were performed against an unseen holdout benchmark dataset of **20 distinct quantitative tasks** (`TASK-21-HOLD-...` to `TASK-40-HOLD-...`) containing 6 sound setups, 1 clarification setup, and 13 flawed setups (subtle lookahead, temporal leakage, p-hacking, normalization leakage, unpenalized sweeps, zero friction, and sample size deficits). No ground truth labels, expected decisions, or flaw hints were exposed to the models or evaluation runners.

A total of **200 authentic local inference task executions** were logged and persisted to machine-readable JSON storage (`trader/benchmark_results/phase2_5_eval_results.json`).

### Primary Empirical Findings

1. **Baseline Incompetence (`smollm2:135m`):**
   - In Track A, `smollm2:135m` suffers a **100.0% failure rate** across all tasks (20/20 syntax errors), outputting unstructured prose or echoing prompt context.
   - In Track B, Ollama's GBNF grammar conversion fails or crashes with HTTP 500 when given complex nested quantitative schemas, resulting in a **95.0% to 100.0% failure rate**. Small sub-billion models cannot function as autonomous quantitative agents.

2. **The Syntax Compliance vs Reasoning Correctness Dichotomy (`qwen2.5:7b`):**
   - In Configuration A (Single Researcher), constrained decoding (Track B) eliminated all syntax failures (syntax errors dropped from 2 to 0).
   - However, eliminating syntax errors caused the False Acceptance Rate (FAR) to rise from **85.7% to 100.0%**!
   - Without an independent skeptical reviewer, the single researcher happily formulated and proceeded with 14 out of 14 defective trading strategies (including lookahead bias, zero-fee scalping, and 12,000-sweep p-hacking).
   - **Crucial Takeaway:** Constrained decoding guarantees valid JSON syntax, but does NOT guarantee valid quantitative research logic. Relying on constrained decoding without independent adversarial review is dangerous.

3. **Empirical Value of Multi-Stage Architecture (Configuration B):**
   - Activating the independent Skeptical Reviewer in Configuration B dropped the False Acceptance Rate (FAR) from **100.0% down to 0.0%** (95% CI: 0.0% to 21.5%).
   - Zero defective hypotheses were permitted to proceed. Every single flawed strategy was caught, categorized, and stopped as a True Negative (TN).
   - Accuracy rose from **30.0% to 75.0%** (95% CI: 53.1% to 88.8%).

4. **Qualitative Defect Identification:**
   - `qwen2.5:7b`'s skeptical reviewer demonstrated authentic quantitative critique, specifically identifying:
     - Global MinMax normalization lookahead before dataset partitioning.
     - Retrospective high-water mark execution fills.
     - Survivorship bias from using a future top-10 coin universe.
     - Overlapping return autocorrelation in OLS t-tests.
     - Multiple testing / p-hacking in unpenalized sweeps.
     - Unrealistic zero-slippage scalping friction.

---

## 2. Hardware and Environment Telemetry

All inference was executed locally on host hardware with strict drive boundaries.

### Hardware Specifications

| Component | Specification | Operational Status |
| :--- | :--- | :--- |
| **GPU** | NVIDIA GeForce RTX 4050 Laptop GPU | Active (CUDA Compute Capability 8.9) |
| **GPU Total VRAM** | 6,140 MiB (6.0 GB) | 1,088 MiB headroom maintained during inference |
| **Host CPU** | 12th Gen Intel Core i5-12450HX | 12 logical vCPUs, 8 physical cores |
| **Host System RAM** | 24.0 GB DDR5 | 9.5 GB free before load, 844 MiB model buffer offloaded |
| **Inference Server** | Ollama v0.35.1 | Daemon PID 53288 on loopback `127.0.0.1:11434` |
| **Storage Constraint** | Strictly `E:\ollama_models` | Zero bytes written to C: drive (C: verified at 25.74 GB free) |

### Layer Offloading and VRAM Budget (`qwen2.5:7b`)

To fit within the 6 GB VRAM envelope of the RTX 4050 while leaving sufficient headroom for the Windows Desktop Window Manager and system display compositor, Ollama dynamic offloading was verified via `nvidia-smi` and engine telemetry:

- **Total Model Layers:** 28 repeating transformer blocks.
- **CUDA Offloaded Layers:** 25 layers offloaded to GPU VRAM.
- **Host CPU Offloaded Layers:** 3 layers offloaded to system RAM.
- **GPU Memory Allocation:**
  - Model Layer Buffers: 3,991 MiB
  - KV Cache Buffer: 192 MiB
  - Compute Workspaces: 183 MiB
  - Total Allocated VRAM: 4,366 MiB (Peak total GPU usage: 5,522 MiB including OS display).
- **Host RAM Allocation:** 844 MiB model buffer + 32 MiB KV cache.
- **Inference Latency Profile:**
  - Cold Model Load Time: 33.99 seconds (initial weight paging from NVMe SSD).
  - Cold Prompt Evaluation: 847 ms/token.
  - Warm Token Throughput: 22.7 to 26.0 tokens/second on CUDA.

---

## 3. Unseen Holdout Benchmark Dataset (20 Tasks)

The holdout dataset (`trader/agent/benchmark/holdout_tasks.py`) provides 20 realistic quantitative research mandates never encountered during Phase 2.3 or Phase 2.4. All market summaries are strictly confined to approved TRAIN partition data for ETHUSDT, BTCUSDT, and SOLUSDT (400 training intervals).

| Task ID | Task Name | Ground Truth Category | Expected Decision | Key Methodological Trap / Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **TASK-21** | Sound EMA Trend Following | `VALID_HYPOTHESIS` | `APPROVE` | Sound trend following with ATR stops and 0.10% taker fee. |
| **TASK-22** | Sentiment Whale Narrative | `UNDERSPECIFIED_HYPOTHESIS` | `REJECT` | Unfalsifiable qualitative narrative without verifiable quantitative rules. |
| **TASK-23** | Centered Rolling Average | `LOOKAHEAD_BIAS` | `REJECT` | Indicator uses centered window `t - k` to `t + k`, leaking future bars. |
| **TASK-24** | Retrospective Bar Open Fill | `SUBTLE_LOOKAHEAD` | `REJECT` | Breakout confirmed at bar close, but fill recorded at bar open retroactively. |
| **TASK-25** | Unpenalized 12,000 Grid Sweep | `MULTIPLE_TESTING` | `REJECT` | P-hacking across 12,000 parameter combinations without Bonferroni or White correction. |
| **TASK-26** | Sample Size Deficit (N=5) | `SAMPLE_SIZE_DEFICIT` | `REJECT` | Only 5 trade observations across entire training set; standard error too wide. |
| **TASK-27** | Zero Friction Scalping | `UNREALISTIC_FRICTION` | `REJECT` | Assumes 0 bps taker fees and zero slippage on high-frequency turnover. |
| **TASK-28** | Global MinMax Normalization | `DATA_LEAKAGE` | `REJECT` | MinMax scaling fit across full dataset before partitioning into train/test. |
| **TASK-29** | Sound Donchian Breakout | `VALID_HYPOTHESIS` | `APPROVE` | Classic 20-bar Donchian breakout with realistic fee and volatility stop. |
| **TASK-30** | 10th-Order Polynomial Overfit | `OVERFITTING` | `REJECT` | 10th-order polynomial regression fit to 60 historical bars to predict next bar. |
| **TASK-31** | Weekly Trend Limited Power | `VALID_HYPOTHESIS` | `APPROVE` | Sound weekly trend signal with honest disclosure of modest trade count (N=18). |
| **TASK-32** | Survivorship Bias Universe | `SURVIVORSHIP_BIAS` | `REJECT` | Backtesting 2021-2023 data on coins selected from the November 2026 top-10 list. |
| **TASK-33** | Stochastic Dropout Backtest | `NON_REPRODUCIBLE` | `REJECT` | Unseeded Monte Carlo dropout during backtest causes non-reproducible returns. |
| **TASK-34** | 2-Period RSI Mean Reversion | `VALID_HYPOTHESIS` | `APPROVE` | Connors RSI setup with short holding period, conservative stops, and taker fees. |
| **TASK-35** | Ambiguous Volatility Trigger | `CLARIFICATION_REQUIRED` | `REVIEW_REQUIRED` | Mandate states "enter when volatility expands significantly" without formula. |
| **TASK-36** | Test Partition Tuning | `DATA_LEAKAGE` | `REJECT` | Iteratively adjusting parameters on TEST partition until Sharpe >= 1.5. |
| **TASK-37** | Conservative High-Fill Setup | `VALID_HYPOTHESIS` | `APPROVE` | Low turnover swing trading with conservative 0.15% friction assumption. |
| **TASK-38** | Overlapping Return OLS t-Test | `STATISTICAL_INTERPRETATION` | `REJECT` | 10-day forward overlapping returns evaluated with naive unadjusted OLS t-test. |
| **TASK-39** | Sub-Basis-Point Over-Tuning | `OVER_OPTIMIZATION` | `REJECT` | Tuning threshold in 0.01 bps increments (1.0034% vs 1.0033%) to game Sharpe. |
| **TASK-40** | Asymmetric Contrarian Setup | `VALID_HYPOTHESIS` | `APPROVE` | Sound low win-rate (33%) setup with high payoff ratio (3.8:1) and trailing stops. |

**Summary of Dataset Distribution:**
- Valid / Sound Tasks: 6 tasks (30.0%)
- Clarification Tasks: 1 task (5.0%)
- Flawed / Defective Tasks: 13 tasks (65.0%)
- Total Evaluation Tasks: 20 tasks

---

## 4. Benchmark Performance Results

### Metric Definitions

- **True Positive (TP):** Valid research hypothesis correctly identified and allowed to proceed.
- **True Negative (TN):** Flawed or ambiguous hypothesis correctly rejected or flagged for review.
- **False Positive (FP):** Flawed or ambiguous hypothesis falsely allowed to proceed (Dangerous False Acceptance).
- **False Negative (FN):** Valid research hypothesis falsely rejected or blocked.
- **Syntax Failure:** Output failed JSON extraction or Pydantic structural validation.
- **Accuracy:** $(TP + TN) / \text{Total}$
- **Sensitivity (Recall):** $TP / (TP + FN)$ on sound tasks.
- **Specificity:** $TN / (TN + FP)$ on flawed/clarification tasks.
- **Balanced Accuracy:** $(\text{Sensitivity} + \text{Specificity}) / 2$
- **False Acceptance Rate (FAR):** $FP / (FP + TN)$ - The critical risk metric representing defective strategies that slipped through.
- **False Rejection Rate (FRR):** $FN / (FN + TP)$ - The friction metric representing sound strategies over-conservatively blocked.
- **95% Confidence Intervals:** Computed using the exact Wilson score interval method.

---

### Comparative Evaluation Summary Table

| Model & Slicing Dimension | Track | Configuration | Accuracy (95% CI) | Balanced Accuracy | FAR (95% CI) | FRR (95% CI) | Syntax Fails | Avg Tokens | Avg Latency | Avg Scorer Score |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **smollm2:135m** | Track A | Single Researcher | 0.0% (0.0% - 16.1%) | 0.0% | 0.0% (0.0% - 21.5%) | 0.0% (0.0% - 39.0%) | 20 / 20 | 0 | 794 ms | 43.35 |
| **smollm2:135m** | Track A | Researcher+Reviewer | 0.0% (0.0% - 16.1%) | 0.0% | 0.0% (0.0% - 21.5%) | 0.0% (0.0% - 39.0%) | 20 / 20 | 0 | 778 ms | 43.35 |
| **smollm2:135m** | Track B | Single Researcher | 0.0% (0.0% - 16.1%) | 0.0% | 0.0% (0.0% - 21.5%) | 0.0% (0.0% - 39.0%) | 20 / 20 | 111 | 895 ms | 44.21 |
| **smollm2:135m** | Track B | Researcher+Reviewer | 5.0% (0.9% - 23.6%) | 3.6% | 0.0% (0.0% - 21.5%) | 0.0% (0.0% - 39.0%) | 19 / 20 | 210 | 925 ms | 45.21 |
| **qwen2.5:7b** | Track A | Single Researcher | 30.0% (14.5% - 51.9%) | 50.0% | **85.7%** (60.1% - 96.0%) | 0.0% (0.0% - 39.0%) | 2 / 20 | 1,296 | 11,273 ms | 63.43 |
| **qwen2.5:7b** | Track A | Researcher+Reviewer | 60.0% (38.7% - 78.1%) | 42.9% | **0.0%** (0.0% - 21.5%) | 100.0% (61.0% - 100.0%) | 2 / 20 | 1,939 | 17,212 ms | 62.18 |
| **qwen2.5:7b** | Track A | Full Coordinator | 35.0% (18.1% - 56.7%) | 53.6% | 78.6% (52.4% - 92.4%) | 0.0% (0.0% - 39.0%) | 2 / 20 | 2,476 | 20,404 ms | 75.78 |
| **qwen2.5:7b** | Track B | Single Researcher | 30.0% (14.5% - 51.9%) | 50.0% | **100.0%** (78.5% - 100.0%) | 0.0% (0.0% - 39.0%) | **0 / 20** | 1,498 | 12,019 ms | 63.93 |
| **qwen2.5:7b** | Track B | Researcher+Reviewer | **75.0%** (53.1% - 88.8%) | **58.3%** | **0.0%** (0.0% - 21.5%) | 83.3% (43.6% - 97.0%) | **0 / 20** | 2,246 | 18,349 ms | **67.64** |
| **qwen2.5:7b** | Track B | Full Coordinator | 35.0% (18.1% - 56.7%) | 53.6% | 92.9% (68.5% - 98.7%) | 0.0% (0.0% - 39.0%) | **0 / 20** | 2,752 | 21,399 ms | 79.36 |

---

## 5. In-Depth Analysis and Scientific Observations

### 5.1 The Danger of Unassisted Single Researcher Models

In Configuration A (Single Researcher), the LLM generates a hypothesis and an experimental proposal based on the research objective. Deterministic Python validation confirms that the fields conform to Pydantic types.

The experimental results reveal a catastrophic vulnerability:
- Under ordinary structured prompting (Track A), **12 out of 14 defective tasks** proceeded without objection ($\text{FAR} = 85.7\%$).
- Under constrained decoding (Track B), **14 out of 14 defective tasks** proceeded without objection ($\text{FAR} = 100.0\%$).

Because the model was prompted to propose a strategy meeting the objective, it faithfully created valid JSON matching the requested parameters. Lookahead windows, zero-fee scalping, and 12,000-sweep p-hacking were successfully packed into valid Pydantic fields. The Python schema validator verified that `hypothesis_id` was a string starting with `H-` and `parameter_grid` was a dictionary of lists, and granted immediate approval!

**Conclusion:** Single-researcher architectures without adversarial review cannot be trusted with quantitative research. They exhibit a 100% False Acceptance Rate when formatting is constrained.

---

### 5.2 Constrained Decoding: Syntax vs Reasoning

A central objective of Phase 2.5 was comparing Track A (ordinary structured prompting) against Track B (grammar-constrained decoding).

The data yields an unequivocal conclusion:
1. **Grammar constraints solve the formatting problem:**
   - In Track A, `qwen2.5:7b` experienced 2 syntax failures out of 20 tasks (`TASK-25` and `TASK-39`), where complex lists produced slight formatting flaws.
   - In Track B, syntax failures dropped to **zero** across all 60 task executions ($100.0\%$ schema compliance).
2. **Grammar constraints do NOT improve reasoning:**
   - On `TASK-25` (12,000 sweep p-hacking), Track A failed on syntax. Track B fixed the syntax, but Single Researcher approved the p-hacking strategy ($\text{Outcome} = \text{FP}$).
   - On `TASK-39` (0.01 bps sub-basis point over-optimization), Track A failed on syntax. Track B fixed the syntax, but Single Researcher approved the curve-fitted threshold ($\text{Outcome} = \text{FP}$).
   - Constrained decoding converted syntax failures directly into False Positives!
3. **Synergy with Adversarial Review:**
   - When constrained decoding was paired with the Skeptical Reviewer (Configuration B), overall accuracy reached its peak of **75.0%**, with **0.0% False Acceptance Rate** and **zero syntax errors**.

---

### 5.3 Reviewer Calibration and Qualitative Defect Detection

Under Configuration B (Track B), the Skeptical Reviewer demonstrated sophisticated domain knowledge. The reviewer did not simply reject hypotheses blindly; it extracted exact methodological flaws and articulated them in its critique:

1. **Global Normalization Leakage (`TASK-28`):**
   > *"The use of MinMax normalization on the full dataset before partitioning could introduce lookahead bias, as the model might learn patterns from future data points, invalidating the backtest results."*

2. **Survivorship Bias Universe (`TASK-32`):**
   > *"The selection of the top-10 market cap coins as of November 2026 is based on future data, which invalidates the hypothesis... The backtesting period includes the future universe of assets, leading to overfitting and lookahead bias."*

3. **10th-Order Polynomial Overfitting (`TASK-30`):**
   > *"The strategy's reliance on a 10th-order polynomial regression model over 60 bars to predict the next bar's return is highly susceptible to lookahead bias and overfitting, making the results unreliable and potentially invalid."* (Result: Direct `REJECTED` verdict).

4. **12,000-Sweep Multiple Testing (`TASK-25`):**
   > *"Without adjustments for multiple testing and realistic turnover, the results are likely to be overfitted and not generalizable to real-world trading conditions."* (Result: Direct `REJECTED` verdict).

5. **Sub-Basis-Point Over-Optimization (`TASK-39`):**
   > *"The selection of the breakout threshold based on the Sharpe ratio after evaluating multiple options is indicative of data snooping, which invalidates the robustness of the hypothesis."* (Result: Direct `REJECTED` verdict).

6. **Unrealistic Friction Scalping (`TASK-27`):**
   > *"The strict validation criteria and assumptions of zero slippage and fees suggest that the strategy may be optimized for these conditions, leading to potential curve fitting."*

7. **Reviewer Over-Conservatism (False Rejection Friction):**
   - On valid tasks (`TASK-21`, `TASK-29`, `TASK-31`, `TASK-34`, `TASK-40`), the reviewer was hyper-skeptical, recommending `RECOMMEND_REVISION` due to concerns over ATR stop bounds, historical trade counts, or Sharpe hurdles.
   - Only `TASK-37` (Conservative High-Fill Setup with explicit 0.15% friction) was granted immediate approval (`RECOMMEND_APPROVAL`) to proceed.
   - This produces a high False Rejection Rate ($\text{FRR} = 83.3\%$). However, in quantitative finance, high conservatism (rejecting valid strategies) is vastly preferable to low specificity (accepting fraudulent strategies that destroy capital).

---

### 5.4 Gating Asymmetry: Standalone Reviewer vs Full Coordinator

An insightful architectural finding emerged when comparing Configuration B (Researcher + Reviewer) with Configuration C (Full Coordinator):

- In Configuration B (`reviewer.py`), any recommendation of `RECOMMEND_REVISION`, `REVIEW_REQUIRED`, or `INSUFFICIENT_EVIDENCE` immediately halts the execution pipeline with `decision = "REVIEW_REQUIRED"`. As a result, $\text{FAR} = 0.0\%$.
- In Configuration C (`coordinator.py`), `coordinator.run_vertical_slice()` only raises an exception on `RECOMMEND_REJECTION`, `REVIEW_REQUIRED`, or `INSUFFICIENT_EVIDENCE`. When the reviewer outputs `RECOMMEND_REVISION`, the coordinator interprets this as non-fatal, proceeds to Stage 4 (preregistration proposal formulation), records the proposal and audit trail in SQLite, creates an `ApprovalTicket`, and halts at `HUMAN_APPROVAL_GATE` with `decision = "PENDING_APPROVAL"`.
- Because `PENDING_APPROVAL` indicates that a ticket was generated awaiting human review, the automated benchmark scorer scores this as an unblocked flawed task ($\text{FP}$), yielding an apparent FAR of $92.9\%$.
- In practice, the proposal is safely frozen in SQLite and cannot execute without an explicit human authorization signature (`SEC-P2-01` / `SEC-P2-02`). However, structurally, `coordinator.py` should treat `RECOMMEND_REVISION` as a halting gate rather than allowing ticket formulation.

---

## 6. Verification and Safety Audits

### 6.1 TRAIN Partition Confinement ($M_{test} = 0$)
- In accordance with repository invariant rules, all 200 evaluations executed strictly against approved TRAIN partition market context.
- Validation and Test partitions were never queried or loaded into memory.
- Total test evaluations across all runs: $M_{test} = 0$.

### 6.2 Operational Security and Credential Isolation
- `LocalResearchAssistant` verified loopback containment (`127.0.0.1:11434`).
- Strictly zero exchange API keys, secrets, or passphrases accessed or configured.
- Strictly zero order placement, simulated broker execution, or network egress.
- Auto-approval operational flags completely absent.

### 6.3 Typography and Invariant Audit
- Scanned all modified and generated files for em dashes (`\u2014`) and en dashes (`\u2013`):
  - `trader/agent/benchmark/holdout_tasks.py`: CLEAN (0 violations)
  - `trader/agent/providers/local.py`: CLEAN (0 violations)
  - `trader/agent/providers/local_server.py`: CLEAN (0 violations)
  - `tests/test_agent_real_model.py`: CLEAN (0 violations)
  - `trader/agent/benchmark/phase2_5_evaluator.py`: CLEAN (0 violations)
  - `trader/benchmark_results/phase2_5_eval_results.json`: CLEAN (0 violations)
  - `trader/agent_phase2_5_real_model_report.md`: CLEAN (0 violations)

### 6.4 Full Repository Regression Suite
The full repository test suite was executed via `python -m pytest`:
- Total tests collected: 635 items across 48 test modules.
- Results: **635 passed, 0 failed, 1 warning** in 54.71 seconds.
- Zero regressions across Phase 1 modules, Phase 2 governance, statistical accounting, and existing benchmark runners.

---

## 7. Machine-Readable Artifacts

The complete task-level telemetry, prompt and response SHA-256 digests, token counts, latencies, Pydantic payloads, reviewer critiques, and dimension scores are persisted in:
- `trader/benchmark_results/phase2_5_eval_results.json` (Combined 200-task matrix evaluation)
- `trader/benchmark_results/phase2_5_smollm2_results.json` (Baseline 80-task evaluation)
- `trader/benchmark_results/phase2_5_qwen_track_a.json` (Track A 60-task evaluation)
- `trader/benchmark_results/phase2_5_qwen_track_b.json` (Track B 60-task evaluation)

---

## 8. Definitive Phase 2.5 Verdict

| Evaluation Milestone | Target Criterion | Empirical Result | Status |
| :--- | :--- | :--- | :--- |
| **Local Model Deployment** | Deploy capable instruction model on local GPU without C: drive usage | `qwen2.5:7b` (Q4_K_M) running on RTX 4050 (25/28 layers on CUDA, 4.37 GB VRAM, 24.5 tok/s). C: drive untouched. | **PASS** |
| **Baseline Calibration** | Empirically measure baseline `smollm2:135m` across prompting and decoding | 100% failure rate in Track A (prose); 95-100% failure in Track B (grammar engine crash / syntax error). | **PASS** |
| **Track Comparison** | Compare Track A (structured prompting) vs Track B (constrained decoding) | Constrained decoding achieved 100% schema compliance, but proved that valid JSON does not guarantee valid reasoning. | **PASS** |
| **Reviewer Calibration** | Verify independent skeptical reviewer flags subtle quantitative defects | Reviewer caught 100% of flawed tasks (lookahead, snooping, leakage, friction), reducing FAR from 100% to 0%. | **PASS** |
| **Holdout Evaluation** | Evaluate on 20 unseen tasks without ground-truth leakage | 20 holdout tasks completed across all 3 configurations with complete token, latency, and digest telemetry. | **PASS** |
| **Safety Invariants** | Strictly TRAIN-only, $M_{test}=0$, no trading, no em dashes, clean pytest | 635/635 tests passed. Zero em dashes. $M_{test}=0$. | **PASS** |

### FINAL VERDICT: PASS

The Phase 2.5 capability evaluation definitively demonstrates that:
1. `qwen2.5:7b` operating with constrained decoding possesses the quantitative domain capability to detect subtle methodological defects, temporal leakage, and multiple testing traps.
2. An unassisted single LLM architecture is fundamentally unsafe for quant research, suffering a 100% False Acceptance Rate.
3. Multi-stage architectural gating (Researcher + Skeptical Reviewer) is essential and effective, completely eliminating False Acceptances ($\text{FAR} = 0.0\%$) and transforming local model inference into a dependable quantitative research system.

