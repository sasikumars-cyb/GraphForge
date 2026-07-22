"""AI-enriched pull request analysis endpoints."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.factory import create_llm_provider
from app.ai.services.ai_analysis_service import AIAnalysisService
from app.analysis.engine.impact_analysis_engine import ImpactAnalysisEngine
from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.github import GitHubVersionControlProvider
from app.models.pull_request import PullRequest
from app.models.pull_request_ai_analysis import PullRequestAIAnalysis
from app.models.repository import Repository
from app.models.user import User
from app.schemas.ai_analysis import AIAnalysisResponse, AIAnalysisResultResponse

router = APIRouter(prefix="/pull-requests", tags=["ai-analysis"])


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


def _build_ai_service(db: AsyncSession) -> AIAnalysisService:
    driver = get_driver()
    impact_engine = ImpactAnalysisEngine(
        db=db,
        graph_repository=Neo4jGraphRepository(driver),
        impact_graph_reader=Neo4jImpactGraphReader(driver),
        version_control_provider=GitHubVersionControlProvider(),
    )
    llm_provider = create_llm_provider()
    return AIAnalysisService(
        db=db,
        llm_provider=llm_provider,
        impact_engine=impact_engine,
    )


@router.post(
    "/{pull_request_id}/ai-analysis",
    response_model=AIAnalysisResultResponse,
    summary="Run AI-enriched impact analysis",
    description=(
        "Runs AI analysis on a pull request. Ensures deterministic analysis "
        "exists first (runs it if missing), then enriches with LLM insights. "
        "Replaces any prior AI analysis for this pull request."
    ),
)
async def run_ai_analysis(
    pull_request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AIAnalysisResultResponse:
    """Trigger AI-enriched analysis for a pull request."""
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)
    service = _build_ai_service(db)
    result = await service.analyze(pull_request.id)
    return AIAnalysisResultResponse(
        executive_summary=result.executive_summary,
        breaking_changes=[bc.model_dump() for bc in result.breaking_changes],
        migration_advice=[ma.model_dump() for ma in result.migration_advice],
        suggested_reviewers=[sr.model_dump() for sr in result.suggested_reviewers],
        regression_tests=[rt.model_dump() for rt in result.regression_tests],
        release_coordination_plan=result.release_coordination_plan.model_dump(),
        confidence=result.confidence.model_dump(),
        prompt_version=result.prompt_version,
    )


@router.get(
    "/{pull_request_id}/ai-analysis",
    response_model=AIAnalysisResponse,
    summary="Get AI analysis result",
    description=(
        "Returns the most recently computed AI analysis for this pull request. "
        "Returns 404 if POST .../ai-analysis hasn't been run yet."
    ),
)
async def get_ai_analysis(
    pull_request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PullRequestAIAnalysis:
    """Retrieve the persisted AI analysis for a pull request."""
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)

    result = await db.execute(
        select(PullRequestAIAnalysis).where(
            PullRequestAIAnalysis.pull_request_id == pull_request.id
        )
    )
    ai_analysis = result.scalar_one_or_none()
    if ai_analysis is None:
        raise NotFoundError("No AI analysis has been run for this pull request yet.")
    return ai_analysis
