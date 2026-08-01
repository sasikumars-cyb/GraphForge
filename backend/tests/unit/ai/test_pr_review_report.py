"""Unit tests for app.ai.services.pr_review_report.

Covers all three output formats (JSON/Markdown/HTML) plus HTML escaping,
since findings/summaries are LLM-produced free text rendered directly into
an HTML document.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.ai.schemas.analysis_result import (
    AIAnalysisResult,
    ConfidenceScore,
    FileReview,
    Finding,
)
from app.ai.services.pr_review_report import (
    ReviewReportContext,
    render_html_report,
    render_json_report,
    render_markdown_report,
)


def _ctx(**overrides) -> ReviewReportContext:
    result = overrides.pop(
        "result",
        AIAnalysisResult(
            executive_summary="Refactors the auth middleware.",
            quality_score=82,
            risk_score=40,
            merge_recommendation="approve_with_comments",
            security_score=90,
            findings=[
                Finding(
                    category="security",
                    severity="high",
                    title="Missing input validation",
                    description="The endpoint does not validate token length.",
                    confidence=ConfidenceScore(score=0.85, reasoning="clear pattern"),
                ),
            ],
            file_reviews=[
                FileReview(
                    file="app/auth/middleware.py",
                    complexity="medium",
                    risk="high",
                    issues=["No input validation"],
                    suggestions=["Add a length check"],
                    summary="Refactored token parsing.",
                ),
            ],
            positive_findings=["Good use of type hints."],
            suggested_improvements=["Add a regression test for empty tokens."],
            confidence=ConfidenceScore(score=0.8),
        ),
    )
    defaults = dict(
        repository="acme/order-svc",
        pull_request_number=7,
        pull_request_title="Refactor auth middleware",
        head_ref="feature/auth",
        base_ref="main",
        analyzed_at=datetime(2026, 1, 1, tzinfo=UTC),
        model_used="gpt-4o",
        result=result,
    )
    defaults.update(overrides)
    return ReviewReportContext(**defaults)


def test_json_report_contains_expected_top_level_sections() -> None:
    ctx = _ctx()
    report = json.loads(render_json_report(ctx))

    assert report["executive_summary"]["pull_request_number"] == 7
    assert report["executive_summary"]["repository"] == "acme/order-svc"
    assert report["executive_summary"]["quality_score"] == 82
    assert report["metrics"]["Security"] == 90
    assert len(report["findings"]) == 1
    assert report["findings"][0]["title"] == "Missing input validation"
    assert len(report["file_reviews"]) == 1


def test_markdown_report_includes_scores_findings_and_files() -> None:
    md = render_markdown_report(_ctx())

    assert "# PR Review Report — acme/order-svc #7" in md
    assert "| Quality | 82 |" in md
    assert "| Risk | 40 |" in md
    assert "### High" in md
    assert "Missing input validation" in md
    assert "`app/auth/middleware.py`" in md


def test_markdown_report_handles_no_findings() -> None:
    result = AIAnalysisResult(
        executive_summary="Nothing to report.", confidence=ConfidenceScore(score=0.5)
    )
    md = render_markdown_report(_ctx(result=result))

    assert "_No findings._" in md


def test_html_report_is_self_contained_and_includes_key_sections() -> None:
    out = render_html_report(_ctx())

    assert "<!doctype html>" in out.lower()
    assert "<style>" in out and "<script>" in out
    # No external network dependencies (CDNs, remote fonts, etc).
    assert "http://" not in out and "https://" not in out
    assert "Merge with Comments" in out  # approve_with_comments label
    assert "Missing input validation" in out
    assert 'data-severity="high"' in out
    assert "app/auth/middleware.py" in out


def test_html_report_escapes_untrusted_llm_text() -> None:
    """Findings/summaries are free text the LLM produced from a diff — an
    injected `<script>` in that text must render as inert text, not a live
    tag, since the report is served directly as text/html."""
    result = AIAnalysisResult(
        executive_summary="<script>alert(1)</script>",
        findings=[
            Finding(
                category="other",
                severity="low",
                title="<img src=x onerror=alert(1)>",
                description="benign",
                confidence=ConfidenceScore(score=0.5),
            )
        ],
        confidence=ConfidenceScore(score=0.5),
    )
    out = render_html_report(_ctx(result=result))

    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
    assert "<img src=x onerror=alert(1)>" not in out


def test_html_report_handles_missing_scores_and_empty_lists() -> None:
    result = AIAnalysisResult(executive_summary="", confidence=ConfidenceScore(score=0.0))
    out = render_html_report(_ctx(result=result))

    assert "N/A" in out
    assert "No findings." in out
    assert "No per-file review was produced." in out


def test_merge_recommendation_labels() -> None:
    for rec, label in (
        ("approve", "Ready to Merge"),
        ("approve_with_comments", "Merge with Comments"),
        ("request_changes", "Changes Required"),
        ("block", "Reject"),
        (None, "Not assessed"),
    ):
        result = AIAnalysisResult(
            executive_summary="x", merge_recommendation=rec, confidence=ConfidenceScore(score=0.5)
        )
        out = render_html_report(_ctx(result=result))
        assert label in out
