"""Binance Autonomous Quant Research Agent Package.

Phase 1 Core Infrastructure:
- Strictly offline quantitative research.
- Immutable data partition and sealed test vault enforcement.
- Append-only research ledger and trial accounting in SQLite.
- Standardized StrategyInterface and SyntheticSMA demonstration.
"""

__version__ = "0.2.0"

from trader.agent.coordinator import ResearchCoordinator
from trader.agent.providers import MockLLMProvider, LocalResearchAssistant
from trader.agent.reporting import ResearchReportCompiler
from trader.agent.schemas import (
    ApprovalTicket,
    AuditEvent,
    ExecutionRequestPayload,
    HypothesisPayload,
    PreregistrationProposalPayload,
    ResearchSessionState,
    SkepticalReviewerOutput,
)

__all__ = [
    "ResearchCoordinator",
    "MockLLMProvider",
    "LocalResearchAssistant",
    "ResearchReportCompiler",
    "HypothesisPayload",
    "SkepticalReviewerOutput",
    "PreregistrationProposalPayload",
    "ApprovalTicket",
    "ExecutionRequestPayload",
    "AuditEvent",
    "ResearchSessionState",
]
