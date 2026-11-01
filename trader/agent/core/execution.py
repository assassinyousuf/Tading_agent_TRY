"""Controlled Research Execution Engine & Governance Framework.

Enforces:
1. Every strategy evaluation requires an associated frozen preregistration.
2. Every parameter configuration is validated against that preregistration.
3. Every execution attempt receives a unique trial record BEFORE simulation begins.
4. Successful, failed, interrupted, and rejected attempts receive explicit dispositions.
5. No execution attempt can silently disappear from multiple-testing accounting.
6. Duplicate experiment IDs are strictly rejected.
7. Experiment records strictly reference their corresponding trial records.
8. Parameters are stored in deterministic canonical JSON (sorted keys, compact separators).
9. Execution records retain strategy metadata, git hash, data hash, partition, and timestamp.
10. Interrupted executions are reconciled upon restart.
"""

from dataclasses import dataclass, field
import datetime
import hashlib
import json
import os
import subprocess
import uuid
from typing import Any, Dict, List, Optional

from trader.agent.core.strategy_base import StrategyInterface
from trader.agent.db.ledger import ResearchLedger
from trader.agent.tools.market_bridge import (
    MarketDataProvider,
    SealedPartitionAccessViolation,
    execute_strategy_simulation,
)


def canonical_parameter_json(params: Dict[str, Any]) -> str:
    """Serialize parameter dictionary into a canonical, deterministic JSON string."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def get_code_git_hash(default: str = "HEAD") -> str:
    """Retrieve current Git commit hash or fallback to default."""
    try:
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return default


class ExecutionGovernanceError(Exception):
    """Base exception for research execution governance violations."""
    pass


class PreregistrationMismatchError(ExecutionGovernanceError):
    """Raised when parameters or universe do not match the frozen preregistration."""
    pass


class DuplicateExperimentError(ExecutionGovernanceError):
    """Raised when an experiment ID has already been recorded."""
    pass


class SimulationExecutionError(ExecutionGovernanceError):
    """Raised when strategy simulation fails during execution."""
    pass


class IdempotencyPayloadConflictError(ExecutionGovernanceError):
    """Raised when an existing request_id is submitted with differing parameters."""
    pass


class InterruptedTrialRetryError(ExecutionGovernanceError):
    """Raised when attempting to re-execute an interrupted trial under the same request_id."""
    pass


class ConcurrentExecutionConflictError(ExecutionGovernanceError):
    """Raised when an execution request with the same request_id is already in progress."""
    pass


class TrialBudgetExceededError(ExecutionGovernanceError):
    """Raised when a hypothesis evaluation budget has been exhausted."""
    pass


@dataclass(frozen=True)
class ControlledExperimentRequest:
    """Immutable request specification for a governed quantitative experiment."""

    hypothesis_id: str
    prereg_id: str
    strategy: StrategyInterface
    parameters: Dict[str, float]
    partition: str  # TRAIN, VALIDATION, TEST
    symbol: str = "BTCUSDT"
    test_token: Optional[str] = None
    cost_model: Optional[Dict[str, float]] = None
    initial_capital: float = 10000.0
    benchmark_id: str = "BTC_NET"
    random_seed: int = 42
    notes: Optional[str] = None
    experiment_id: Optional[str] = None
    request_id: Optional[str] = None


@dataclass(frozen=True)
class ControlledExperimentResult:
    """Structured result returned by the governed execution engine."""

    experiment_id: str
    trial_id: int
    cumulative_m_total: int
    status: str  # SUCCESS, FAILED, INTERRUPTED
    metrics: Dict[str, Any]
    daily_returns: List[float]
    sim_results: Dict[str, Any]
    audit_data: Optional[Dict[str, Any]] = None


class ResearchExecutionEngine:
    """Authoritative research execution engine enforcing strict trial accounting and data governance."""

    def __init__(self, ledger: ResearchLedger, provider: MarketDataProvider) -> None:
        self.ledger = ledger
        self.provider = provider
        # Reconcile any trials left in STARTED state from prior interrupted runs
        self.ledger.reconcile_interrupted_trials()

    def execute(self, request: ControlledExperimentRequest) -> ControlledExperimentResult:
        """Execute a controlled strategy evaluation under strict multiple-testing governance.

        Execution Lifecycle:
        1. Validates preregistration and parameter grid membership.
        2. Assigns a unique experiment_id and checks uniqueness.
        3. Creates a persistent trial record in SQLite with disposition='STARTED' BEFORE simulation.
        4. Retrieves partition data with token verification.
        5. Executes strategy simulation with failure traps.
        6. Records completed experiment and metrics atomically.
        7. Transitions trial disposition to 'COMPLETED'.
        """
        # 0. Phase 2 Vertical Slice & Governance Pre-Flight Checks
        if os.environ.get("PHASE2_VERTICAL_SLICE_TRAIN_ONLY") == "1":
            if request.partition != "TRAIN":
                raise PermissionError(
                    f"Access denied: Vertical slice is strictly restricted to TRAIN partition. Attempted: {request.partition}"
                )

        # Hypothesis Lifetime Evaluation Budget Gate (F-03: No trial_ledger write on administrative budget denial)
        budget = self.ledger.get_hypothesis_budget(request.hypothesis_id)
        consumed = self.ledger.get_hypothesis_evaluation_count(request.hypothesis_id)
        if consumed >= budget:
            raise TrialBudgetExceededError(
                f"Hypothesis '{request.hypothesis_id}' evaluation budget exceeded ({consumed}/{budget})."
            )

        # Idempotency Contract Check (F-07)
        req_id = request.request_id or f"REQ-{uuid.uuid4().hex[:12].upper()}"
        payload_str = f"{request.prereg_id}:{canonical_parameter_json(request.parameters)}:{request.partition}:{request.symbol}"
        payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        existing_req = self.ledger.get_execution_request(req_id)
        if existing_req is not None:
            if existing_req["payload_hash"] != payload_hash:
                raise IdempotencyPayloadConflictError(
                    f"Request ID '{req_id}' was previously submitted with differing parameters."
                )
            if existing_req["status"] == "COMPLETED":
                # Return cached result without re-executing backtest
                cached_exp_id = existing_req["experiment_id"]
                cached_trial_id = existing_req["trial_id"]
                cur = self.ledger.conn.cursor()
                cur.execute("SELECT raw_metrics_json FROM experiment_metrics WHERE experiment_id = ?;", (cached_exp_id,))
                m_row = cur.fetchone()
                cached_metrics = json.loads(m_row[0]) if m_row and m_row[0] else {}
                cur.execute("SELECT cumulative_m_total FROM trial_ledger WHERE trial_id = ?;", (cached_trial_id,))
                t_row = cur.fetchone()
                cached_m_total = t_row[0] if t_row else 1
                return ControlledExperimentResult(
                    experiment_id=cached_exp_id,
                    trial_id=cached_trial_id,
                    cumulative_m_total=cached_m_total,
                    status="SUCCESS",
                    metrics=cached_metrics,
                    daily_returns=[],
                    sim_results={"raw_metrics": cached_metrics},
                )
            if existing_req["status"] == "INTERRUPTED":
                raise InterruptedTrialRetryError(
                    f"Request ID '{req_id}' was interrupted. An explicit rerun requires a new request_id."
                )
            if existing_req["status"] == "STARTED":
                raise ConcurrentExecutionConflictError(
                    f"Request ID '{req_id}' is currently in progress."
                )

        # 1. Parameter Validation against Frozen Preregistration
        is_valid, rejection_reason = self.ledger.verify_preregistration(
            prereg_id=request.prereg_id,
            config_params=request.parameters,
            partition=request.partition,
            universe=[request.symbol],
        )

        if not is_valid:
            # Record pre-test rejection in trial ledger so p-hacking attempt is counted
            self.ledger.record_trial(
                hypothesis_id=request.hypothesis_id,
                prereg_id=request.prereg_id,
                experiment_id=None,
                parameters_evaluated=request.parameters,
                disposition="PRE_TEST_REJECTED",
                notes=f"Preregistration rejection: {rejection_reason}",
            )
            raise PreregistrationMismatchError(f"Preregistration validation failed: {rejection_reason}")

        # 2. Assign Unique Experiment ID
        exp_id = request.experiment_id or f"EXP-{uuid.uuid4().hex[:12].upper()}"

        cur = self.ledger.conn.cursor()
        cur.execute("SELECT experiment_id FROM experiments WHERE experiment_id = ?;", (exp_id,))
        if cur.fetchone():
            raise DuplicateExperimentError(f"Experiment ID '{exp_id}' already exists in ledger.")

        # Record initial execution request state in SQLite
        self.ledger.record_execution_request(
            request_id=req_id,
            hypothesis_id=request.hypothesis_id,
            prereg_id=request.prereg_id,
            payload_hash=payload_hash,
            experiment_id=exp_id,
            status="STARTED",
        )

        # 3. Pre-Execution Trial Registration (CRITICAL: created BEFORE simulation starts)
        trial_id = self.ledger.start_trial(
            hypothesis_id=request.hypothesis_id,
            prereg_id=request.prereg_id,
            experiment_id=exp_id,
            parameters_evaluated=request.parameters,
            disposition="STARTED",
            notes=request.notes or "Controlled experiment execution initiated",
        )

        git_hash = get_code_git_hash()

        # 4. Partition Data Retrieval
        try:
            partition_data = self.provider.get_partition_data(
                partition=request.partition,
                symbol=request.symbol,
                test_token=request.test_token,
                warmup_bars=request.strategy.warmup_bars_required,
                prereg_id=request.prereg_id,
            )
        except Exception as exc:
            self.ledger.update_execution_request(req_id, status="FAILED", experiment_id=exp_id, trial_id=trial_id)
            # Finalize trial as FAILED
            self.ledger.finalize_trial(
                trial_id=trial_id,
                disposition="FAILED",
                experiment_id=exp_id,
                notes=f"Data retrieval failed: {str(exc)}",
            )
            raise

        data_hash = partition_data.get("data_hash", "UNKNOWN")

        # 5. Execute Simulation with Failure Traps
        try:
            sim_results = execute_strategy_simulation(
                strategy=request.strategy,
                partition_data=partition_data,
                params=request.parameters,
                cost_model=request.cost_model,
                initial_capital=request.initial_capital,
            )
        except Exception as exc:
            self.ledger.update_execution_request(req_id, status="FAILED", experiment_id=exp_id, trial_id=trial_id)
            # Finalize trial as FAILED
            self.ledger.finalize_trial(
                trial_id=trial_id,
                disposition="FAILED",
                experiment_id=exp_id,
                notes=f"Simulation crashed: {str(exc)}",
            )
            # Record failed experiment row in experiments table
            self.ledger.record_failed_experiment(
                experiment_id=exp_id,
                prereg_id=request.prereg_id,
                trial_id=trial_id,
                partition_name=request.partition,
                parameters=request.parameters,
                strategy_name=request.strategy.name,
                strategy_version=request.strategy.version,
                data_hash=data_hash,
                error_message=str(exc),
                random_seed_63bit=request.random_seed,
                code_git_hash=git_hash,
            )
            raise SimulationExecutionError(f"Strategy simulation failed: {str(exc)}") from exc

        # 6. Record Experiment Result Atomically
        exp_data = {
            "experiment_id": exp_id,
            "prereg_id": request.prereg_id,
            "trial_id": trial_id,
            "partition_name": request.partition,
            "strategy_name": request.strategy.name,
            "strategy_version": request.strategy.version,
            "parameters": request.parameters,
            "random_seed_63bit": request.random_seed,
            "code_git_hash": git_hash,
            "data_hash": data_hash,
            "status": "SUCCESS",
        }

        try:
            self.ledger.record_experiment_result(
                experiment_data=exp_data,
                metrics_data=sim_results["raw_metrics"],
                audit_data=None,
            )
        except Exception as exc:
            self.ledger.update_execution_request(req_id, status="FAILED", experiment_id=exp_id, trial_id=trial_id)
            try:
                self.ledger.finalize_trial(
                    trial_id=trial_id,
                    disposition="FAILED",
                    experiment_id=exp_id,
                    notes=f"Experiment recording failed: {str(exc)}",
                )
            except Exception:
                pass  # If DB is unresponsive, trial remains in STARTED and is reconciled on restart
            raise SimulationExecutionError(f"Failed to record experiment result: {str(exc)}") from exc

        # 7. Finalize Trial as COMPLETED
        try:
            self.ledger.finalize_trial(
                trial_id=trial_id,
                disposition="COMPLETED",
                experiment_id=exp_id,
                notes="Simulation completed successfully",
            )
        except Exception as exc:
            self.ledger.update_execution_request(req_id, status="FAILED", experiment_id=exp_id, trial_id=trial_id)
            # If finalization fails, trial remains in STARTED state and will be reconciled on restart
            raise SimulationExecutionError(f"Failed to finalize trial: {str(exc)}") from exc

        self.ledger.update_execution_request(req_id, status="COMPLETED", experiment_id=exp_id, trial_id=trial_id)

        # Get latest cumulative M_total
        cur.execute("SELECT cumulative_m_total FROM trial_ledger WHERE trial_id = ?;", (trial_id,))
        row = cur.fetchone()
        m_total = row[0] if row else 1

        return ControlledExperimentResult(
            experiment_id=exp_id,
            trial_id=trial_id,
            cumulative_m_total=m_total,
            status="SUCCESS",
            metrics=sim_results["raw_metrics"],
            daily_returns=sim_results["daily_returns"],
            sim_results=sim_results,
        )

    def reconcile_interrupted_trials(self) -> int:
        """Reconcile any orphaned trials left in STARTED state from an interrupted process."""
        return self.ledger.reconcile_interrupted_trials()
