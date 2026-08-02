"""`RelationshipFeedback` — the raw input a caller (API layer) submits.
The caller states its *intent* explicitly (`kind`) rather than the engine
inferring intent from a confidence delta — an explicit "the user rejected
this" is unambiguous where a confidence-state comparison can be an
inference away from wrong (e.g. two different confirmed states are not
obviously "approval" vs. "correction" without the caller's own framing).
`app.learning_engine.engine.build_learning_event` is the only place
`kind` is interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource

FeedbackKind = Literal[
    "approve",
    "reject",
    "correct_confidence",
    "flag_missing_relationship",
    "flag_weak_evidence",
    "flag_incorrect_explanation",
]

# Kinds that target an existing, already-persisted relationship.
RELATIONSHIP_TARGETING_KINDS: frozenset[FeedbackKind] = frozenset(
    {
        "approve",
        "reject",
        "correct_confidence",
        "flag_weak_evidence",
        "flag_incorrect_explanation",
    }
)


@dataclass(frozen=True)
class RelationshipFeedback:
    repository_id: str
    source: CorrectionSource
    kind: FeedbackKind
    reason: str
    created_at: datetime
    # Required for every kind in RELATIONSHIP_TARGETING_KINDS; must be
    # absent for "flag_missing_relationship" (see LearningEvent's own
    # relationship-key nullability rule).
    relationship_type: str | None = None
    source_entity: str | None = None
    target_entity: str | None = None
    # Only meaningful (and required) for "correct_confidence" — the state
    # the human asserts is correct. `None` there means "reject entirely",
    # matching `UserCorrection.corrected_state`'s own contract exactly.
    corrected_state: ConfidenceState | None = None

    def __post_init__(self) -> None:
        if not self.repository_id.strip():
            raise ValueError("RelationshipFeedback.repository_id must not be empty")
        if not self.reason.strip():
            raise ValueError("RelationshipFeedback.reason must not be empty")
        targets_relationship = self.kind in RELATIONSHIP_TARGETING_KINDS
        has_relationship_ref = bool(
            self.relationship_type and self.source_entity and self.target_entity
        )
        if targets_relationship and not has_relationship_ref:
            raise ValueError(
                f"RelationshipFeedback.relationship_type/source_entity/target_entity are "
                f"required for kind={self.kind!r}"
            )
        if not targets_relationship and has_relationship_ref:
            raise ValueError(
                f"RelationshipFeedback must not reference a relationship for kind={self.kind!r}"
            )
        if self.kind == "correct_confidence" and self.corrected_state is None:
            raise ValueError(
                "RelationshipFeedback.corrected_state is required for kind='correct_confidence'"
            )
