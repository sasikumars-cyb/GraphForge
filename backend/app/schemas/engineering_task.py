"""Request/response schemas for `POST /api/v1/engineering-tasks` — Phase
7's minimal end-to-end integration.

Deliberately exposes only what this first slice needs to demonstrate:
no internal Grant/Policy details, no raw Engineering State payloads.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class CreateEngineeringTaskRequest(BaseModel):
    description: str = Field(..., min_length=1, description="The Goal's description.")
    postconditions: list[str] = Field(
        ..., min_length=1, description="Checkable postconditions for the Goal (ES §11)."
    )


class EngineeringTaskObservation(BaseModel):
    """A minimal, non-internal view of one `ObservationRecorded` event —
    no raw Grant/Tool internals exposed."""

    success: bool | None
    classification: str | None
    actor: str | None


class EngineeringTaskResponse(BaseModel):
    task_id: uuid.UUID
    goal_event_id: uuid.UUID
    plan_event_id: uuid.UUID
    plan_step_event_id: uuid.UUID
    generator_observation: EngineeringTaskObservation
    verifier_observation: EngineeringTaskObservation


__all__ = [
    "CreateEngineeringTaskRequest",
    "EngineeringTaskObservation",
    "EngineeringTaskResponse",
]
