"""Unit tests for app.ai.services.persistence.persist_ai_analysis_result.

Regression coverage for the general-purpose review fields (quality_score,
risk_score, merge_recommendation, findings, category scores, file_reviews)
that AIAnalysisResult already carries but were previously dropped on the
way into PullRequestAIAnalysis — see AIAnalysisResult's own docstring
("turn this from a breaking-change/migration-focused report into a
general-purpose PR review"), which only the schema had actually done.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.schemas.analysis_result import (
    AIAnalysisResult,
    ConfidenceScore,
    FileReview,
    Finding,
)
from app.ai.services.persistence import persist_ai_analysis_result


def _full_result() -> AIAnalysisResult:
    return AIAnalysisResult(
        executive_summary="Looks good overall.",
        quality_score=88,
        risk_score=22,
        merge_recommendation="approve_with_comments",
        findings=[
            Finding(
                category="security",
                severity="medium",
                title="Missing rate limit",
                description="The new endpoint has no rate limiting.",
                confidence=ConfidenceScore(score=0.7),
            )
        ],
        architecture_observations=["Uses the existing repository pattern."],
        maintainability_observations=["Function is a bit long."],
        reliability_observations=["No retry on the new HTTP call."],
        testing_review="Adequate unit coverage; no integration test added.",
        documentation_review="Docstring updated to match new behavior.",
        positive_findings=["Clear variable names."],
        suggested_improvements=["Extract the validation block into a helper."],
        security_score=80,
        testing_score=60,
        documentation_score=70,
        architecture_score=85,
        performance_score=90,
        maintainability_score=75,
        file_reviews=[
            FileReview(
                file="app/api/orders.py",
                complexity="medium",
                risk="low",
                issues=[],
                suggestions=["Add a docstring"],
                summary="New endpoint added.",
            )
        ],
        confidence=ConfidenceScore(score=0.9),
    )


@pytest.mark.asyncio
async def test_persist_new_row_includes_all_general_review_fields() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    pull_request_id = uuid.uuid4()
    await persist_ai_analysis_result(db, pull_request_id, _full_result())

    db.add.assert_called_once()
    added = db.add.call_args.args[0]

    assert added.quality_score == 88
    assert added.risk_score == 22
    assert added.merge_recommendation == "approve_with_comments"
    assert added.findings == [
        {
            "category": "security",
            "severity": "medium",
            "title": "Missing rate limit",
            "description": "The new endpoint has no rate limiting.",
            "confidence": {"score": 0.7, "reasoning": ""},
        }
    ]
    assert added.architecture_observations == ["Uses the existing repository pattern."]
    assert added.testing_review == "Adequate unit coverage; no integration test added."
    assert added.documentation_review == "Docstring updated to match new behavior."
    assert added.positive_findings == ["Clear variable names."]
    assert added.suggested_improvements == ["Extract the validation block into a helper."]
    assert added.security_score == 80
    assert added.testing_score == 60
    assert added.documentation_score == 70
    assert added.architecture_score == 85
    assert added.performance_score == 90
    assert added.maintainability_score == 75
    assert added.file_reviews == [
        {
            "file": "app/api/orders.py",
            "complexity": "medium",
            "risk": "low",
            "issues": [],
            "suggestions": ["Add a docstring"],
            "summary": "New endpoint added.",
        }
    ]


@pytest.mark.asyncio
async def test_persist_updates_existing_row_in_place() -> None:
    existing = MagicMock()
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing))
    )
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    pull_request_id = uuid.uuid4()
    result = await persist_ai_analysis_result(db, pull_request_id, _full_result())

    db.add.assert_not_called()
    assert result is existing
    assert existing.quality_score == 88
    assert existing.merge_recommendation == "approve_with_comments"
    assert existing.file_reviews[0]["file"] == "app/api/orders.py"
