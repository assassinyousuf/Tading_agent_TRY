"""Deterministic Temporal Integrity and Lookahead Leakage Detection Module.

Part of Phase 2.7.1 Autonomous Research Governance.

Enforces:
1. Deterministic, Python-authoritative detection of temporal violations.
2. Identifies future-data access (negative shifts, centered rolling windows, forward lookups).
3. Evaluates signal and execution timing models (same-bar close vs next-bar open).
4. Identifies full-sample estimation leakage (Johansen cointegration, PCA, scalers across full sample vs rolling or train-only).
5. Validates timestamp alignment, timezone consistency, duplicate timestamps, and availability boundaries.
6. Enforces runtime point-in-time availability assertions on data observations.
7. Produces structured, serializable findings with deterministic disposition:
   - REJECT: Definitive prohibited temporal violation detected.
   - REVISE: Remediable timing or alignment problem detected.
   - REVIEW_REQUIRED: Only heuristic or ambiguous evidence exists.
   - ELIGIBLE_FOR_REVIEW: No blocking temporal issue detected within coverage.
8. Strictly blocks LLM attempts to override deterministic findings.
9. Zero em dashes (\\u2014) or en dashes (\\u2013) across code, comments, and docstrings.
"""

import ast
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field


DETECTOR_VERSION = "1.0.0"


class TemporalResultState(str, Enum):
    """Explicit evaluation result states for temporal rules."""
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TemporalDisposition(str, Enum):
    """Overall deterministic audit disposition for a strategy or proposal."""
    REJECT = "REJECT"
    REVISE = "REVISE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ELIGIBLE_FOR_REVIEW = "ELIGIBLE_FOR_REVIEW"


class TemporalFindingSeverity(str, Enum):
    """Categorical severity of a temporal integrity finding."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"


class ConfidenceClassification(str, Enum):
    """Certainty level of a finding without conflating heuristic score with probability."""
    DEFINITIVE = "DEFINITIVE"
    HIGH_HEURISTIC = "HIGH_HEURISTIC"
    LOW_HEURISTIC = "LOW_HEURISTIC"
    INCONCLUSIVE = "INCONCLUSIVE"


class TemporalFindingCategory(str, Enum):
    """Category of temporal integrity violation or observation."""
    FUTURE_DATA_ACCESS = "FUTURE_DATA_ACCESS"
    EXECUTION_TIMING = "EXECUTION_TIMING"
    ROLLING_WINDOW = "ROLLING_WINDOW"
    FULL_SAMPLE_ESTIMATION = "FULL_SAMPLE_ESTIMATION"
    TIMESTAMP_ALIGNMENT = "TIMESTAMP_ALIGNMENT"
    RUNTIME_ASSERTION = "RUNTIME_ASSERTION"
    TARGET_LEAKAGE = "TARGET_LEAKAGE" 


class TemporalIntegrityFinding(BaseModel):
    """Structured, serializable finding produced by a temporal integrity rule."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(..., description="Stable unique finding identifier")
    rule_id: str = Field(..., description="Unique rule identifier, e.g. RULE-TI-01-FUTURE-SHIFT")
    rule_version: str = Field(default="1.0.0", description="Rule implementation version")
    severity: TemporalFindingSeverity = Field(..., description="Finding severity")
    category: TemporalFindingCategory = Field(..., description="Finding category")
    title: str = Field(..., description="Short descriptive title of the finding")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Affected code, AST nodes, or data references")
    explanation: str = Field(..., description="Detailed explanation of the temporal violation or observation")
    is_definitive: bool = Field(..., description="True if proven deterministically; False if heuristic")
    confidence: ConfidenceClassification = Field(..., description="Certainty classification")
    recommended_remediation: str = Field(..., description="Prescriptive remediation guidance")
    timestamp_utc: str = Field(..., description="UTC ISO timestamp when finding was generated")
    input_digest: str = Field(..., description="SHA-256 fingerprint of audited input artifact")
    detector_version: str = Field(default=DETECTOR_VERSION, description="Detector engine version")
    result_state: TemporalResultState = Field(..., description="Individual rule result state")


class TemporalIntegrityAuditReport(BaseModel):
    """Comprehensive, deterministic temporal integrity audit report."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(..., description="Unique audit identifier")
    hypothesis_id: Optional[str] = Field(default=None, description="Hypothesis identifier if known")
    strategy_name: Optional[str] = Field(default=None, description="Strategy name if known")
    disposition: TemporalDisposition = Field(..., description="Deterministic overall disposition")
    disposition_rationale: str = Field(..., description="Detailed explanation of disposition")
    has_definitive_violation: bool = Field(..., description="True if any definitive FAIL finding exists")
    total_findings: int = Field(default=0, description="Total number of findings recorded")
    findings: List[TemporalIntegrityFinding] = Field(default_factory=list, description="List of structured findings")
    detector_version: str = Field(default=DETECTOR_VERSION, description="Detector engine version")
    input_digest: str = Field(..., description="SHA-256 fingerprint of aggregated input")
    timestamp_utc: str = Field(..., description="UTC ISO timestamp of audit completion")
    disclaimer: str = Field(
        default=(
            "ELIGIBLE_FOR_REVIEW indicates absence of detected temporal violations within detector coverage; "
            "it does not confer trading approval, statistical validity, profitability, or execution authorization."
        ),
        description="Mandatory governance disclaimer",
    )


class TemporalIntegrityViolationError(Exception):
    """Raised when a definitive temporal integrity violation blocks research progression."""
    pass


class DeterministicOverrideBlockedError(Exception):
    """Raised when an external agent or LLM attempts to override a deterministic temporal audit finding."""
    pass


# -----------------------------------------------------------------------------
# Static AST Code Visitor for Strategy Source Code
# -----------------------------------------------------------------------------

class StrategyASTVisitor(ast.NodeVisitor):
    """Context-aware static AST analyzer detecting temporal integrity violations in Python source code."""

    def __init__(self, source_code: str, input_digest: str) -> None:
        self.source_code = source_code
        self.input_digest = input_digest
        self.lines = source_code.splitlines()
        self.findings: List[TemporalIntegrityFinding] = []

        # Context and Data Flow Tracking
        self.lookahead_functions: Dict[str, str] = {}
        self.target_vars: Set[str] = set()
        self.tainted_vars: Dict[str, Dict[str, Any]] = {}  # var_name -> info
        self.referenced_vars: Set[str] = set()
        self.current_function: List[str] = []

    def _get_line_snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return ""

    @staticmethod
    def _is_target_name(name: str) -> bool:
        lower = name.lower()
        return (
            any(t in lower for t in ("target", "label", "ground_truth", "future_return", "forward_return", "ret_target"))
            or lower in ("y", "target_y", "y_true", "y_train")
        )

    @staticmethod
    def _is_retrospective_name(name: str) -> bool:
        lower = name.lower()
        return any(
            r in lower
            for r in (
                "retrospective",
                "ex_post",
                "historical_regime",
                "benchmark_study",
                "regime_study",
                "plot_",
                "report_",
            )
        )

    @staticmethod
    def _is_non_temporal_container(name: str) -> bool:
        lower = name.lower()
        return any(
            nt in lower
            for nt in (
                "matrix",
                "grid",
                "spatial",
                "coords",
                "weights",
                "params",
                "param_grid",
                "layer",
                "tensor",
                "table",
                "dims",
                "shape",
            )
        )

    @staticmethod
    def _is_train_partition(name: str) -> bool:
        lower = name.lower()
        return any(
            tr in lower for tr in ("train_df", "x_train", "train_data", "train_obs", "train_set")
        ) and not any(f in lower for f in ("full", "entire", "val", "test", "cross"))

    @staticmethod
    def _is_full_or_future_partition(name: str) -> bool:
        lower = name.lower()
        return any(
            f in lower
            for f in (
                "full_sample",
                "full_dataset",
                "entire_sample",
                "all_data",
                "all_bars",
                "cross_partition",
                "test_df",
                "val_df",
                "unpartitioned",
            )
        )

    def _get_target_name(self, target_node: ast.AST) -> Optional[str]:
        if isinstance(target_node, ast.Name):
            return target_node.id
        elif isinstance(target_node, ast.Subscript):
            if isinstance(target_node.slice, ast.Constant):
                return str(target_node.slice.value)
            elif isinstance(target_node.slice, ast.Name):
                return target_node.slice.id
        return None

    def _inspect_function_definition(self, func_node: ast.FunctionDef) -> None:
        """Scan function body to check if helper function returns or produces future data."""
        has_future_shift = False
        for child in ast.walk(func_node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Attribute) and child.func.attr == "shift":
                    if child.args and isinstance(child.args[0], ast.UnaryOp) and isinstance(child.args[0].op, ast.USub):
                        has_future_shift = True
                        break
        if has_future_shift:
            self.lookahead_functions[func_node.name] = "returns future-shifted series"

    def visit_Module(self, node: ast.Module) -> None:
        """Multi-pass traversal: scan helper functions, traverse nodes, then resolve data flow."""
        # Pass 1: Scan helper functions for lookahead return expressions
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                self._inspect_function_definition(item)

        # Pass 2: Standard AST traversal with context tracking
        self.generic_visit(node)

        # Pass 3: Post-traversal unresolved data-flow check
        for var_name, taint_info in self.tainted_vars.items():
            if var_name not in self.referenced_vars and var_name not in self.target_vars:
                # Suspicious but unproven intermediate variable
                self.findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-SHIFT-UNPROVEN-{len(self.findings) + 1:03d}",
                        rule_id="RULE-TI-01-FUTURE-SHIFT",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.HIGH,
                        category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                        title=f"Suspicious unproven future data shift in variable '{var_name}' ({taint_info.get('source')})",
                        evidence={
                            "line_number": taint_info.get("line"),
                            "snippet": taint_info.get("snippet"),
                            "variable": var_name,
                        },
                        explanation=(
                            f"Variable '{var_name}' is assigned future-shifted data but its downstream consumption "
                            "cannot be proven. Without explicit target or feature lineage, data flow is ambiguous."
                        ),
                        is_definitive=False,
                        confidence=ConfidenceClassification.INCONCLUSIVE,
                        recommended_remediation="Clarify whether variable is used for target construction or remove unused lookahead.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.WARNING,
                    )
                )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.current_function.append(node.name)
        self.generic_visit(node)
        self.current_function.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        target_name = None
        if node.targets:
            target_name = self._get_target_name(node.targets[0])

        current_func = self.current_function[-1] if self.current_function else ""
        snippet = self._get_line_snippet(node.lineno)

        # 1. Check RHS for .shift(-k)
        has_negative_shift = False
        shift_val = 0
        for child in ast.walk(node.value):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "shift":
                if child.args and isinstance(child.args[0], ast.UnaryOp) and isinstance(child.args[0].op, ast.USub):
                    if isinstance(child.args[0].operand, ast.Constant) and isinstance(child.args[0].operand.value, (int, float)):
                        val = child.args[0].operand.value
                        if val > 0:
                            has_negative_shift = True
                            shift_val = val

        if has_negative_shift and target_name:
            if self._is_target_name(target_name) or self._is_target_name(current_func):
                self.target_vars.add(target_name)
                self.findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-TARGET-CONSTRUCT-{len(self.findings) + 1:03d}",
                        rule_id="RULE-TI-09-TARGET-CONSTRUCTION",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.INFORMATIONAL,
                        category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                        title=f"Legitimate supervised learning target construction: {target_name} = shift(-{shift_val})",
                        evidence={"line_number": node.lineno, "snippet": snippet, "target_name": target_name, "shift_value": -shift_val},
                        explanation=f"Method call .shift(-{shift_val}) is used strictly for supervised training target/label generation.",
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation="Ensure target variable is excluded from feature matrix X at decision time.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.PASS,
                    )
                )
            elif any(s in target_name.lower() for s in ("signal", "feature", "feat", "alpha", "entry", "exit", "order", "indicator")) or any(s in current_func.lower() for s in ("signal", "feature", "strategy", "on_bar", "evaluate")):
                self.findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-SHIFT-{len(self.findings) + 1:03d}",
                        rule_id="RULE-TI-01-FUTURE-SHIFT",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.CRITICAL,
                        category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                        title=f"Prohibited negative shift in trading feature/signal: {target_name} = shift(-{shift_val})",
                        evidence={"line_number": node.lineno, "snippet": snippet, "variable": target_name, "shift_value": -shift_val},
                        explanation=f"Method call .shift(-{shift_val}) computes trading signal or feature '{target_name}' using future data.",
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation=f"Replace negative shift with causal backward shift (e.g. shift({shift_val})) or remove lookahead.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.FAIL,
                    )
                )
            else:
                # Intermediate variable taint
                self.tainted_vars[target_name] = {"source": f"shift(-{shift_val})", "line": node.lineno, "snippet": snippet}

        # 2. Check RHS for call to lookahead helper function
        for child in ast.walk(node.value):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                called_func = child.func.id
                if called_func in self.lookahead_functions and target_name:
                    if self._is_target_name(target_name) or self._is_target_name(current_func):
                        self.target_vars.add(target_name)
                    else:
                        self.findings.append(
                            TemporalIntegrityFinding(
                                finding_id=f"TI-FIND-HIDDEN-HELPER-{len(self.findings) + 1:03d}",
                                rule_id="RULE-TI-01-FUTURE-SHIFT-HIDDEN",
                                rule_version="1.0.0",
                                severity=TemporalFindingSeverity.CRITICAL,
                                category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                                title=f"Future-dependent feature hidden behind helper function '{called_func}'",
                                evidence={"line_number": node.lineno, "snippet": snippet, "helper_function": called_func, "target_variable": target_name},
                                explanation=f"Helper function '{called_func}' accesses future observations and is called to construct trading feature '{target_name}'.",
                                is_definitive=True,
                                confidence=ConfidenceClassification.DEFINITIVE,
                                recommended_remediation=f"Refactor helper function '{called_func}' to use causal historical data.",
                                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                input_digest=self.input_digest,
                                result_state=TemporalResultState.FAIL,
                            )
                        )

        # 3. Check RHS for use of tainted intermediate variables
        for child in ast.walk(node.value):
            if isinstance(child, ast.Name) and child.id in self.tainted_vars and target_name:
                self.referenced_vars.add(child.id)
                taint_info = self.tainted_vars[child.id]
                if self._is_target_name(target_name) or self._is_target_name(current_func):
                    self.target_vars.add(target_name)
                elif any(s in target_name.lower() for s in ("signal", "feature", "feat", "alpha", "entry", "exit", "order", "indicator")) or any(s in current_func.lower() for s in ("signal", "feature", "strategy", "on_bar", "evaluate")):
                    self.findings.append(
                        TemporalIntegrityFinding(
                            finding_id=f"TI-FIND-INTERMEDIATE-SHIFT-{len(self.findings) + 1:03d}",
                            rule_id="RULE-TI-01-FUTURE-SHIFT",
                            rule_version="1.0.0",
                            severity=TemporalFindingSeverity.CRITICAL,
                            category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                            title=f"Future-dependent feature hidden behind intermediate variable '{child.id}' ({taint_info.get('source')})",
                            evidence={"line_number": node.lineno, "snippet": snippet, "intermediate_variable": child.id, "target_variable": target_name},
                            explanation=f"Trading feature '{target_name}' depends on intermediate variable '{child.id}' which was tainted by {taint_info.get('source')}.",
                            is_definitive=True,
                            confidence=ConfidenceClassification.DEFINITIVE,
                            recommended_remediation="Remove lookahead data flow from intermediate calculations.",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            input_digest=self.input_digest,
                            result_state=TemporalResultState.FAIL,
                        )
                    )
                else:
                    self.tainted_vars[target_name] = {"source": f"tainted_by_{child.id}", "line": node.lineno, "snippet": snippet}

        # 4. Check RHS for centered rolling window
        has_centered_rolling = False
        for child in ast.walk(node.value):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "rolling":
                for kw in child.keywords:
                    if kw.arg == "center" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        has_centered_rolling = True
                        break

        if has_centered_rolling and target_name:
            if self._is_retrospective_name(target_name) or self._is_retrospective_name(current_func):
                self.findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-RETRO-CENTER-{len(self.findings) + 1:03d}",
                        rule_id="RULE-TI-02-RETROSPECTIVE-WINDOW",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.INFORMATIONAL,
                        category=TemporalFindingCategory.ROLLING_WINDOW,
                        title=f"Retrospective-only centered rolling window verified: {target_name}",
                        evidence={"line_number": node.lineno, "snippet": snippet, "target_variable": target_name},
                        explanation="Centered rolling window is used strictly for retrospective/ex-post evaluation or reporting.",
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation="No action required for retrospective calculations.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.PASS,
                    )
                )
            elif any(s in target_name.lower() for s in ("signal", "feature", "feat", "alpha", "entry", "exit", "indicator")) or any(s in current_func.lower() for s in ("signal", "feature", "strategy", "on_bar", "evaluate")):
                self.findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-CENTER-{len(self.findings) + 1:03d}",
                        rule_id="RULE-TI-02-CENTERED-WINDOW",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.CRITICAL,
                        category=TemporalFindingCategory.ROLLING_WINDOW,
                        title=f"Prohibited centered rolling window in trading signal/feature: {target_name}",
                        evidence={"line_number": node.lineno, "snippet": snippet, "target_variable": target_name},
                        explanation="Centered rolling window calculates forward-looking observations for decision signal.",
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation="Set center=False (the default) to ensure rolling calculations incorporate strictly past observations.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.FAIL,
                    )
                )
            else:
                self.findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-CENTER-WARN-{len(self.findings) + 1:03d}",
                        rule_id="RULE-TI-02-CENTERED-WINDOW",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.HIGH,
                        category=TemporalFindingCategory.ROLLING_WINDOW,
                        title=f"Centered rolling window on variable '{target_name}' with ambiguous purpose",
                        evidence={"line_number": node.lineno, "snippet": snippet, "target_variable": target_name},
                        explanation="Centered rolling window detected without explicit retrospective or decision naming.",
                        is_definitive=False,
                        confidence=ConfidenceClassification.HIGH_HEURISTIC,
                        recommended_remediation="Clarify whether calculation is retrospective or adopt trailing causal window.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.WARNING,
                    )
                )

        # 5. Check Feature Matrix X construction vs Target Leakage
        if target_name in ("X", "features_df", "feature_matrix", "x_data"):
            if isinstance(node.value, ast.Subscript):
                if isinstance(node.value.slice, ast.List):
                    selected_cols = [elt.value for elt in node.value.slice.elts if isinstance(elt, ast.Constant)]
                    leaked_targets = [col for col in selected_cols if col in self.target_vars or self._is_target_name(str(col))]
                    if leaked_targets:
                        self.findings.append(
                            TemporalIntegrityFinding(
                                finding_id=f"TI-FIND-TARGET-LEAK-{len(self.findings) + 1:03d}",
                                rule_id="RULE-TI-09-TARGET-LEAKAGE-IN-X",
                                rule_version="1.0.0",
                                severity=TemporalFindingSeverity.CRITICAL,
                                category=TemporalFindingCategory.TARGET_LEAKAGE,
                                title=f"Future-return target(s) {leaked_targets} accidentally included in feature matrix {target_name}",
                                evidence={"line_number": node.lineno, "snippet": snippet, "feature_matrix": target_name, "leaked_columns": leaked_targets},
                                explanation="Target column containing future returns was included in model input feature matrix X.",
                                is_definitive=True,
                                confidence=ConfidenceClassification.DEFINITIVE,
                                recommended_remediation="Remove target column from feature matrix column list.",
                                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                input_digest=self.input_digest,
                                result_state=TemporalResultState.FAIL,
                            )
                        )
                    else:
                        self.findings.append(
                            TemporalIntegrityFinding(
                                finding_id=f"TI-FIND-TARGET-EXCL-{len(self.findings) + 1:03d}",
                                rule_id="RULE-TI-09-TARGET-EXCLUDED-FROM-X",
                                rule_version="1.0.0",
                                severity=TemporalFindingSeverity.INFORMATIONAL,
                                category=TemporalFindingCategory.TARGET_LEAKAGE,
                                title=f"Future-return targets successfully excluded from feature matrix {target_name}",
                                evidence={"line_number": node.lineno, "snippet": snippet, "feature_matrix": target_name, "selected_columns": selected_cols},
                                explanation="Feature matrix X explicitly selects features and excludes target variables.",
                                is_definitive=True,
                                confidence=ConfidenceClassification.DEFINITIVE,
                                recommended_remediation="No action required.",
                                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                input_digest=self.input_digest,
                                result_state=TemporalResultState.PASS,
                            )
                        )
            elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "drop":
                dropped_cols = []
                for kw in node.value.keywords:
                    if kw.arg in ("columns", "labels") and isinstance(kw.value, ast.List):
                        dropped_cols = [elt.value for elt in kw.value.elts if isinstance(elt, ast.Constant)]
                if self.target_vars and not any(t in dropped_cols for t in self.target_vars):
                    self.findings.append(
                        TemporalIntegrityFinding(
                            finding_id=f"TI-FIND-TARGET-LEAK-DROP-{len(self.findings) + 1:03d}",
                            rule_id="RULE-TI-09-TARGET-LEAKAGE-IN-X",
                            rule_version="1.0.0",
                            severity=TemporalFindingSeverity.CRITICAL,
                            category=TemporalFindingCategory.TARGET_LEAKAGE,
                            title=f"Target column(s) {list(self.target_vars)} not dropped when constructing feature matrix {target_name}",
                            evidence={"line_number": node.lineno, "snippet": snippet, "target_variables": list(self.target_vars), "dropped_columns": dropped_cols},
                            explanation="Dataframe.drop() was called to produce feature matrix X but failed to drop future target column(s).",
                            is_definitive=True,
                            confidence=ConfidenceClassification.DEFINITIVE,
                            recommended_remediation=f"Explicitly add {list(self.target_vars)} to drop columns list.",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            input_digest=self.input_digest,
                            result_state=TemporalResultState.FAIL,
                        )
                    )

        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is None:
            self.generic_visit(node)
            return

        current_func = self.current_function[-1] if self.current_function else ""
        snippet = self._get_line_snippet(node.lineno)

        # 1. Check if return expression directly calls .shift(-k)
        for child in ast.walk(node.value):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "shift":
                if child.args and isinstance(child.args[0], ast.UnaryOp) and isinstance(child.args[0].op, ast.USub):
                    if isinstance(child.args[0].operand, ast.Constant) and isinstance(child.args[0].operand.value, (int, float)):
                        val = child.args[0].operand.value
                        if val > 0:
                            if self._is_target_name(current_func):
                                self.findings.append(
                                    TemporalIntegrityFinding(
                                        finding_id=f"TI-FIND-TARGET-RETURN-{len(self.findings) + 1:03d}",
                                        rule_id="RULE-TI-09-TARGET-CONSTRUCTION",
                                        rule_version="1.0.0",
                                        severity=TemporalFindingSeverity.INFORMATIONAL,
                                        category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                                        title=f"Target construction function '{current_func}' returns shift(-{val})",
                                        evidence={"line_number": node.lineno, "snippet": snippet, "function": current_func},
                                        explanation="Target generation function legitimately returns future-shifted series for model supervision.",
                                        is_definitive=True,
                                        confidence=ConfidenceClassification.DEFINITIVE,
                                        recommended_remediation="No action required.",
                                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                        input_digest=self.input_digest,
                                        result_state=TemporalResultState.PASS,
                                    )
                                )
                            else:
                                self.findings.append(
                                    TemporalIntegrityFinding(
                                        finding_id=f"TI-FIND-SHIFT-RETURN-{len(self.findings) + 1:03d}",
                                        rule_id="RULE-TI-01-FUTURE-SHIFT",
                                        rule_version="1.0.0",
                                        severity=TemporalFindingSeverity.CRITICAL,
                                        category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                                        title=f"Function '{current_func}' returns future-shifted value: shift(-{val})",
                                        evidence={"line_number": node.lineno, "snippet": snippet, "function": current_func, "shift_value": -val},
                                        explanation=f"Function returns value computed with .shift(-{val}) directly affecting signals or decision outputs.",
                                        is_definitive=True,
                                        confidence=ConfidenceClassification.DEFINITIVE,
                                        recommended_remediation="Remove lookahead shift from returned signal expression.",
                                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                        input_digest=self.input_digest,
                                        result_state=TemporalResultState.FAIL,
                                    )
                                )

        # 2. Check if return expression directly calls centered rolling
        for child in ast.walk(node.value):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "rolling":
                for kw in child.keywords:
                    if kw.arg == "center" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        if self._is_retrospective_name(current_func):
                            self.findings.append(
                                TemporalIntegrityFinding(
                                    finding_id=f"TI-FIND-RETRO-RETURN-{len(self.findings) + 1:03d}",
                                    rule_id="RULE-TI-02-RETROSPECTIVE-WINDOW",
                                    rule_version="1.0.0",
                                    severity=TemporalFindingSeverity.INFORMATIONAL,
                                    category=TemporalFindingCategory.ROLLING_WINDOW,
                                    title=f"Retrospective function '{current_func}' returns centered rolling calculation",
                                    evidence={"line_number": node.lineno, "snippet": snippet, "function": current_func},
                                    explanation="Retrospective evaluation function returns centered calculation for historical analysis.",
                                    is_definitive=True,
                                    confidence=ConfidenceClassification.DEFINITIVE,
                                    recommended_remediation="No action required.",
                                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                    input_digest=self.input_digest,
                                    result_state=TemporalResultState.PASS,
                                )
                            )
                        else:
                            self.findings.append(
                                TemporalIntegrityFinding(
                                    finding_id=f"TI-FIND-CENTER-RETURN-{len(self.findings) + 1:03d}",
                                    rule_id="RULE-TI-02-CENTERED-WINDOW",
                                    rule_version="1.0.0",
                                    severity=TemporalFindingSeverity.CRITICAL,
                                    category=TemporalFindingCategory.ROLLING_WINDOW,
                                    title=f"Decision function '{current_func}' returns centered rolling indicator",
                                    evidence={"line_number": node.lineno, "snippet": snippet, "function": current_func},
                                    explanation="Strategy function directly returns indicator computed with center=True.",
                                    is_definitive=True,
                                    confidence=ConfidenceClassification.DEFINITIVE,
                                    recommended_remediation="Set center=False for causal trading indicators.",
                                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                    input_digest=self.input_digest,
                                    result_state=TemporalResultState.FAIL,
                                )
                            )

        # 3. Check if return expression uses a tainted variable
        for child in ast.walk(node.value):
            if isinstance(child, ast.Name) and child.id in self.tainted_vars:
                self.referenced_vars.add(child.id)
                taint_info = self.tainted_vars[child.id]
                if self._is_target_name(current_func):
                    pass
                else:
                    self.findings.append(
                        TemporalIntegrityFinding(
                            finding_id=f"TI-FIND-SHIFT-TAINT-RETURN-{len(self.findings) + 1:03d}",
                            rule_id="RULE-TI-01-FUTURE-SHIFT",
                            rule_version="1.0.0",
                            severity=TemporalFindingSeverity.CRITICAL,
                            category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                            title=f"Function '{current_func}' returns future-tainted variable '{child.id}' ({taint_info.get('source')})",
                            evidence={"line_number": node.lineno, "snippet": snippet, "function": current_func, "tainted_variable": child.id},
                            explanation=f"Function returns decision output derived from future-tainted variable '{child.id}'.",
                            is_definitive=True,
                            confidence=ConfidenceClassification.DEFINITIVE,
                            recommended_remediation="Remove future-tainted variable from returned decision output.",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            input_digest=self.input_digest,
                            result_state=TemporalResultState.FAIL,
                        )
                    )

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        snippet = self._get_line_snippet(node.lineno)
        # Model fit / fit_transform inspection
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("fit", "fit_transform"):
            arg_reprs = [ast.unparse(a) for a in node.args]
            for arg_text in arg_reprs:
                if self._is_train_partition(arg_text):
                    self.findings.append(
                        TemporalIntegrityFinding(
                            finding_id=f"TI-FIND-TRAIN-FIT-{len(self.findings) + 1:03d}",
                            rule_id="RULE-TI-05-TRAIN-ONLY-FITTING",
                            rule_version="1.0.0",
                            severity=TemporalFindingSeverity.INFORMATIONAL,
                            category=TemporalFindingCategory.FULL_SAMPLE_ESTIMATION,
                            title=f"Model fitting verified strictly on TRAIN partition: {arg_text}",
                            evidence={"line_number": node.lineno, "snippet": snippet, "dataset_argument": arg_text},
                            explanation=f"Call to .{node.func.attr}() operates strictly on approved TRAIN partition.",
                            is_definitive=True,
                            confidence=ConfidenceClassification.DEFINITIVE,
                            recommended_remediation="No action required.",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            input_digest=self.input_digest,
                            result_state=TemporalResultState.PASS,
                        )
                    )
                elif self._is_full_or_future_partition(arg_text):
                    self.findings.append(
                        TemporalIntegrityFinding(
                            finding_id=f"TI-FIND-FULL-FIT-{len(self.findings) + 1:03d}",
                            rule_id="RULE-TI-05-FULL-SAMPLE-ESTIMATION",
                            rule_version="1.0.0",
                            severity=TemporalFindingSeverity.CRITICAL,
                            category=TemporalFindingCategory.FULL_SAMPLE_ESTIMATION,
                            title=f"Prohibited full-sample model fitting spanning future observations: {arg_text}",
                            evidence={"line_number": node.lineno, "snippet": snippet, "dataset_argument": arg_text},
                            explanation=f"Call to .{node.func.attr}() fits parameters on full/future dataset '{arg_text}'.",
                            is_definitive=True,
                            confidence=ConfidenceClassification.DEFINITIVE,
                            recommended_remediation="Fit transformations strictly on rolling historical windows or freeze parameters on TRAIN partition only.",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            input_digest=self.input_digest,
                            result_state=TemporalResultState.FAIL,
                        )
                    )

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        slice_node = node.slice
        if isinstance(slice_node, ast.BinOp) and isinstance(slice_node.op, ast.Add):
            if isinstance(slice_node.right, ast.Constant) and isinstance(slice_node.right.value, (int, float)):
                offset = slice_node.right.value
                if offset > 0 and isinstance(slice_node.left, ast.Name):
                    var_name = slice_node.left.id
                    container_name = ast.unparse(node.value)
                    snippet = self._get_line_snippet(node.lineno)

                    if self._is_non_temporal_container(container_name) or var_name in ("r", "c", "row", "col", "x", "y", "dim", "layer"):
                        self.findings.append(
                            TemporalIntegrityFinding(
                                finding_id=f"TI-FIND-NON-TEMPORAL-INDEX-{len(self.findings) + 1:03d}",
                                rule_id="RULE-TI-01-NON-TEMPORAL-INDEX",
                                rule_version="1.0.0",
                                severity=TemporalFindingSeverity.INFORMATIONAL,
                                category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                                title=f"Positive indexing verified in non-temporal container: {container_name}[{var_name} + {offset}]",
                                evidence={"line_number": node.lineno, "snippet": snippet, "container": container_name, "variable": var_name, "offset": offset},
                                explanation="Positive indexing occurs on non-temporal matrix, grid, or parameter structure, not time series observations.",
                                is_definitive=True,
                                confidence=ConfidenceClassification.DEFINITIVE,
                                recommended_remediation="No action required.",
                                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                input_digest=self.input_digest,
                                result_state=TemporalResultState.PASS,
                            )
                        )
                    elif var_name in ("i", "t", "idx", "bar_idx", "step"):
                        self.findings.append(
                            TemporalIntegrityFinding(
                                finding_id=f"TI-FIND-FWD-INDEX-{len(self.findings) + 1:03d}",
                                rule_id="RULE-TI-01-FORWARD-INDEX",
                                rule_version="1.0.0",
                                severity=TemporalFindingSeverity.CRITICAL,
                                category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                                title=f"Forward-looking indexing detected in temporal iteration: {container_name}[{var_name} + {offset}]",
                                evidence={"line_number": node.lineno, "snippet": snippet, "container": container_name, "variable": var_name, "offset": offset},
                                explanation=f"Array or series indexing using [{var_name} + {offset}] accesses future observations.",
                                is_definitive=True,
                                confidence=ConfidenceClassification.DEFINITIVE,
                                recommended_remediation=f"Replace [{var_name} + {offset}] with current [{var_name}] or lag [{var_name} - {offset}].",
                                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                input_digest=self.input_digest,
                                result_state=TemporalResultState.FAIL,
                            )
                        )

        self.generic_visit(node)
# -----------------------------------------------------------------------------
# Metadata and Strategy Specification Analyzer
# -----------------------------------------------------------------------------

class StrategySpecificationAuditor:
    """Audits structured strategy metadata and natural-language research specifications."""

    def __init__(self, input_digest: str) -> None:
        self.input_digest = input_digest

    def audit_metadata(self, metadata: Dict[str, Any]) -> List[TemporalIntegrityFinding]:
        """Analyze structured strategy configuration metadata for timing and estimation rules."""
        findings: List[TemporalIntegrityFinding] = []

        # 1. Signal and Execution Timing Model Check (TI-09, TI-10)
        execution_timing = metadata.get("execution_timing", {})
        signal_timing = metadata.get("signal_timing", {})

        signal_bar = signal_timing.get("bar_reference", "CLOSE")
        execution_bar = execution_timing.get("bar_reference")
        execution_lag = execution_timing.get("execution_lag_bars", 1)
        fill_model = execution_timing.get("fill_model")

        if execution_bar is not None:
            # Case A: Same-bar Close execution without valid fill model
            if signal_bar == "CLOSE" and execution_bar == "CLOSE" and execution_lag == 0:
                if not fill_model or fill_model == "ZERO_LATENCY_IDEAL":
                    findings.append(
                        TemporalIntegrityFinding(
                            finding_id=f"TI-FIND-TIMING-{len(findings) + 1:03d}",
                            rule_id="RULE-TI-04-EXECUTION-TIMING",
                            rule_version="1.0.0",
                            severity=TemporalFindingSeverity.HIGH,
                            category=TemporalFindingCategory.EXECUTION_TIMING,
                            title="Same-close signal evaluation and execution without defensible latency model",
                            evidence={
                                "signal_bar": signal_bar,
                                "execution_bar": execution_bar,
                                "execution_lag_bars": execution_lag,
                                "fill_model": fill_model,
                            },
                            explanation=(
                                "Strategy evaluates signals on bar Close and executes immediately at the same Close "
                                "without an execution latency model. Real-world order routing requires non-zero latency."
                            ),
                            is_definitive=False,
                            confidence=ConfidenceClassification.HIGH_HEURISTIC,
                            recommended_remediation="Adopt next-bar Open execution (Day t Close -> Day t+1 Open) or document a verifiable intra-bar execution model with slippage.",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            input_digest=self.input_digest,
                            result_state=TemporalResultState.WARNING,
                        )
                    )
            # Case B: Standard next-bar Open execution
            elif signal_bar == "CLOSE" and execution_bar == "OPEN" and execution_lag >= 1:
                findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-TIMING-{len(findings) + 1:03d}",
                        rule_id="RULE-TI-04-EXECUTION-TIMING",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.INFORMATIONAL,
                        category=TemporalFindingCategory.EXECUTION_TIMING,
                        title="Valid causal next-bar execution model verified",
                        evidence={
                            "signal_bar": signal_bar,
                            "execution_bar": execution_bar,
                            "execution_lag_bars": execution_lag,
                        },
                        explanation="Signals generated at bar t Close are executed at bar t+1 Open, maintaining strict causality.",
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation="No action required.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.PASS,
                    )
                )

        # 2. Preprocessing & Fitting Protocol Check (TI-05, TI-06, TI-07, TI-08)
        fitting_protocol = metadata.get("fitting_protocol", {})
        estimation_type = fitting_protocol.get("estimation_type")
        fitting_scope = fitting_protocol.get("fitting_scope")

        if estimation_type in ("COINTEGRATION", "HEDGE_RATIO", "NORMALIZATION", "STANDARDIZATION", "PCA"):
            # Full-sample estimation across whole dataset
            if fitting_scope in ("FULL_SAMPLE", "ENTIRE_DATASET", "UNPARTITIONED"):
                findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-FULLSAMPLE-{len(findings) + 1:03d}",
                        rule_id="RULE-TI-05-FULL-SAMPLE-ESTIMATION",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.CRITICAL,
                        category=TemporalFindingCategory.FULL_SAMPLE_ESTIMATION,
                        title=f"Prohibited full-sample estimation detected for {estimation_type}",
                        evidence={
                            "estimation_type": estimation_type,
                            "fitting_scope": fitting_scope,
                            "fitting_protocol": fitting_protocol,
                        },
                        explanation=(
                            f"Strategy specifies full-sample estimation for {estimation_type} across '{fitting_scope}'. "
                            "Estimating parameters (such as cointegration eigenvectors or normalization moments) over the entire sample "
                            "leaks future price relationships into historical trading decisions."
                        ),
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation="Estimate parameters using past-only rolling windows or freeze parameters strictly on the approved TRAIN partition.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.FAIL,
                    )
                )
            # Legitimate train-only frozen or rolling estimation
            elif fitting_scope in ("TRAIN_ONLY_FROZEN", "PAST_ONLY_ROLLING", "PAST_ONLY_EXPANDING"):
                findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-FROZEN-{len(findings) + 1:03d}",
                        rule_id="RULE-TI-05-FULL-SAMPLE-ESTIMATION",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.INFORMATIONAL,
                        category=TemporalFindingCategory.FULL_SAMPLE_ESTIMATION,
                        title=f"Causal {fitting_scope.lower()} estimation protocol verified for {estimation_type}",
                        evidence={
                            "estimation_type": estimation_type,
                            "fitting_scope": fitting_scope,
                        },
                        explanation=f"Parameters for {estimation_type} are fitted using causal {fitting_scope} without future data leakage.",
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation="No action required.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.PASS,
                    )
                )

        return findings

    def audit_objective_and_rationale(self, text: str) -> List[TemporalIntegrityFinding]:
        """Audit textual research objective and rationale for explicit lookahead design declarations (e.g. TASK-52)."""
        findings: List[TemporalIntegrityFinding] = []
        lower_text = text.lower()

        # Detection of TASK-52 pattern: Full-sample cointegration / Johansen test over entire partition
        coint_patterns = [
            r"johansen\s+(?:test|estimation)\s+.*entire\s+(?:two-year|sample|partition|dataset)",
            r"cointegrat(?:ing|ion)\s+vector\s+.*entire\s+(?:sample|partition|dataset)",
            r"run(?:ning)?\s+(?:the\s+)?johansen\s+test\s+once\s+over\s+the\s+entire",
            r"estimate\s+.*beta\s+.*over\s+the\s+entire\s+(?:sample|two-year)",
        ]
        for pattern in coint_patterns:
            match = re.search(pattern, lower_text)
            if match:
                findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-TXT-COINT-{len(findings) + 1:03d}",
                        rule_id="RULE-TI-05-FULL-SAMPLE-ESTIMATION",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.CRITICAL,
                        category=TemporalFindingCategory.FULL_SAMPLE_ESTIMATION,
                        title="Full-sample cointegration eigenvector estimation declared in objective",
                        evidence={
                            "matched_pattern": pattern,
                            "matched_text": match.group(0),
                        },
                        explanation=(
                            "The research specification explicitly prescribes estimating the cointegrating vector "
                            "by running a cointegration test once over the entire sample. This incorporates future "
                            "observations across the entire backtest partition into historical spread definitions."
                        ),
                        is_definitive=True,
                        confidence=ConfidenceClassification.DEFINITIVE,
                        recommended_remediation="Specify a rolling Johansen estimation or fit the cointegrating vector strictly on historical pre-sample data.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.FAIL,
                    )
                )
                break

        # Detection of centered rolling window in narrative (untyped narrative without executable code -> WARNING)
        centered_patterns = [
            r"centered\s+(?:rolling|moving\s+average|window)",
            r"bilateral\s+(?:filter|moving\s+average)",
            r"two-sided\s+(?:filter|window)",
        ]
        for pattern in centered_patterns:
            match = re.search(pattern, lower_text)
            if match:
                findings.append(
                    TemporalIntegrityFinding(
                        finding_id=f"TI-FIND-TXT-CENTER-{len(findings) + 1:03d}",
                        rule_id="RULE-TI-02-CENTERED-WINDOW",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.HIGH,
                        category=TemporalFindingCategory.ROLLING_WINDOW,
                        title="Centered rolling window referenced in objective narrative",
                        evidence={
                            "matched_pattern": pattern,
                            "matched_text": match.group(0),
                        },
                        explanation=(
                            "Narrative references centered rolling window or bilateral filter. "
                            "Without executable code, this is an untyped narrative claim indicating potential lookahead bias."
                        ),
                        is_definitive=False,
                        confidence=ConfidenceClassification.HIGH_HEURISTIC,
                        recommended_remediation="Clarify that one-sided causal rolling indicators ending strictly at current bar will be used.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=self.input_digest,
                        result_state=TemporalResultState.WARNING,
                    )
                )
                break

        return findings


# -----------------------------------------------------------------------------
# Runtime Data and Alignment Validator
# -----------------------------------------------------------------------------

class RuntimeTemporalAssertionValidator:
    """Validates runtime candle records, feature lineage, and timestamp alignment."""

    def __init__(self, input_digest: str) -> None:
        self.input_digest = input_digest

    def validate_observations(
        self,
        observations: List[Dict[str, Any]],
        decision_timestamp: Optional[Union[str, datetime]] = None,
    ) -> List[TemporalIntegrityFinding]:
        """Validate an array of candle observations for temporal alignment and availability."""
        findings: List[TemporalIntegrityFinding] = []

        if not observations:
            return findings

        # Parse decision_timestamp if provided
        decision_dt: Optional[datetime] = None
        if decision_timestamp is not None:
            if isinstance(decision_timestamp, datetime):
                decision_dt = decision_timestamp
            else:
                try:
                    decision_dt = datetime.fromisoformat(decision_timestamp.replace("Z", "+00:00"))
                except Exception:
                    decision_dt = None

        seen_timestamps: Set[Any] = set()
        duplicate_timestamps: List[Any] = []
        timezone_mismatch: bool = False
        tz_types_seen: Set[str] = set()
        missing_availability_count = 0
        future_observation_count = 0

        prev_dt: Optional[datetime] = None

        for idx, obs in enumerate(observations):
            ts_raw = obs.get("timestamp") or obs.get("open_time")
            avail_raw = obs.get("availability_timestamp")

            # Check missing availability timestamp (TI-15)
            if avail_raw is None:
                missing_availability_count += 1

            # Parse timestamps
            dt: Optional[datetime] = None
            if ts_raw is not None:
                if isinstance(ts_raw, (int, float)):
                    # Milliseconds or seconds epoch
                    dt = datetime.fromtimestamp(ts_raw / 1000.0 if ts_raw > 1e11 else ts_raw, tz=timezone.utc)
                    tz_types_seen.add("UTC_EPOCH")
                elif isinstance(ts_raw, str):
                    try:
                        dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                        tz_types_seen.add("ISO_STRING")
                    except Exception:
                        pass
                elif isinstance(ts_raw, datetime):
                    dt = ts_raw
                    tz_types_seen.add(str(dt.tzinfo))

            if dt is not None:
                # Duplicate timestamp detection (TI-13)
                if dt in seen_timestamps:
                    duplicate_timestamps.append(dt.isoformat())
                seen_timestamps.add(dt)

                # Monotonic ordering check
                if prev_dt is not None and dt < prev_dt:
                    findings.append(
                        TemporalIntegrityFinding(
                            finding_id=f"TI-FIND-ORDER-{len(findings) + 1:03d}",
                            rule_id="RULE-TI-06-DATA-ALIGNMENT",
                            rule_version="1.0.0",
                            severity=TemporalFindingSeverity.HIGH,
                            category=TemporalFindingCategory.TIMESTAMP_ALIGNMENT,
                            title=f"Non-monotonic timestamp order at record {idx}",
                            evidence={"index": idx, "current": dt.isoformat(), "previous": prev_dt.isoformat()},
                            explanation="Observation sequence decreases chronologically, violating chronological order.",
                            is_definitive=True,
                            confidence=ConfidenceClassification.DEFINITIVE,
                            recommended_remediation="Sort input data chronologically before evaluating backtests.",
                            timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            input_digest=self.input_digest,
                            result_state=TemporalResultState.FAIL,
                        )
                    )
                prev_dt = dt

            # Runtime Assertion: Availability timestamp cannot exceed decision timestamp (TI-14)
            if avail_raw is not None and decision_dt is not None:
                avail_dt: Optional[datetime] = None
                if isinstance(avail_raw, (int, float)):
                    avail_dt = datetime.fromtimestamp(avail_raw / 1000.0 if avail_raw > 1e11 else avail_raw, tz=timezone.utc)
                elif isinstance(avail_raw, str):
                    try:
                        avail_dt = datetime.fromisoformat(avail_raw.replace("Z", "+00:00"))
                    except Exception:
                        pass
                elif isinstance(avail_raw, datetime):
                    avail_dt = avail_raw

                if avail_dt is not None and avail_dt > decision_dt:
                    future_observation_count += 1

            # Forward-fill boundary check (TI-11)
            if obs.get("is_forward_filled") is True and decision_dt is not None and dt is not None:
                orig_raw = obs.get("original_event_timestamp")
                if orig_raw is not None:
                    # If original event is ahead of decision time, forward fill leaked future
                    orig_dt: Optional[datetime] = None
                    if isinstance(orig_raw, str):
                        try:
                            orig_dt = datetime.fromisoformat(orig_raw.replace("Z", "+00:00"))
                        except Exception:
                            pass
                    if orig_dt is not None and orig_dt > decision_dt:
                        findings.append(
                            TemporalIntegrityFinding(
                                finding_id=f"TI-FIND-FFILL-{len(findings) + 1:03d}",
                                rule_id="RULE-TI-06-FORWARD-FILL-LEAKAGE",
                                rule_version="1.0.0",
                                severity=TemporalFindingSeverity.CRITICAL,
                                category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                                title=f"Forward-fill crossed decision boundary at index {idx}",
                                evidence={"index": idx, "original_event": orig_dt.isoformat(), "decision_time": decision_dt.isoformat()},
                                explanation="Observation contains a forward-fill whose original event occurred after the decision timestamp.",
                                is_definitive=True,
                                confidence=ConfidenceClassification.DEFINITIVE,
                                recommended_remediation="Clamp forward-fills strictly to historical events preceding the decision timestamp.",
                                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                input_digest=self.input_digest,
                                result_state=TemporalResultState.FAIL,
                            )
                        )

        # Report future observations (TI-14)
        if future_observation_count > 0:
            findings.append(
                TemporalIntegrityFinding(
                    finding_id=f"TI-FIND-FUT-OBS-{len(findings) + 1:03d}",
                    rule_id="RULE-TI-07-RUNTIME-FUTURE-OBS",
                    rule_version="1.0.0",
                    severity=TemporalFindingSeverity.CRITICAL,
                    category=TemporalFindingCategory.RUNTIME_ASSERTION,
                    title=f"Future observations supplied to runtime calculation ({future_observation_count} records)",
                    evidence={"future_observation_count": future_observation_count, "decision_timestamp": decision_dt.isoformat() if decision_dt else None},
                    explanation="One or more observations have availability timestamps occurring after the specified decision timestamp.",
                    is_definitive=True,
                    confidence=ConfidenceClassification.DEFINITIVE,
                    recommended_remediation="Filter runtime inputs strictly where availability_timestamp <= decision_timestamp.",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    input_digest=self.input_digest,
                    result_state=TemporalResultState.FAIL,
                )
            )

        # Report duplicate timestamps (TI-13)
        if duplicate_timestamps:
            findings.append(
                TemporalIntegrityFinding(
                    finding_id=f"TI-FIND-DUP-TS-{len(findings) + 1:03d}",
                    rule_id="RULE-TI-06-DUPLICATE-TIMESTAMPS",
                    rule_version="1.0.0",
                    severity=TemporalFindingSeverity.MEDIUM,
                    category=TemporalFindingCategory.TIMESTAMP_ALIGNMENT,
                    title=f"Duplicate timestamps with ambiguous ordering detected ({len(duplicate_timestamps)} instances)",
                    evidence={"sample_duplicates": duplicate_timestamps[:5], "total_duplicates": len(duplicate_timestamps)},
                    explanation="Dataset contains repeated identical timestamps, creating ambiguous causal order.",
                    is_definitive=False,
                    confidence=ConfidenceClassification.INCONCLUSIVE,
                    recommended_remediation="Deduplicate observations and resolve tie-breaking explicitly before execution.",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    input_digest=self.input_digest,
                    result_state=TemporalResultState.INCONCLUSIVE,
                )
            )

        # Report missing availability timestamps (TI-15)
        if missing_availability_count > 0:
            findings.append(
                TemporalIntegrityFinding(
                    finding_id=f"TI-FIND-NO-AVAIL-{len(findings) + 1:03d}",
                    rule_id="RULE-TI-07-MISSING-AVAILABILITY",
                    rule_version="1.0.0",
                    severity=TemporalFindingSeverity.LOW,
                    category=TemporalFindingCategory.RUNTIME_ASSERTION,
                    title=f"Observations missing availability timestamps ({missing_availability_count} / {len(observations)})",
                    evidence={"missing_count": missing_availability_count, "total_records": len(observations)},
                    explanation="Observations lack explicit point-in-time availability timestamps; point-in-time proof cannot be certified.",
                    is_definitive=False,
                    confidence=ConfidenceClassification.INCONCLUSIVE,
                    recommended_remediation="Add explicit point-in-time availability_timestamp attributes to data feeds.",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    input_digest=self.input_digest,
                    result_state=TemporalResultState.INCONCLUSIVE,
                )
            )

        # Report timezone mismatch (TI-12)
        if len(tz_types_seen) > 1:
            findings.append(
                TemporalIntegrityFinding(
                    finding_id=f"TI-FIND-TZ-MISMATCH-{len(findings) + 1:03d}",
                    rule_id="RULE-TI-06-TIMEZONE-MISMATCH",
                    rule_version="1.0.0",
                    severity=TemporalFindingSeverity.MEDIUM,
                    category=TemporalFindingCategory.TIMESTAMP_ALIGNMENT,
                    title="Timezone heterogeneity detected in observation stream",
                    evidence={"tz_types_seen": sorted(list(tz_types_seen))},
                    explanation="Observations incorporate heterogeneous timezone representations or mix naive and aware timestamps.",
                    is_definitive=False,
                    confidence=ConfidenceClassification.HIGH_HEURISTIC,
                    recommended_remediation="Standardize all timestamps explicitly to UTC ISO-8601 representation.",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    input_digest=self.input_digest,
                    result_state=TemporalResultState.WARNING,
                )
            )

        return findings


# -----------------------------------------------------------------------------
# Main Orchestrating Detector Engine
# -----------------------------------------------------------------------------

class TemporalIntegrityDetector:
    """Authoritative deterministic temporal integrity and lookahead leakage detector."""

    def __init__(self, detector_version: str = DETECTOR_VERSION) -> None:
        self.detector_version = detector_version

    def compute_input_digest(self, payload: Any) -> str:
        """Compute deterministic SHA-256 digest of input artifacts."""
        if isinstance(payload, str):
            canonical_bytes = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            canonical_bytes = payload
        else:
            try:
                canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            except Exception:
                canonical_bytes = str(payload).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def audit_source_code(self, source_code: str, hypothesis_id: Optional[str] = None) -> TemporalIntegrityAuditReport:
        """Perform deterministic AST inspection on Python strategy source code."""
        input_digest = self.compute_input_digest(source_code)
        audit_id = f"TI-AUDIT-{input_digest[:12].upper()}"

        try:
            tree = ast.parse(source_code)
            visitor = StrategyASTVisitor(source_code=source_code, input_digest=input_digest)
            visitor.visit(tree)
            findings = visitor.findings
        except SyntaxError as syn_err:
            findings = [
                TemporalIntegrityFinding(
                    finding_id="TI-FIND-SYNTAX-ERR",
                    rule_id="RULE-TI-00-SYNTAX-ERROR",
                    rule_version="1.0.0",
                    severity=TemporalFindingSeverity.HIGH,
                    category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                    title="Source code syntax error prevented AST temporal analysis",
                    evidence={"syntax_error": str(syn_err), "line_number": syn_err.lineno},
                    explanation=f"Source code could not be parsed: {str(syn_err)}",
                    is_definitive=False,
                    confidence=ConfidenceClassification.INCONCLUSIVE,
                    recommended_remediation="Ensure strategy code is valid Python syntax before audit.",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    input_digest=input_digest,
                    result_state=TemporalResultState.INCONCLUSIVE,
                )
            ]

        return self._resolve_disposition(
            audit_id=audit_id,
            input_digest=input_digest,
            findings=findings,
            hypothesis_id=hypothesis_id,
        )

    def audit_proposal(
        self,
        hypothesis_id: str,
        objective: str,
        economic_rationale: Optional[str] = None,
        strategy_source: Optional[str] = None,
        strategy_metadata: Optional[Dict[str, Any]] = None,
    ) -> TemporalIntegrityAuditReport:
        """Comprehensive audit of a research proposal across code, objective, and metadata."""
        aggregated_payload = {
            "hypothesis_id": hypothesis_id,
            "objective": objective,
            "economic_rationale": economic_rationale,
            "strategy_source": strategy_source,
            "strategy_metadata": strategy_metadata,
        }
        input_digest = self.compute_input_digest(aggregated_payload)
        audit_id = f"TI-AUDIT-{input_digest[:12].upper()}"

        findings: List[TemporalIntegrityFinding] = []

        # 1. Audit objective and rationale text (e.g. TASK-52 detection)
        spec_auditor = StrategySpecificationAuditor(input_digest=input_digest)
        combined_text = f"{objective}\n{economic_rationale or ''}"
        findings.extend(spec_auditor.audit_objective_and_rationale(combined_text))

        # 2. Audit structured strategy metadata (timing, fitting protocols)
        if strategy_metadata:
            findings.extend(spec_auditor.audit_metadata(strategy_metadata))

        # 3. Audit strategy source code if provided
        if strategy_source:
            try:
                tree = ast.parse(strategy_source)
                visitor = StrategyASTVisitor(source_code=strategy_source, input_digest=input_digest)
                visitor.visit(tree)
                findings.extend(visitor.findings)
            except SyntaxError as syn_err:
                findings.append(
                    TemporalIntegrityFinding(
                        finding_id="TI-FIND-SYNTAX-ERR",
                        rule_id="RULE-TI-00-SYNTAX-ERROR",
                        rule_version="1.0.0",
                        severity=TemporalFindingSeverity.HIGH,
                        category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                        title="Source code syntax error prevented AST temporal analysis",
                        evidence={"syntax_error": str(syn_err)},
                        explanation=f"Source code could not be parsed: {str(syn_err)}",
                        is_definitive=False,
                        confidence=ConfidenceClassification.INCONCLUSIVE,
                        recommended_remediation="Ensure strategy code is valid Python syntax before audit.",
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        input_digest=input_digest,
                        result_state=TemporalResultState.INCONCLUSIVE,
                    )
                )

        return self._resolve_disposition(
            audit_id=audit_id,
            input_digest=input_digest,
            findings=findings,
            hypothesis_id=hypothesis_id,
        )

    def audit_runtime_data(
        self,
        observations: List[Dict[str, Any]],
        decision_timestamp: Optional[Union[str, datetime]] = None,
        hypothesis_id: Optional[str] = None,
    ) -> TemporalIntegrityAuditReport:
        """Perform runtime assertions on candle streams and feature records."""
        input_digest = self.compute_input_digest({"len": len(observations), "decision": str(decision_timestamp)})
        audit_id = f"TI-AUDIT-{input_digest[:12].upper()}"

        validator = RuntimeTemporalAssertionValidator(input_digest=input_digest)
        findings = validator.validate_observations(observations, decision_timestamp=decision_timestamp)

        return self._resolve_disposition(
            audit_id=audit_id,
            input_digest=input_digest,
            findings=findings,
            hypothesis_id=hypothesis_id,
        )

    def audit_custom_transformation(
        self,
        transformation_name: str,
        is_supported: bool = False,
        hypothesis_id: Optional[str] = None,
    ) -> TemporalIntegrityAuditReport:
        """Audit an unsupported or unrecognized transformation (TI-16)."""
        input_digest = self.compute_input_digest({"transformation": transformation_name})
        audit_id = f"TI-AUDIT-{input_digest[:12].upper()}"

        findings: List[TemporalIntegrityFinding] = []
        if not is_supported:
            findings.append(
                TemporalIntegrityFinding(
                    finding_id="TI-FIND-UNSUPPORTED-XFORM",
                    rule_id="RULE-TI-08-UNSUPPORTED-TRANSFORMATION",
                    rule_version="1.0.0",
                    severity=TemporalFindingSeverity.LOW,
                    category=TemporalFindingCategory.FUTURE_DATA_ACCESS,
                    title=f"Unrecognized or unsupported transformation: {transformation_name}",
                    evidence={"transformation_name": transformation_name},
                    explanation="Transformation is not recognized by temporal integrity analyzer; causal proof cannot be certified.",
                    is_definitive=False,
                    confidence=ConfidenceClassification.INCONCLUSIVE,
                    recommended_remediation="Implement custom temporal proof or wrap transformation in verified causal wrapper.",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    input_digest=input_digest,
                    result_state=TemporalResultState.INCONCLUSIVE,
                )
            )

        return self._resolve_disposition(
            audit_id=audit_id,
            input_digest=input_digest,
            findings=findings,
            hypothesis_id=hypothesis_id,
        )

    def _resolve_disposition(
        self,
        audit_id: str,
        input_digest: str,
        findings: List[TemporalIntegrityFinding],
        hypothesis_id: Optional[str] = None,
    ) -> TemporalIntegrityAuditReport:
        """Deterministic resolution of overall audit disposition from collected findings."""
        # Check for definitive prohibited temporal violations
        definitive_fails = [f for f in findings if f.result_state == TemporalResultState.FAIL and f.is_definitive]
        remediable_issues = [
            f for f in findings
            if f.result_state in (TemporalResultState.FAIL, TemporalResultState.WARNING)
            and not f.is_definitive
            and f.severity in (TemporalFindingSeverity.CRITICAL, TemporalFindingSeverity.HIGH)
            and f.confidence != ConfidenceClassification.INCONCLUSIVE
        ]
        heuristic_warnings = [
            f for f in findings
            if (f.result_state in (TemporalResultState.WARNING, TemporalResultState.INCONCLUSIVE)
                and (f.confidence == ConfidenceClassification.INCONCLUSIVE or f.severity not in (TemporalFindingSeverity.CRITICAL, TemporalFindingSeverity.HIGH)))
        ]

        if definitive_fails:
            disposition = TemporalDisposition.REJECT
            rationale = (
                f"REJECT: {len(definitive_fails)} definitive prohibited temporal violation(s) detected. "
                f"Primary defect: {definitive_fails[0].title}. Progression blocked."
            )
            has_definitive = True
        elif remediable_issues:
            disposition = TemporalDisposition.REVISE
            rationale = (
                f"REVISE: {len(remediable_issues)} remediable temporal timing/alignment defect(s) detected. "
                f"Primary issue: {remediable_issues[0].title}. Revisions required before review."
            )
            has_definitive = False
        elif heuristic_warnings:
            disposition = TemporalDisposition.REVIEW_REQUIRED
            rationale = (
                f"REVIEW_REQUIRED: {len(heuristic_warnings)} heuristic or inconclusive observation(s) recorded. "
                "Requires human quantitative investigation."
            )
            has_definitive = False
        else:
            disposition = TemporalDisposition.ELIGIBLE_FOR_REVIEW
            rationale = (
                "ELIGIBLE_FOR_REVIEW: No blocking temporal integrity violations detected within detector coverage scope. "
                "Strategy may proceed to independent research review."
            )
            has_definitive = False

        return TemporalIntegrityAuditReport(
            audit_id=audit_id,
            hypothesis_id=hypothesis_id,
            disposition=disposition,
            disposition_rationale=rationale,
            has_definitive_violation=has_definitive,
            total_findings=len(findings),
            findings=findings,
            detector_version=self.detector_version,
            input_digest=input_digest,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    def verify_override_attempt(
        self,
        audit_report: TemporalIntegrityAuditReport,
        proposed_action: str,
    ) -> None:
        """Assert that an external LLM or agent cannot override a deterministic REJECT."""
        if audit_report.disposition == TemporalDisposition.REJECT:
            if proposed_action in ("APPROVE", "RECOMMEND_APPROVAL", "PROCEED", "TICKET_GENERATION", "OVERRIDE"):
                raise DeterministicOverrideBlockedError(
                    f"Security violation: Deterministic audit rejected proposal with {audit_report.disposition_rationale}. "
                    f"External proposed action '{proposed_action}' is strictly prohibited."
                )
