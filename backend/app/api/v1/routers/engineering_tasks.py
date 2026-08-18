"""`POST /api/v1/engineering-tasks` — Phase 7's minimal end-to-end
integration entry point.

The first, and currently only, production entry point into the Phase 1-6
Engineering State / Control Plane stack. Deliberately API-only (no UI in
this phase) and deliberately independent of the legacy
Workflow/AgentRun/RunCoordinator system — see the Phase 7 design's
Option A rationale.

**This module appends exactly one Engineering State event type directly:
`GoalCreated`** (via `EngineeringTaskService`, the narrow approved
exception — the authenticated API boundary is the human's verified
proxy for their own Goal). It never appends `Authorization*`,
`Workspace*`, or `ObservationRecorded` events — those reach Engineering
State only through `ControlPlane`, structurally enforced by
`tests/unit/architecture/test_reasoning_plane_boundary.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.capabilities.registry import CapabilityRegistry
from app.control_plane.policy import PolicyStore
from app.control_plane.runtime import get_capability_registry, get_policy_store
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.engineering_task import (
    CreateEngineeringTaskRequest,
    EngineeringTaskResponse,
)
from app.services.engineering_task_service import EngineeringTaskService
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
