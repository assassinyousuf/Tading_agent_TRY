"""CLI Harness for Phase 2 Autonomous Quant Research Agent.

Commands:
  run-vertical-slice: Formulate hypothesis and proposal up to human approval gate.
  list-tickets: List pending, approved, or rejected review tickets.
  show-ticket: Inspect proposal parameters and submission notes for a ticket.
  approve-ticket: Consecrate human approval and freeze preregistration in SQLite.
  reject-ticket: Reject a proposal ticket and record NO_GO decision.
  authorize-rerun: Authorize a human-signed rerun for an interrupted evaluation.
  list-rerun-authorizations: List active or consumed rerun authorizations.
  resume-session: Resume execution of an approved research session.
  status: Query ledger status, multiple-testing counts, and schema version.
  benchmark: Execute controlled quantitative research agent benchmark.
  db migrate: Explicitly upgrade database schema with backup and validation.
"""

import argparse
import json
import os
import sys

from trader.agent.coordinator import ResearchCoordinator
from trader.agent.core.execution import ResearchExecutionEngine
from trader.agent.db.ledger import ResearchLedger
from trader.agent.db.schema import CURRENT_SCHEMA_VERSION, migrate_database
from trader.agent.providers import MockLLMProvider
from trader.agent.tools.market_bridge import MarketDataProvider


def cmd_status(args: argparse.Namespace) -> None:
    """Display current research ledger status and multiple-testing counts."""
    ledger = ResearchLedger(db_path=args.db_path)
    try:
        version = ledger.get_schema_version()
        counts = ledger.get_trial_counts()
        print("=== Binance Autonomous Quant Research Ledger Status ===")
        print(f"Database Path: {ledger.db_path}")
        print(f"Schema Version: {version}")
        print(f"Cumulative System Trials (M_total): {counts['M_total']}")
        print(f"Evaluated Experiments (M_evaluated): {counts['M_evaluated']}")
        print(f"TEST Partition Queries (M_test): {counts['M_test']}")
        print(f"Pre-Test Rejections (M_rejected): {counts['M_rejected']}")
    finally:
        ledger.close()


def cmd_run_vertical_slice(args: argparse.Namespace) -> None:
    """Execute autonomous quant research loop up to the human approval gate."""
    print("=== Executing Phase 2 Autonomous Quant Research Formulation ===")
    print(f"Objective: {args.objective}")

    os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"

    ledger = ResearchLedger(db_path=args.db_path)
    provider = MarketDataProvider(ledger=ledger)
    engine = ResearchExecutionEngine(ledger=ledger, provider=provider)
    llm = MockLLMProvider()

    coordinator = ResearchCoordinator(
        ledger=ledger,
        provider=provider,
        execution_engine=engine,
        llm_provider=llm,
    )

    try:
        state = coordinator.run_vertical_slice(
            objective=args.objective,
        )

        print("\n=== Research Formulation Completed: Awaiting Human Authorization ===")
        print(f"Session ID: {state.session_id}")
        print(f"Current Stage: {state.current_stage}")

        if state.hypothesis:
            print(f"Hypothesis ID: {state.hypothesis.hypothesis_id}")
            print(f"Strategy Primitive: {state.hypothesis.strategy_name}")

        if state.critique:
            print(f"Skeptical Review: {state.critique.overall_recommendation}")
            print(f"Lookahead Risk Score: {state.critique.lookahead_risk_score}/5")
            print(f"Snooping Risk Score: {state.critique.snooping_risk_score}/5")

        if state.ticket:
            print(f"\n[APPROVAL TICKET CREATED]")
            print(f"Ticket ID: {state.ticket.ticket_id}")
            print(f"Status: {state.ticket.status}")
            print("\nNext Steps:")
            print(f"  1. Inspect ticket:  python -m trader.agent.cli show-ticket {state.ticket.ticket_id}")
            print(f"  2. Approve ticket:  python -m trader.agent.cli approve-ticket {state.ticket.ticket_id} --identity \"<Your Name>\" --rationale \"<Decision Justification>\"")
            print(f"  3. Resume session:  python -m trader.agent.cli resume-session {state.session_id}")

    finally:
        coordinator.close()
        ledger.close()


def cmd_list_tickets(args: argparse.Namespace) -> None:
    """List proposals and approval tickets from persistent SQLite ledger."""
    ledger = ResearchLedger(db_path=args.db_path)
    try:
        tickets = ledger.list_tickets(status=args.status)
        print(f"=== Preregistration Approval Tickets ({len(tickets)} total) ===")
        for t in tickets:
            print(
                f"- Ticket: {t['ticket_id']} | Status: {t['status']:<8} | "
                f"Hypothesis: {t['hypothesis_id']} | Strategy: {t['strategy_name']} | "
                f"Created: {t['created_at'][:19]}"
            )
    finally:
        ledger.close()


def cmd_show_ticket(args: argparse.Namespace) -> None:
    """Inspect detailed proposal configuration for an approval ticket."""
    ledger = ResearchLedger(db_path=args.db_path)
    try:
        t = ledger.get_ticket(args.ticket_id)
        if not t:
            print(f"Error: Ticket '{args.ticket_id}' not found in ledger.")
            sys.exit(1)

        print(f"=== Proposal Review Ticket: {t['ticket_id']} ===")
        print(f"Status: {t['status']}")
        print(f"Hypothesis ID: {t['hypothesis_id']}")
        print(f"Strategy Primitive: {t['strategy_name']}")
        print(f"Target Universe: {t['universe']}")
        print(f"Timeframe: {t['timeframe']}")
        print(f"Parameter Grid: {json.dumps(t['parameter_grid'], indent=2)}")
        print(f"Cost Model: {json.dumps(t['cost_model'])}")
        print(f"Partition Protocol: {json.dumps(t['partition_protocol'])}")
        print(f"Submission Notes: {t['submission_notes']}")
        print(f"Created At: {t['created_at']}")
        print(f"Expires At: {t['expires_at']}")
        if t["approved_by"]:
            print(f"Approved By: {t['approved_by']} at {t['approved_at']}")
            print(f"Decision Rationale: {t['decision_rationale']}")
            print(f"Bound Preregistration ID: {t['prereg_id']}")
    finally:
        ledger.close()


def cmd_approve_ticket(args: argparse.Namespace) -> None:
    """Consecrate human approval for a ticket and freeze preregistration in SQLite."""
    ledger = ResearchLedger(db_path=args.db_path)
    try:
        prereg_id = ledger.approve_proposal_transaction(
            ticket_id=args.ticket_id,
            human_identity=args.identity,
            rationale=args.rationale,
        )
        print("=== Human Approval Consecrated ===")
        print(f"Ticket ID: {args.ticket_id}")
        print(f"Status: APPROVED")
        print(f"Authorized By: {args.identity}")
        print(f"Rationale: {args.rationale}")
        print(f"Frozen Preregistration ID: {prereg_id}")
        print("\nTo execute authorized backtest simulations, run:")
        print(f"  python -m trader.agent.cli resume-session <session-id>")
    finally:
        ledger.close()


def cmd_reject_ticket(args: argparse.Namespace) -> None:
    """Reject a proposal ticket and record NO_GO decision in research_decisions."""
    ledger = ResearchLedger(db_path=args.db_path)
    try:
        ledger.reject_proposal_transaction(
            ticket_id=args.ticket_id,
            human_identity=args.identity,
            rationale=args.rationale,
        )
        print("=== Human Decision: Proposal REJECTED ===")
        print(f"Ticket ID: {args.ticket_id}")
        print(f"Status: REJECTED")
        print(f"Decided By: {args.identity}")
        print(f"Rationale: {args.rationale}")
    finally:
        ledger.close()


def cmd_authorize_rerun(args: argparse.Namespace) -> None:
    """Authorize a human-signed rerun for an interrupted execution request."""
    ledger = ResearchLedger(db_path=args.db_path)
    try:
        auth_record = ledger.authorize_rerun_transaction(
            original_request_id=args.request_id,
            authorized_by=args.identity,
            rationale=args.rationale,
        )
        print("=== Interrupted Evaluation Rerun Authorized ===")
        print(f"Authorization ID: {auth_record['auth_id']}")
        print(f"Original Request ID: {auth_record['original_request_id']}")
        print(f"New Authorized Request ID: {auth_record['new_request_id']}")
        print(f"Hypothesis ID: {auth_record['hypothesis_id']}")
        print(f"Preregistration ID: {auth_record['prereg_id']}")
        print(f"Configuration Digest: {auth_record['configuration_digest']}")
        print(f"Authorized By: {auth_record['authorized_by']}")
        print(f"Rationale: {auth_record['rationale']}")
        print(f"Timestamp: {auth_record['authorized_at']}")
        print("\nTo resume the research session with authorized rerun, execute:")
        print("  python -m trader.agent.cli resume-session <session-id>")
    finally:
        ledger.close()


def cmd_list_rerun_authorizations(args: argparse.Namespace) -> None:
    """List rerun authorizations from persistent SQLite ledger."""
    ledger = ResearchLedger(db_path=args.db_path)
    try:
        is_consumed = None
        if args.status == "ACTIVE":
            is_consumed = 0
        elif args.status == "CONSUMED":
            is_consumed = 1
        auths = ledger.list_rerun_authorizations(is_consumed=is_consumed)
        print(f"=== Interrupted Evaluation Rerun Authorizations ({len(auths)} total) ===")
        for a in auths:
            status_str = "CONSUMED" if a["is_consumed"] else "ACTIVE"
            print(
                f"- Auth: {a['auth_id']} | Status: {status_str:<8} | "
                f"Orig: {a['original_request_id']} -> New: {a['new_request_id']} | "
                f"By: {a['authorized_by']} | At: {a['authorized_at'][:19]}"
            )
    finally:
        ledger.close()


def cmd_resume_session(args: argparse.Namespace) -> None:
    """Resume execution of an approved research session."""
    print(f"=== Resuming Research Session: {args.session_id} ===")
    os.environ["PHASE2_VERTICAL_SLICE_TRAIN_ONLY"] = "1"

    ledger = ResearchLedger(db_path=args.db_path)
    provider = MarketDataProvider(ledger=ledger)
    engine = ResearchExecutionEngine(ledger=ledger, provider=provider)
    llm = MockLLMProvider()

    coordinator = ResearchCoordinator(
        ledger=ledger,
        provider=provider,
        execution_engine=engine,
        llm_provider=llm,
    )

    try:
        state = coordinator.resume_session(session_id=args.session_id)
        print("\n=== Research Session Execution Completed ===")
        print(f"Session ID: {state.session_id}")
        print(f"Final Stage: {state.current_stage}")
        print(f"Completed Successfully: {state.is_completed}")
        print(f"Frozen Preregistration ID: {state.prereg_id}")
        print(f"Executed Experiments: {state.experiment_ids}")
        print(f"Compiled Report: {state.report_path}")
        print(f"Audit Trail Events: {len(state.audit_events)}")
    finally:
        coordinator.close()
        ledger.close()


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Execute controlled research agent benchmark comparing LLM configurations."""
    print("=== Phase 2.4 Research Agent Benchmark ===")
    from trader.agent.benchmark.harness import BenchmarkHarness
    from trader.agent.benchmark.mock_provider import BenchmarkMockLLMProvider
    from trader.agent.benchmark.repeated_runner import RepeatedBenchmarkRunner
    from trader.agent.providers.local import LocalResearchAssistant
    from trader.agent.providers.base import LLMProvider

    provider: LLMProvider
    if args.provider == "local":
        provider = LocalResearchAssistant(
            endpoint_url=args.endpoint_url,
            model_identifier=args.model,
            temperature=args.temperature,
            timeout_seconds=args.timeout,
            max_tokens=args.max_tokens,
        )
        print(f"Using Local LLM Endpoint: {args.endpoint_url} (model: {args.model}, temp: {args.temperature})")
    else:
        provider = BenchmarkMockLLMProvider()
        print("Using Deterministic Benchmark Mock Provider")

    configs = [args.config] if args.config != "ALL" else ["SINGLE_RESEARCHER", "RESEARCHER_REVIEWER", "FULL_COORDINATOR"]

    if getattr(args, "repeated", False):
        print(f"\n--- Running Repeated Multi-Temperature Evaluation ({args.repetitions} reps per temp) ---")

        def p_factory(temp: float) -> LLMProvider:
            if args.provider == "local":
                return LocalResearchAssistant(
                    endpoint_url=args.endpoint_url,
                    model_identifier=args.model,
                    temperature=temp,
                    timeout_seconds=args.timeout,
                    max_tokens=args.max_tokens,
                )
            return BenchmarkMockLLMProvider()

        runner = RepeatedBenchmarkRunner(
            provider_factory=p_factory,
            temperatures=[0.0, 0.2, 0.5, 0.8],
            repetitions_per_temp=args.repetitions,
            configurations=configs,
            results_dir=args.output_dir,
        )
        res = runner.run_repeated_evaluation(expanded=args.expanded)
        print(f"\nRepeated Benchmark Complete. Summary saved to: {res['persisted_path']}")
        print("\n=== Aggregated Results by (Config, Temperature) ===")
        for key, agg in res["aggregated_by_temp_and_config"].items():
            print(f"\n{key}:")
            print(f"  Score: {agg['overall_score']['mean']} +/- {agg['overall_score']['std']}")
            print(f"  Recall: {agg['rejection_recall']['mean']}% +/- {agg['rejection_recall']['std']}%")
            print(f"  Precision: {agg['rejection_precision']['mean']}% +/- {agg['rejection_precision']['std']}%")
            print(f"  False Rejection Rate: {agg['false_rejection_rate']['mean']}% +/- {agg['false_rejection_rate']['std']}%")
            print(f"  Tokens: {agg['tokens']['mean']} +/- {agg['tokens']['std']}")
            print(f"  Quality / 1k Tokens: {agg['cost_effectiveness']['quality_score_per_1k_tokens']}")
            print(f"  Quality / Second: {agg['cost_effectiveness']['quality_score_per_second']}")
        return

    harness = BenchmarkHarness(provider=provider, results_dir=args.output_dir)
    summaries = harness.run_comparative_benchmark(configs=configs, expanded=args.expanded)
    print("\n=== Benchmark Evaluation Results ===")
    for cfg, s in summaries.items():
        print(f"\nConfiguration: {cfg}")
        print(f"  Total Tasks: {s.total_tasks}")
        print(f"  Mean Overall Score: {s.mean_overall_score}/100")
        print(f"  Rejection Precision: {s.rejection_precision}%")
        print(f"  Rejection Recall: {s.rejection_recall}%")
        print(f"  Total Tokens: {s.total_tokens}")
        print(f"  Mean Latency: {s.mean_latency_ms} ms")
        print("  Dimension Scores:")
        for dim, score in s.dimension_means.items():
            print(f"    - {dim}: {score}")


def cmd_db_migrate(args: argparse.Namespace) -> None:
    """Explicitly upgrade research ledger database schema with backup and validation."""
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    default_db = os.path.join(root_dir, "research_ledger.db")
    db_path = args.db_path or default_db

    print(f"=== Explicit Database Schema Migration ===")
    print(f"Target Database: {db_path}")
    print(f"Target Schema Version: v{CURRENT_SCHEMA_VERSION}")

    res = migrate_database(db_path=db_path, target_version=CURRENT_SCHEMA_VERSION)
    print(f"Status: {res['status']}")
    print(f"Message: {res['message']}")
    if "backup_path" in res:
        print(f"Verified Backup: {res['backup_path']}")
        print(f"Tables Verified: {', '.join(res['tables_verified'])}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI interface for Binance Autonomous Quant Research Agent."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status command
    p_status = subparsers.add_parser("status", help="Inspect research ledger and trial counts.")
    p_status.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_status.set_defaults(func=cmd_status)

    # run-vertical-slice command
    p_run = subparsers.add_parser(
        "run-vertical-slice",
        help="Run autonomous quant research formulation up to human approval gate.",
    )
    p_run.add_argument(
        "--objective",
        default="Evaluate SyntheticSMAStrategy momentum on BTCUSDT Daily TRAIN data.",
        help="Quantitative research objective mandate",
    )
    p_run.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_run.set_defaults(func=cmd_run_vertical_slice)

    # list-tickets command
    p_list = subparsers.add_parser("list-tickets", help="List proposal review tickets.")
    p_list.add_argument("--status", default=None, choices=["PENDING", "APPROVED", "REJECTED", "EXPIRED"])
    p_list.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_list.set_defaults(func=cmd_list_tickets)

    # show-ticket command
    p_show = subparsers.add_parser("show-ticket", help="Inspect detailed proposal configuration.")
    p_show.add_argument("ticket_id", help="Ticket ID (e.g. TICK-XXXXXXXX)")
    p_show.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_show.set_defaults(func=cmd_show_ticket)

    # approve-ticket command
    p_app = subparsers.add_parser("approve-ticket", help="Consecrate human approval for a ticket.")
    p_app.add_argument("ticket_id", help="Ticket ID to approve")
    p_app.add_argument("--identity", required=True, help="Human Director identity name")
    p_app.add_argument("--rationale", required=True, help="Decision justification rationale")
    p_app.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_app.set_defaults(func=cmd_approve_ticket)

    # reject-ticket command
    p_rej = subparsers.add_parser("reject-ticket", help="Reject a proposal ticket.")
    p_rej.add_argument("ticket_id", help="Ticket ID to reject")
    p_rej.add_argument("--identity", required=True, help="Human Director identity name")
    p_rej.add_argument("--rationale", required=True, help="Rejection justification rationale")
    p_rej.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_rej.set_defaults(func=cmd_reject_ticket)

    # authorize-rerun command
    p_authr = subparsers.add_parser("authorize-rerun", help="Authorize a human-signed rerun for an interrupted evaluation.")
    p_authr.add_argument("request_id", help="Original interrupted request ID (e.g. REQ-SESS-01)")
    p_authr.add_argument("--identity", required=True, help="Human Director identity name")
    p_authr.add_argument("--rationale", required=True, help="Decision justification rationale for rerun")
    p_authr.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_authr.set_defaults(func=cmd_authorize_rerun)

    # list-rerun-authorizations command
    p_lra = subparsers.add_parser("list-rerun-authorizations", help="List rerun authorizations.")
    p_lra.add_argument("--status", default=None, choices=["ACTIVE", "CONSUMED"])
    p_lra.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_lra.set_defaults(func=cmd_list_rerun_authorizations)

    # resume-session command
    p_res = subparsers.add_parser("resume-session", help="Resume execution of an approved session.")
    p_res.add_argument("session_id", help="Session ID to resume")
    p_res.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_res.set_defaults(func=cmd_resume_session)

    # benchmark command
    p_bench = subparsers.add_parser("benchmark", help="Execute controlled research agent benchmark.")
    p_bench.add_argument(
        "--config",
        default="ALL",
        choices=["ALL", "SINGLE_RESEARCHER", "RESEARCHER_REVIEWER", "FULL_COORDINATOR"],
        help="Configuration to evaluate",
    )
    p_bench.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "local"],
        help="Inference provider: deterministic mock or local HTTP assistant",
    )
    p_bench.add_argument(
        "--endpoint-url",
        default="http://127.0.0.1:11434/v1/chat/completions",
        help="Local LLM endpoint URL (e.g. Ollama or vLLM)",
    )
    p_bench.add_argument(
        "--model",
        default="local-research-assistant",
        help="Model identifier",
    )
    p_bench.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature",
    )
    p_bench.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds",
    )
    p_bench.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Max output tokens",
    )
    p_bench.add_argument(
        "--expanded",
        action="store_true",
        help="Run expanded 14-task benchmark suite",
    )
    p_bench.add_argument(
        "--repeated",
        action="store_true",
        help="Run repeated multi-temperature evaluation suite",
    )
    p_bench.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Repetitions per temperature for repeated evaluation",
    )
    p_bench.add_argument("--output-dir", default=None, help="Directory to persist benchmark results")
    p_bench.set_defaults(func=cmd_benchmark)

    # db subcommand
    p_db = subparsers.add_parser("db", help="Database administration and schema migration.")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)
    p_mig = db_sub.add_parser("migrate", help="Explicitly upgrade schema with verified backup.")
    p_mig.add_argument("--db-path", default=None, help="Path to SQLite research_ledger.db")
    p_mig.set_defaults(func=cmd_db_migrate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
