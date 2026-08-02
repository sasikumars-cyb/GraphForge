"""ADR 0018 RFC-07 — Learning & Feedback Engine.

Observes user feedback on already-persisted `KnowledgeRelationship`s and
turns it into `LearningEvent`s: an append-only signal store, separate from
Engineering Memory, that future RFCs may read from (prompt evolution,
validator/confidence calibration, a recommendation engine, repository
health scoring, organization-wide learning, model benchmarking — see
`app/learning_engine/aggregation.py`'s module docstring for how each of
those can be built on top of this without a schema change).

This package never indexes, generates hypotheses, validates, or computes
confidence — it only records, aggregates, and exposes signals about
decisions those other components already made. `app.knowledge_engine` and
everything it depends on remain completely unaware this package exists.
"""

from __future__ import annotations
