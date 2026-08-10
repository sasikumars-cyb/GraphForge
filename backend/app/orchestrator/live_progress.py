"""Best-effort, out-of-band live progress checkpoints for a running agent.

The smallest change that gets a genuinely *live* signal out of a reasoning
loop that otherwise runs synchronously inside one agent step, start to
finish, with a single commit at the end (see `RunCoordinator.execute_run`'s
own docstring on that atomicity guarantee). Rather than touching that
guarantee — restructuring execution to stream/commit incrementally through
the main session would be a real redesign of the execution engine, exactly
what was ruled out — this writes through a **separate, independent session**
opened and closed for each checkpoint, so it can never participate in, block
on, or corrupt the main run's transaction. If this write fails, it is logged
and swallowed: a stalled or unreachable checkpoint write must never fail the
investigation it was reporting on, the same isolation Investigation
Intelligence's own writes already have (see
`app.investigation_intelligence.service`).

Every consumer treats `Run.live_progress` as optional: `None` means either
"nothing has been written yet" or "this agent doesn't opt in" — both read
identically as "no live progress to show," never as an error.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import update

from app.database.session import AsyncSessionLocal
from app.models.run import Run

logger = logging.getLogger(__name__)

StepStatus = Literal["done", "active"]


class LiveProgressStep(BaseModel):
    label: str
    status: StepStatus


class LiveProgress(BaseModel):
    iteration: int
    max_iterations: int
    steps: list[LiveProgressStep] = Field(default_factory=list)


async def write_live_progress(run_id: uuid.UUID, progress: LiveProgress) -> None:
    """Persist one checkpoint. Never raises — see this module's docstring."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Run).where(Run.id == run_id).values(live_progress=progress.model_dump())
            )
            await db.commit()
    except Exception:
        logger.exception("live_progress_write_failed run_id=%s", run_id)
