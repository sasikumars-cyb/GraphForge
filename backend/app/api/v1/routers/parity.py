"""Graph Parity Engine REST API — read-only. Runs a live comparison via
`app.services.parity_service.run_parity_check` (which itself only reads —
`IGraphRepository.get_full_graph` and `materialize_repository_graph`,
never a `replace_*` write) and returns the resulting `ParityReport`.

No persistence, no feature flag, no write path anywhere in this router —
it does not implement Shadow Mode or Production Cutover, only the ability
to ask "are these two graphs the same, right now."
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.repository import Repository
from app.models.user import User
from app.schemas.parity import ParityReportResponse
from app.services.parity_service import run_parity_check

router = APIRouter(prefix="/repositories/{repository_id}/parity", tags=["parity"])


async def _get_owned_repository(
    db: AsyncSession, repository_id: uuid.UUID, current_user: User
) -> Repository:
    result = await db.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.user_id == current_user.id
        )
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found.")
    return repository


@router.get("", response_model=ParityReportResponse)
async def get_parity_report(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ParityReportResponse:
    """Runs a fresh parity comparison and returns it — no caching, no
    stored history: the report always reflects the current state of Neo4j
    and Engineering Memory at the moment of the request."""
    await _get_owned_repository(db, repository_id, current_user)

    graph_repository = Neo4jGraphRepository(get_driver())
    report = await run_parity_check(db, graph_repository, repository_id)
    return ParityReportResponse.model_validate(report)
