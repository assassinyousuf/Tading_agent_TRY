# Phase 2.6 Implementation Report: Research Evidence Quality System

**Audit Date:** 2026-11-02  
**Author:** Independent Research-Engineering Systems Auditor (Queue 3)  
**Repository:** `E:\projects\binance`  
**Classification:** Research Evidence Quality and Reproducibility Architecture  

---

## 1. Executive Summary

Phase 2.6 implements an authoritative Research Evidence Quality System designed to make every quantitative strategy experiment independently auditable, verifiable, and reproducible.

Previous phases demonstrated that while local LLMs can formulate hypotheses and conduct preliminary reviews, they exhibit a high False Acceptance Rate (65% to 83%) on novel econometric flaws and cannot be trusted as authoritative evaluators of quantitative truth. Phase 2.6 solves this challenge by shifting the authority of evidence from subjective model narratives to deterministic, hash-verified Python and database artifacts.

The implementation comprises:
1. Immutable experiment evidence manifests (`trader/agent/evidence/manifest.py`).
2. An independent four-way reproducibility verification engine (`trader/agent/evidence/reproducibility.py`).
3. An unbroken research provenance tracker (`trader/agent/evidence/provenance.py`).
4. Complete failure, negative-result, and crash preservation (`trader/agent/evidence/reporting.py`).
5. A deterministic multi-dimensional evidence reporter that maintains strict separation of methodological, statistical, economic, reproducibility, and governance dimensions without score collapse.
6. Relational database schema v7 with immutability triggers and transactional migration (`trader/agent/db/schema.py`).
7. Automatic evidence generation in `ResearchExecutionEngine.execute()` (`trader/agent/core/execution.py`).
8. Comprehensive test coverage in `tests/test_agent_evidence_quality_p26.py` (13 unit and integration tests).

---

## 2. Architecture of the Evidence Quality Framework

The evidence framework is organized in `trader/agent/evidence/`:

```
trader/agent/evidence/
├── __init__.py            # Exported interfaces and classes
├── manifest.py            # ExperimentEvidenceManifest & canonical hashing
├── reproducibility.py     # 4-way ReproducibilityEngine & tolerance checking
├── provenance.py          # ProvenanceTracker & lineage chain validation
└── reporting.py           # DeterministicEvidenceReporter & 5-dimension evaluation
```

### 2.1 Experiment Evidence Manifest (`manifest.py`)
Each completed, failed, or interrupted experiment receives an authoritative manifest record containing 28 fields:
* **Identification:** `manifest_id` (format: `MAN-EXP-XXXXXXXX`), `experiment_id`, `hypothesis_id`, `prereg_id`, `session_id`, `ticket_id`, `request_id`, `trial_id`.
* **Strategy & Code Specification:** `strategy_name`, `strategy_version`, `strategy_digest` (SHA-256 of canonical params and strategy name/version), `code_revision` (Git commit hash), `execution_engine_version`.
* **Data Context:** `dataset_id` (e.g. `BTCUSDT_TRAIN`), `partition_name` (strictly `'TRAIN'`), `dataset_fingerprint` (SHA-256 of partition data array).
* **Execution Parameters & Assumptions:** `parameter_definitions` (canonical dictionary), `fee_assumptions` (maker/taker fees), `slippage_assumptions`, `funding_assumptions`, `benchmark_id`, `random_seeds`.
* **Artifact Hashes:** `input_artifact_hash` (binding preregistration, parameters, dataset fingerprint, and dataset ID), `output_artifact_hash` (binding result status and metrics), `manifest_hash` (SHA-256 of canonical JSON over all content keys).
* **Timing & Status:** `start_timestamp_utc`, `completion_timestamp_utc`, `result_status` (`COMPLETED`, `FAILED`, `INTERRUPTED`), `failure_or_interruption_status`, `metrics_summary`.

**Strict Partition Isolation:** Manifest generation strictly validates `partition_name == "TRAIN"`. Any attempt to create a manifest referencing `'TEST'` or `'VALIDATION'` partitions immediately raises a `ValueError`.

### 2.2 Reproducibility Verification Engine (`reproducibility.py`)
The `ReproducibilityEngine` evaluates independent replications against reference manifests. Rather than using ambiguous binary match flags, it categorizes outcomes into four discrete verdicts:
1. `EXACT_REPRODUCIBILITY`: All parameters, random seeds, dataset fingerprints, output artifact hashes, and metrics match bit-for-bit.
2. `NUMERICALLY_EQUIVALENT`: All parameters, seeds, and dataset fingerprints match; all numeric metrics match within documented floating-point tolerances (`metric_abs_tol = 1e-6`, `metric_rel_tol = 1e-5`), while low-bit differences cause minor hash divergence.
3. `NON_REPRODUCIBLE`: Fundamental inputs diverge (modified parameters, different dataset fingerprint, altered seeds) or metric differences exceed tolerance bounds.
4. `INSUFFICIENT_INFORMATION`: Essential replication prerequisites are missing (absent seeds, missing parameters, corrupt data fingerprint).

**No Silent Approximation:** Minor discrepancies are never silently upgraded to exact matches. Every observed metric difference is recorded in `metric_discrepancies` with original, reproduced, and difference values.

### 2.3 Research Provenance Tracker (`provenance.py`)
The `ProvenanceTracker` records the complete origin and custody chain:
* `HUMAN_AUTHORED`: Hypothesis and parameters formulated directly by a human researcher.
* `LLM_ASSISTED`: Hypothesis generated with LLM assistance within the coordinator loop.
* `RESEARCHER_REVISION`: Hypothesis revised in response to skeptical review critiques.
* `HUMAN_APPROVED`: Proposal formally reviewed and approved by human ticket signature.
* `PROGRAMMATIC_GENERATION`: Pre-programmed baseline strategy or benchmark task.

The tracker enforces unbroken relational bindings (`hypothesis_id` -> `proposal_id` -> `revisions` -> `ticket_id` -> `request_id` -> `trial_id` -> `experiment_id` -> `manifest_id`).

### 2.4 Deterministic Evidence Reporter (`reporting.py`)
The `DeterministicEvidenceReporter` evaluates experiments across five orthogonal dimensions:
1. **Methodological Validity:** Verifies partition bounds (strictly TRAIN), causal execution (no same-bar fills on signal close), realistic friction deductions (taker fees >= 5 bps), and absence of data leakage.
2. **Statistical Evidence:** Verifies trade count power ($N \ge 30$), risk-adjusted hurdles (Sharpe ratio), and accounts for cumulative trials ($M_{total}$).
3. **Economic Performance:** Audits net return after frictions, annualized volatility, maximum drawdown (hurdle < 25%), profit factor, and win rate.
4. **Independent Reproducibility:** Records replication status (`EXACT_REPRODUCIBILITY`, `NUMERICALLY_EQUIVALENT`, `NON_REPRODUCIBLE`, `INSUFFICIENT_INFORMATION`).
5. **Research Governance:** Verifies frozen preregistration, human ticket approval, execution authorization, and preservation of $M_{test} = 0$.

**Zero Score Collapse:** These five dimensions are never averaged, collapsed, or compressed into a scalar score. A strategy with high historical returns but insufficient trades ($N < 30$) is classified as `INSUFFICIENT_STATISTICAL_POWER`. A strategy with flawless methodology but negative net returns is classified as `METHODOLOGICALLY_VALID_ECONOMICALLY_UNVIABLE`.

---

## 3. Relational Schema v7 and Database Migrations

Database schema version was incremented from v6 to v7 in `trader/agent/db/schema.py`:
1. **Table `experiment_evidence_manifests`:** Stores all manifest records with relational foreign keys to `experiments`, `hypotheses`, and `preregistrations`.
2. **Table `reproducibility_verifications`:** Stores independent replication audit verdicts with foreign keys to `experiment_evidence_manifests`.
3. **Immutability Triggers:**
   * `prevent_evidence_manifest_update`: Prohibits `UPDATE` on `experiment_evidence_manifests`.
   * `prevent_evidence_manifest_delete`: Prohibits `DELETE` on `experiment_evidence_manifests`.
   * `prevent_reproducibility_verification_update`: Prohibits `UPDATE` on `reproducibility_verifications`.
   * `prevent_reproducibility_verification_delete`: Prohibits `DELETE` on `reproducibility_verifications`.
4. **Transactional Migration:** `migrate_database(db_path, target_version=7)` safely creates verified `.bak` backups before migration, executes table and trigger creation, inserts version 7 metadata, and validates database row counts and PRAGMA integrity.

---

## 4. Execution Engine Integration

In `trader/agent/core/execution.py`, `ResearchExecutionEngine.execute()` was updated to automatically generate and persist an evidence manifest upon simulation completion.

When an experiment completes successfully:
1. Authoritative metrics and simulated trade statistics are captured directly from Python execution results.
2. `generate_evidence_manifest()` builds the manifest with exact Git hash, data hash, parameters, seeds, and timing.
3. `self.ledger.record_evidence_manifest()` persists the record to SQLite within the execution transaction.

When a simulation crashes or fails:
1. A failure manifest is created with `result_status = "FAILED"` and `failure_or_interruption_status` detailing the exception.
2. The failure manifest is persisted to SQLite, ensuring that negative results and crashes remain permanently auditable.

---

## 5. Verification and Test Results

The implementation was validated with a dedicated 13-test suite in `tests/test_agent_evidence_quality_p26.py`:
* `test_01`: Manifest creation and deterministic SHA-256 canonical hashing (PASSED).
* `test_02`: Partition isolation blocks TEST partition manifest generation (PASSED).
* `test_03`: Manifest SQLite persistence and trigger protection against UPDATE/DELETE (PASSED).
* `test_04`: Exact bit-for-bit reproducibility verification (PASSED).
* `test_05`: Numerical equivalence verification within documented tolerances (PASSED).
* `test_06`: Non-reproducible detection on parameter modification (PASSED).
* `test_07`: Non-reproducible detection on metric divergence (PASSED).
* `test_08`: Insufficient information handling on missing seeds or parameters (PASSED).
* `test_09`: Provenance tracking and unbroken lineage chain validation (PASSED).
* `test_10`: Negative result and failure preservation in manifest ledger (PASSED).
* `test_11`: Deterministic evidence reporting maintaining 5-dimension separation (PASSED).
* `test_12`: Database migration from v6 to v7 with backup verification (PASSED).
* `test_13`: Execution engine automatic manifest generation end-to-end (PASSED).

Result: **13 passed, 0 failures** in 0.84 seconds.

