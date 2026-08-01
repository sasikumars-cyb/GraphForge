"""Unit tests for the pure-function helpers in app.api.v1.routers.ai_analysis
that carry the general-purpose review fields between an AIAnalysisResult,
the persisted PullRequestAIAnalysis row, and the API response — no DB or
HTTP layer needed since these are plain data transforms.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.schemas.analysis_result import AIAnalysisResult, ConfidenceScore, FileReview, Finding
from app.api.v1.routers.ai_analysis import _ai_analysis_row_to_result, _general_review_fields


def _result_with_general_fields() -> AIAnalysisResult:
    return AIAnalysisResult(
        executive_summary="Solid change.",
        quality_score=91,
        risk_score=15,
        merge_recommendation="approve",
        findings=[
            Finding(
                category="performance",
                severity="low",
                title="N+1 query",
                description="Loop issues one query per item.",
                confidence=ConfidenceScore(score=0.6),
            )
        ],
        architecture_observations=["Follows existing service boundary."],
        maintainability_observations=[],
        reliability_observations=[],
        testing_review="Covered by existing suite.",
        documentation_review="",
        positive_findings=["Clean diff."],
        suggested_improvements=[],
        security_score=None,
        testing_score=70,
        documentation_score=None,
        architecture_score=95,
        performance_score=40,
        maintainability_score=88,
        file_reviews=[
            FileReview(
                file="app/jobs/sync.py",
                complexity="low",
                risk="medium",
                issues=["N+1 query in the loop"],
                suggestions=[],
                summary="Batch sync job.",
            )
        ],
        confidence=ConfidenceScore(score=0.75),
    )


def test_general_review_fields_maps_every_field() -> None:
    result = _result_with_general_fields()
    fields = _general_review_fields(result)

    assert fields["quality_score"] == 91
    assert fields["risk_score"] == 15
    assert fields["merge_recommendation"] == "approve"
    assert fields["findings"] == [
        {
            "category": "performance",
            "severity": "low",
            "title": "N+1 query",
            "description": "Loop issues one query per item.",
            "confidence": {"score": 0.6, "reasoning": ""},
        }
    ]
    assert fields["architecture_observations"] == ["Follows existing service boundary."]
    assert fields["security_score"] is None
    assert fields["performance_score"] == 40
    assert fields["file_reviews"] == [
        {
            "file": "app/jobs/sync.py",
            "complexity": "low",
            "risk": "medium",
            "issues": ["N+1 query in the loop"],
            "suggestions": [],
            "summary": "Batch sync job.",
        }
    ]


@dataclass
class _FakeAIAnalysisRow:
    """Stands in for the PullRequestAIAnalysis ORM row — same field names,
    plain dataclass so no DB is needed to exercise the reconstruction."""

    executive_summary: str = ""
    breaking_changes: list = field(default_factory=list)
    migration_advice: list = field(default_factory=list)
    suggested_reviewers: list = field(default_factory=list)
    regression_tests: list = field(default_factory=list)
    release_coordination_plan: dict | None = None
    confidence_score: float = 0.0
    confidence_reasoning: str = ""
    prompt_version: str = ""
    quality_score: float | None = None
    risk_score: float | None = None
    merge_recommendation: str | None = None
    findings: list = field(default_factory=list)
    architecture_observations: list = field(default_factory=list)
    maintainability_observations: list = field(default_factory=list)
    reliability_observations: list = field(default_factory=list)
    testing_review: str = ""
    documentation_review: str = ""
    positive_findings: list = field(default_factory=list)
    suggested_improvements: list = field(default_factory=list)
    security_score: float | None = None
    testing_score: float | None = None
    documentation_score: float | None = None
    architecture_score: float | None = None
    performance_score: float | None = None
    maintainability_score: float | None = None
    file_reviews: list = field(default_factory=list)


def test_ai_analysis_row_to_result_round_trips_general_fields() -> None:
    row = _FakeAIAnalysisRow(
        executive_summary="Round trip.",
        quality_score=77,
        risk_score=33,
        merge_recommendation="request_changes",
        findings=[
            {
                "category": "reliability",
                "severity": "critical",
                "title": "Unhandled exception",
                "description": "No try/except around the network call.",
                "confidence": {"score": 0.95, "reasoning": "obvious"},
            }
        ],
        security_score=60,
        file_reviews=[
            {
                "file": "app/net/client.py",
                "complexity": "high",
                "risk": "high",
                "issues": ["No error handling"],
                "suggestions": ["Wrap in try/except"],
                "summary": "New HTTP client wrapper.",
            }
        ],
        confidence_score=0.6,
    )

    result = _ai_analysis_row_to_result(row)

    assert isinstance(result, AIAnalysisResult)
    assert result.quality_score == 77
    assert result.merge_recommendation == "request_changes"
    assert len(result.findings) == 1
    assert result.findings[0].category == "reliability"
    assert result.findings[0].severity == "critical"
    assert result.security_score == 60
    assert len(result.file_reviews) == 1
    assert result.file_reviews[0].file == "app/net/client.py"
    assert result.confidence.score == 0.6
