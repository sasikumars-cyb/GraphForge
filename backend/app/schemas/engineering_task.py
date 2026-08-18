"""Request/response schemas for `POST`/`GET /api/v1/engineering-tasks` —
Phase 7's minimal end-to-end integration, plus the Phase 7.1 read-only
visibility slice.

Deliberately exposes only what these two endpoints need to demonstrate:
no internal Grant/Policy details, no raw Engineering State payloads.
`EngineeringTaskResponse` is shared, unmodified in meaning, between
create (`POST`) and retrieve (`GET`) — both are built by the same
`app.services.engineering_task_service._build_response` helper from the
same materialized state, so the two views can never drift.
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
    no raw Grant/Tool internals exposed."""

    success: bool | None
    outcome: str | None
    classification: str | None
    actor: str | None


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


__all__ = [
    "CreateEngineeringTaskRequest",
    "EngineeringTaskGoal",
    "EngineeringTaskObservation",
    "EngineeringTaskPlanStep",
    "EngineeringTaskResponse",
]
