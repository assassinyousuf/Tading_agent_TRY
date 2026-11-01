# Phase 2: Autonomous Research Orchestration Architecture Specification (Revised v2.1)

**Project:** Autonomous Binance Quantitative Research Agent  
**Repository:** `E:\projects\binance`  
**Classification:** Research Architecture Specification (Governance & Remediation Revision)  
**Status:** PROPOSED ARCHITECTURAL SPECIFICATION FOR HUMAN REVIEW (v2.1 Remediation)  

---

## Document Revision and Correction Log

| Revision | Date | Summary of Governance and Security Corrections |
| :---: | :---: | :--- |
| **v1.0** | 2026-10-30 | Initial architecture design for single-agent orchestration and tool registry. |
| **v2.0** | 2026-10-30 | Governance hardening: explicit preregistration lifecycle, secret isolation for TEST partition, immutable partition protocol, simplified execution contract, persistent lifetime evaluation budgets, and deterministic report structure. |
| **v2.1** | 2026-10-30 | **Adversarial Audit Remediation (Addressing F-01 through F-16 and C-01 through C-06):**<br>1. *F-01 / C-01 (Preregistration Status Compatibility):* Preserved existing Phase 1 `FROZEN` status vocabulary. Proposals are held in Orchestrator pending registry and only committed to SQLite `preregistrations` as immutable `FROZEN` upon explicit human approval, strictly respecting Phase 1 `prevent_preregistration_update` triggers.<br>2. *F-03 / C-03 (Budget Accounting & Self-Reinforcing Loop):* Disentangled administrative budget denials from statistical parameter probes. Budget denials do not write to `trial_ledger` and do not increment $M_{\text{total}}$, preventing infinite loops.<br>3. *F-04 (TEST Authorization Status):* Added safe read-only tool `get_test_authorization_status` (Tool 10) returning token availability without exposing secret bits.<br>4. *F-05 (Approval Integrity):* Removed undefined `approval_signature_hash`. Documented exact SQLite immutability trigger guarantees and defined optional HMAC-SHA256 signature specification.<br>5. *F-06 / C-04 (Human Decision Visibility):* Separated ticket creation from decision observation. Tool 7 returns only ticket metadata; the Orchestrator runtime inspects approvals out-of-band.<br>6. *F-07 (Request Idempotency):* Formalized complete idempotency contract: matching payloads return cached results; mismatched payloads reject; interrupted trials cannot be silently re-run; explicit retries require new `request_id`.<br>7. *F-08 (Budget Concurrency):* Enforced single-process, single-threaded runtime constraint for Phase 2.1 via file lock and threading assertions. Defined atomic transaction reservation for future multi-process scaling.<br>8. *F-11 / C-05 (Schema v3 Migration Planning):* Fully specified Schema v3 migration for `max_evaluation_budget`, with rollback protocols and test requirements.<br>9. *F-09 (Windows Path Containment):* Implemented case-normalized `os.path.normcase` validation and documented symlink limitations.<br>10. *F-10 (Runtime TRAIN Enforcement):* Enforced strict partition checking in Python code in addition to JSON Schema.<br>11. *F-12 (Skeptical Reviewer Rubric):* Defined structured multi-dimensional evaluation schema for automated self-critique.<br>12. *F-13 / F-14 / F-15 / F-16:* Defined session crash recovery, authoritative report path controls, preferred secure token delivery, and MockLLMProvider contracts.<br>13. *C-06 (Crash Sequence in TEST Access):* Formalized staged execution lifecycle distinguishing crashes before vs after token consumption. |

---

## 1. System Architecture Diagram

```
+--------------------------------------------------------------------------------------------------------+
|                                        HUMAN RESEARCH DIRECTOR                                         |
|  - Defines Research Objective                                                                         |
|  - Reviews Preregistration Proposal Tickets and Approves via External CLI (Freezing into Ledger)       |
|  - Injects Single-Use Out-of-Band TEST Authorization Tokens Directly to Secure Token Holder           |
|  - Consecrates Final Research Decisions (GO / NO_GO / INCONCLUSIVE)                                    |
+------------------------------------+-----------------------------------+-------------------------------+
                                     |                                   |
              [1. Signs Approval     |                                   | [2. Out-of-Band TEST Token
                  Ticket via CLI]    |                                   |     Direct Injection]
                                     v                                   v
+--------------------------------------------------------------------------------------------------------+
|                                   ORCHESTRATION LAYER (PHASE 2)                                        |
|                                                                                                        |
|   +------------------------------------------------------------------------------------------------+   |
|   |                          RESEARCH ORCHESTRATOR (Typed State Machine)                          |   |
|   |  - Tracks ResearchSessionState (Pydantic v2) across 12 sequential research stages              |   |
|   |  - Manages Preregistration Proposal Queue (DRAFT / PENDING_APPROVAL / REJECTED)                 |   |
|   |  - Enforces Lifetime Hypothesis Evaluation Budgets (M_budget_max)                              |   |
|   |  - Dispatches Tasks to LLM Role Modes with Structured Schema Validation                        |   |
|   |  - Enforces Single-Process Execution via OS Lock (.research_ledger.lock)                       |   |
|   +-----------------------------------------------+------------------------------------------------+   |
|                                                   |                                                    |
|                                                   v                                                    |
|   +------------------------------------------------------------------------------------------------+   |
|   |                         SINGLE LLM RESEARCH AGENT (Role-Conditioned)                           |   |
|   |  [Coordinator] <-> [Market Analyst] <-> [Hypothesis Researcher] <-> [Skeptical Reviewer]       |   |
|   |  * Role-specific prompt templates with strict structured JSON output enforcement               |   |
|   |  * Vendor-neutral LLMProviderAdapter (OpenAI, Anthropic, Gemini, DeepSeek, Local Ollama)       |   |
|   |  * Captures persistent prompt/model/sampling metadata (Acknowledges T=0 non-determinism)       |   |
|   |  * ZERO access to shell, filesystem traversal, raw candle JSONs, or test vault secrets         |   |
|   +-----------------------------------------------+------------------------------------------------+   |
|                                                   |                                                    |
|                                                   v                                                    |
|   +------------------------------------------------------------------------------------------------+   |
|   |                                 STRICT ALLOWLISTED TOOL GATEWAY                                |   |
|   |  - get_dataset_metadata()                     - get_partition_summary()                        |   |
|   |  - get_market_summary() (TRAIN only)          - inspect_available_features()                   |   |
|   |  - propose_hypothesis()                       - submit_preregistration_proposal()              |   |
|   |  - create_approval_ticket() (Ticket creation)  - get_test_authorization_status() (Read-only)    |   |
|   |  - execute_registered_experiment() (ONLY gateway to Phase 1 execution engine)                 |   |
|   |  - generate_research_report() (Deterministic renderer restricted to trader/reports/)           |   |
|   +-----------------------------------------------+------------------------------------------------+   |
+---------------------------------------------------|----------------------------------------------------+
                                                    |
                                                    v
+--------------------------------------------------------------------------------------------------------+
|                                DETERMINISTIC QUANT INFRASTRUCTURE (PHASE 1)                           |
|                                                                                                        |
|    +----------------------------------------------------------------------------------------------+    |
|    |                           RESEARCH EXECUTION ENGINE (execution.py)                           |    |
|    |   - Validates that preregistration is strictly in FROZEN state in SQLite ledger              |    |
|    |   - Validates candidate parameters against frozen grid bounds                                |    |
|    |   - Pre-registers trial in trial_ledger (STARTED, M_total += 1) before simulation            |    |
|    |   - Validates and consumes single-use TEST vault token (delivered directly from Director)    |    |
|    |   - Executes low-level simulation kernel (execute_strategy_simulation)                       |    |
|    |   - Atomically records experiment metrics, finalizes trial (COMPLETED / FAILED)              |    |
|    |   - Reconciles dangling trials on restart (INTERRUPTED)                                      |    |
|    +------------------------------+-------------------------------+-------------------------------+    |
|                                   |                               |                                    |
|                                   v                               v                                    |
|    +----------------------------------------------+  +--------------------------------------------+    |
|    |           MARKET DATA PROVIDER               |  |              RESEARCH LEDGER               |    |
|    |  - Partition slicing (WARMUP/TRAIN/VAL/TEST) |  |  - SQLite (research_ledger.db - Schema v2) |    |
|    |  - Hash-verified point-in-time candles       |  |  - Immutability triggers (UPDATE/DELETE)   |    |
|    |  - Denies unauthenticated TEST partition     |  |  - Append-only multiple-testing ledger     |    |
|    +----------------------------------------------+  +--------------------------------------------+    |
+--------------------------------------------------------------------------------------------------------+
```

---

## 2. Component Responsibilities and Agent Architecture

### 2.1 Single-Agent Architecture with Logical Role Conditioning

Phase 2 implements a Single LLM Research Agent conditioned across discrete sequential execution roles:

1. **Research Coordinator:**
   - Interprets the human research mandate.
   - Coordinates progression through the 12 sequential stages of the research state machine.
   - Creates structured approval request tickets for human review.
   - Prohibited from inventing experimental assumptions or synthesizing numerical results.

2. **Market Analyst:**
   - Queries dataset metadata, partition date bounds, and summary statistics.
   - Evaluates baseline market regimes strictly within the approved `TRAIN` partition.
   - Python tool gateway enforces runtime rejection if called on `VALIDATION` or `TEST`.

3. **Hypothesis Researcher:**
   - Formulates falsifiable quantitative hypotheses based on economic mechanisms.
   - Defines strategy rationale, universe, and candidate parameter spaces.
   - Submits structured preregistration proposals with finite, bounded parameter grids.

4. **Skeptical Reviewer (Automated Adversarial Self-Critique):**
   - Conducts a pre-flight audit of proposed hypotheses and parameter grids.
   - Applies a structured multi-dimensional evaluation rubric (look-ahead bias, data snooping risk, parameter granularity, turnover realism).
   - Issues a formal recommendation (`RECOMMEND_APPROVAL`, `RECOMMEND_REVISION`, `RECOMMEND_REJECTION`).
   - *Honest Demarcation:* This component is an internal automated prompt check and does not constitute an independent third-party audit.

5. **Research Reporter:**
   - Compiles qualitative scientific interpretation and contextual limitations.
   - Passes qualitative text to the deterministic report renderer.
   - Prohibited from altering or rendering authoritative numerical tables.

---

## 3. Preregistration Lifecycle and Status Compatibility

### 3.1 Resolving Phase 1 Status Compatibility (F-01 / C-01)

Phase 1 `schema.py` enforces total immutability on `preregistrations`:
```sql
CREATE TRIGGER prevent_preregistration_update
BEFORE UPDATE ON preregistrations
BEGIN
    SELECT RAISE(ABORT, 'Immutability violation: UPDATE prohibited on historical table preregistrations');
END;
```
Any attempt to update a row in `preregistrations` triggers an immediate SQLite abort. Furthermore, `ResearchLedger.verify_preregistration()` strictly checks:
```python
if status != "FROZEN":
    return False, f"Preregistration '{prereg_id}' is not in FROZEN state (status: {status})."
```

To achieve 100% compatibility without modifying Phase 1 code or mutating historical triggers, Phase 2 separates the **Proposal Lifecycle** from the **Frozen Ledger Entry**:

```
[Agent: DRAFT Proposal]
         |
         v
[Agent invokes submit_preregistration_proposal()]
         |
         v
[Orchestrator Pending Store: PENDING_APPROVAL]
(Stored in session state or pending proposals queue; NOT yet in SQLite preregistrations)
         |
         +---------------------------------------+
         |                                       |
         | (Human Approves via CLI)              | (Human Rejects via CLI)
         v                                       v
[Orchestrator calls Phase 1                     [Orchestrator updates proposal
 ResearchLedger.preregister_experiment()]        status to REJECTED]
         |                                       |
         v                                       v
[Inserted into SQLite preregistrations          [Proposal is permanently archived;
 with immutable status = 'FROZEN']               NEVER enters SQLite preregistrations]
         |
         v
[ResearchExecutionEngine executes experiment]
```

### 3.2 State Transition Rules

1. **`DRAFT`:** The agent develops the hypothesis and bounded parameter grid.
2. **`PENDING_APPROVAL`:** The agent invokes `submit_preregistration_proposal()`. The proposal is assigned a unique proposal ID (`PROP-<uuid>`) and stored in the Orchestrator's proposal queue. It is not written to `preregistrations`.
3. **`FROZEN`:** The Human Research Director inspects the proposal ticket and executes the approval command via external CLI. The approval service calls `ResearchLedger.preregister_experiment()`, which assigns `prereg_id` and inserts the record into SQLite `preregistrations` with `status = 'FROZEN'`. It is now permanently immutable.
4. **`REJECTED`:** If the Human Director rejects the proposal, its status in the proposal queue transitions to `REJECTED`. It is never inserted into `preregistrations` and can never be executed.
5. **Engine Validation:** `ResearchExecutionEngine` queries `preregistrations WHERE prereg_id = ?`. Because the row was inserted upon approval with `status = 'FROZEN'`, Phase 1 validation passes seamlessly.

---

## 4. TEST Authorization Secret Isolation and Status Tool

### 4.1 Secret Isolation Invariants

Phase 1 established single-use, SHA-256 hashed authorization tokens for the sealed `TEST` partition (`test_evaluation_authorizations`). In Phase 2:
1. **No Secret in LLM Tools:** The `test_token` secret is completely absent from all LLM tool schemas.
2. **Out-of-Band Delivery:** The Human Director provides the raw token directly to the execution runtime via an interactive prompt (`getpass.getpass()`) or a protected local configuration file (`chmod 0600`). Plain OS environment variables are explicitly deprecated due to process listing exposure.
3. **Orchestrator Gating:** The Orchestrator verifies token presence and validity before requesting TEST execution.

### 4.2 Tool 10: Safe Read-Only TEST Authorization Status Query (F-04)

To allow the research workflow to inspect authorization state without exposing secret material, Phase 2 adds a dedicated, read-only status tool to the registry:

- **Tool Name:** `get_test_authorization_status`
- **Input Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "prereg_id": {"type": "string", "description": "Target preregistration identifier"}
    },
    "required": ["prereg_id"],
    "additionalProperties": false
  }
  ```
- **Output Schema:**
  ```json
  {
    "prereg_id": "PREREG-8F2A10",
    "test_authorization_status": "AVAILABLE",
    "uses_remaining": 1,
    "expires_at": "2026-10-30T18:00:00Z"
  }
  ```
- **Allowed Status Values:** `AVAILABLE`, `CONSUMED`, `EXPIRED`, `NONE`.
- **Security Invariant:** Returns zero token strings, zero token hashes, and zero authorization credentials.

---

## 5. Immutable Partition Protocol & Data Access Boundaries

Phase 1 established chronological partition boundaries on the 729-day dataset:
- `WARMUP`: Bars 0 to 30 (Warmup indicator period; zero trades).
- `TRAIN`: Bars 30 to 430 (400 usable return intervals).
- `VALIDATION`: Bars 430 to 580 (150 usable return intervals).
- `TEST`: Bars 580 to 729 (149 usable return intervals; sealed).

### Architectural Boundary Rules

1. **Protocol Identifier:** Preregistrations bind strictly to `"partition_protocol_id": "CHRONOLOGICAL_V1"`. Dynamic partition ratio arguments are prohibited.
2. **Runtime Code Enforcement (F-10):** `get_market_summary` restricts access to `TRAIN`. In addition to JSON Schema constraints, the Python implementation enforces:
   ```python
   if partition.upper() != "TRAIN":
       raise ValueError(f"Partition access violation: get_market_summary is strictly restricted to TRAIN. Attempted: {partition}")
   ```
3. **First Vertical Slice Isolation:** For the Phase 2.1 minimal vertical slice, `ResearchExecutionEngine` enforces an engine-level assertion:
   ```python
   if os.environ.get("PHASE2_VERTICAL_SLICE_TRAIN_ONLY") == "1":
       if request.partition != "TRAIN":
           raise PermissionError(f"Vertical slice restricted strictly to TRAIN partition. Attempted: {request.partition}")
   ```

---

## 6. Multiple-Testing Accounting and Budget Disentanglement

### 6.1 Disentangling Budget Denials from Quantitative Trials (F-03 / C-03)

In Phase 1, any invalid parameter configuration evaluated against an approved preregistration is logged as `PRE_TEST_REJECTED` in `trial_ledger` ($M_{\text{total}} += 1$), because exploring unapproved parameters constitutes an intentional parameter probe (data snooping).

However, an **administrative budget denial** (attempting an experiment after the hypothesis evaluation budget has been exhausted) is an authorization failure, not a quantitative research trial. If budget denials were written to `trial_ledger` as `PRE_TEST_REJECTED`, every denial would increment the hypothesis trial count, creating an infinite self-reinforcing rejection loop upon client retries.

Phase 2 establishes clear event categorization:

| Event Type | Condition | Logged In | Increments $M_{\text{total}}$? | Increments $M_{\text{evaluated}}$? | Consumes Budget? |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **Conversational Dialogue** | Planning / analysis discussion | Session Trace | No | No | No |
| **Hypothesis Proposal** | `propose_hypothesis()` | `hypotheses` | No | No | No |
| **Preregistration Submission** | `submit_preregistration_proposal()` | Proposal Store | No | No | No |
| **Preregistration Rejection** | Human rejects proposal ticket | Decisions / Tickets | No | No | No |
| **Preregistration Freezing** | Human approves proposal ticket | `preregistrations` | No | No | No |
| **Administrative Budget Denial** | Request exceeds `max_evaluation_budget` | Audit Event Log | **No** | **No** | **No** |
| **Grid Parameter Violation** | Parameters outside frozen grid | `trial_ledger` (`PRE_TEST_REJECTED`) | **Yes (+1)** | No | **Yes (+1)** |
| **Simulation Exception** | Engine crashed during execution | `trial_ledger` (`FAILED`) | **Yes (+1)** | No | **Yes (+1)** |
| **Process Termination** | Crash during run; reconciled on restart | `trial_ledger` (`INTERRUPTED`) | **Yes (+1)** | No | **Yes (+1)** |
| **Completed Simulation** | Simulation finishes successfully | `trial_ledger` (`COMPLETED`) | **Yes (+1)** | **Yes (+1)** | **Yes (+1)** |

### 6.2 Authoritative Budget Query

The hypothesis evaluation budget query counts only intentional parameter evaluation attempts:
```sql
SELECT COUNT(*) FROM trial_ledger
WHERE hypothesis_id = ?
  AND disposition IN ('STARTED', 'COMPLETED', 'FAILED', 'INTERRUPTED', 'PRE_TEST_REJECTED');
```
Because administrative budget denials are rejected at the gateway and logged only in the audit trace, repeated calls do not increment `trial_ledger` and cannot cause a self-reinforcing count loop.

---

## 7. Execution Request Idempotency Contract (F-07)

`execute_registered_experiment` enforces a strict idempotency contract to eliminate duplicate simulations and prevent ambiguous retries:

### Idempotency Specification

1. **Idempotency Key:** Every execution call provides a client-generated `request_id` (e.g. `REQ-20261030-8F2A`).
2. **Payload Hash:** The execution engine computes a canonical SHA-256 hash over `(prereg_id, canonical_json(candidate_parameters), partition)`.
3. **Idempotency State Table (`execution_requests`):**
   ```sql
   CREATE TABLE IF NOT EXISTS execution_requests (
       request_id TEXT PRIMARY KEY,
       hypothesis_id TEXT NOT NULL,
       prereg_id TEXT NOT NULL,
       payload_hash TEXT NOT NULL,
       trial_id INTEGER,
       status TEXT NOT NULL, -- PENDING, COMPLETED, FAILED, INTERRUPTED
       created_at TEXT NOT NULL
   );
   ```
4. **Contract Behavior Matrix:**
   - **Matching `request_id` + Identical Payload Hash:**
     - If prior trial status is `COMPLETED` or `FAILED`: Return the existing cached execution result immediately. Zero new simulations executed; $M_{\text{total}}$ is not incremented.
     - If prior trial status is `INTERRUPTED`: Reject with `InterruptedTrialRetryError`. The interrupted trial record is permanent. Resuming an interrupted trial under the same `request_id` is prohibited.
     - If prior trial status is `PENDING`: Reject with `ConcurrentExecutionConflictError`.
   - **Matching `request_id` + Different Payload Hash:**
     - Reject immediately with `IdempotencyPayloadConflictError`. A `request_id` cannot be reused with differing parameters.
   - **New `request_id`:**
     - Executes a new trial. Increments $M_{\text{total}} += 1$ and consumes 1 unit of hypothesis budget.

---

## 8. Human Approval Decoupling & Tamper-Evidence (F-05 / F-06)

### 8.1 Ticket Creation vs Decision Observation

Approval semantics are decoupled to prevent the LLM from observing or polling human decision data:

1. **Ticket Creation (Tool 7):** The agent invokes `create_approval_ticket`.
   - Input: `prereg_id`, `approval_scope`, `submission_notes`.
   - Output: `{"ticket_id": "TICK-8921", "prereg_id": "PREREG-01", "status": "SUBMITTED", "created_at": "..."}`.
   - The tool output returns zero decision fields.
2. **Out-of-Band Human Review:** The Human Director inspects the ticket via CLI:
   ```bash
   python -m trader.agent.cli review-ticket --ticket-id TICK-8921
   ```
3. **Out-of-Band Decision Recording:** The Human Director signs the approval via CLI:
   ```bash
   python -m trader.agent.cli approve-ticket --ticket-id TICK-8921 --decision APPROVED --rationale "..."
   ```
4. **Internal State Machine Transition:** The Orchestrator runtime checks the ticket store internally and calls `ResearchLedger.preregister_experiment()` to commit the preregistration to SQLite. The LLM never invokes or observes the authorization transition directly.

### 8.2 Honest SQLite Tamper-Evidence Model (F-05)

The previous draft included an unverified `approval_signature_hash`. That field is removed. In its place, the architecture documents the exact security properties provided by SQLite:

1. **Database Triggers:** Immutability triggers on `preregistrations`, `experiments`, `experiment_metrics`, `audit_results`, `research_decisions`, and `test_evaluation_records` block all `UPDATE` and `DELETE` queries at the SQLite engine level.
2. **Audit Logging:** Every granted and denied TEST access attempt is permanently recorded in `test_evaluation_records`.
3. **Application Boundary Limitation:** SQLite triggers protect against accidental tampering, programming errors, and rogue agent tool calls. They do not protect against an attacker with raw operating-system write access to `research_ledger.db`. True non-repudiation in multi-tenant environments requires external cryptographic key signing.
4. **Optional External HMAC Signature:** If cryptographic non-repudiation is required by organizational policy, human approval records may include an HMAC-SHA256 signature generated by the Director's external CLI using an out-of-band secret key:
   $$\text{Signature} = \text{HMAC-SHA256}\Big(K_{\text{director}}, \;\text{canonical\_json}(approval\_payload)\Big)$$
   The execution service verifies this signature against the Director's public/pre-shared key before accepting the approval.

---

## 9. Persistent Lifetime Evaluation Budgets & Concurrency (F-08 / F-11)

### 9.1 Phase 2.1 Concurrency Boundary (F-08)

To eliminate race conditions between budget checks and trial inserts (TOCTOU) without introducing complex distributed lock managers, Phase 2.1 enforces a strict single-process constraint:

1. **Process-Level Lock:** On startup, the Orchestrator acquires an exclusive file lock on `.research_ledger.lock`. If another process holds the lock, initialization aborts with `ConcurrentProcessError`.
2. **Threading Invariant:** The Orchestrator asserts `threading.active_count() == 1` before initiating any experiment execution.
3. **Multi-Process Scaling Protocol:** For future multi-threaded or distributed execution, budget reservation and trial insertion must execute inside an atomic SQLite write transaction:
   ```sql
   BEGIN IMMEDIATE;
   SELECT COUNT(*) FROM trial_ledger WHERE hypothesis_id = ? AND disposition IN (...);
   -- If count >= budget: ROLLBACK and raise TrialBudgetExceededError
   INSERT INTO trial_ledger (...) VALUES (...);
   COMMIT;
   ```

### 9.2 Schema v3 Migration Specification (F-11)

To support persistent hypothesis evaluation budgets, the database schema will transition to Version 3 via a controlled, testable migration:

- **Target Version:** `CURRENT_SCHEMA_VERSION = 3`
- **Migration DDL:**
  ```sql
  ALTER TABLE hypotheses ADD COLUMN max_evaluation_budget INTEGER NOT NULL DEFAULT 10;
  
  INSERT INTO schema_metadata (version, applied_at, description)
  VALUES (3, datetime('now'), 'Phase 2: Add max_evaluation_budget to hypotheses');
  ```
- **Backward Compatibility:** All existing Phase 1 hypotheses automatically receive a default budget of 10 evaluations.
- **Rollback Protocol:** SQLite `ALTER TABLE` operations on historical tables cannot be cleanly reverted via `DROP COLUMN` in older engines. Therefore, migration runners must take an atomic filesystem backup (`research_ledger.db.bak_v2`) before executing migration DDL.
- **Migration Pre-Requisite:** Under the design-only rule, this migration is fully specified but not executed until Phase 2.1 implementation authorization.

---

## 10. Filesystem Security, Path Validation & Report Integrity (F-09 / F-14)

### 10.1 Windows-Safe Path Containment (F-09)

The report renderer compiles verified SQLite data into markdown reports restricted strictly to `trader/reports/`. To prevent path traversal attacks on Windows operating systems (where drive letters and paths are case-insensitive but Python string comparisons are case-sensitive), path validation uses canonical case normalization:

```python
REPORTS_DIR = os.path.realpath(os.path.join(ROOT_DIR, "trader", "reports"))

def resolve_secure_report_path(filename: str) -> str:
    """Enforce strict directory isolation with Windows-safe case normalization."""
    # Primary defensive barrier: strip all path separators and directory components
    clean_name = os.path.basename(filename.strip())
    if not clean_name.endswith(".md"):
        clean_name += ".md"
        
    target_path = os.path.realpath(os.path.join(REPORTS_DIR, clean_name))
    canonical_reports_dir = os.path.realpath(REPORTS_DIR)
    
    # Secondary validation: case-normalized prefix containment
    norm_target = os.path.normcase(target_path)
    norm_reports = os.path.normcase(canonical_reports_dir + os.sep)
    
    if not norm_target.startswith(norm_reports):
        raise PermissionError(f"Directory traversal detected. Reports must reside in {REPORTS_DIR}")
        
    return target_path
```

### 10.2 Authoritative Defense Hierarchy (F-14)

`os.path.basename` is the primary, load-bearing defensive barrier against path traversal. The regex pattern in Tool 9 (`^[a-zA-Z0-9_-]+\.md$`) provides secondary input sanitization. If the regex is modified in future releases, `os.path.basename` guarantees directory isolation cannot be broken. Symlink TOCTOU races are accepted as a minimal, documented residual risk in a single-user local development environment.

---

## 11. Automated Adversarial Critique Rubric (F-12)

The Skeptical Reviewer role executes an automated pre-flight audit of proposed hypotheses and parameter grids using a structured rubric:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SkepticalReviewerOutput",
  "type": "object",
  "properties": {
    "lookahead_risk_score": {
      "type": "integer",
      "minimum": 1,
      "maximum": 5,
      "description": "1 = No future data exposure; 5 = Severe lookahead bias"
    },
    "snooping_risk_score": {
      "type": "integer",
      "minimum": 1,
      "maximum": 5,
      "description": "1 = Economically motivated; 5 = Arbitrary data snooping"
    },
    "granularity_risk_score": {
      "type": "integer",
      "minimum": 1,
      "maximum": 5,
      "description": "1 = Sparse bounded grid; 5 = Dense continuous search space"
    },
    "turnover_realism_score": {
      "type": "integer",
      "minimum": 1,
      "maximum": 5,
      "description": "1 = Realistic friction; 5 = Hyperactive unmodelled turnover"
    },
    "overall_recommendation": {
      "type": "string",
      "enum": ["RECOMMEND_APPROVAL", "RECOMMEND_REVISION", "RECOMMEND_REJECTION"]
    },
    "critique_rationale": {
      "type": "string",
      "minLength": 50,
      "description": "Detailed explanation of risk assessment"
    },
    "required_revisions": {
      "type": "array",
      "items": {"type": "string"}
    }
  },
  "required": [
    "lookahead_risk_score",
    "snooping_risk_score",
    "granularity_risk_score",
    "turnover_realism_score",
    "overall_recommendation",
    "critique_rationale"
  ],
  "additionalProperties": false
}
```

The review output is logged immutably in the session audit trace. If any risk score exceeds 3, the Orchestrator halts and requires the agent to revise the proposal before generating an approval ticket.

---

## 12. Complete 10-Tool Registry Specification

Every tool accessible to the agent is documented with its formal schema and side effects:

### Tool 1: `get_dataset_metadata`
- **Purpose:** Inspect available symbols, bar counts, date bounds, and data provenance hashes.
- **Input Schema:** `{"symbol": {"type": "string", "enum": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"], "default": "BTCUSDT"}}`
- **Output Schema:** `{"symbol": str, "timeframe": str, "total_bars": int, "start_time": str, "end_time": str, "data_hash": str}`
- **Authorization:** Public Read-Only. Idempotent. Zero side effects.

### Tool 2: `get_partition_summary`
- **Purpose:** Inspect partition metadata without accessing candle prices.
- **Input Schema:** `{"partition": {"type": "string", "enum": ["WARMUP", "TRAIN", "VALIDATION", "TEST"]}}`
- **Output Schema:** `{"partition": str, "start_bar": int, "end_bar": int, "usable_return_intervals": int, "is_sealed": bool}`
- **Authorization:** Public Read-Only. Idempotent. Zero side effects.

### Tool 3: `get_market_summary`
- **Purpose:** Retrieve regime and autocorrelation statistics for the approved training period.
- **Input Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "symbol": {"type": "string", "enum": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]},
      "partition": {"type": "string", "enum": ["TRAIN"]}
    },
    "required": ["symbol", "partition"],
    "additionalProperties": false
  }
  ```
- **Output Schema:** `{"symbol": str, "partition": str, "mean_daily_return_pct": float, "annualized_vol_pct": float, "max_drawdown_pct": float, "trend_direction": str, "autocorrelation_lag1": float}`
- **Authorization:** Restricted strictly to `TRAIN`. Enforced in Python code and JSON Schema.

### Tool 4: `inspect_available_features`
- **Purpose:** List approved deterministic indicators and strategy primitives.
- **Input Schema:** `{}` (empty object)
- **Output Schema:** `{"available_strategies": ["SyntheticSMAStrategy"], "available_indicators": ["SMA", "ATR", "Donchian"], "cost_models": ["taker_0.1pct_slippage_0.05pct"]}`
- **Authorization:** Public Read-Only. Idempotent.

### Tool 5: `propose_hypothesis`
- **Purpose:** Record a structured research hypothesis.
- **Input Schema:** `{"hypothesis_id": str, "strategy_name": str, "economic_rationale": str, "validation_criteria": dict, "max_evaluation_budget": int}`
- **Output Schema:** `{"hypothesis_id": str, "status": "PREREGISTERED", "created_at": str}`
- **Authorization:** Ledger Writer. Inserts row into `hypotheses`.

### Tool 6: `submit_preregistration_proposal`
- **Purpose:** Submit an experimental configuration grid to the Orchestrator proposal queue.
- **Input Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "hypothesis_id": {"type": "string"},
      "universe": {"type": "array", "items": {"type": "string"}, "minItems": 1},
      "timeframe": {"type": "string", "enum": ["1d"]},
      "parameter_grid": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "number"}, "maxItems": 5}},
      "partition_protocol_id": {"type": "string", "enum": ["CHRONOLOGICAL_V1"]}
    },
    "required": ["hypothesis_id", "universe", "timeframe", "parameter_grid", "partition_protocol_id"],
    "additionalProperties": false
  }
  ```
- **Output Schema:** `{"proposal_id": str, "hypothesis_id": str, "status": "PENDING_APPROVAL", "total_grid_points": int}`
- **Authorization:** Proposal Queue Writer. Does NOT write to SQLite `preregistrations`.

### Tool 7: `create_approval_ticket` (Formerly request_human_approval)
- **Purpose:** Create a pending human review ticket. Returns zero decision data to LLM.
- **Input Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "proposal_id": {"type": "string"},
      "approval_scope": {"type": "string", "enum": ["PREREGISTRATION_APPROVAL", "TEST_EVALUATION"]},
      "submission_notes": {"type": "string"}
    },
    "required": ["proposal_id", "approval_scope"],
    "additionalProperties": false
  }
  ```
- **Output Schema:** `{"ticket_id": str, "proposal_id": str, "status": "SUBMITTED", "created_at": str}`
- **Authorization:** Workflow Ticket Creator. Idempotent.

### Tool 8: `execute_registered_experiment`
- **Purpose:** The sole gateway to `ResearchExecutionEngine.execute()`.
- **Input Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "prereg_id": {"type": "string"},
      "candidate_parameters": {"type": "object", "additionalProperties": {"type": "number"}},
      "partition": {"type": "string", "enum": ["TRAIN", "VALIDATION", "TEST"]},
      "request_id": {"type": "string", "description": "Client idempotency token"}
    },
    "required": ["prereg_id", "candidate_parameters", "partition", "request_id"],
    "additionalProperties": false
  }
  ```
- **Output Schema:** `{"experiment_id": str, "trial_id": int, "cumulative_m_total": int, "status": "SUCCESS" | "FAILED", "summary_metrics": dict}`
- **Authorization:** Controlled Execution Gateway. Idempotent per `request_id`.

### Tool 9: `generate_research_report`
- **Purpose:** Compiles verified SQLite records and qualitative LLM commentary into a markdown report.
- **Input Schema:** `{"hypothesis_id": str, "prereg_id": str, "experiment_ids": list, "qualitative_interpretation": str, "report_filename": str}`
- **Output Schema:** `{"report_path": str, "report_sha256": str, "status": "COMPILED"}`
- **Authorization:** Sandboxed Reporter (restricted strictly to `trader/reports/`).

### Tool 10: `get_test_authorization_status` (New)
- **Purpose:** Read-only query of TEST authorization status. Returns zero secret bits.
- **Input Schema:** `{"prereg_id": {"type": "string"}}`
- **Output Schema:** `{"prereg_id": str, "test_authorization_status": "AVAILABLE" | "CONSUMED" | "EXPIRED" | "NONE", "uses_remaining": int, "expires_at": Optional[str]}`
- **Authorization:** Public Read-Only.

---

## 13. Critical Crash Recovery and TEST Token Lifecycle (C-06)

To resolve audit finding C-06 and guarantee that crashes do not cause untraceable retries or accidental token burns, execution follows an atomic sequence:

```
[Step 1: Validate Preregistration & Parameter Bounds]
                       |
                       v
[Step 2: Pre-check TEST Token Presence (Read-Only)]
  (Asserts valid token exists in holder; does NOT consume token)
                       |
                       v
[Step 3: Register Trial in trial_ledger (STARTED, M_total += 1)]
  (Persisted to SQLite BEFORE data retrieval or simulation)
                       |
                       +---------------------------------------+
                       |                                       |
                       | (Crash occurs BEFORE Step 4)          | (Execution proceeds)
                       v                                       v
[On Restart: Trial reconciled to INTERRUPTED]         [Step 4: Atomically Consume Token]
  - Note: 'Crashed before TEST token consumption'       - UPDATE test_evaluation_authorizations
  - uses_count remains 0 (TOKEN NOT BURNED)               SET uses_count = uses_count + 1
  - Token remains valid for retry with new request_id   - INSERT test_evaluation_records
  - M_total preserved                                          |
                                                               v
                                                      [Step 5: Retrieve Sliced TEST Data]
                                                               |
                                                               v
                                                      [Step 6: Execute Backtest Simulation]
                                                               |
                                                               +-----------------------------------+
                                                               |                                   |
                                                               | (Crash during simulation)         | (Success)
                                                               v                                   v
                                                      [On Restart: INTERRUPTED]          [Trial: COMPLETED]
                                                        - uses_count is 1 (CONSUMED)       - Metrics committed
                                                        - TEST data WAS accessed           - Trial finalized
                                                        - Human MUST issue new token
```

---

## 14. Minimal First Vertical Slice Specification

The initial Phase 2.1 implementation must demonstrate the complete research workflow under strict isolation:

1. **Target Asset:** `BTCUSDT` Daily (729 bars).
2. **Strategy Primitive:** `SyntheticSMAStrategy`.
3. **Parameter Grid (M=2):**
   - Configuration 1: `fast_period = 10.0`, `slow_period = 30.0`
   - Configuration 2: `fast_period = 15.0`, `slow_period = 30.0`
4. **Execution Partition:** `TRAIN` only. Enforced at engine level.
5. **Human Gate:** Proposal must be approved via CLI and frozen into `preregistrations` (`status = 'FROZEN'`) before any simulation runs.
6. **Independence:** Both configurations are frozen in the preregistration before the first execution call.
7. **MockLLMProvider Contract (F-16):**
   - A deterministic test provider returning pre-validated JSON payloads:
     - Stage 1 (`HYPOTHESIS_FORMULATION`): Returns valid `HypothesisPayload` for SMA momentum.
     - Stage 2 (`SKEPTICAL_REVIEW`): Returns valid `SkepticalReviewerOutput` with all scores <= 2.
     - Stage 3 (`PREREGISTRATION_PROPOSAL`): Returns bounded M=2 parameter grid.
     - Stage 4 (`RESEARCH_REPORTING`): Returns structured qualitative interpretation.
8. **Deterministic Artifact:** Compiled markdown report at `trader/reports/vertical_slice_01_report.md`.

---

## 15. Review Checklist: Remediation Verification

- [x] **1. Preregistration Status Vocabulary (F-01 / C-01):** Preserved Phase 1 `FROZEN` status. Separated proposal queue from frozen ledger. Zero modifications to Phase 1 code required.
- [x] **2. Budget Accounting Loop (F-03 / C-03):** Administrative budget denials do not write to `trial_ledger` and do not increment $M_{\text{total}}$, preventing self-reinforcing loops.
- [x] **3. TEST Authorization Status Tool (F-04):** Documented Tool 10 (`get_test_authorization_status`) returning safe metadata with zero token secrets.
- [x] **4. Approval Integrity (F-05):** Removed unverified `approval_signature_hash`. Documented SQLite immutability trigger guarantees and optional HMAC-SHA256 specification.
- [x] **5. Human Decision Decoupling (F-06):** Tool 7 returns only ticket metadata. Decision recording and inspection handled out-of-band by Orchestrator.
- [x] **6. Request Idempotency Contract (F-07):** Defined exact behavior for matching payloads, conflicting payloads, interrupted trials, and explicit retries.
- [x] **7. Concurrency & Single-Process Enforcement (F-08):** Pinned Phase 2.1 to single-process with OS file lock and threading assertions.
- [x] **8. Schema v3 Migration Planning (F-11):** Fully specified Schema v3 migration for `max_evaluation_budget` with rollback and testing rules.
- [x] **9. Filesystem Security (F-09 / F-14):** Implemented case-normalized path resolution and documented authoritative defense hierarchy.
- [x] **10. Runtime Partition Checks (F-10):** Enforced TRAIN-only validation in Python code.
- [x] **11. Skeptical Reviewer Rubric (F-12):** Defined structured multi-dimensional evaluation schema.
- [x] **12. Critical Crash Recovery (C-06):** Formalized staged execution separating pre-consumption crashes from post-consumption crashes.
- [x] **13. Non-Mutating Verification:** Zero Phase 1 files modified. Zero database migrations executed. Zero em dashes (`\u2014`) introduced.
