"""The `engineering_events` table — Phase 1 of the frozen implementation
sequencing plan.

This is the minimum event substrate `docs/graphforge/
ENGINEERING_STATE_ARCHITECTURE.md` §8 requires: "the event log... is the
sole write path and the sole source of authoritative truth about this
task's reasoning trace" (Final Contract §11). Every column here exists
because that contract (or the final adversarial sequencing review's own
accepted guardrails) requires it — nothing is speculative:

- `id` — event identity (ES §8 "unique, immutable identifier").
- `task_id` + `sequence_number` — "belong to exactly one task's event
  stream", "single, total, append-determined order" (ES §8 Ordering).
  `(task_id, sequence_number)` is unique, enforced at the database, not
  merely by application discipline — closing the concurrent-append race
  the final adversarial sequencing review's §12 attacked directly.
- `event_type` — closed vocabulary; see `app.engineering_state.events`
  for the Phase 1 set. `CHECK`-constrained at the database as well as
  validated in the repository, so a typo can't silently create a new,
  unrecognized event class.
- `payload` — the event-type-specific data (JSONB; schema per
  `app.engineering_state.events`).
- `schema_version` — ES §9's "Reasoning replay... requires... Knowledge
  index version" and general schema-evolution discipline: the payload
  shape may change across phases; old events must remain readable as
  what they actually said at the time.
- `actor` — provenance ("the Role/actor that invoked" per ES §4). Phase 1
  has no Role abstraction yet (that is Reasoning Engine contract
  territory, Phase 2+), so this is a plain string identifying who/what
  recorded the event (e.g. `"legacy:run_coordinator:agent=<id>"`,
  `"human:<user_id>"`) — enough to satisfy the provenance requirement
  without inventing Role infrastructure this phase doesn't need.
- `causation_event_id` — "every event that arises as a consequence of
  another... MUST reference the causing record(s) by identifier" (ES §8
  Causal relationships). Self-referential, nullable (a `GoalCreated`
  event has no cause within this log).
- `execution_context` — ES §7's Execution Context binding, "where
  applicable" (Phase 1 instruction §2): populated for event types that
  carry a claim about the software system (Evidence, Observation), null
  for ones that don't (Goal, Plan, Decision).
- `recorded_at` — when this event was durably appended. Not an "occurred
  at" distinct from "recorded at": Phase 1 has no reconciliation/delta
  concept yet (that is ES §8 territory for a later phase), so collapsing
  the two here is not a simplification of the contract, it is simply not
  yet needing the distinction.

Deliberately absent: any `updated_at` column. There is nothing to update.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.engineering_state.events import EVENT_TYPES

# `sa.CheckConstraint`'s SQL text, built once from the same closed
# vocabulary the repository validates against in Python — one source of
# truth for "what event types exist", enforced twice (DB + app), never
# defined independently in two places that could drift apart.
_EVENT_TYPE_LIST_SQL = ", ".join(f"'{t}'" for t in sorted(EVENT_TYPES))


class EngineeringEvent(Base):
    """One immutable fact in one task's Engineering State event log.

    No `update`/`delete` capability is exposed anywhere in this codebase
    for this model — see `app.repositories.engineering_event_repository`,
    which defines only `append`/`list_for_task`. The append-only property
    is additionally enforced at the database level by triggers created in
    this table's migration (`engineering_events_forbid_update`/
    `_forbid_delete`) — not merely by the absence of application code that
    would violate it. See the final adversarial sequencing review, §14,
    guardrail: "DB-permission-level enforcement... on the events table."
    """

    __tablename__ = "engineering_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    task_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence_number: Mapped[int] = mapped_column(nullable=False)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    actor: Mapped[str] = mapped_column(String(255), nullable=False)

    causation_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("engineering_events.id", ondelete="RESTRICT"), nullable=True
    )
    execution_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("task_id", "sequence_number", name="uq_engineering_events_task_sequence"),
        Index("ix_engineering_events_task_id", "task_id"),
        CheckConstraint(
            f"event_type IN ({_EVENT_TYPE_LIST_SQL})",
            name="ck_engineering_events_event_type",
        ),
        CheckConstraint("sequence_number > 0", name="ck_engineering_events_sequence_positive"),
    )
