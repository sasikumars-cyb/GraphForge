"""`POST`/`GET /api/v1/engineering-tasks` — Phase 7's minimal end-to-end
integration entry point, the Phase 7.1 read-only visibility slice, and
Phase 7.2's productization (list view).

The first, and currently only, production entry point into the Phase 1-6
Engineering State / Control Plane stack. Deliberately API-only-for-create
(no create UI in this phase) and deliberately independent of the legacy
Workflow/AgentRun/RunCoordinator system — see the Phase 7 design's
Option A rationale.

**This module appends exactly one Engineering State event type directly:
`GoalCreated`** (via `EngineeringTaskService`, the narrow approved
exception — the authenticated API boundary is the human's verified
proxy for their own Goal). It never appends `Authorization*`,
`Workspace*`, or `ObservationRecorded` events — those reach Engineering
State only through `ControlPlane`, structurally enforced by
`tests/unit/architecture/test_reasoning_plane_boundary.py`.

**`GET /{task_id}` (Phase 7.1) and `GET ""` (Phase 7.2, the list view)
are both pure read paths** — they call `get_engineering_task`/
`list_engineering_tasks`, functions that import and construct no
`ControlPlane`/`ReasoningPlane`/`CapabilityRegistry`/`PolicyStore`/
`ToolRegistry` at all (unlike the `POST` handler immediately below,
which needs all of them). Neither reads the legacy `Run`/`Workflow`/
`AgentRun` models — only `EngineeringEventRepository`/`fold()`.

**Known limitation, as of this phase:** Engineering State carries no
per-user/tenant field anywhere yet, so `GET ""` and `GET /{task_id}`
return/accept every task in the system to/from any authenticated user,
not scoped to the caller's own. This is tracked as follow-up work, not
fixed in this phase.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.capabilities.registry import CapabilityRegistry
from app.control_plane.policy import PolicyStore
from app.control_plane.runtime import get_capability_registry, get_policy_store
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.engineering_task import (
    CreateEngineeringTaskRequest,
    EngineeringTaskResponse,
    EngineeringTaskSummary,
)
from app.services.engineering_task_service import (
    EngineeringTaskService,
    get_engineering_task,
    list_engineering_tasks,
)
from app.tools.registry import ToolRegistry, get_tool_registry

router = APIRouter(prefix="/engineering-tasks", tags=["engineering-tasks"])


@router.post("", response_model=EngineeringTaskResponse, status_code=201)
async def create_engineering_task(
    body: CreateEngineeringTaskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    capability_registry: CapabilityRegistry = Depends(get_capability_registry),
    policy_store: PolicyStore = Depends(get_policy_store),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> EngineeringTaskResponse:
    """Run one complete Engineering State task end-to-end, synchronously:

        GoalCreated -> ReasoningPlane -> PlanCreated/PlanStepCreated
        -> ActionProposal -> ControlPlane authorization pipeline
        -> query_knowledge_graph -> ObservationRecorded
        -> Independent Verification -> verifier ObservationRecorded

    Synchronous by design for this first slice: the whole point is to
    prove the stack executes end-to-end, and a single read-only graph
    query is fast enough not to need the background-job machinery the
    legacy system uses.

    `capability_registry`/`policy_store`/`tool_registry` are ordinary
    FastAPI dependencies (not plain function calls) specifically so
    tests can override them via `app.dependency_overrides`, mirroring
    this codebase's own established `db_client`/`get_db_session`
    convention exactly — real Neo4j is not part of this test suite's
    infrastructure (confirmed: every existing test exercising
    `query_knowledge_graph` uses a fake Tool), so integration tests
    substitute a locally-built registry bound to a fake Tool the same
    way every prior phase's own tests already do. `get_tool_registry`
    itself is unmodified, already-public (`app.tools.registry`); this
    is the first caller to inject it as a FastAPI dependency rather than
    calling it directly.
    """
    service = EngineeringTaskService(
        db=db,
        capability_registry=capability_registry,
        policy_store=policy_store,
        tool_registry=tool_registry,
    )
    return await service.create_and_execute(
        description=body.description,
        postconditions=body.postconditions,
        user_id=user.id,
    )


@router.get("", response_model=list[EngineeringTaskSummary])
async def list_engineering_tasks_endpoint(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[EngineeringTaskSummary]:
    """List every Engineering Task, read-only, newest first.

    `user` gates authenticated access only — it does not scope the
    result. See this module's own docstring for the known limitation.
    """
    return await list_engineering_tasks(db=db)


@router.get("/{task_id}", response_model=EngineeringTaskResponse)
async def get_engineering_task_endpoint(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> EngineeringTaskResponse:
    """Retrieve one task's materialized Engineering State, read-only.

    `user` gates authenticated access only — it does not scope the
    result. See this module's own docstring for the known limitation.

    404s when no Engineering State exists for `task_id` — never a
    fabricated empty response.
    """
    result = await get_engineering_task(db=db, task_id=task_id)
    if result is None:
        raise NotFoundError(f"No engineering task found for id {task_id}.")
    return result
