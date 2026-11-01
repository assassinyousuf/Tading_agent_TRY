"""Configuration C: Full Research Coordinator runner for Phase 2.3 Benchmark.

Enforces:
1. Multi-stage quantitative research orchestration via ResearchCoordinator.
2. Market analysis strictly on approved TRAIN partition data.
3. Automated skeptical review critique gate.
4. Persistent proposal and approval ticket generation in SQLite.
5. Session state checkpointing and immutable audit telemetry.
6. Preserves human approval gate: halts at HUMAN_APPROVAL_GATE without automatic execution.
7. Deterministic scoring via BenchmarkScorer.
"""

import hashlib
import json
import os
import shutil
import tempfile
import time
from typing import Any, Dict, Optional

from trader.agent.benchmark.schema import (
    BenchmarkTask,
    DimensionScores,
    TaskRunResult,
)
from trader.agent.benchmark.scorer import BenchmarkScorer
from trader.agent.coordinator import (
    ResearchCoordinator,
    SkepticalReviewRejectionError,
)
from trader.agent.core.execution import ResearchExecutionEngine
from trader.agent.db.ledger import ResearchLedger
from trader.agent.providers.base import LLMProvider
from trader.agent.tools.market_bridge import MarketDataProvider


class FullCoordinatorBenchmarkRunner:
    """Benchmark evaluation runner for Configuration C: Full Research Coordinator."""

    def __init__(
        self,
        provider: LLMProvider,
        db_path: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.config_id = "FULL_COORDINATOR"
        self.db_path = db_path

    def run_task(self, task: BenchmarkTask) -> TaskRunResult:
        """Execute a benchmark task through the multi-stage research coordinator."""
        start_time = time.perf_counter()
        prompt_str = task.objective_prompt
        prompt_sha = hashlib.sha256(prompt_str.encode("utf-8")).hexdigest()
        dataset_digest = hashlib.sha256(json.dumps(task.market_context, sort_keys=True).encode("utf-8")).hexdigest()

        # Isolate database for benchmark task run
        temp_dir = tempfile.mkdtemp()
        task_db_path = os.path.join(temp_dir, f"coord_task_{task.task_id}.db")
        lock_path = os.path.join(temp_dir, f"coord_task_{task.task_id}.lock")

        ledger = ResearchLedger(db_path=task_db_path)
        data_provider = MarketDataProvider(ledger=ledger)
        engine = ResearchExecutionEngine(ledger=ledger, provider=data_provider)

        coordinator = ResearchCoordinator(
            ledger=ledger,
            provider=data_provider,
            execution_engine=engine,
            llm_provider=self.provider,
            lock_path=lock_path,
        )

        error_msg: Optional[str] = None
        decision = "FAILED"
        decision_rationale: Optional[str] = None
        raw_hypothesis: Optional[Dict[str, Any]] = None
        raw_proposal: Optional[Dict[str, Any]] = None
        raw_critique: Optional[Dict[str, Any]] = None

        total_prompt_tokens = 0
        total_completion_tokens = 0
        start_call_count = len(getattr(self.provider, "call_history", []))

        try:
            # Execute coordinator workflow up to human approval gate
            os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"
            session_state = coordinator.run_vertical_slice(
                objective=f"Task ID: {task.task_id}\nMandate: {task.objective_prompt}",
            )

            # Extract generated artifacts
            if session_state.hypothesis:
                raw_hypothesis = session_state.hypothesis.model_dump()
            if session_state.critique:
                raw_critique = session_state.critique.model_dump()
            if session_state.proposal:
                raw_proposal = session_state.proposal.model_dump()

            if session_state.ticket:
                decision = "PENDING_APPROVAL"
                decision_rationale = (
                    f"Coordinator halted at HUMAN_APPROVAL_GATE. Ticket {session_state.ticket.ticket_id} "
                    f"created in SQLite with proposal {session_state.proposal.proposal_id}."
                )

        except SkepticalReviewRejectionError as exc:
            decision = "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in str(exc) else "REJECTED"
            error_msg = str(exc)
            decision_rationale = f"Coordinator halted proposal at skeptical review gate: {str(exc)}"
            # Retrieve critique from coordinator if available
            if hasattr(coordinator, "pending_proposals"):
                pass
            # Query session state from ledger
            sessions = ledger.conn.cursor().execute("SELECT session_state_json FROM research_sessions ORDER BY created_at DESC LIMIT 1;").fetchone()
            if sessions and sessions[0]:
                saved_state = json.loads(sessions[0])
                raw_hypothesis = saved_state.get("hypothesis")
                raw_critique = saved_state.get("critique")

        except Exception as exc:
            decision = "FAILED"
            error_msg = f"Coordinator execution exception: {str(exc)}"
            decision_rationale = error_msg

        finally:
            coordinator.close()
            ledger.close()
            shutil.rmtree(temp_dir, ignore_errors=True)
            os.environ.pop("PHASE2_VERTICAL_SLICE_TRAIN_ONLY", None)

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Sum tokens incurred specifically during this task run (delta accounting)
        current_history = getattr(self.provider, "call_history", [])
        task_calls = current_history[start_call_count:]
        for call in task_calls:
            resp = call.get("response")
            if resp:
                total_prompt_tokens += resp.prompt_tokens
                total_completion_tokens += resp.completion_tokens

        artifact_dict = {
            "hypothesis": raw_hypothesis,
            "critique": raw_critique,
            "proposal": raw_proposal,
            "decision": decision,
        }
        artifact_json = json.dumps(artifact_dict, sort_keys=True)
        artifact_digest = hashlib.sha256(artifact_json.encode("utf-8")).hexdigest()

        scores = BenchmarkScorer.evaluate(
            task=task,
            decision=decision,
            hypothesis=raw_hypothesis,
            proposal=raw_proposal,
            critique=raw_critique,
            error_message=error_msg,
        )

        return TaskRunResult(
            task_id=task.task_id,
            config_id=self.config_id,
            model_identifier=self.provider.model_identifier,
            prompt_sha256=prompt_sha,
            dataset_digest=dataset_digest,
            artifact_digest=artifact_digest,
            decision=decision,
            decision_rationale=decision_rationale,
            raw_hypothesis=raw_hypothesis,
            raw_proposal=raw_proposal,
            reviewer_critique=raw_critique,
            scores=scores,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            latency_ms=round(latency_ms, 3),
            error_message=error_msg,
        )

