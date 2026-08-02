"""`LearningEvent` — one immutable observation about how a piece of
engineering knowledge was judged, by a human or (in the future) an agent.

Reuses `app.knowledge_engine.contracts.correction.CorrectionSource`
rather than duplicating an identity/trust-level shape — the "who is
giving this feedback and how much do we trust them" question is already
answered by RFC-04's correction contract, and a `LearningEvent` is, at
its core, always traceable back to either a correction or an explicit
observation about one.

`relationship_key`/`relationship_type` are both nullable: a
`missing_relationship` event reports the *absence* of a relationship the
user expected, so there is no existing relationship to key it to. Every
other event type refers to a specific, already-persisted relationship and
must populate both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.knowledge_engine.contracts.correction import CorrectionSource

LearningEventType = Literal[
    "approved_relationship",
    "rejected_relationship",
    "missing_relationship",
    "weak_evidence",
    "incorrect_explanation",
    "low_confidence",
    "high_confidence",
    "repeated_false_positive",
]

_RELATIONSHIP_SCOPED_EVENT_TYPES: frozenset[LearningEventType] = frozenset(
    {
        "approved_relationship",
        "rejected_relationship",
        "weak_evidence",
        "incorrect_explanation",
        "low_confidence",
        "high_confidence",
        "repeated_false_positive",
    }
)


@dataclass(frozen=True)
class LearningEvent:
    """A single append-only fact. Never mutated once created — a change of
    mind is a *new* `LearningEvent`, never an edit to this one, the same
    invariant `UserCorrectionRecord`/`KnowledgeRelationshipRecord` already
    enforce for their own histories."""

    id: str
    repository_id: str
    event_type: LearningEventType
    source: CorrectionSource
    detail: str
    created_at: datetime
    relationship_key: str | None = None
    relationship_type: str | None = None
    # Every generator whose provenance contributed to the relationship
    # being judged, if known — the primitive future calibration/
    # benchmarking RFCs need to attribute feedback to a specific
    # generator or validator without this contract changing shape.
    generator_names: tuple[str, ...] = ()
    confidence_state_at_event: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("LearningEvent.id must not be empty")
        if not self.repository_id.strip():
            raise ValueError("LearningEvent.repository_id must not be empty")
        if not self.detail.strip():
            raise ValueError("LearningEvent.detail must not be empty")
        if self.event_type in _RELATIONSHIP_SCOPED_EVENT_TYPES:
            if not self.relationship_key or not self.relationship_type:
                raise ValueError(
                    f"LearningEvent.relationship_key and relationship_type are required "
                    f"for event_type={self.event_type!r}"
                )
        elif self.event_type == "missing_relationship" and self.relationship_key is not None:
            raise ValueError(
                "LearningEvent.relationship_key must be None for missing_relationship "
                "events — there is no existing relationship to key it to"
            )
