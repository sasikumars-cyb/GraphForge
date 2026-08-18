"""Request/response schemas for `POST`/`GET /api/v1/engineering-tasks` —
Phase 7's minimal end-to-end integration, the Phase 7.1 read-only
visibility slice, Phase 7.2's productization (list view), and Phase 8's
Observation/Evidence detail surfacing.

Deliberately exposes only what these endpoints need to demonstrate: no
internal Grant/Policy details, no raw Engineering State payloads.
`EngineeringTaskResponse` is shared, unmodified in meaning, between
create (`POST`) and retrieve (`GET`) — both are built by the same
`app.services.engineering_task_service._build_response` helper from the
same materialized state, so the two views can never drift.
`EngineeringTaskSummary` (Phase 7.2) is a thinner, list-oriented
projection built from that identical helper's output, for the same
reason — the list and detail views can never disagree about one task.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateEngineeringTaskRequest(BaseModel):
    description: str = Field(..., min_length=1, description="The Goal's description.")
    postconditions: list[str] = Field(
        ..., min_length=1, description="Checkable postconditions for the Goal (ES §11)."
    )


class EngineeringTaskGoal(BaseModel):
    """The Goal's own durable content — ES §11."""

    description: str
    postconditions: list[str]


class EngineeringTaskPlanStep(BaseModel):
    """The PlanStep's own durable content — ES §11 / Cap §15.1. `invalidated`
    reflects any later `PlanStepInvalidated` overlay (Phase 6), unmodified."""

    event_id: uuid.UUID
    description: str
    postcondition: str
    invalidated: bool


class EngineeringTaskObservation(BaseModel):
    """A minimal, non-internal view of one `ObservationRecorded` event —
    no raw Grant/Tool internals exposed.

    Phase 8 (Observation/Evidence Detail Surfacing) adds `summary`,
    `error`, and `capability` — all already durably recorded on the
    event (`raw_result.summary`/`raw_result.error`/`capability`;
    Phase 8 Design Audit §2), never fetched live from a Tool and never
    fabricated here. `summary`/`error` are passed through
    `app.core.redact.redact_secrets` before being set on this model —
    known credential/token-shaped patterns are redacted; this is NOT a
    claim of perfect semantic secrecy (Phase 8 Design Audit §4)."""

    success: bool | None
    outcome: str | None
    classification: str | None
    actor: str | None
    summary: str | None
    error: str | None
    capability: str | None


class EngineeringTaskResponse(BaseModel):
    task_id: uuid.UUID
    created_at: datetime
    goal_event_id: uuid.UUID
    goal: EngineeringTaskGoal
    plan_event_id: uuid.UUID
    plan_step_event_id: uuid.UUID
    # `None` only in the theoretical, never-durably-observable case of a
    # task whose PlanStep hasn't been created yet — see
    # `app.services.engineering_task_service._build_response`'s own
    # docstring on why this is defensive, not an expected real shape.
    plan_step: EngineeringTaskPlanStep | None
    generator_observation: EngineeringTaskObservation
    verifier_observation: EngineeringTaskObservation


class EngineeringTaskSummary(BaseModel):
    """One row in the Engineering Task list — Phase 7.2. Deliberately
    thinner than `EngineeringTaskResponse`: no Plan Step, no raw actor,
    just what a list view needs to be useful and honest.

    `classification` is the VERIFIER's classification
    (`verifier_observation.classification`), never the generator's raw,
    unverified one — Cap §15/§16: Independent Verification is the only
    classification a caller should treat as the trustworthy "how did
    this turn out" signal for a task-level summary. `None` only in the
    same theoretical not-yet-verified case `EngineeringTaskResponse`
    already tolerates defensively.

    `updated_at` is the `recorded_at` of the task's most recent event —
    genuinely present on every `EngineeringEvent` already, not a
    fabricated field: Engineering State events are immutable, so "most
    recent event's timestamp" is the honest, existing notion of "last
    activity" for a task, not a new concept invented for this list.
    """

    task_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    description: str
    classification: str | None


__all__ = [
    "CreateEngineeringTaskRequest",
    "EngineeringTaskGoal",
    "EngineeringTaskObservation",
    "EngineeringTaskPlanStep",
    "EngineeringTaskResponse",
    "EngineeringTaskSummary",
]
