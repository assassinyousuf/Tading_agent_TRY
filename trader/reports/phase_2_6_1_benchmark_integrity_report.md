# Phase 2.6.1 Benchmark Integrity and Telemetry Reconciliation Report

**Audit Date:** 2026-11-02  
**Auditor:** Independent Research-Engineering Systems Auditor & Integrity Lead  
**Repository:** `E:\projects\binance`  
**Classification:** Forensic Benchmark Telemetry, Metric Recalculation, and Extrapolation Audit  

---

## 1. Executive Summary

This report delivers the forensic recalculation of benchmark confusion matrices, telemetry provenance, and out-of-sample generalization claims across Phase 2.5.1, Phase 2.5.2, and Phase 2.6. All metrics are recalculated directly from immutable raw JSON evaluation artifacts:
- `trader/benchmark_results/phase2_5_1_eval_results.json` (180 evaluations across 30 tasks)
- `trader/benchmark_results/sealed_replication_results.json` (5 evaluations on quarantined sealed tasks)
- `trader/benchmark_results/phase2_5_eval_results.json` (historical baseline comparisons)

Crucially, this audit isolates syntax/schema validation failures from classification denominators, untangles contradictory metric narratives, and conducts a forensic reconciliation of the repeated claim that local instruction models exhibit an "83.3% False Acceptance Rate on TASK-51 through TASK-70".

---

## 2. Forensic Telemetry Breakdown: Real Inference vs Mock Simulation

The repository contains multiple benchmark result files spanning different development phases. To maintain evidence integrity, real local model inference must be strictly distinguished from simulated or mock executions:

| Phase | Benchmark Run File | Provider / Backend | Model Identifier | Task Suite | Real Inference? | Status & Isolation |
|:---|:---|:---|:---|:---:|:---:|:---|
| **Phase 2.3** | `benchmark_*_20261101_*.json` | `BenchmarkMockLLMProvider` | Mock Simulated Engine | `TASK-01` to `TASK-20` | **NO** | Simulated responses; isolated from evaluation |
| **Phase 2.4** | `repeated_benchmark_*.json` | `LocalOpenAITestServer` | Simulated Loopback Server | `TASK-01` to `TASK-20` | **NO** | Simulated responses; isolated from evaluation |
| **Phase 2.4.1**| `real_model_inference_telemetry.json`| Ollama (`127.0.0.1:11434`) | `smollm2:135m` | `TASK-01` to `TASK-20` | **YES** | Real local inference verified with token hashes |
| **Phase 2.5** | `phase2_5_eval_results.json` | Ollama (`127.0.0.1:11434`) | `smollm2:135m` vs `qwen2.5:7b` | `TASK-21` to `TASK-40` | **YES** | Real local inference (200 records) |
| **Phase 2.5.1**| `phase2_5_1_eval_results.json` | Ollama (`127.0.0.1:11434`) | `qwen2.5:7b-instruct-q4_k_m` | `TASK-21` to `TASK-50` | **YES** | Real local inference (180 records across 6 configs) |
| **Phase 2.5.2**| `sealed_replication_results.json` | Ollama (`127.0.0.1:11434`) | `qwen2.5:7b-instruct-q4_k_m` | `TASK-51` to `TASK-58` | **YES** | Real local inference (5 records on sealed suite) |

All 180 records in `phase2_5_1_eval_results.json` and all 5 records in `sealed_replication_results.json` represent genuine local GPU inference executed on `qwen2.5:7b-instruct-q4_k_m` hosted via Ollama on hardware RTX 4050 Laptop GPU. Prompt SHA-256 hashes, completion token counts, and latencies are recorded for every single execution.

---

## 3. Metric Recalculation Directly from Raw Records

### 3.1 Methodological Definitions and Scoring Standard

In quantitative research governance:
- **Negative Class ($N$ - Sound Hypotheses):** Structurally and methodologically valid proposals (`is_valid=True`). The correct action is to advance them (`PENDING_APPROVAL` or `PROCEEDED`).
  - True Negative ($TN$): Sound hypothesis correctly approved.
  - False Positive ($FP$): Sound hypothesis falsely rejected or halted (False Rejection).
- **Positive Class ($P$ - Flawed / Clarification Hypotheses):** Proposals with lookahead bias, data leakage, multiple testing snooping, unrealistic friction, or ambiguity (`is_valid=False`). The correct action is to intercept them (`REJECTED`, `REVIEW_REQUIRED`, or `REVISION_EXHAUSTED`).
  - True Positive ($TP$): Flawed hypothesis correctly halted or rejected.
  - False Negative ($FN$): Flawed hypothesis falsely approved (False Acceptance).
- **Syntax / Schema Failures:** Excluded from the classification denominator ($TP + TN + FP + FN$) to prevent syntax errors from distorting methodological discrimination accuracy.

### 3.2 Complete Recalculation Table: Phase 2.5.1 Suite (TASK-21 through TASK-50, N=30)

Across all 6 benchmark configurations, the exact recalculated metrics are:

| Track | Architecture Configuration | Total Tasks | Valid Coverage | Syntax Failures | TP | TN | FP | FN | Sensitivity (TPR) | Specificity (TNR) | False Acceptance (FAR) | False Rejection (FRR) | Balanced Accuracy |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Track A** | `SINGLE_RESEARCHER` | 30 | 24 / 30 (80.0%) | 6 | 0 | 9 | 0 | 15 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.5000 |
| **Track A** | `RESEARCHER_REVIEWER` | 30 | 21 / 30 (70.0%) | 9 | 1 | 8 | 0 | 12 | 0.0769 | 1.0000 | 0.9231 | 0.0000 | 0.5385 |
| **Track A** | `FULL_COORDINATOR` | 30 | 23 / 30 (76.7%) | 7 | 2 | 10 | 0 | 11 | 0.1538 | 1.0000 | 0.8462 | 0.0000 | 0.5769 |
| **Track B** | `SINGLE_RESEARCHER` | 30 | 30 / 30 (100.0%) | 0 | 0 | 10 | 0 | 20 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.5000 |
| **Track B** | `RESEARCHER_REVIEWER` | 30 | 30 / 30 (100.0%) | 0 | 5 | 9 | 1 | 15 | 0.2500 | 0.9000 | 0.7500 | 0.1000 | 0.5750 |
| **Track B** | `FULL_COORDINATOR` | 30 | 30 / 30 (100.0%) | 0 | 7 | 10 | 0 | 13 | 0.3500 | 1.0000 | 0.6500 | 0.0000 | 0.6750 |

### 3.3 Breakdown by Hypothesis Category under Track B FULL_COORDINATOR

Under Track B with Constrained Decoding and Full Coordinator orchestration (the production candidate architecture):
1. **Sound Hypotheses ($N=10$):**
   - 10 evaluated (`TASK-21`, `29`, `31`, `34`, `37`, `40`, `41`, `42`, `43`, `44`).
   - 10 approved (`TN=10`, `FP=0`).
   - Specificity = 100.0% (Zero False Rejections).
2. **Methodologically Flawed Hypotheses ($N=18$):**
   - 18 evaluated.
   - 6 correctly halted (`TP=6`: `TASK-23`, `25`, `26`, `33`, `36`, `39`).
   - 12 falsely approved (`FN=12`: `TASK-22`, `24`, `27`, `28`, `30`, `32`, `38`, `45`, `46`, `47`, `48`, `49`).
   - Interception Rate = 33.3% (6 / 18).
3. **Clarification-Required Hypotheses ($N=2$):**
   - 2 evaluated (`TASK-35`, `TASK-50`).
   - 1 correctly halted via review/revision (`TP=1`: `TASK-50`).
   - 1 falsely approved (`FN=1`: `TASK-35`).
4. **Combined Flawed / Clarification Interception:**
   - Total Positive Class = 20 tasks (18 flawed + 2 clarification).
   - Correctly intercepted = 7 tasks (`TP=7`).
   - Falsely accepted = 13 tasks (`FN=13`).
   - Sensitivity (TPR) = 35.0% (7 / 20).
   - False Acceptance Rate (FAR) = 65.0% (13 / 20).
   - Balanced Accuracy = (1.0000 + 0.3500) / 2 = 67.50%.

---

## 4. Forensic Resolution of the "83.3% FAR" Narrative

A pervasive claim appeared across Queue 2 and Queue 3 reports asserting that local LLM inference exhibited an "83.3% False Acceptance Rate on TASK-51 through TASK-70". A forensic review was conducted to trace the origin of the number "83.3%" across all benchmark files.

### 4.1 The Three Distinct Historical Appearances of 83.3%

The number 83.3% ($5/6$) appeared in three completely distinct contexts during project history:

1. **Phase 2.5 Baseline Reviewer Hyper-Skepticism (FRR = 83.3%):**
   - File: `trader/agent_phase2_5_real_model_report.md`
   - Context: In uncalibrated Phase 2.5 Track B, the skeptical reviewer rejected 5 out of 6 sound hypotheses.
   - Metric: False Rejection Rate (FRR) = 5 / 6 = **83.33%**.
2. **Phase 2.5.2 New Holdout Sensitivity Deficit (FAR = 83.3%):**
   - File: `trader/reports/phase_2_5_2_benchmark_replication.md` (Section 5)
   - Context: On the 10 newly introduced holdout tasks (`TASK-41` through `TASK-50`), there were 6 flawed/clarification tasks (`TASK-45`, `46`, `47`, `48`, `49`, `50`) and 4 sound tasks.
   - Outcome: The Full Coordinator caught only 1 flawed task (`TASK-50`, TP=1) and falsely accepted 5 flawed tasks (`TASK-45`, `46`, `47`, `48`, `49`, FN=5).
   - Metric: False Acceptance Rate (FAR) = 5 / 6 = **83.33%**.
3. **Phase 2.5.2 Sealed Replication Balanced Accuracy (Balanced Accuracy = 83.3%):**
   - File: `trader/benchmark_results/sealed_replication_results.json`
   - Context: On the 5 evaluated tasks from the sealed suite, the model achieved Sensitivity = 2/3 (66.67%) and Specificity = 2/2 (100.0%).
   - Metric: Balanced Accuracy = (0.6667 + 1.0000) / 2 = **83.33%**.

### 4.2 The Conflation and False Extrapolation

In Phase 2.5.2 and Phase 2.6 narrative reporting, the 83.33% FAR measured on the 10-task holdout subset (`TASK-41` to `TASK-50`) was conflated with the 20-task quarantined sealed suite (`TASK-51` to `TASK-70`). Later reports asserted:
*"On the sealed suite TASK-51 through TASK-70, the reviewer exhibited an 83.3% False Acceptance Rate."*

**Factual Reality from `sealed_replication_results.json`:**
1. Exactly **5 tasks** were evaluated: `TASK-51`, `TASK-52`, `TASK-54`, `TASK-55`, and `TASK-58`.
2. Exactly **15 tasks** were **NEVER EVALUATED**: `TASK-53`, `TASK-56`, `TASK-57`, `TASK-59`, `TASK-60`, `TASK-61`, `TASK-62`, `TASK-63`, `TASK-64`, `TASK-65`, `TASK-66`, `TASK-67`, `TASK-68`, `TASK-69`, and `TASK-70`.
3. Of the 5 evaluated tasks:
   - 2 were Sound: `TASK-51` (approved, TN), `TASK-54` (approved, TN).
   - 3 were Flawed / Clarification: `TASK-52` (cointegration leakage), `TASK-55` (whale flow ambiguity), `TASK-58` (5,000 parameter grid sweep).
   - Outcomes on flawed tasks:
     * `TASK-52`: Falsely approved (`FN=1`, False Acceptance).
     * `TASK-55`: Revisions exhausted (`TP=1`, correctly intercepted).
     * `TASK-58`: Revisions exhausted (`TP=1`, correctly intercepted).
4. Actual empirical FAR on the evaluated sealed tasks:
   $$\text{FAR} = \frac{FN}{TP + FN} = \frac{1}{2 + 1} = \frac{1}{3} = \mathbf{33.33\%}$$
   (Wilson 95% Confidence Interval: [6.1%, 79.2%]).

**Conclusion:** Extrapolating an "83.3% FAR across TASK-51 through TASK-70" was an unsubstantiated claim and an erroneous conflation. Only 5 tasks were evaluated, producing an empirical FAR of 33.33% (1 false acceptance on `TASK-52`). The remaining 15 tasks remain completely unexposed.

---

## 5. Evaluation Eligibility and Contamination Matrix

To establish an authoritative baseline for future milestones, every task in the repository is formally classified:

| Suite / Cohort | Task Range | Count | Historical Exposure | Holdout Eligibility | Evaluation Recommendation |
|:---|:---:|:---:|:---|:---:|:---|
| **Canonical Set** | `TASK-01` to `TASK-20` | 20 | Heavy prompt tuning across Phases 2.3, 2.4, 2.4.1 | **INELIGIBLE** | Regression testing only |
| **Calibration Set** | `TASK-21` to `TASK-40` | 20 | Prompt calibration, literature baselines, and rubric tuning in Phase 2.5.1 | **INELIGIBLE** | Training / prompt calibration only |
| **Holdout Cohort A**| `TASK-41` to `TASK-50` | 10 | Evaluated in Phase 2.5.1; defect patterns and failure modes analyzed | **SEMI-EXPOSED** | Calibration verification only |
| **Sealed Cohort B1**| `TASK-51`, `52`, `54`, `55`, `58` | 5 | Evaluated with live inference in Phase 2.5.2 | **EXPOSED** | Replication verification only |
| **Sealed Cohort B2**| `TASK-53`, `56`, `57`, `59`, `60`, `61`, `62`, `63`, `64`, `65`, `66`, `67`, `68`, `69`, `70` | 15 | **ZERO EVALUATION RUNS**. Completely quarantined in `sealed_tasks.py` | **100% ELIGIBLE** | **Authoritative sealed double-blind benchmark for Milestone 2.8** |

---

## 6. Summary of Reconciled Facts

1. **Exact Benchmark Telemetry:** All 180 records in `phase2_5_1_eval_results.json` and 5 records in `sealed_replication_results.json` represent authentic local LLM inference.
2. **Denominators Verified:** Full Coordinator under Track B evaluated 30 valid tasks with 0 syntax failures, achieving 10/10 Specificity (100.0%) and 7/20 Sensitivity (35.0%), resulting in a 65.0% FAR and 67.5% Balanced Accuracy.
3. **Extrapolation Rectified:** Local instruction models do NOT have a measured 83.3% FAR on the sealed benchmark suite. Only 5 sealed tasks were evaluated, yielding an empirical FAR of 33.3% (1/3). Exactly 15 sealed tasks remain unexposed and reserved for Milestone 2.8.
