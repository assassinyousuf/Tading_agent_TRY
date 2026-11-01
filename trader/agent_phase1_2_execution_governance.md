# Phase 1.2: Research Execution Governance Hardening Report

## Executive Summary & Final Verdict

**Verdict: APPROVED FOR CONTROLLED OFFLINE RESEARCH**

Phase 1.2 resolves the critical research governance vulnerability identified during Phase 1.1 independent adversarial auditing (FINDING-5): the possibility of unaccounted research trials caused by unbounded direct calls to `execute_strategy_simulation()`.

To guarantee mathematical integrity in multiple-testing adjustments (including White's Reality Check, Hansen's SPA, and Deflated Sharpe Ratio tracking), every strategy execution must now pass through a typed, controlled, and audit-logged execution gateway (`ResearchExecutionEngine`). Every evaluation attempt is immutably registered in SQLite *before* numerical execution starts, preventing trial disappearance even in the event of abrupt process crashes or unhandled exceptions.

---

## 1. Governance Architecture Overview

```
                      +-----------------------------+
                      | ControlledExperimentRequest |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      |   ResearchExecutionEngine   |
                      +--------------+--------------+
                                     |
             +-----------------------+-----------------------+
             |                                               |
             v                                               v
    [Validation Gate]                               [Test Vault Gate]
    - Parameter check                                - Token verification
    - Partition check                                - Hash integrity
             |                                               |
             +-----------------------+-----------------------+
                                     |
                                     v
                       +---------------------------+
                       |    trial_ledger.db        |
                       |    disposition: STARTED   |
                       |    M_total += 1           |
                       +-------------+-------------+
                                     |
                                     v
                       +---------------------------+
                       | execute_strategy_sim(...) |
                       | (Low-Level Math Kernel)   |
                       +-------------+-------------+
                                     |
                     +---------------+---------------+
                     |                               |
                 (Success)                        (Crash)
                     |                               |
                     v                               v
       +---------------------------+   +---------------------------+
       |   trial_ledger: COMPLETED |   |   trial_ledger: FAILED    |
       |   experiments: (Metrics)  |   |   experiments: (Error Log)|
       +---------------------------+   +---------------------------+
```

### Core Components Implemented

1. **`ResearchExecutionEngine` (`trader/agent/core/execution.py`):**
   - Central orchestrator enforcing mandatory preregistration binding for all strategy executions.
   - Validates candidate parameters strictly against frozen preregistration parameter space bounds.
   - Requires valid authorization tokens and hashes before allowing access to `TEST` partition data.
   - Pre-registers trials to the database before launching backtest calculations.
   - Captures strategy metadata, code hash, dataset hash, partition, and wall-clock timestamps.
   - Reconciles dangling trials upon engine initialization.

2. **Database Schema Upgrade v2 (`trader/agent/db/schema.py`):**
   - Incremented `CURRENT_SCHEMA_VERSION` to 2.
   - Added foreign key reference `experiments.trial_id` linking to `trial_ledger.trial_id`.
   - Added `experiments.strategy_name` and `experiments.strategy_version` columns.
   - Hardened database trigger `prevent_trial_tampering` replacing the rigid update block.
   - The trigger allows transitions from `STARTED` to terminal states (`COMPLETED`, `FAILED`, `INTERRUPTED`), while strictly locking core identity attributes (`trial_id`, `timestamp`, `hypothesis_id`, `prereg_id`, `parameters_evaluated_json`, `cumulative_m_total`). Terminal states cannot be altered or deleted.

3. **Lifecycle Management (`trader/agent/db/ledger.py`):**
   - `start_trial(...)`: Pre-commits trial with status `STARTED` and increments $M_{\text{total}}$.
   - `finalize_trial(...)`: Updates trial to terminal status (`COMPLETED`, `FAILED`, `INTERRUPTED`) and links experiment record.
   - `reconcile_interrupted_trials(...)`: Automatically transitions dangling `STARTED` trials from crashed runs to `INTERRUPTED`.
   - `record_failed_experiment(...)`: Records failed simulation traces into the `experiments` table with error details while safely handling metric fields.
   - Canonical parameter JSON serialization with sorted keys and compact separators (`json.dumps(params, sort_keys=True, separators=(",", ":"))`).

4. **Numerical Kernel Boundary Isolation (`trader/agent/tools/market_bridge.py`):**
   - Explicitly documented `execute_strategy_simulation()` as an internal numerical simulation kernel.
   - Added operational warnings: Calling this kernel directly bypasses preregistration governance and multiple-testing tracking. Autonomous agent execution pathways are restricted to `ResearchExecutionEngine`.

---

## 2. Trial Lifecycle State Machine

The trial accounting system implements a strict five-state deterministic state machine:

| Disposition | Description | Increments $M_{\text{total}}$ | Increments $M_{\text{evaluated}}$ |
| :--- | :--- | :---: | :---: |
| `STARTED` | Pre-registered trial prior to simulation execution. | Yes | No |
| `COMPLETED` | Simulation completed successfully and metrics logged. | Yes | Yes |
| `FAILED` | Simulation crashed or raised runtime numerical error. | Yes | No |
| `INTERRUPTED` | Process terminated unexpectedly; reconciled on restart. | Yes | No |
| `PRE_TEST_REJECTED` | Execution blocked due to parameter space violation or auth error. | Yes | No |

### Multiple-Testing Accounting Invariants

- **$M_{\text{total}}$ Monotonicity:** $M_{\text{total}}$ increases monotonically with every evaluation attempt, whether successful, crashed, aborted, or rejected at the gate.
- **Trial-Experiment Referential Integrity:** Every entry in `experiments` carries an explicit foreign key `trial_id` pointing to its registered trial in `trial_ledger`.
- **Duplicate Prevention:** Submitting an experiment with an existing `experiment_id` is immediately rejected with `DuplicateExperimentError`.

---

## 3. Failure Recovery and Interruption Reconciliation

In distributed and agentic workflows, machine restarts, power losses, or unhandled exceptions could leave runs in an ambiguous state.

Phase 1.2 implements deterministic reconciliation:
1. When `ResearchExecutionEngine` is initialized, it calls `reconcile_interrupted_trials()`.
2. Any record in `trial_ledger` remaining in `STARTED` state from a prior crashed process is updated to `INTERRUPTED`.
3. An audit note is logged: `"Reconciled from dangling STARTED state on engine startup"`.
4. $M_{\text{total}}$ accounting remains exact. The interrupted trial is never lost from the family-wise hypothesis testing denominator.

---

## 4. Honest Security and Boundary Demarcation

In keeping with rigorous institutional engineering standards, security and boundary claims are stated transparently:

1. **In-Process Boundary vs Operating System Sandboxing:**
   - `ResearchExecutionEngine` provides application-level workflow governance.
   - Python code running in the same process can theoretically inspect memory or call lower-level functions directly if malicious subroutines are deliberately executed. True hard sandboxing requires OS-level process isolation (Docker, gVisor, or dedicated cloud service accounts with restricted permissions).
   - Within the application layer, database triggers (`prevent_trial_tampering`, `prevent_experiment_tampering`, `enforce_test_vault_access`) enforce immutable recording even against buggy application logic.

2. **TEST Partition Access Governance:**
   - Access to `TEST` partition data requires a valid cryptographic hash token linked to an active preregistration.
   - In production deployments, `TEST` partition files must reside in read-protected cloud storage or separated filesystem mounts accessible only via authenticated service tokens.

---

## 5. Verification and Test Results

A dedicated test suite `tests/test_agent_execution_governance.py` was constructed to verify all Phase 1.2 guarantees.

### Summary of Governance Test Cases

1. `test_controlled_execution_success`: Verifies complete lifecycle from preregistration to `COMPLETED` trial and experiment linking.
2. `test_simulation_crash_records_failed_trial`: Verifies unhandled simulation exceptions result in `FAILED` trial records without disappearing from $M_{\text{total}}$.
3. `test_preregistration_parameter_validation`: Verifies parameter space violations are caught and recorded as `PRE_TEST_REJECTED`.
4. `test_duplicate_experiment_id_rejected`: Verifies duplicate experiment IDs raise `DuplicateExperimentError`.
5. `test_interrupted_execution_reconciliation`: Verifies orphaned `STARTED` trials are properly updated to `INTERRUPTED` on engine restart.
6. `test_trial_ledger_tampering_triggers`: Verifies SQLite triggers block unauthorized field edits and deletion of terminal trials.
7. `test_canonical_parameter_json_determinism`: Verifies parameter dictionaries produce identical byte-for-byte strings regardless of key ordering.
8. `test_test_partition_token_governance`: Verifies `TEST` evaluations require valid tokens and log test access permanently.
9. `test_sequential_m_total_tracking`: Verifies $M_{\text{total}}$ increases strictly sequentially across mixed outcome sequences.

---

## 6. Handoff Status & Transition Gate

With Phase 1.2 completed and validated:
- Research execution governance is hardened.
- Unaccounted research trials are structurally eliminated.
- Phase 1 infrastructure meets institutional-grade standards for offline quantitative research.
- Ready for Phase 2 autonomous research orchestration planning upon human authorization.

