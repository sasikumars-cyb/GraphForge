"""Correction — a human or agent explicitly overriding or rejecting a
`KnowledgeRelationship`'s current state (ADR 0018's Engineering Memory
lifecycle and the "future multi-agent support" amendment from the
architecture freeze review).

`CorrectionSource.kind="agent"` is deliberately *not* granted the same
unconditional override authority `kind="human"` gets by default —
ADR 0018's engineering invariants: "an agent-sourced correction is never an
unconditional override — it flows through the same validation/confidence
pipeline as any other hypothesis; only a human-sourced correction carries
unconditional override authority."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.knowledge_engine.contracts.confidence import ConfidenceState

CorrectionKind = Literal["human", "agent"]


@dataclass(frozen=True)
class CorrectionSource:
    kind: CorrectionKind
    identity: str
    trust_level: float

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("CorrectionSource.identity must not be empty")
        if not (0.0 <= self.trust_level <= 1.0):
            raise ValueError("CorrectionSource.trust_level must be within [0.0, 1.0]")


@dataclass(frozen=True)
class UserCorrection:
    """`corrected_state=None` means "reject this relationship entirely"
    rather than reassigning it to a different `ConfidenceState` — kept as
    an explicit `None` rather than adding a separate boolean flag, since
    the two cases (correct-to-a-different-state vs. reject-outright) are
    mutually exclusive by construction this way.
    """

    id: str
    relationship_id: str
    source: CorrectionSource
    corrected_state: ConfidenceState | None
    reason: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("UserCorrection.id must not be empty")
        if not self.relationship_id.strip():
            raise ValueError("UserCorrection.relationship_id must not be empty")
        if not self.reason.strip():
            raise ValueError("UserCorrection.reason must not be empty")
