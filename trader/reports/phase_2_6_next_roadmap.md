# Phase 2.6 Next-Milestone Research Roadmap

**Document Date:** 2026-11-02  
**Author:** Independent Research-Engineering Systems Auditor (Queue 3)  
**Repository:** `E:\projects\binance`  
**Classification:** Research Governance, Architecture Planning, and Milestone Roadmap  

---

## 1. Executive Overview and Strategic Context

With the successful completion of Phase 2.6, the quantitative research system has established an authoritative, immutable Research Evidence Quality System. Every strategy simulated by the execution engine now produces an immutable `ExperimentEvidenceManifest`, a discrete 4-way reproducibility verification (`EXACT_REPRODUCIBILITY`, `NUMERICALLY_EQUIVALENT`, `NON_REPRODUCIBLE`, `INSUFFICIENT_INFORMATION`), comprehensive provenance lineage tracking, negative-result preservation, and orthogonal multi-dimensional evidence reporting across five uncollapsed dimensions.

However, the Phase 2.5.2 audit demonstrated that local 7B instruction LLMs exhibit an 83.33% False Acceptance Rate (5/6) on the newly introduced holdout cohort (`TASK-41` to `TASK-50`). In the quarantined sealed suite (`TASK-51` to `TASK-70`), only five tasks were evaluated with live inference, where the empirical FAR was 1/3 = 33.33% across the three evaluated flawed/clarification tasks (with Johansen full-sample cointegration leakage missed in `TASK-52`), while the remaining fifteen tasks remain unexecuted and provisionally quarantined. This confirms that an LLM cannot be relied upon as the sole frontline gate for mathematical and statistical rigor.

This roadmap outlines the next three sequential research-engineering milestones (Milestones 2.7, 2.8, and 2.9). These milestones establish a defense-in-depth architecture where deterministic Python AST and econometric pre-screening filters flawed proposals before LLM invocation, followed by sealed double-blind benchmark validation and multi-model consensus governance.

---

## 2. Milestone 2.7: Deterministic Econometric Pre-Screening and AST Lookahead Analyzer

### 2.1 Strategic Objective
Directly resolve vulnerability `HIGH-P252-01` by removing reliance on LLM prompt comprehension for structural econometric integrity. Implement a deterministic static analysis and statistical validation layer that parses strategy code prior to simulation and reviewer dispatch.

### 2.2 Core Capabilities to Implement
1. **Python AST Lookahead Analyzer:**
   - Parse strategy logic into Python Abstract Syntax Trees.
   - Detect negative indexing and future shift operations (for example, `df['close'].shift(-k)`, forward-looking indexing).
   - Detect non-causal rolling window calculations and expanding aggregations that access future indices.
   - Detect lookahead in custom target generation and indicator labeling.

2. **Deterministic Econometric Pre-Screener:**
   - Detect full-sample parameter estimation: Verify that cointegration vectors (Engle-Granger, Johansen), Kalman filter state matrices, and PCA components are estimated strictly on rolling/expanding in-sample windows.
   - Feature scaling audit: Ensure standard scalers, quantile transformers, and min-max normalizers do not fit across full-sample data boundaries.
   - Stationarity verification: Require Augmented Dickey-Fuller (ADF) or KPSS tests for mean-reverting spread pairs before hypothesis execution.
   - Parameter parsimony bounds: Reject overparameterized hypotheses exceeding degrees-of-freedom thresholds relative to effective sample size.

3. **Pre-Screening Gate in Coordinator Pipeline:**
   - Execute AST analysis and econometric checks immediately after hypothesis formulation.
   - If pre-screening fails, automatically transition proposal to `RECOMMEND_REVISION` with structured compiler diagnostics, bypassing LLM reviewer invocation and conserving compute budget.

### 2.3 Prerequisites
- Phase 2.6 Evidence Quality System fully operational (completed).
- Schema v7 SQLite tables and immutability triggers active (completed).
- Full regression suite passing at 691 tests with 0 failures (completed).

### 2.4 Required Test Deliverables
- `tests/test_ast_lookahead_analyzer.py`: Comprehensive test suite verifying 100% detection rate on 20 synthetic lookahead patterns.
- `tests/test_econometric_prescreener.py`: Validation of full-sample leakage detection against cointegration, PCA, and normalization flaws.
- `tests/test_coordinator_prescreen_integration.py`: End-to-end integration tests confirming flawed proposals are rejected or revised without LLM reviewer invocation.

### 2.5 Strict Stop Conditions
- Any false positive rate > 5.0% on known sound baseline strategies.
- Any undetected lookahead leak in the standard testing suite.
- Any execution outside the TRAIN data partition.

---

## 3. Milestone 2.8: Independent Double-Blind Expert Panel Benchmark Validation

### 3.1 Strategic Objective
Eliminate prompt calibration contamination and establish rigorous out-of-sample generalization metrics using the sealed 20-task benchmark suite (`TASK-51` through `TASK-70`).

### 3.2 Core Capabilities to Implement
1. **Sealed Suite Cryptographic Isolation:**
   - Isolate `TASK-51` through `TASK-70` with SHA-256 integrity verification.
   - Ensure task prompts, flawed variants, and ground-truth classifications are inaccessible to the researcher and reviewer prompts during calibration.

2. **Double-Blind Evaluation Protocol:**
   - Anonymize and randomize proposal representations, mixing LLM-generated proposals with human-authored gold standards and adversarially crafted flawed proposals.
   - Implement blind scoring where independent evaluations (algorithmic, deterministic AST, LLM reviewer, and human reviewer) are collected without cross-contamination.

3. **Generalization Performance Metrics:**
   - Compute confusion matrices, Balanced Accuracy, False Acceptance Rate (FAR), and False Rejection Rate (FRR) on unseen tasks.
   - Compare performance against Phase 2.5 baseline (`smollm2:135m`) and Phase 2.5.1 calibrated `qwen2.5:7b`.

### 3.3 Prerequisites
- Milestone 2.7 AST lookahead analyzer and econometric pre-screening operational.
- Sealed task repository cryptographic integrity verified.

### 3.4 Required Test Deliverables
- `tests/test_sealed_benchmark_runner.py`: Deterministic harness for executing the sealed 20-task suite.
- `reports/milestone_2_8_double_blind_evaluation_report.md`: Formal benchmarking report detailing confusion matrices, balanced accuracy, and failure breakdowns.
- Evidence manifest audit verifying that all 20 benchmark executions generated complete schema v7 manifests and reproducibility records.

### 3.5 Strict Stop Conditions
- Coordinator Balanced Accuracy on sealed suite falls below 0.70.
- Reviewer False Acceptance Rate (FAR) on flawed proposals exceeds 0.20 after deterministic pre-screening.
- Any leakage of sealed task labels into LLM context or prompts.

---

## 4. Milestone 2.9: Multi-Model Ensembling and Research Governance Hardening

### 4.1 Strategic Objective
Eliminate single-model cognitive vulnerabilities by ensembling diverse local instruction models and hardening execution governance prior to any production consideration.

### 4.2 Core Capabilities to Implement
1. **Multi-Model Consensus Review:**
   - Implement ensemble review combining multiple local model architectures (for example, Qwen2.5-7B, DeepSeek-Coder, Mistral-Instruct) with diverse system prompt framings.
   - Establish Byzantine-tolerant voting: A single high-severity methodological flag from any reviewer halts the proposal for revision or human review.

2. **Cryptographic Human Approval Workflow:**
   - Extend the human approval ticket workflow with Ed25519 or HMAC-SHA256 signature verification.
   - Enforce that human approval tickets generated by the coordinator cannot be approved programmatically by any automated script or agent.

3. **Adversarial Stress Testing and Prompt Injection Hardening:**
   - Evaluate system resistance against prompt injection embedded within market data commentary, external research papers, or user-provided strategy descriptions.
   - Ensure the researcher and reviewer remain strictly bound to JSON schema outputs and never execute embedded instructions.

### 4.3 Prerequisites
- Milestone 2.8 sealed benchmark completed and passed.
- Storage and compute budget verified: C: drive free space >= 24 GB, Ollama models stored exclusively on E: drive.

### 4.4 Required Test Deliverables
- `tests/test_multimodel_consensus_review.py`: Verification of consensus rules, minority veto, and failure handling.
- `tests/test_cryptographic_human_approval.py`: Cryptographic signature verification and tamper detection tests.
- `tests/test_prompt_injection_resistance.py`: Adversarial test suite evaluating injection payloads.

### 4.5 Strict Stop Conditions
- Any model ensemble disagreement causing unhandled deadlock or bypassed gates.
- C: drive available capacity falls below 24.0 GB at any point during model management.
- Any automated bypass of human approval or execution authorization gates.

---

## 5. Non-Negotiable Safety and Governance Invariants

All future milestones are strictly bound by the following repository invariants:

| Category | Invariant Rule | Enforcement Mechanism |
|:---|:---|:---|
| **Trading Operations** | Zero live trading; zero paper trading; zero Binance testnet | Hardcoded absence of API credentials; network isolation to local host |
| **Order Management** | Zero order placement or execution | Engine restricted to pure historical simulation |
| **Partition Security** | Zero TEST partition access; M_test = 0 strictly maintained | Hardcoded partition gating; test token vault sealed; zero release |
| **Human Governance** | Human approval gate mandatory; execution authorization mandatory | Tickets created in PENDING state; execution requires distinct human authorization |
| **Research Integrity** | Frozen V1-V5 artifacts untouched; Phase 1 formulas intact | Git index checks; immutable statistical library functions |
| **Database Integrity** | Additive schema migrations only; triggers prevent UPDATE/DELETE | Schema v7 triggers raise sqlite3.IntegrityError on mutation |
| **Filesystem Safety** | C: drive free space >= 24.0 GB; models stored exclusively on E: | Ollama environment variables bound to `E:\ollama_models` |
| **Typographic Standard** | Strictly zero em dashes (U+2014) and zero en dashes (U+2013) | Standard ASCII hyphens, colons, and parentheses enforced across all files |

---

## 6. Implementation Sequence and Resource Budget

```
+-----------------------------------------------------------------------------------+
| Milestone 2.6: Research Evidence Quality System (COMPLETED)                       |
| - Schema v7 manifests, reproducibility engine, provenance, multidimensional report|
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| Milestone 2.7: Deterministic Econometric Pre-Screening & AST Analyzer             |
| - Target: Resolve HIGH-P252-01 (100% deterministic lookahead & leakage detection) |
| - Compute: 0 LLM inference tokens required for pre-screening                      |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| Milestone 2.8: Independent Double-Blind Sealed Benchmark Validation               |
| - Target: Measure generalization across TASK-51 to TASK-70                        |
| - Validation: Double-blind scoring, FAR <= 0.20, Balanced Accuracy >= 0.70        |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| Milestone 2.9: Multi-Model Ensembling & Governance Hardening                      |
| - Target: Byzantine-tolerant consensus review & cryptographic human gate          |
| - Storage: Maintain C: >= 24 GB, E: models only                                   |
+-----------------------------------------------------------------------------------+
```

---

## 7. Roadmap Sign-Off

This roadmap establishes a concrete, mathematically defensible progression for the quantitative research system. Advancing to Milestone 2.7 directly attacks the primary empirical vulnerability identified in Phase 2.5.2, creating an automated research assistant that is bounded by deterministic mathematical guardrails before any probabilistic model is consulted.

