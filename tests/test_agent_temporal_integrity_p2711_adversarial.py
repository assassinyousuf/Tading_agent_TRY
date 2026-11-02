"""Adversarial Context-Aware Validation Suite for Temporal Integrity Detector.

Phase 2.7.1.1 Verification Suite.
Validates the 14 mandatory adversarial cases:
1. shift(-1) used exclusively for future-return target construction (PASS / ELIGIBLE_FOR_REVIEW).
2. shift(-1) used in a trading feature (FAIL / REJECT).
3. Centered rolling calculation used exclusively for retrospective analysis (PASS / ELIGIBLE_FOR_REVIEW).
4. Centered rolling calculation used in a trading signal (FAIL / REJECT).
5. Positive indexing in a forward chronological iteration (FAIL / REJECT).
6. Positive indexing in a non-temporal array context (PASS / ELIGIBLE_FOR_REVIEW).
7. Full-sample .fit_transform() on TRAIN-only observations (PASS / ELIGIBLE_FOR_REVIEW).
8. Full-sample .fit_transform() spanning TRAIN and future observations (FAIL / REJECT).
9. A future-dependent feature hidden behind a helper function (FAIL / REJECT).
10. A future-dependent feature hidden behind an intermediate variable (FAIL / REJECT).
11. A legitimate rolling cointegration calculation (PASS / ELIGIBLE_FOR_REVIEW).
12. Full-sample cointegration estimation with future observations (FAIL / REJECT).
13. Future-return labels excluded from the feature matrix (PASS / ELIGIBLE_FOR_REVIEW).
14. Future-return labels accidentally included in the feature matrix (FAIL / REJECT).

Additional Verifications:
15. Suspicious but unproven intermediate variables yield WARNING / REVIEW_REQUIRED.
16. Coordinator integration: Legitimate target construction advances past Stage 2.5 without rejection.
17. Coordinator integration: Accidental target leakage halts at Stage 2.5 before skeptical review.
18. LLM cannot override deterministic rejection.
19. Relational ledger immutability and idempotency.

Strict invariant: 0 em dashes (\u2014) and 0 en dashes (\u2013).
"""

from datetime import datetime, timezone
import json
import os
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional
import unittest

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


class MockLLMProviderP2711(LLMProvider):
    """Deterministic mock LLM provider for coordinator integration testing."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        super().__init__(name="mock_provider_p2711", model_identifier="mock-p2711-evaluator")
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
            raise RuntimeError("MockLLMProviderP2711 exhausted responses.")
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


class TestTemporalDetectorAdversarialP2711(unittest.TestCase):
    """Adversarial test suite verifying context-aware temporal discrimination."""

    def setUp(self) -> None:
        self.detector = TemporalIntegrityDetector()

    # -------------------------------------------------------------------------
    # Case 1: shift(-1) used exclusively for future-return target construction
    # -------------------------------------------------------------------------
    def test_case_01_shift_exclusively_for_target_construction(self) -> None:
        """Case 1: shift(-1) for supervised learning target yields PASS / ELIGIBLE_FOR_REVIEW."""
        source = (
            "import pandas as pd\n\n"
            "def build_supervised_dataset(df: pd.DataFrame):\n"
            "    df['target'] = df['close'].shift(-1) / df['close'] - 1.0\n"
            "    features = ['volatility_20', 'sma_ratio_10_30']\n"
            "    X = df[features]\n"
            "    y = df['target']\n"
            "    return X, y\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-01")
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        target_findings = [f for f in report.findings if f.rule_id == "RULE-TI-09-TARGET-CONSTRUCTION"]
        self.assertEqual(len(target_findings), 1)
        self.assertEqual(target_findings[0].result_state, TemporalResultState.PASS)
        self.assertTrue(target_findings[0].is_definitive)

    # -------------------------------------------------------------------------
    # Case 2: shift(-1) used in a trading feature
    # -------------------------------------------------------------------------
    def test_case_02_shift_used_in_trading_feature(self) -> None:
        """Case 2: shift(-1) used in trading feature/signal yields FAIL / REJECT."""
        source = (
            "import pandas as pd\n\n"
            "def compute_trading_signals(df: pd.DataFrame) -> pd.Series:\n"
            "    df['signal'] = (df['close'].shift(-1) > df['close']).astype(int)\n"
            "    return df['signal']\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-02")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        shift_findings = [f for f in report.findings if f.rule_id == "RULE-TI-01-FUTURE-SHIFT"]
        self.assertEqual(len(shift_findings), 1)
        self.assertEqual(shift_findings[0].result_state, TemporalResultState.FAIL)
        self.assertTrue(shift_findings[0].is_definitive)

    # -------------------------------------------------------------------------
    # Case 3: Centered rolling calculation used exclusively for retrospective analysis
    # -------------------------------------------------------------------------
    def test_case_03_centered_rolling_exclusively_retrospective(self) -> None:
        """Case 3: Centered rolling in retrospective analysis yields PASS / ELIGIBLE_FOR_REVIEW."""
        source = (
            "import pandas as pd\n\n"
            "def retrospective_regime_study(df: pd.DataFrame) -> pd.DataFrame:\n"
            "    df['retrospective_trend'] = df['close'].rolling(50, center=True).mean()\n"
            "    return df\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-03")
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        retro_findings = [f for f in report.findings if f.rule_id == "RULE-TI-02-RETROSPECTIVE-WINDOW"]
        self.assertEqual(len(retro_findings), 1)
        self.assertEqual(retro_findings[0].result_state, TemporalResultState.PASS)

    # -------------------------------------------------------------------------
    # Case 4: Centered rolling calculation used in a trading signal
    # -------------------------------------------------------------------------
    def test_case_04_centered_rolling_in_trading_signal(self) -> None:
        """Case 4: Centered rolling in trading signal yields FAIL / REJECT."""
        source = (
            "import pandas as pd\n\n"
            "def generate_signals(df: pd.DataFrame) -> pd.Series:\n"
            "    df['signal'] = df['close'] > df['close'].rolling(20, center=True).mean()\n"
            "    return df['signal']\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-04")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        center_findings = [f for f in report.findings if f.rule_id == "RULE-TI-02-CENTERED-WINDOW"]
        self.assertEqual(len(center_findings), 1)
        self.assertEqual(center_findings[0].result_state, TemporalResultState.FAIL)

    # -------------------------------------------------------------------------
    # Case 5: Positive indexing in a forward chronological iteration
    # -------------------------------------------------------------------------
    def test_case_05_positive_indexing_temporal_iteration(self) -> None:
        """Case 5: Positive indexing in forward chronological bar iteration yields FAIL / REJECT."""
        source = (
            "def loop_trading_signals(bars):\n"
            "    signals = []\n"
            "    for t in range(len(bars) - 1):\n"
            "        signals.append(bars[t + 1] > bars[t])\n"
            "    return signals\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-05")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        fwd_findings = [f for f in report.findings if f.rule_id == "RULE-TI-01-FORWARD-INDEX"]
        self.assertEqual(len(fwd_findings), 1)
        self.assertEqual(fwd_findings[0].result_state, TemporalResultState.FAIL)

    # -------------------------------------------------------------------------
    # Case 6: Positive indexing in a non-temporal array context
    # -------------------------------------------------------------------------
    def test_case_06_positive_indexing_non_temporal_context(self) -> None:
        """Case 6: Positive indexing on spatial matrix or parameter grid yields PASS / ELIGIBLE_FOR_REVIEW."""
        source = (
            "def compute_spatial_weights(matrix, grid):\n"
            "    weights = []\n"
            "    for r in range(len(matrix) - 1):\n"
            "        weights.append(matrix[r + 1][0] * grid[r + 1])\n"
            "    return weights\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-06")
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        non_temp_findings = [f for f in report.findings if f.rule_id == "RULE-TI-01-NON-TEMPORAL-INDEX"]
        self.assertGreaterEqual(len(non_temp_findings), 1)
        self.assertEqual(non_temp_findings[0].result_state, TemporalResultState.PASS)

    # -------------------------------------------------------------------------
    # Case 7: Full-sample .fit_transform() on TRAIN-only observations
    # -------------------------------------------------------------------------
    def test_case_07_fit_transform_on_train_only(self) -> None:
        """Case 7: .fit_transform() strictly on train_df yields PASS / ELIGIBLE_FOR_REVIEW."""
        source = (
            "from sklearn.preprocessing import StandardScaler\n\n"
            "def preprocess_training_data(train_df):\n"
            "    scaler = StandardScaler()\n"
            "    scaled = scaler.fit_transform(train_df)\n"
            "    return scaled\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-07")
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        train_fit_findings = [f for f in report.findings if f.rule_id == "RULE-TI-05-TRAIN-ONLY-FITTING"]
        self.assertEqual(len(train_fit_findings), 1)
        self.assertEqual(train_fit_findings[0].result_state, TemporalResultState.PASS)

    # -------------------------------------------------------------------------
    # Case 8: Full-sample .fit_transform() spanning TRAIN and future observations
    # -------------------------------------------------------------------------
    def test_case_08_fit_transform_spanning_future_data(self) -> None:
        """Case 8: .fit_transform() on full_dataset spanning validation/test yields FAIL / REJECT."""
        source = (
            "from sklearn.preprocessing import StandardScaler\n\n"
            "def preprocess_unpartitioned_data(full_dataset):\n"
            "    scaler = StandardScaler()\n"
            "    scaled = scaler.fit_transform(full_dataset)\n"
            "    return scaled\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-08")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        full_fit_findings = [f for f in report.findings if f.rule_id == "RULE-TI-05-FULL-SAMPLE-ESTIMATION"]
        self.assertEqual(len(full_fit_findings), 1)
        self.assertEqual(full_fit_findings[0].result_state, TemporalResultState.FAIL)

    # -------------------------------------------------------------------------
    # Case 9: Future-dependent feature hidden behind a helper function
    # -------------------------------------------------------------------------
    def test_case_09_future_feature_hidden_behind_helper_function(self) -> None:
        """Case 9: Feature derived from helper returning shift(-1) yields FAIL / REJECT."""
        source = (
            "def peek_ahead(series):\n"
            "    return series.shift(-1)\n\n"
            "def strategy_signals(df):\n"
            "    lead = peek_ahead(df['close'])\n"
            "    return lead > df['close']\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-09")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        hidden_findings = [f for f in report.findings if f.rule_id == "RULE-TI-01-FUTURE-SHIFT-HIDDEN"]
        self.assertEqual(len(hidden_findings), 1)
        self.assertEqual(hidden_findings[0].result_state, TemporalResultState.FAIL)

    # -------------------------------------------------------------------------
    # Case 10: Future-dependent feature hidden behind an intermediate variable
    # -------------------------------------------------------------------------
    def test_case_10_future_feature_hidden_behind_intermediate_var(self) -> None:
        """Case 10: Feature derived through chain of intermediate variables yields FAIL / REJECT."""
        source = (
            "def strategy_logic(df):\n"
            "    temp = df['close'].shift(-1)\n"
            "    intermediate = temp * 1.05\n"
            "    feature = intermediate - df['close']\n"
            "    signal = feature > 0\n"
            "    return signal\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-10")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        taint_findings = [f for f in report.findings if f.rule_id == "RULE-TI-01-FUTURE-SHIFT"]
        self.assertGreaterEqual(len(taint_findings), 1)
        self.assertEqual(taint_findings[0].result_state, TemporalResultState.FAIL)

    # -------------------------------------------------------------------------
    # Case 11: Legitimate rolling cointegration calculation
    # -------------------------------------------------------------------------
    def test_case_11_legitimate_rolling_cointegration(self) -> None:
        """Case 11: Rolling cointegration on past historical windows yields PASS / ELIGIBLE_FOR_REVIEW."""
        metadata = {
            "fitting_protocol": {
                "estimation_type": "COINTEGRATION",
                "fitting_scope": "ROLLING_PAST_ONLY",
            }
        }
        report = self.detector.audit_proposal(
            hypothesis_id="ADV-CASE-11",
            objective="Statistical arbitrage pairs trading using rolling 60-day Johansen cointegration.",
            strategy_metadata=metadata,
        )
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)

    # -------------------------------------------------------------------------
    # Case 12: Full-sample cointegration estimation with future observations
    # -------------------------------------------------------------------------
    def test_case_12_full_sample_cointegration_leakage(self) -> None:
        """Case 12: Full-sample cointegration vector estimation yields FAIL / REJECT."""
        metadata = {
            "fitting_protocol": {
                "estimation_type": "COINTEGRATION",
                "fitting_scope": "FULL_SAMPLE",
            }
        }
        report = self.detector.audit_proposal(
            hypothesis_id="ADV-CASE-12",
            objective="Estimate cointegrating vector once on the entire two-year dataset.",
            strategy_metadata=metadata,
        )
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)

    # -------------------------------------------------------------------------
    # Case 13: Future-return labels excluded from the feature matrix
    # -------------------------------------------------------------------------
    def test_case_13_future_return_labels_excluded_from_features(self) -> None:
        """Case 13: Target constructed but cleanly excluded from X yields PASS / ELIGIBLE_FOR_REVIEW."""
        source = (
            "import pandas as pd\n\n"
            "def prepare_training_data(df: pd.DataFrame):\n"
            "    df['target'] = df['close'].shift(-1) / df['close'] - 1.0\n"
            "    X = df[['volatility', 'sma_ratio']]\n"
            "    y = df['target']\n"
            "    return X, y\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-13")
        self.assertEqual(report.disposition, TemporalDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertFalse(report.has_definitive_violation)
        excl_findings = [f for f in report.findings if f.rule_id == "RULE-TI-09-TARGET-EXCLUDED-FROM-X"]
        self.assertEqual(len(excl_findings), 1)
        self.assertEqual(excl_findings[0].result_state, TemporalResultState.PASS)

    # -------------------------------------------------------------------------
    # Case 14: Future-return labels accidentally included in the feature matrix
    # -------------------------------------------------------------------------
    def test_case_14_future_return_labels_accidentally_in_features(self) -> None:
        """Case 14: Target column accidentally included in feature matrix X yields FAIL / REJECT."""
        source = (
            "import pandas as pd\n\n"
            "def prepare_training_data(df: pd.DataFrame):\n"
            "    df['target'] = df['close'].shift(-1) / df['close'] - 1.0\n"
            "    X = df[['volatility', 'target']]\n"  # Accidental target leakage into X
            "    y = df['target']\n"
            "    return X, y\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-14")
        self.assertEqual(report.disposition, TemporalDisposition.REJECT)
        self.assertTrue(report.has_definitive_violation)
        leak_findings = [f for f in report.findings if f.rule_id == "RULE-TI-09-TARGET-LEAKAGE-IN-X"]
        self.assertEqual(len(leak_findings), 1)
        self.assertEqual(leak_findings[0].result_state, TemporalResultState.FAIL)

    # -------------------------------------------------------------------------
    # Case 15: Suspicious but unproven intermediate variables
    # -------------------------------------------------------------------------
    def test_case_15_unproven_intermediate_variable_warning(self) -> None:
        """Case 15: Shift assigned to variable whose destination cannot be proven yields WARNING / REVIEW_REQUIRED."""
        source = (
            "import pandas as pd\n\n"
            "def mysterious_function(df: pd.DataFrame):\n"
            "    unproven_var = df['close'].shift(-1)\n"
            "    return df['close'] > df['open']\n"
        )
        report = self.detector.audit_source_code(source, hypothesis_id="ADV-CASE-15")
        self.assertEqual(report.disposition, TemporalDisposition.REVIEW_REQUIRED)
        self.assertFalse(report.has_definitive_violation)
        unproven_findings = [f for f in report.findings if f.rule_id == "RULE-TI-01-FUTURE-SHIFT"]
        self.assertEqual(len(unproven_findings), 1)
        self.assertEqual(unproven_findings[0].result_state, TemporalResultState.WARNING)
        self.assertFalse(unproven_findings[0].is_definitive)


class TestCoordinatorAdversarialIntegrationP2711(unittest.TestCase):
    """Integration verification: Coordinator gating, target tolerance, and override immunity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "research_p2711.db")
        self.lock_path = os.path.join(self.temp_dir.name, "research_p2711.lock")
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

    def test_coordinator_tolerates_legitimate_target_construction(self) -> None:
        """Coordinator Stage 2.5 does NOT reject legitimate target construction and proceeds to review."""
        responses = [
            # 1. Hypothesis with legitimate target construction
            {
                "hypothesis_id": "H-P2711-TARGET-OK",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Train machine learning momentum classifier on 1-day forward return target y = close.shift(-1) with features X = [sma_10, rsi_14].",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            },
            # 2. Skeptical Reviewer output
            {
                "lookahead_risk_score": 1,
                "snooping_risk_score": 1,
                "granularity_risk_score": 1,
                "turnover_realism_score": 1,
                "overall_recommendation": "RECOMMEND_APPROVAL",
                "critique_rationale": "Methodology strictly causal; target y is cleanly separated from input features X.",
                "required_revisions": [],
                "revision_assessment": "NOT_APPLICABLE",
            },
            # 3. Coordinator Preregistration Proposal
            {
                "proposal_id": "PROP-P2711-01",
                "hypothesis_id": "H-P2711-TARGET-OK",
                "universe": ["BTCUSDT"],
                "parameter_grid": {
                    "fast_period": [10.0, 15.0],
                    "slow_period": [30.0],
                },
            },
        ]
        llm = MockLLMProviderP2711(responses)
        self.coord = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm,
            lock_path=self.lock_path,
        )

        state = self.coord.run_vertical_slice(
            objective="Supervised momentum strategy with target construction.",
        )
        self.assertEqual(state.current_stage, "HUMAN_APPROVAL_GATE")
        self.assertFalse(state.is_failed)
        self.assertIsNotNone(state.temporal_audit)
        self.assertEqual(state.temporal_audit["disposition"], "ELIGIBLE_FOR_REVIEW")

    def test_coordinator_halts_on_accidental_target_leakage(self) -> None:
        """Coordinator Stage 2.5 halts when target column is accidentally included in feature matrix."""
        # Note: In coordinator, hypothesis with full-sample Johansen cointegration is tested
        responses = [
            {
                "hypothesis_id": "H-P2711-LEAK",
                "strategy_name": "SyntheticSMAStrategy",
                "economic_rationale": "Estimate cointegrating vector by running Johansen test once over the entire sample.",
                "validation_criteria": {"min_sharpe": 1.0, "max_drawdown_pct": 20.0},
                "max_evaluation_budget": 2,
            }
        ]
        llm = MockLLMProviderP2711(responses)
        self.coord = ResearchCoordinator(
            ledger=self.ledger,
            provider=self.provider,
            execution_engine=self.engine,
            llm_provider=llm,
            lock_path=self.lock_path,
        )

        with self.assertRaises(TemporalIntegrityViolationError):
            self.coord.run_vertical_slice(
                objective="Full sample cointegration strategy.",
            )
        self.assertEqual(llm.call_count, 1)  # Reviewer never called


if __name__ == "__main__":
    unittest.main()
