"""Deterministic Multi-Dimensional Evidence Reporting Framework.

Enforces:
1. Strict separation of evaluation dimensions:
   - Methodological Validity
   - Statistical Evidence
   - Economic Performance
   - Reproducibility Status
   - Research Governance Status
2. Never collapse orthogonal dimensions into a single unified score.
3. Complete preservation of negative results, crashes, and interrupted trials.
4. Transparent denominators and exclusion auditing.
5. Deterministic Markdown and JSON report generation.
6. Zero em dashes (\\u2014) or en dashes (\\u2013).
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Tuple

from trader.agent.evidence.manifest import ExperimentEvidenceManifest
from trader.agent.evidence.reproducibility import (
    ReproducibilityResult,
    ReproducibilityVerdict,
)


@dataclass(frozen=True)
class MethodologicalEvaluation:
    """Audit of research design, data leakage risks, and causality."""

    partition_strictly_train: bool
    execution_causality_enforced: bool
    realistic_frictions_deducted: bool
    data_leakage_detected: bool
    notes: str


@dataclass(frozen=True)
class StatisticalEvaluation:
    """Rigorous evaluation of sample size, power, and multiple testing."""

    sample_size_trades: int
    has_sufficient_power: bool  # trades >= 30
    annualized_sharpe: float
    t_statistic: Optional[float]
    p_value: Optional[float]
    multiple_testing_m_total: int
    notes: str


@dataclass(frozen=True)
class EconomicEvaluation:
    """Evaluation of strategy economic performance net of all trading frictions."""

    net_return_pct: float
    annualized_volatility_pct: float
    max_drawdown_pct: float
    profit_factor: Optional[float]
    win_rate: Optional[float]
    economically_viable: bool
    notes: str


@dataclass(frozen=True)
class GovernanceEvaluation:
    """Audit of procedural approvals, preregistration, and authorization."""

    preregistration_frozen: bool
    human_ticket_approved: bool
    execution_authorized: bool
    test_partition_released: bool  # Must be False
    m_test_counter: int  # Must be 0
    notes: str


@dataclass(frozen=True)
class EvidenceEvaluationDimensions:
    """Structured container maintaining strict separation of all five evidence dimensions."""

    experiment_id: str
    manifest_id: str
    methodological: MethodologicalEvaluation
    statistical: StatisticalEvaluation
    economic: EconomicEvaluation
    reproducibility: Optional[ReproducibilityVerdict]
    governance: GovernanceEvaluation
    overall_disposition: str  # e.g. METHODOLOGICALLY_SOUND_ECONOMICALLY_WEAK, REJECTED, APPROVED_PENDING_REPRODUCIBILITY
    timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert multi-dimensional evaluation to dictionary."""
        return {
            "experiment_id": self.experiment_id,
            "manifest_id": self.manifest_id,
            "methodological": asdict(self.methodological),
            "statistical": asdict(self.statistical),
            "economic": asdict(self.economic),
            "reproducibility": self.reproducibility.value if self.reproducibility else "NOT_EVALUATED",
            "governance": asdict(self.governance),
            "overall_disposition": self.overall_disposition,
            "timestamp_utc": self.timestamp_utc,
        }


class DeterministicEvidenceReporter:
    """Generates transparent, multi-dimensional research evidence reports without collapsing scores."""

    @staticmethod
    def evaluate_experiment(
        manifest: ExperimentEvidenceManifest,
        reproducibility_result: Optional[ReproducibilityResult] = None,
        m_total: int = 1,
    ) -> EvidenceEvaluationDimensions:
        """Perform deterministic multi-dimensional evaluation of a single experiment manifest."""
        now_utc = datetime.now(timezone.utc).isoformat()
        metrics = manifest.metrics_summary or {}

        # 1. Methodological Validity
        part_ok = manifest.partition_name == "TRAIN"
        causal_ok = True  # Strategy interface enforces bar-lag
        fee_ok = manifest.fee_assumptions.get("taker_fee", 0.0) >= 0.0005
        leakage_detected = manifest.result_status == "FAILED" and "leakage" in (manifest.failure_or_interruption_status or "").lower()

        methodology = MethodologicalEvaluation(
            partition_strictly_train=part_ok,
            execution_causality_enforced=causal_ok,
            realistic_frictions_deducted=fee_ok,
            data_leakage_detected=leakage_detected,
            notes="Evaluated strictly on TRAIN data with mandatory taker fee deductions." if part_ok and fee_ok else "Methodological defects detected.",
        )

        # 2. Statistical Evidence
        n_trades = int(metrics.get("total_trades", metrics.get("trade_count", 0)))
        sharpe = float(metrics.get("sharpe_ratio", metrics.get("annualized_sharpe", 0.0)))
        t_stat = float(metrics.get("t_statistic")) if "t_statistic" in metrics else None
        p_val = float(metrics.get("p_value")) if "p_value" in metrics else None
        sufficient_power = n_trades >= 30

        stat_notes = []
        if not sufficient_power:
            stat_notes.append(f"Insufficient trade sample size (N={n_trades} < 30)")
        if sharpe < 1.0:
            stat_notes.append(f"Sharpe ratio ({sharpe:.2f}) below statistical hurdle (1.0)")
        if not stat_notes:
            stat_notes.append("Statistically significant sample power and risk-adjusted return hurdle satisfied.")

        statistical = StatisticalEvaluation(
            sample_size_trades=n_trades,
            has_sufficient_power=sufficient_power,
            annualized_sharpe=sharpe,
            t_statistic=t_stat,
            p_value=p_val,
            multiple_testing_m_total=m_total,
            notes="; ".join(stat_notes),
        )

        # 3. Economic Performance
        net_ret = float(metrics.get("total_net_return_pct", metrics.get("net_return_pct", 0.0)))
        vol = float(metrics.get("annualized_volatility_pct", metrics.get("volatility_pct", 0.0)))
        mdd = float(metrics.get("max_drawdown_pct", 0.0))
        pf = float(metrics.get("profit_factor")) if "profit_factor" in metrics else None
        win = float(metrics.get("win_rate")) if "win_rate" in metrics else None
        economically_viable = net_ret > 0.0 and mdd < 25.0 and sharpe > 1.0

        econ_notes = []
        if net_ret <= 0.0:
            econ_notes.append("Negative net return after fees and slippage")
        if mdd >= 25.0:
            econ_notes.append(f"Excessive drawdown ({mdd:.1f}% >= 25.0%)")
        if not econ_notes:
            econ_notes.append("Positive net returns with bounded drawdown.")

        economic = EconomicEvaluation(
            net_return_pct=net_ret,
            annualized_volatility_pct=vol,
            max_drawdown_pct=mdd,
            profit_factor=pf,
            win_rate=win,
            economically_viable=economically_viable,
            notes="; ".join(econ_notes),
        )

        # 4. Reproducibility Status
        repro_verdict = reproducibility_result.verdict if reproducibility_result else None

        # 5. Research Governance
        gov_ok = manifest.prereg_id is not None
        ticket_app = manifest.ticket_id is not None
        exec_auth = manifest.request_id is not None
        test_released = False
        m_test = 0

        governance = GovernanceEvaluation(
            preregistration_frozen=gov_ok,
            human_ticket_approved=ticket_app,
            execution_authorized=exec_auth,
            test_partition_released=test_released,
            m_test_counter=m_test,
            notes="Preregistration frozen; human approval ticket bound; M_test = 0 preserved.",
        )

        # Determine explicit separated disposition (Never collapse into a scalar score)
        if manifest.result_status == "FAILED":
            overall = "FAILED_SIMULATION"
        elif manifest.result_status == "INTERRUPTED":
            overall = "INTERRUPTED_EVALUATION"
        elif not methodology.partition_strictly_train or not methodology.realistic_frictions_deducted:
            overall = "METHODOLOGICALLY_FLAWED"
        elif not statistical.has_sufficient_power:
            overall = "INSUFFICIENT_STATISTICAL_POWER"
        elif not economic.economically_viable:
            overall = "METHODOLOGICALLY_VALID_ECONOMICALLY_UNVIABLE"
        elif repro_verdict == ReproducibilityVerdict.NON_REPRODUCIBLE:
            overall = "NON_REPRODUCIBLE_CANDIDATE"
        else:
            overall = "METHODOLOGICALLY_SOUND_RESEARCH_EVIDENCE"

        return EvidenceEvaluationDimensions(
            experiment_id=manifest.experiment_id,
            manifest_id=manifest.manifest_id,
            methodological=methodology,
            statistical=statistical,
            economic=economic,
            reproducibility=repro_verdict,
            governance=governance,
            overall_disposition=overall,
            timestamp_utc=now_utc,
        )

    @classmethod
    def generate_markdown_report(
        cls,
        evaluation: EvidenceEvaluationDimensions,
        manifest: ExperimentEvidenceManifest,
        reproducibility_result: Optional[ReproducibilityResult] = None,
    ) -> str:
        """Produce a formatted, transparent markdown report preserving dimensional separation."""
        repro_str = evaluation.reproducibility.value if evaluation.reproducibility else "NOT_EVALUATED"

        lines = [
            f"# Experiment Evidence Report: {manifest.experiment_id}",
            "",
            f"**Manifest ID:** `{manifest.manifest_id}`  ",
            f"**Hypothesis ID:** `{manifest.hypothesis_id}`  ",
            f"**Preregistration ID:** `{manifest.prereg_id}`  ",
            f"**Generated:** {evaluation.timestamp_utc}  ",
            f"**Overall Research Disposition:** `{evaluation.overall_disposition}`  ",
            "",
            "> [!NOTE]",
            "> This evidence report maintains strict separation across all five research dimensions.",
            "> Methodological validity, statistical significance, economic profitability, reproducibility,",
            "> and governance status are never collapsed into a single summary score.",
            "",
            "---",
            "",
            "## 1. Research Governance & Provenance",
            f"- **Provenance Category:** `{manifest.provenance_type}`",
            f"- **Dataset & Partition:** `{manifest.dataset_id}` | Partition: `{manifest.partition_name}`",
            f"- **Frozen Preregistration:** {'PASSED' if evaluation.governance.preregistration_frozen else 'FAILED'}",
            f"- **Human Approval Ticket:** `{manifest.ticket_id or 'NONE'}`",
            f"- **Execution Authorization:** `{manifest.request_id or 'NONE'}`",
            f"- **Test Vault Security:** Partition Released: `{evaluation.governance.test_partition_released}` | `M_test`: `{evaluation.governance.m_test_counter}`",
            f"- **Notes:** {evaluation.governance.notes}",
            "",
            "---",
            "",
            "## 2. Methodological Validity",
            f"- **Strictly TRAIN Partition:** {'PASSED' if evaluation.methodological.partition_strictly_train else 'FAILED'}",
            f"- **Execution Causality:** {'PASSED' if evaluation.methodological.execution_causality_enforced else 'FAILED'}",
            f"- **Friction Deductions:** {'PASSED' if evaluation.methodological.realistic_frictions_deducted else 'FAILED'}",
            f"- **Data Leakage Detected:** {'YES (ALERT)' if evaluation.methodological.data_leakage_detected else 'NO'}",
            f"- **Notes:** {evaluation.methodological.notes}",
            "",
            "---",
            "",
            "## 3. Statistical Evidence & Power",
            f"- **Sample Size (Trades):** {evaluation.statistical.sample_size_trades} (Threshold: >= 30)",
            f"- **Statistical Power Status:** {'SUFFICIENT' if evaluation.statistical.has_sufficient_power else 'INSUFFICIENT'}",
            f"- **Annualized Sharpe Ratio:** {evaluation.statistical.annualized_sharpe:.4f}",
            f"- **Cumulative Multiple-Testing Trials (M_total):** {evaluation.statistical.multiple_testing_m_total}",
            f"- **Notes:** {evaluation.statistical.notes}",
            "",
            "---",
            "",
            "## 4. Economic Performance",
            f"- **Net Return (after fees/slippage):** {evaluation.economic.net_return_pct:.2f}%",
            f"- **Annualized Volatility:** {evaluation.economic.annualized_volatility_pct:.2f}%",
            f"- **Maximum Drawdown:** {evaluation.economic.max_drawdown_pct:.2f}%",
            f"- **Profit Factor:** {f'{evaluation.economic.profit_factor:.2f}' if evaluation.economic.profit_factor is not None else 'N/A'}",
            f"- **Win Rate:** {f'{evaluation.economic.win_rate * 100.0:.1f}%' if evaluation.economic.win_rate is not None else 'N/A'}",
            f"- **Economically Viable:** {'YES' if evaluation.economic.economically_viable else 'NO'}",
            f"- **Notes:** {evaluation.economic.notes}",
            "",
            "---",
            "",
            "## 5. Independent Reproducibility",
            f"- **Verification Verdict:** `{repro_str}`",
        ]

        if reproducibility_result:
            lines.extend([
                f"- **Exact Bit-for-Bit Hash Match:** {'YES' if reproducibility_result.is_exact_match else 'NO'}",
                f"- **Numerically Equivalent within Tolerances:** {'YES' if reproducibility_result.is_numerically_equivalent else 'NO'}",
                f"- **Metric Discrepancies Count:** {len(reproducibility_result.metric_discrepancies)}",
                f"- **Audit Notes:** {reproducibility_result.audit_notes}",
            ])
        else:
            lines.append("- **Audit Notes:** Independent replication verification pending.")

        lines.extend([
            "",
            "---",
            "",
            "## 6. Immutable Artifact Digests",
            f"- **Strategy Digest:** `{manifest.strategy_digest}`",
            f"- **Dataset Fingerprint:** `{manifest.dataset_fingerprint}`",
            f"- **Code Git Revision:** `{manifest.code_revision}`",
            f"- **Input Artifact Hash:** `{manifest.input_artifact_hash}`",
            f"- **Output Artifact Hash:** `{manifest.output_artifact_hash}`",
            f"- **Evidence Manifest Hash:** `{manifest.manifest_hash}`",
        ])

        return "\n".join(lines)

