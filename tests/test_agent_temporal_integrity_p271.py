"""Deterministic Temporal Integrity and Lookahead Leakage Detection Test Suite.

Phase 2.7.1 Verification Suite.
Validates:
1. Deterministic detection of negative shift (.shift(-k)).
2. Centered rolling window (.rolling(..., center=True)).
3. Past-only rolling window (.rolling(..., center=False)).
4. Expanding calculation ending at decision time.
5. Full-sample normalization.
6. Train-only frozen normalization.
7. Full-sample cointegration estimation (Johansen on full sample, TASK-52).
8. Past-only rolling cointegration estimation.
9. Same-close signal evaluation and execution without latency model.
10. Next-bar execution with valid timing metadata.
11. Forward fill crossing decision-time boundary.
12. Timezone mismatch detection.
13. Duplicate timestamps with ambiguous ordering.
14. Future observation supplied to runtime calculation.
15. Missing availability timestamps.
16. Unrecognized or unsupported transformation.
17. LLM attempt to override deterministic FAIL/REJECT blocked.
18. Idempotency and reproducible input digests.
19. Coordinator stage 2.5 integration and ledger persistence.
20. SQLite immutability triggers on temporal_integrity_audits table.
21. False-positive and false-negative resistance.

Strict invariant: 0 em dashes (\u2014) and 0 en dashes (\u2013).
"""

from datetime import datetime, timezone
import json
import os
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional
import unittest

from trader.agent.benchmark.canonical_registry import lookup_canonical_task
from trader.agent.coordinator import (
    ResearchCoordinator,
    SkepticalReviewRejectionError,
)
from trader.agent.core.execution import ResearchExecutionEngine
from trader.agent.db.ledger import ResearchLedger
from trader.agent.providers.base import LLMProvider, LLMResponse
from trader.agent.schemas import HypothesisPayload, SkepticalReviewerOutput
from trader.agent.tools.market_bridge import MarketDataProvider
from trader.agent.validation.temporal_integrity import (
    ConfidenceClassification,
    DeterministicOverrideBlockedError,
    TemporalDisposition,
    TemporalFindingCategory,
    TemporalFindingSeverity,
    TemporalIntegrityAuditReport,
    TemporalIntegrityDetector,
    TemporalIntegrityFinding,
    TemporalIntegrityViolationError,
    TemporalResultState,
)


class MockLLMProvider(LLMProvider):
    """Deterministic mock LLM provider for coordinator integration testing."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        super().__init__(name="mock_provider", model_identifier="mock-p271-evaluator")
        self.responses = list(responses)
        self.call_count = 0

    def generate(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[type] = None,
        system_instruction: Optional[str] = None,
    ) -> LLMResponse:
        self.call_count += 1
        if not self.responses:
            raise RuntimeError("MockLLMProvider exhausted responses.")
        next_data = self.responses.pop(0)

        validated_data = None
        if schema_class is not None:
            obj = schema_class.model_validate(next_data)
            validated_data = obj.model_dump()
        else:
            validated_data = next_data

        content = json.dumps(validated_data, indent=2)
        return LLMResponse(
            content=content,
            structured_data=validated_data,
            prompt_tokens=50,
            completion_tokens=50,
            latency_ms=5.0,
            prompt_sha256=self.compute_sha256(prompt),
            response_sha256=self.compute_sha256(content),
            provider_name=self.name,
            model_identifier=self.model_identifier,
        )


class TestTemporalIntegrityDetector(unittest.TestCase):
    """Authoritative test suite for Phase 2.7.1 temporal integrity detector."""

    def setUp(self) -> None:
        self.detector = TemporalIntegrityDetector()

    # -------------------------------------------------------------------------
    # TI-01: Negative Shift in Signal Calculation
    # -------------------------------------------------------------------------
    def test_ti_01_negative_shift_ast_detection(self) -> None:
        """TI-01: Negative shift in signal calculation (.shift(-1)) yields FAIL and REJECT."""
        source = (
            "import pandas as pd\n\n"
            "def calculate_signals(df: pd.DataFrame) -> pd.Series:\n"
            "    future_close = df['close'].shift(-1)\n"
            "    return (future_close > df['close']).astype(int)\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="TI-01")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        self.assertGreaterEqual(report.total_findings, 1)

        shift_findings = [f for f in report.findings if f.rule_id == "RULE-TI-01-FUTURE-SHIFT"]
        self.assertEqual(len(shift_findings), 1)
        finding = shift_findings[0]
        self.assertEqual(finding.result_state, TemporalResultState.FAIL)
        self.assertTrue(finding.is_definitive)
        self.assertEqual(finding.severity, TemporalFindingSeverity.CRITICAL)
        self.assertEqual(finding.confidence, ConfidenceClassification.DEFINITIVE)
        self.assertIn("shift(-1)", finding.title)

    # -------------------------------------------------------------------------
    # TI-02: Centered Rolling Window
    # -------------------------------------------------------------------------
    def test_ti_02_centered_rolling_window_ast_detection(self) -> None:
        """TI-02: Centered rolling window (center=True) yields FAIL and REJECT."""
        source = (
            "import pandas as pd\n\n"
            "def compute_indicator(df: pd.DataFrame) -> pd.Series:\n"
            "    return df['price'].rolling(window=20, center=True).mean()\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="TI-02")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)

        center_findings = [f for f in report.findings if f.rule_id == "RULE-TI-02-CENTERED-WINDOW"]
        self.assertEqual(len(center_findings), 1)
        finding = center_findings[0]
        self.assertEqual(finding.result_state, TemporalResultState.FAIL)
        self.assertTrue(finding.is_definitive)
        self.assertEqual(finding.severity, TemporalFindingSeverity.CRITICAL)

    # -------------------------------------------------------------------------
    # TI-03: Past-Only Rolling Calculation
    # -------------------------------------------------------------------------
    def test_ti_03_past_only_rolling_window_clean(self) -> None:
        """TI-03: Past-only rolling window (center=False, past-only) yields ELIGIBLE_FOR_REVIEW."""
        source = (
            "import pandas as pd\n\n"
            "def compute_indicator(df: pd.DataFrame) -> pd.Series:\n"
            "    fast_ma = df['close'].rolling(window=10, center=False).mean()\n"
            "    slow_ma = df['close'].rolling(window=30).mean()\n"
            "    return (fast_ma > slow_ma).astype(int)\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="TI-03")
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        self.assertEqual(report.total_findings, 0)

    # -------------------------------------------------------------------------
    # TI-04: Expanding Calculation Ending at Decision Time
    # -------------------------------------------------------------------------
    def test_ti_04_expanding_calculation_clean(self) -> None:
        """TI-04: Expanding calculation ending at decision time yields ELIGIBLE_FOR_REVIEW."""
        source = (
            "import pandas as pd\n\n"
            "def compute_expanding_mean(df: pd.DataFrame) -> pd.Series:\n"
            "    return df['close'].expanding(min_periods=5).mean()\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="TI-04")
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)

    # -------------------------------------------------------------------------
    # TI-05: Full-Sample Normalization
    # -------------------------------------------------------------------------
    def test_ti_05_full_sample_normalization_metadata(self) -> None:
        """TI-05: Full-sample normalization yields FAIL and REJECT."""
        metadata = {
            "fitting_protocol": {
                "estimation_type": "NORMALIZATION",
                "fitting_scope": "FULL_SAMPLE",
            }
        }
        report = self.detector.audit_proposal(
            hypothesis_id="TI-05",
            objective="Evaluate Z-score momentum using full-sample standardized returns.",
            strategy_metadata=metadata,
        )
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        full_sample_findings = [f for f in report.findings if f.rule_id == "RULE-TI-05-FULL-SAMPLE-ESTIMATION"]
        self.assertGreaterEqual(len(full_sample_findings), 1)
        self.assertEqual(full_sample_findings[0].result_state, TemporalResultState.FAIL)

    # -------------------------------------------------------------------------
    # TI-06: Train-Only Frozen Normalization
    # -------------------------------------------------------------------------
    def test_ti_06_train_only_frozen_normalization(self) -> None:
        """TI-06: Train-only frozen normalization yields PASS / ELIGIBLE_FOR_REVIEW."""
        metadata = {
            "fitting_protocol": {
                "estimation_type": "NORMALIZATION",
                "fitting_scope": "TRAIN_ONLY_FROZEN",
            }
        }
        report = self.detector.audit_proposal(
            hypothesis_id="TI-06",
            objective="Evaluate Z-score momentum using parameters frozen on TRAIN partition.",
            strategy_metadata=metadata,
        )
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        pass_findings = [f for f in report.findings if f.result_state == TemporalResultState.PASS]
        self.assertGreaterEqual(len(pass_findings), 1)

    # -------------------------------------------------------------------------
    # TI-07: Full-Sample Cointegration Estimation (TASK-52 Direct Reproduction)
    # -------------------------------------------------------------------------
    def test_ti_07_full_sample_cointegration_task_52(self) -> None:
        """TI-07: Full-sample cointegration (TASK-52) deterministically yields FAIL and REJECT."""
        task = lookup_canonical_task("TASK-52-SEALED-COINT-FULLSAMPLE-LEAKAGE")
        self.assertIsNotNone(task)
        report = self.detector.audit_proposal(
            hypothesis_id=task.task_id,
            objective=task.objective_prompt,
        )
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        coint_findings = [f for f in report.findings if f.rule_id == "RULE-TI-05-FULL-SAMPLE-ESTIMATION"]
        self.assertEqual(len(coint_findings), 1)
        self.assertEqual(coint_findings[0].result_state, TemporalResultState.FAIL)
        self.assertTrue(coint_findings[0].is_definitive)
        self.assertEqual(coint_findings[0].severity, TemporalFindingSeverity.CRITICAL)

    # -------------------------------------------------------------------------
    # TI-08: Past-Only Rolling Cointegration Estimation
    # -------------------------------------------------------------------------
    def test_ti_08_past_only_rolling_cointegration(self) -> None:
        """TI-08: Past-only rolling cointegration yields PASS / ELIGIBLE_FOR_REVIEW."""
        metadata = {
            "fitting_protocol": {
                "estimation_type": "COINTEGRATION",
                "fitting_scope": "ROLLING_PAST_ONLY",
            }
        }
        report = self.detector.audit_proposal(
            hypothesis_id="TI-08",
            objective="Pairs trading with rolling 60-day Johansen estimation on historical windows.",
            strategy_metadata=metadata,
        )
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)

    # -------------------------------------------------------------------------
    # TI-09: Same-Close Signal Evaluation and Execution Without Valid Model
    # -------------------------------------------------------------------------
    def test_ti_09_same_close_signal_execution_warning(self) -> None:
        """TI-09: Same-close signal and execution without valid latency model yields WARNING and REVISE."""
        metadata = {
            "signal_timing": {"bar_reference": "CLOSE"},
            "execution_timing": {
                "bar_reference": "CLOSE",
                "execution_lag_bars": 0,
                "fill_model": "ZERO_LATENCY_IDEAL",
            },
        }
        report = self.detector.audit_proposal(
            hypothesis_id="TI-09",
            objective="Evaluate closing price momentum with immediate close execution.",
            strategy_metadata=metadata,
        )
        self.assertEqual(report.disposition, TemporalDisposition.REVISE)
        self.assertFalse(report.has_definitive_violation)
        timing_findings = [f for f in report.findings if f.rule_id == "RULE-TI-04-EXECUTION-TIMING"]
        self.assertEqual(len(timing_findings), 1)
        self.assertEqual(timing_findings[0].result_state, TemporalResultState.WARNING)
        self.assertFalse(timing_findings[0].is_definitive)

    # -------------------------------------------------------------------------
    # TI-10: Next-Bar Execution With Valid Timing Metadata
    # -------------------------------------------------------------------------
    def test_ti_10_next_bar_open_execution_clean(self) -> None:
        """TI-10: Next-bar Open execution with valid timing metadata yields PASS / ELIGIBLE_FOR_REVIEW."""
        metadata = {
            "signal_timing": {"bar_reference": "CLOSE"},
            "execution_timing": {
                "bar_reference": "OPEN",
                "execution_lag_bars": 1,
                "fill_model": "NEXT_BAR_OPEN_PLUS_SLIPPAGE",
            },
        }
        report = self.detector.audit_proposal(
            hypothesis_id="TI-10",
            objective="Causal daily breakout evaluated at Close with order execution at next Open.",
            strategy_metadata=metadata,
        )
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        timing_findings = [f for f in report.findings if f.rule_id == "RULE-TI-04-EXECUTION-TIMING"]
        self.assertEqual(len(timing_findings), 1)
        self.assertEqual(timing_findings[0].result_state, TemporalResultState.PASS)

    # -------------------------------------------------------------------------
    # TI-11: Forward Fill Crossing Decision-Time Boundary
    # -------------------------------------------------------------------------
    def test_ti_11_forward_fill_crossing_decision_boundary(self) -> None:
        """TI-11: Forward fill whose original event occurred after decision time yields FAIL and REJECT."""
        observations = [
            {
                "timestamp": "2024-01-01T12:00:00Z",
                "is_forward_filled": True,
                "original_event_timestamp": "2024-01-01T13:00:00Z",  # Event in the future
                "availability_timestamp": "2024-01-01T12:00:00Z",
            }
        ]
        decision_time = "2024-01-01T12:00:00Z"
        report = self.detector.audit_runtime_data(
            observations=observations,
            decision_timestamp=decision_time,
            hypothesis_id="TI-11",
        )
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        ffill_findings = [f for f in report.findings if f.rule_id == "RULE-TI-06-FORWARD-FILL-LEAKAGE"]
        self.assertEqual(len(ffill_findings), 1)
        self.assertEqual(ffill_findings[0].result_state, TemporalResultState.FAIL)
        self.assertTrue(ffill_findings[0].is_definitive)

    # -------------------------------------------------------------------------
    # TI-12: Timezone Mismatch
    # -------------------------------------------------------------------------
    def test_ti_12_timezone_heterogeneity_warning(self) -> None:
        """TI-12: Timezone heterogeneity yields WARNING and REVIEW_REQUIRED."""
        observations = [
            {"timestamp": 1704110400000, "availability_timestamp": 1704110400000},  # UTC Epoch
            {"timestamp": "2024-01-01T12:00:00+00:00", "availability_timestamp": "2024-01-01T12:00:00+00:00"},  # ISO
        ]
        report = self.detector.audit_runtime_data(observations, hypothesis_id="TI-12")
        self.assertEqual(report.disposition, TemporalDisposition.REVIEW_REQUIRED)
        tz_findings = [f for f in report.findings if f.rule_id == "RULE-TI-06-TIMEZONE-MISMATCH"]
        self.assertEqual(len(tz_findings), 1)
        self.assertEqual(tz_findings[0].result_state, TemporalResultState.WARNING)

    # -------------------------------------------------------------------------
    # TI-13: Duplicate Timestamps With Ambiguous Ordering
    # -------------------------------------------------------------------------
    def test_ti_13_duplicate_timestamps_inconclusive(self) -> None:
        """TI-13: Duplicate timestamps yield INCONCLUSIVE and REVIEW_REQUIRED."""
        observations = [
            {"timestamp": "2024-01-01T10:00:00Z", "availability_timestamp": "2024-01-01T10:00:00Z"},
            {"timestamp": "2024-01-01T10:00:00Z", "availability_timestamp": "2024-01-01T10:00:00Z"},
        ]
        report = self.detector.audit_runtime_data(observations, hypothesis_id="TI-13")
        self.assertEqual(report.disposition, TemporalDisposition.REVIEW_REQUIRED)
        dup_findings = [f for f in report.findings if f.rule_id == "RULE-TI-06-DUPLICATE-TIMESTAMPS"]
        self.assertEqual(len(dup_findings), 1)
        self.assertEqual(dup_findings[0].result_state, TemporalResultState.INCONCLUSIVE)

    # -------------------------------------------------------------------------
    # TI-14: Future Observation Supplied to Runtime Feature Calculation
    # -------------------------------------------------------------------------
    def test_ti_14_future_observation_runtime_assertion(self) -> None:
        """TI-14: Observation with availability_timestamp > decision_timestamp yields FAIL and REJECT."""
        observations = [
            {
                "timestamp": "2024-01-01T10:00:00Z",
                "availability_timestamp": "2024-01-01T12:30:00Z",  # Arrives at 12:30
            }
        ]
        decision_time = "2024-01-01T12:00:00Z"  # Deciding at 12:00
        report = self.detector.audit_runtime_data(
            observations=observations,
            decision_timestamp=decision_time,
            hypothesis_id="TI-14",
        )
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        runtime_findings = [f for f in report.findings if f.rule_id == "RULE-TI-07-RUNTIME-FUTURE-OBS"]
        self.assertEqual(len(runtime_findings), 1)
        self.assertEqual(runtime_findings[0].result_state, TemporalResultState.FAIL)
        self.assertTrue(runtime_findings[0].is_definitive)

    # -------------------------------------------------------------------------
    # TI-15: Missing Availability Timestamp
    # -------------------------------------------------------------------------
    def test_ti_15_missing_availability_timestamp_inconclusive(self) -> None:
        """TI-15: Observations lacking availability timestamps yield INCONCLUSIVE and REVIEW_REQUIRED."""
        observations = [
            {"timestamp": "2024-01-01T10:00:00Z"}  # Missing availability_timestamp
        ]
        report = self.detector.audit_runtime_data(observations, hypothesis_id="TI-15")
        self.assertEqual(report.disposition, TemporalDisposition.REVIEW_REQUIRED)
        no_avail_findings = [f for f in report.findings if f.rule_id == "RULE-TI-07-MISSING-AVAILABILITY"]
        self.assertEqual(len(no_avail_findings), 1)
        self.assertEqual(no_avail_findings[0].result_state, TemporalResultState.INCONCLUSIVE)

    # -------------------------------------------------------------------------
    # TI-16: Unrecognized or Unsupported Transformation
    # -------------------------------------------------------------------------
    def test_ti_16_unrecognized_transformation_inconclusive(self) -> None:
        """TI-16: Unrecognized transformation yields INCONCLUSIVE and REVIEW_REQUIRED."""
        report = self.detector.audit_custom_transformation(
            transformation_name="black_box_neural_wavelet_filter",
            is_supported=False,
            hypothesis_id="TI-16",
        )
        self.assertEqual(report.disposition, TemporalDisposition.REVIEW_REQUIRED)
        self.assertFalse(report.has_definitive_violation)
        xform_findings = [f for f in report.findings if f.rule_id == "RULE-TI-08-UNSUPPORTED-TRANSFORMATION"]
        self.assertEqual(len(xform_findings), 1)
        self.assertEqual(xform_findings[0].result_state, TemporalResultState.INCONCLUSIVE)

    # -------------------------------------------------------------------------
    # TI-17: LLM Attempts to Override a Deterministic FAIL/REJECT
    # -------------------------------------------------------------------------
    def test_ti_17_llm_override_attempt_blocked(self) -> None:
        """TI-17: External agent/LLM attempting to approve or proceed on REJECT raises DeterministicOverrideBlockedError."""
        source = "def bad(df): return df['close'].shift(-1)\n"
        report = self.detector.audit_source_code(source, hypothesis_id="TI-17")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)

        # Prohibited actions: APPROVE, RECOMMEND_APPROVAL, PROCEED, TICKET_GENERATION, OVERRIDE
        prohibited_actions = ["APPROVE", "RECOMMEND_APPROVAL", "PROCEED", "TICKET_GENERATION", "OVERRIDE"]
        for action in prohibited_actions:
            with self.subTest(action=action):
                with self.assertRaises(DeterministicOverrideBlockedError):
                    self.detector.verify_override_attempt(report, action)

    # -------------------------------------------------------------------------
    # TI-18: Idempotency and Reproducible Fingerprints
    # -------------------------------------------------------------------------
    def test_ti_18_idempotency_and_stable_fingerprints(self) -> None:
        """TI-18: Identical inputs produce identical input_digest, audit findings, and disposition."""
        source = "def strategy(df): return df['close'].shift(-2)\n"
        report_1 = self.detector.audit_source_code(source, hypothesis_id="TI-18")
        report_2 = self.detector.audit_source_code(source, hypothesis_id="TI-18")

        self.assertEqual(report_1.input_digest, report_2.input_digest)
        self.assertEqual(report_1.disposition, report_2.disposition)
        self.assertEqual(report_1.total_findings, report_2.total_findings)
        self.assertEqual(report_1.findings[0].rule_id, report_2.findings[0].rule_id)
        self.assertEqual(report_1.findings[0].title, report_2.findings[0].title)

    # -------------------------------------------------------------------------
    # Adversarial False Positive Resistance
    # -------------------------------------------------------------------------
    def test_adversarial_clean_strategies_zero_false_positives(self) -> None:
        """Ensure clean, standard quantitative strategies produce zero false positives."""
        clean_strategies = [
            # SMA crossover
            "def sma(df):\n    f = df['close'].rolling(10).mean()\n    s = df['close'].rolling(30).mean()\n    return f > s\n",
            # EMA trailing lag
            "def ema(df):\n    e = df['close'].ewm(span=20).mean()\n    return df['close'] > e\n",
            # Lagged shift (causal)
            "def lagged(df):\n    prev = df['close'].shift(1)\n    return df['close'] > prev\n",
            # Trailing Donchian breakout
            "def donchian(df):\n    high_20 = df['high'].shift(1).rolling(20).max()\n    return df['close'] > high_20\n",
        ]
        for idx, code in enumerate(clean_strategies):
            with self.subTest(idx=idx):
                rep = self.detector.audit_source_code(code, hypothesis_id=f"CLEAN-{idx}")
                self.assertEqual(rep.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
                self.assertEqual(rep.total_findings, 0)
                self.assertFalse(rep.has_definitive_violation)

    # -------------------------------------------------------------------------
    # Adversarial False Negative Resistance
    # -------------------------------------------------------------------------
    def test_adversarial_obfuscated_lookahead_caught(self) -> None:
        """Ensure forward array lookahead [i + 1] and negative shifts are caught."""
        obfuscated_codes = [
            # Forward index in subscript
            "def forward_idx(bars):\n    signals = []\n    for i in range(len(bars)):\n        signals.append(bars[i + 1] > bars[i])\n    return signals\n",
            # Multi-bar negative shift
            "def deep_shift(df):\n    return df['close'].shift(-5) > df['open']\n",
        ]
        for idx, code in enumerate(obfuscated_codes):
            with self.subTest(idx=idx):
                rep = self.detector.audit_source_code(code, hypothesis_id=f"OBF-{idx}")
                self.assertEqual(rep.disposition, TemporalDisposition.REJECT)
                self.assertTrue(rep.has_definitive_violation)


class TestCoordinatorTemporalIntegration(unittest.TestCase):
    """Integration tests verifying Coordinator Stage 2.5 enforcement and SQLite persistence."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "research_test.db")
        self.lock_path = os.path.join(self.temp_dir.name, "research.lock")
        self.ledger = ResearchLedger(db_path=self.db_path)
        self.provider = MarketDataProvider()
        self.engine = ResearchExecutionEngine(ledger=self.ledger, provider=self.provider)
        self.coord: Optional[ResearchCoordinator] = None

    def tearDown(self) -> None:
        if self.coord is not None:
            self.coord.close()
        self.ledger.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_coordinator_stage_2_5_rejection_halts_before_review(self) -> None:
        """Coordinator halts at Stage 2.5 when deterministic audit detects full-sample cointegration leakage."""
        responses = [
            {
                "hypothesis_id": "H-P271-COINT",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Estimate cointegrating vector by running Johansen test once over the entire two-year sample.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            }
        ]
        llm = MockLLMProvider(responses)
        self.coord = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm,
            lock_path=self.lock_path,
        )

        with self.assertRaises(TemporalIntegrityViolationError) as ctx:
            self.coord.run_vertical_slice(
                objective="Statistical arbitrage cointegration over the entire sample.",
            )
        self.assertIn("Deterministic temporal integrity audit rejected hypothesis", str(ctx.exception))
        # Ensure LLM reviewer was never called because pipeline halted at Stage 2.5
        self.assertEqual(llm.call_count, 1)

        # Verify audit was persisted in SQLite ledger
        audit_record = self.ledger.get_temporal_integrity_audit("H-P271-COINT")
        self.assertIsNotNone(audit_record)
        self.assertEqual(audit_record["disposition"], "REJECT")
        self.assertEqual(audit_record["has_definitive_violation"], 1)

    def test_sqlite_temporal_audit_immutability_triggers(self) -> None:
        """Verify SQLite triggers prevent update or delete on temporal_integrity_audits table."""
        self.ledger.record_hypothesis(
            hypothesis_id="H-IMMUTABLE-01",
            strategy_name="SyntheticSMAStrategy",
            economic_rationale="Testing immutability triggers.",
            validation_criteria={"min_sharpe": 1.0},
            max_evaluation_budget=2,
        )

        audit_data = {
            "audit_id": "TI-AUDIT-TEST-001",
            "hypothesis_id": "H-IMMUTABLE-01",
            "disposition": "ELIGIBLE_FOR_REVIEW",
            "disposition_rationale": "All causal checks passed.",
            "has_definitive_violation": False,
            "total_findings": 0,
            "findings": [],
            "detector_version": "1.0.0",
            "input_digest": "abcdef1234567890",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "disclaimer": "No trading approval granted.",
        }
        self.ledger.record_temporal_integrity_audit(audit_data)

        # Attempt UPDATE: must fail with sqlite3.IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.conn.execute(
                "UPDATE temporal_integrity_audits SET disposition = 'REJECT' WHERE audit_id = 'TI-AUDIT-TEST-001'"
            )

        # Attempt DELETE: must fail with sqlite3.IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.conn.execute(
                "DELETE FROM temporal_integrity_audits WHERE audit_id = 'TI-AUDIT-TEST-001'"
            )


if __name__ == "__main__":
    unittest.main()
