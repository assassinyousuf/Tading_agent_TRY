"""Phase 2.6 Research Evidence Quality and Reproducibility Framework.

Establishes:
1. Immutable experiment evidence manifests.
2. Independent reproducibility verification engine.
3. Provenance and audit-chain tracking.
4. Failure, negative-result, and crash preservation.
5. Deterministic multi-dimensional evidence reporting.

Strict safety invariants:
- Zero live trading, paper trading, testnet, or exchange credentials.
- Strictly TRAIN partition data. Zero access to TEST or VALIDATION partitions.
- Zero em dashes (\\u2014) or en dashes (\\u2013).
"""

from trader.agent.evidence.manifest import (
    EvidenceManifestContradictionError,
    EvidenceManifestError,
    EvidenceManifestPersistenceError,
    ExperimentEvidenceManifest,
    ProvenanceType,
    compute_manifest_hash,
    generate_evidence_manifest,
)
from trader.agent.evidence.reproducibility import (
    ReproducibilityEngine,
    ReproducibilityResult,
    ReproducibilityTolerances,
    ReproducibilityVerdict,
)
from trader.agent.evidence.provenance import (
    ProvenanceRecord,
    ProvenanceTracker,
)
from trader.agent.evidence.reporting import (
    DeterministicEvidenceReporter,
    EvidenceEvaluationDimensions,
)

__all__ = [
    "EvidenceManifestError",
    "EvidenceManifestContradictionError",
    "EvidenceManifestPersistenceError",
    "ExperimentEvidenceManifest",
    "ProvenanceType",
    "compute_manifest_hash",
    "generate_evidence_manifest",
    "ReproducibilityEngine",
    "ReproducibilityResult",
    "ReproducibilityTolerances",
    "ReproducibilityVerdict",
    "ProvenanceRecord",
    "ProvenanceTracker",
    "DeterministicEvidenceReporter",
    "EvidenceEvaluationDimensions",
]

