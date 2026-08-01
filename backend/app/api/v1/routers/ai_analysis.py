"""AI-enriched pull request analysis endpoints."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import STAGE_REVIEW, StageAwareLLMProvider
from app.ai.agent.investigation_agent import InvestigationAgent
from app.ai.schemas.analysis_result import (
    AIAnalysisResult,
    BreakingChange,
    ConfidenceScore,
    FileReview,
    Finding,
    MigrationAdvice,
    RegressionTest,
    ReleaseCoordinationPlan,
    SuggestedReviewer,
)
from app.ai.services.ai_analysis_service import AIAnalysisService
from app.ai.services.github_comment_formatter import format_review_comment
from app.ai.services.pr_review_report import (
    ReviewReportContext,
    render_html_report,
    render_json_report,
    render_markdown_report,
)
from app.analysis.engine.impact_analysis_engine import ImpactAnalysisEngine
from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.api.v1.dependencies import get_current_user
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError, UnauthorizedError
from app.database.session import get_db_session
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.factory import create_git_write_provider, create_version_control_provider
from app.models.pull_request import PullRequest
from app.models.pull_request_ai_analysis import PullRequestAIAnalysis
from app.models.pull_request_analysis import PullRequestAnalysis
from app.models.repository import Repository
from app.models.user import User
from app.schemas.ai_analysis import (
    AIAnalysisResponse,
    AIAnalysisResultResponse,
    InvestigationResponse,
    ObservationResponse,
    PublishReviewResponse,
    ReasoningStepResponse,
    RunAIAnalysisRequest,
)
from app.services.github_service import get_decrypted_access_token

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


def _general_review_fields(result: AIAnalysisResult) -> dict[str, Any]:
    """Shared kwargs for the general-purpose review fields every
    ``AIAnalysisResult``-mirroring response carries (see
    ``GeneralReviewFieldsMixin``) — kept in one place so the two call
    sites below (POST .../ai-analysis and POST .../investigate) can't
    drift out of sync with each other or with the schema."""
    return {
        "quality_score": result.quality_score,
        "risk_score": result.risk_score,
        "merge_recommendation": result.merge_recommendation,
        "findings": [f.model_dump() for f in result.findings],
        "architecture_observations": result.architecture_observations,
        "maintainability_observations": result.maintainability_observations,
        "reliability_observations": result.reliability_observations,
        "testing_review": result.testing_review,
        "documentation_review": result.documentation_review,
        "positive_findings": result.positive_findings,
        "suggested_improvements": result.suggested_improvements,
        "security_score": result.security_score,
        "testing_score": result.testing_score,
        "documentation_score": result.documentation_score,
        "architecture_score": result.architecture_score,
        "performance_score": result.performance_score,
        "maintainability_score": result.maintainability_score,
        "file_reviews": [fr.model_dump() for fr in result.file_reviews],
    }


def _build_ai_service(db: AsyncSession, model: str | None = None) -> AIAnalysisService:
    driver = get_driver()
    impact_engine = ImpactAnalysisEngine(
        db=db,
        graph_repository=Neo4jGraphRepository(driver),
        impact_graph_reader=Neo4jImpactGraphReader(driver),
        version_control_provider=create_version_control_provider(get_settings()),
    )
    llm_provider = StageAwareLLMProvider(stage=STAGE_REVIEW, model=model)
    return AIAnalysisService(
        db=db,
        llm_provider=llm_provider,
        impact_engine=impact_engine,
    )


def _build_investigation_agent(db: AsyncSession, model: str | None = None) -> InvestigationAgent:
    driver = get_driver()
    return InvestigationAgent(
        db=db,
        graph_repository=Neo4jGraphRepository(driver),
        impact_graph_reader=Neo4jImpactGraphReader(driver),
        version_control_provider=create_version_control_provider(get_settings()),
        llm_provider=StageAwareLLMProvider(stage=STAGE_REVIEW, model=model),
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
    payload: RunAIAnalysisRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AIAnalysisResultResponse:
    """Trigger AI-enriched analysis for a pull request."""
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)
    service = _build_ai_service(db, model=payload.model if payload else None)
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
        **_general_review_fields(result),
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


@router.post(
    "/{pull_request_id}/investigate",
    response_model=InvestigationResponse,
    summary="Run the Change Investigation Agent",
    description=(
        "Autonomously investigates a pull request: the agent decides which "
        "evidence (graph traversal, cross-repository metadata, indexing "
        "summary, diff, git history) is actually worth gathering before "
        "generating the final AI-enriched impact analysis, and records every "
        "decision - including skips - in the returned reasoning log. Replaces "
        "any prior AI analysis for this pull request, the same as POST "
        ".../ai-analysis."
    ),
)
async def investigate_pull_request(
    pull_request_id: uuid.UUID,
    payload: RunAIAnalysisRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> InvestigationResponse:
    """Trigger the Change Investigation Agent for a pull request."""
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)
    agent = _build_investigation_agent(db, model=payload.model if payload else None)
    investigation = await agent.investigate(pull_request.id)
    result = investigation.analysis
    return InvestigationResponse(
        executive_summary=result.executive_summary,
        breaking_changes=[bc.model_dump() for bc in result.breaking_changes],
        migration_advice=[ma.model_dump() for ma in result.migration_advice],
        suggested_reviewers=[sr.model_dump() for sr in result.suggested_reviewers],
        regression_tests=[rt.model_dump() for rt in result.regression_tests],
        release_coordination_plan=result.release_coordination_plan.model_dump(),
        confidence=result.confidence.model_dump(),
        prompt_version=result.prompt_version,
        **_general_review_fields(result),
        reasoning_log=[
            ReasoningStepResponse(
                step_number=step.step_number,
                goal=step.goal,
                plan=step.plan,
                tool_selected=step.tool_selected,
                observation=(
                    ObservationResponse(
                        tool_name=step.observation.tool_name, summary=step.observation.summary
                    )
                    if step.observation
                    else None
                ),
                decision=step.decision,
            )
            for step in investigation.reasoning_log
        ],
    )


@router.post(
    "/{pull_request_id}/publish-review",
    response_model=PublishReviewResponse,
    summary="Publish the stored AI analysis as a GitHub PR comment",
    description=(
        "Posts the most recently persisted AI analysis for this pull request "
        "(from POST .../ai-analysis or .../investigate) as a comment on the "
        "corresponding GitHub pull request. Does not call the LLM again - "
        "publishes whatever was last computed and stored."
    ),
)
async def publish_review(
    pull_request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PublishReviewResponse:
    """Format the persisted AI analysis as markdown and post it as a real
    GitHub PR comment. Never invokes an LLM provider - the analysis must
    already have been run and persisted via .../ai-analysis or
    .../investigate."""
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)

    ai_result = await db.execute(
        select(PullRequestAIAnalysis).where(
            PullRequestAIAnalysis.pull_request_id == pull_request.id
        )
    )
    ai_analysis = ai_result.scalar_one_or_none()
    if ai_analysis is None:
        raise NotFoundError("No AI analysis has been run for this pull request yet.")

    deterministic_result = await db.execute(
        select(PullRequestAnalysis).where(PullRequestAnalysis.pull_request_id == pull_request.id)
    )
    deterministic = deterministic_result.scalar_one_or_none()

    access_token = await get_decrypted_access_token(db, current_user.id)
    if access_token is None:
        raise UnauthorizedError("GitHub is not connected for this user.")

    repository = await db.get(Repository, pull_request.repository_id)
    if repository is None:
        # FK to repositories.id; the ownership check above already required
        # this row to exist. An explicit raise (not `assert`) so this still
        # fails loudly and clearly if the interpreter is ever run with
        # `-O`/`-OO`, which strips `assert` statements entirely.
        raise AppError(
            f"Repository '{pull_request.repository_id}' referenced by pull request "
            f"'{pull_request.id}' no longer exists.",
            status_code=500,
            error_code="repository_not_found",
        )

    ai_result_model = AIAnalysisResult(
        executive_summary=ai_analysis.executive_summary,
        breaking_changes=[BreakingChange.model_validate(bc) for bc in ai_analysis.breaking_changes],
        migration_advice=[
            MigrationAdvice.model_validate(ma) for ma in ai_analysis.migration_advice
        ],
        suggested_reviewers=[
            SuggestedReviewer.model_validate(sr) for sr in ai_analysis.suggested_reviewers
        ],
        regression_tests=[RegressionTest.model_validate(rt) for rt in ai_analysis.regression_tests],
        release_coordination_plan=ReleaseCoordinationPlan.model_validate(
            ai_analysis.release_coordination_plan or {}
        ),
        confidence=ConfidenceScore(
            score=ai_analysis.confidence_score, reasoning=ai_analysis.confidence_reasoning
        ),
        prompt_version=ai_analysis.prompt_version,
    )

    comment_body = format_review_comment(
        ai_result=ai_result_model,
        risk=deterministic.risk if deterministic else "UNKNOWN",
        directly_impacted_services=(
            deterministic.directly_impacted_services if deterministic else []
        ),
        indirectly_impacted_services=(
            deterministic.indirectly_impacted_services if deterministic else []
        ),
    )

    provider = create_git_write_provider()
    posted = await provider.post_pull_request_comment(
        owner=repository.owner,
        repo=repository.name,
        pull_number=pull_request.number,
        body=comment_body,
        access_token=access_token,
    )

    return PublishReviewResponse(comment_id=posted.id, comment_url=posted.html_url)


_REPORT_MEDIA_TYPES = {
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "json": "application/json",
}


def _ai_analysis_row_to_result(ai_analysis: PullRequestAIAnalysis) -> AIAnalysisResult:
    """Rehydrate the full `AIAnalysisResult` (general-review fields
    included) from the persisted row, the same reconstruction
    `publish_review` already does for the breaking-change fields, extended
    to the fields that route added."""
    return AIAnalysisResult(
        executive_summary=ai_analysis.executive_summary,
        breaking_changes=[BreakingChange.model_validate(bc) for bc in ai_analysis.breaking_changes],
        migration_advice=[
            MigrationAdvice.model_validate(ma) for ma in ai_analysis.migration_advice
        ],
        suggested_reviewers=[
            SuggestedReviewer.model_validate(sr) for sr in ai_analysis.suggested_reviewers
        ],
        regression_tests=[RegressionTest.model_validate(rt) for rt in ai_analysis.regression_tests],
        release_coordination_plan=ReleaseCoordinationPlan.model_validate(
            ai_analysis.release_coordination_plan or {}
        ),
        confidence=ConfidenceScore(
            score=ai_analysis.confidence_score, reasoning=ai_analysis.confidence_reasoning
        ),
        prompt_version=ai_analysis.prompt_version,
        quality_score=ai_analysis.quality_score,
        risk_score=ai_analysis.risk_score,
        merge_recommendation=ai_analysis.merge_recommendation,
        findings=[Finding.model_validate(f) for f in ai_analysis.findings],
        architecture_observations=ai_analysis.architecture_observations,
        maintainability_observations=ai_analysis.maintainability_observations,
        reliability_observations=ai_analysis.reliability_observations,
        testing_review=ai_analysis.testing_review,
        documentation_review=ai_analysis.documentation_review,
        positive_findings=ai_analysis.positive_findings,
        suggested_improvements=ai_analysis.suggested_improvements,
        security_score=ai_analysis.security_score,
        testing_score=ai_analysis.testing_score,
        documentation_score=ai_analysis.documentation_score,
        architecture_score=ai_analysis.architecture_score,
        performance_score=ai_analysis.performance_score,
        maintainability_score=ai_analysis.maintainability_score,
        file_reviews=[FileReview.model_validate(fr) for fr in ai_analysis.file_reviews],
    )


@router.get(
    "/{pull_request_id}/review-report",
    summary="Get the PR Review executive report (HTML/Markdown/JSON)",
    description=(
        "Renders the most recently persisted AI analysis for this pull request "
        "as a standalone executive report. `format=html` (default) returns a "
        "self-contained dark-theme dashboard with no external dependencies; "
        "`format=markdown` returns a Markdown report; `format=json` returns "
        "the structured report. Never invokes an LLM - the analysis must "
        "already have been run via .../ai-analysis or .../investigate."
    ),
)
async def get_review_report(
    pull_request_id: uuid.UUID,
    report_format: Literal["html", "markdown", "json"] = Query("html", alias="format"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    pull_request = await _get_owned_pull_request(db, pull_request_id, current_user)

    ai_result = await db.execute(
        select(PullRequestAIAnalysis).where(
            PullRequestAIAnalysis.pull_request_id == pull_request.id
        )
    )
    ai_analysis = ai_result.scalar_one_or_none()
    if ai_analysis is None:
        raise NotFoundError("No AI analysis has been run for this pull request yet.")

    repository = await db.get(Repository, pull_request.repository_id)
    if repository is None:
        raise AppError(
            f"Repository '{pull_request.repository_id}' referenced by pull request "
            f"'{pull_request.id}' no longer exists.",
            status_code=500,
            error_code="repository_not_found",
        )

    ctx = ReviewReportContext(
        repository=f"{repository.owner}/{repository.name}",
        pull_request_number=pull_request.number,
        pull_request_title=pull_request.title,
        head_ref=pull_request.head_ref,
        base_ref=pull_request.base_ref,
        analyzed_at=ai_analysis.analyzed_at,
        model_used="",
        result=_ai_analysis_row_to_result(ai_analysis),
    )

    if report_format == "html":
        body = render_html_report(ctx)
    elif report_format == "markdown":
        body = render_markdown_report(ctx)
    else:
        body = render_json_report(ctx)

    return Response(content=body, media_type=_REPORT_MEDIA_TYPES[report_format])
