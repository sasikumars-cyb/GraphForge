"""Persists an `AIAnalysisResult` to `PullRequestAIAnalysis`.

Extracted from `AIAnalysisService` so `app.ai.agent.InvestigationAgent`
can persist its own (adaptively-gathered) result through the exact same
path, rather than duplicating the upsert logic.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.analysis_result import AIAnalysisResult
from app.models.pull_request_ai_analysis import PullRequestAIAnalysis


async def persist_ai_analysis_result(
    db: AsyncSession, pull_request_id: uuid.UUID, result: AIAnalysisResult
) -> PullRequestAIAnalysis:
    """Insert or replace the AI analysis row for a pull request."""
    stmt = select(PullRequestAIAnalysis).where(
        PullRequestAIAnalysis.pull_request_id == pull_request_id
    )
    existing = await db.execute(stmt)
    ai_analysis = existing.scalar_one_or_none()

    fields: dict[str, object] = {
        "executive_summary": result.executive_summary,
        "breaking_changes": [bc.model_dump() for bc in result.breaking_changes],
        "migration_advice": [ma.model_dump() for ma in result.migration_advice],
        "suggested_reviewers": [sr.model_dump() for sr in result.suggested_reviewers],
        "regression_tests": [rt.model_dump() for rt in result.regression_tests],
        "release_coordination_plan": result.release_coordination_plan.model_dump(),
        "confidence_score": result.confidence.score,
        "confidence_reasoning": result.confidence.reasoning,
        "prompt_version": result.prompt_version,
    }

    if ai_analysis is None:
        ai_analysis = PullRequestAIAnalysis(pull_request_id=pull_request_id, **fields)
        db.add(ai_analysis)
    else:
        for field_name, value in fields.items():
            setattr(ai_analysis, field_name, value)

    await db.commit()
    await db.refresh(ai_analysis)
    return ai_analysis
