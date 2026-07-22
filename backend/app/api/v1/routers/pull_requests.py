"""Phase 7: deterministic pull request impact analysis."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.engine.impact_analysis_engine import ImpactAnalysisEngine
from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.api.v1.dependencies import get_current_user
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.factory import create_version_control_provider
from app.models.pull_request import PullRequest
from app.models.pull_request_analysis import PullRequestAnalysis
from app.models.repository import Repository
from app.models.user import User
from app.schemas.analysis import PullRequestAnalysisResponse

router = APIRouter(prefix="/pull-requests", tags=["pull-requests"])


async def _get_owned_pull_request(
    db: AsyncSession, pull_request_id: uuid.UUID, current_user: User
) -> PullRequest:
    result = await db.execute(
        select(PullRequest)
        .join(Repository, PullRequest.repository_id == Repository.id)
        .where(PullRequest.id == pull_request_id, Repository.user_id == current_user.id)
    )
    pull_request = result.scalar_one_or_none()
    if pull_request is None:
        raise NotFoundError("Pull request not found.")
    return pull_request


def _build_engine(db: AsyncSession) -> ImpactAnalysisEngine:
    driver = get_driver()
    return ImpactAnalysisEngine(
        db=db,
        graph_repository=Neo4jGraphRepository(driver),
        impact_graph_reader=Neo4jImpactGraphReader(driver),
        version_control_provider=create_version_control_provider(get_settings()),
    )


@router.post("/{pull_request_id}/analyze", response_model=PullRequestAnalysisResponse)
async def analyze_pull_request(
    pull_request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PullRequestAnalysis:
    """Computes a fresh analysis and persists it, replacing any prior
    analysis for this pull request. Synchronous - unlike repository
    indexing, this is just Neo4j queries plus one GitHub API call, not a
    clone-and-parse pipeline, so there's no need for a background job."""
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)
    engine = _build_engine(db)
    return await engine.analyze_pull_request(pull_request.id)


@router.get("/{pull_request_id}/analysis", response_model=PullRequestAnalysisResponse)
async def get_pull_request_analysis(
    pull_request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PullRequestAnalysis:
    """Returns the most recently computed analysis - 404 if `POST
    .../analyze` hasn't been run for this pull request yet."""
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)

    result = await db.execute(
        select(PullRequestAnalysis).where(PullRequestAnalysis.pull_request_id == pull_request.id)
    )
    analysis = result.scalar_one_or_none()
    if analysis is None:
        raise NotFoundError("No analysis has been run for this pull request yet.")
    return analysis
