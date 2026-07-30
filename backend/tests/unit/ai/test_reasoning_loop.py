"""Unit tests for the Context Discovery reasoning loop
(app.context_pipeline.reasoning_loop).

Covers:
- evaluate_readiness: policy-based READY/PARTIAL/BLOCKED via capability
  checks, not a bare confidence threshold
- _detect_blocking_issues: the deterministic BlockingIssue checks named in
  the reasoning-driven Context Discovery design (repo not found, two
  repositories tied, unresolved Jira reference, missing documentation)
- run_discovery_loop: seeds a WorkingContext from ContextResolutionPipeline
  output and pauses when a blocking issue is raised
- resume_discovery: resolves the answered issue, re-assesses, performs a
  real targeted re-gather when the answer names a repository, and caps at
  MAX_CLARIFICATION_ROUNDS
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.planning.classifier import analyse
from app.context_pipeline.models import EnrichedPlanningRequest, Reference, ReferenceType
from app.context_pipeline.reasoning_loop import (
    MAX_CLARIFICATION_ROUNDS,
    _detect_blocking_issues,
    _seed_working_context,
    evaluate_readiness,
    resume_discovery,
    run_discovery_loop,
)
from app.context_pipeline.working_context import BlockingIssue, ClarificationQuestion, WorkingContext


def _make_enriched(
    *,
    indexed_repositories: list | None = None,
    graph_components: list | None = None,
    resolved_references: list[Reference] | None = None,
    ranked_repository_names: list[str] | None = None,
) -> EnrichedPlanningRequest:
    request = "Add a rate limiter to the payment API"
    return EnrichedPlanningRequest(
        original_request=request,
        enriched_text=request,
        resolved_references=resolved_references or [],
        artifacts=[],
        profile=analyse(request),
        indexed_repositories=indexed_repositories or [],
        graph_components=graph_components or [],
        graph_topics=[],
        ranked_repository_names=ranked_repository_names or [],
        graph_context_text="",
        graph_available=True,
        graph_has_data=bool(graph_components),
        additional_context_recommendation=None,
        evidence=[],
        planning_metadata={},
    )


def _blocking_issue(issue_id: str = "repo_ambiguous") -> BlockingIssue:
    return BlockingIssue(
        issue_id=issue_id,
        type="repository_ambiguous",
        severity="blocking",
        message="tie",
        reason="tie",
        clarification_question=ClarificationQuestion(
            question_id=issue_id, question="Which repo?", why="tie"
        ),
    )


# ---------------------------------------------------------------------------
# evaluate_readiness — policy-based
# ---------------------------------------------------------------------------


def test_readiness_blocked_when_unresolved_blocking_issue() -> None:
    wc = WorkingContext()
    wc.reasoning.blocking_issues = [_blocking_issue()]
    assert evaluate_readiness(wc) == "BLOCKED"


def test_readiness_ready_when_required_checks_satisfied() -> None:
    wc = WorkingContext()
    wc.knowledge.repositories = [{"name": "payment-service"}]
    wc.knowledge.graph.available = True
    assert evaluate_readiness(wc) == "READY"


def test_readiness_still_ready_when_only_recommended_check_fails() -> None:
    """Missing documentation (a recommended check) must not by itself
    prevent READY — matches the Discovery Summary example: candidates
    found + Confluence unavailable is still Readiness: READY."""
    wc = WorkingContext()
    wc.knowledge.repositories = [{"name": "payment-service"}]
    wc.knowledge.graph.available = True
    wc.knowledge.implementation_candidates = []  # recommended check fails
    assert evaluate_readiness(wc) == "READY"


def test_readiness_partial_when_required_check_fails_without_blocking_issue() -> None:
    wc = WorkingContext()
    wc.knowledge.graph.available = False  # required check fails, no issue raised
    assert evaluate_readiness(wc) == "PARTIAL"


def test_readiness_not_blocked_once_issue_resolved() -> None:
    wc = WorkingContext()
    wc.reasoning.blocking_issues = [_blocking_issue("q1")]
    wc.reasoning.resolve_issue("q1", "payment-service")
    wc.knowledge.repositories = [{"name": "payment-service"}]
    wc.knowledge.graph.available = True
    assert wc.reasoning.next_blocking_issue() is None
    assert evaluate_readiness(wc) == "READY"


# ---------------------------------------------------------------------------
# _detect_blocking_issues
# ---------------------------------------------------------------------------


def test_detect_repo_not_found_when_repo_entity_but_no_indexed_repos() -> None:
    enriched = _make_enriched(
        resolved_references=[
            Reference(
                type=ReferenceType.LOCAL_REPOSITORY,
                provider="graph",
                confidence=1.0,
                raw_value="payment-service",
                normalized_value="payment-service",
            )
        ],
        indexed_repositories=[],
    )
    wc = _seed_working_context(enriched)
    issues = _detect_blocking_issues(wc, enriched)
    types = [i.type for i in issues]
    assert "repository_not_found" in types
    found = next(i for i in issues if i.type == "repository_not_found")
    assert found.severity == "blocking"
    assert found.clarification_question is not None
    assert found.recommended_action


def test_detect_no_repo_issue_when_no_repo_entity_detected() -> None:
    enriched = _make_enriched(indexed_repositories=[])
    wc = _seed_working_context(enriched)
    issues = _detect_blocking_issues(wc, enriched)
    assert not any(i.type == "repository_not_found" for i in issues)


def test_detect_repo_ambiguous_when_scores_tie() -> None:
    enriched = _make_enriched(
        indexed_repositories=[{"name": "payment-service"}, {"name": "billing-service"}],
        graph_components=[
            {"name": "c1", "repository": "payment-service", "type": "consumer"},
            {"name": "c2", "repository": "billing-service", "type": "consumer"},
        ],
        ranked_repository_names=["payment-service", "billing-service"],
    )
    wc = _seed_working_context(enriched)
    with patch(
        "app.context_pipeline.reasoning_loop.rank_repositories",
        return_value=[(1.0, "payment-service"), (1.0, "billing-service")],
    ):
        issues = _detect_blocking_issues(wc, enriched)
    assert any(i.type == "repository_ambiguous" for i in issues)


def test_detect_no_ambiguity_when_leader_clearly_ahead() -> None:
    enriched = _make_enriched(
        indexed_repositories=[{"name": "payment-service"}, {"name": "billing-service"}],
        graph_components=[],
        ranked_repository_names=["payment-service", "billing-service"],
    )
    wc = _seed_working_context(enriched)
    with patch(
        "app.context_pipeline.reasoning_loop.rank_repositories",
        return_value=[(5.0, "payment-service"), (0.5, "billing-service")],
    ):
        issues = _detect_blocking_issues(wc, enriched)
    assert not any(i.type == "repository_ambiguous" for i in issues)


def test_detect_jira_unresolved_when_reference_present_but_not_fetched() -> None:
    enriched = _make_enriched(
        resolved_references=[
            Reference(
                type=ReferenceType.JIRA_ISSUE,
                provider="jira",
                confidence=1.0,
                raw_value="PROT-1",
                normalized_value="PROT-1",
            )
        ],
    )
    wc = _seed_working_context(enriched)
    issues = _detect_blocking_issues(wc, enriched)
    assert any(i.type == "jira_unresolved" for i in issues)


def test_detect_no_documentation_issue_without_a_work_item() -> None:
    """A bare freeform request with no Jira reference has nothing this
    check should complain is missing."""
    enriched = _make_enriched()
    wc = _seed_working_context(enriched)
    issues = _detect_blocking_issues(wc, enriched)
    assert not any(i.type == "documentation_unavailable" for i in issues)


# ---------------------------------------------------------------------------
# run_discovery_loop / resume_discovery
# ---------------------------------------------------------------------------


def _assessment_json(confidence: float = 0.8) -> str:
    return json.dumps({"confidence": confidence, "assumptions": [], "unresolved_questions": []})


@pytest.mark.asyncio
async def test_run_discovery_loop_ready_when_confident_and_unambiguous() -> None:
    enriched = _make_enriched(
        indexed_repositories=[{"name": "payment-service"}],
        graph_components=[{"name": "c1", "repository": "payment-service"}],
    )
    with (
        patch(
            "app.context_pipeline.reasoning_loop.ContextResolutionPipeline"
        ) as mock_pipeline_cls,
        patch(
            "app.context_pipeline.reasoning_loop.invoke_llm_json",
            new=AsyncMock(return_value=_assessment_json(0.9)),
        ),
    ):
        mock_pipeline_cls.return_value.resolve = AsyncMock(return_value=enriched)
        result = await run_discovery_loop(
            raw_request="Add a rate limiter to the payment API",
            db=AsyncMock(),
            graph_repo_override=None,
            user_id=None,
            model=None,
            extras={},
            stage="context_discovery",
        )

    assert result.working_context.reasoning.readiness == "READY"
    assert result.paused is False
    assert result.pending_question is None


@pytest.mark.asyncio
async def test_run_discovery_loop_pauses_on_blocking_ambiguity() -> None:
    enriched = _make_enriched(
        resolved_references=[
            Reference(
                type=ReferenceType.LOCAL_REPOSITORY,
                provider="graph",
                confidence=1.0,
                raw_value="payment-service",
                normalized_value="payment-service",
            )
        ],
        indexed_repositories=[],
    )
    with (
        patch(
            "app.context_pipeline.reasoning_loop.ContextResolutionPipeline"
        ) as mock_pipeline_cls,
        patch(
            "app.context_pipeline.reasoning_loop.invoke_llm_json",
            new=AsyncMock(return_value=_assessment_json(0.9)),
        ),
    ):
        mock_pipeline_cls.return_value.resolve = AsyncMock(return_value=enriched)
        result = await run_discovery_loop(
            raw_request="Fix the thing in payment-service",
            db=AsyncMock(),
            graph_repo_override=None,
            user_id=None,
            model=None,
            extras={},
            stage="context_discovery",
        )

    assert result.paused is True
    assert result.working_context.reasoning.readiness == "BLOCKED"
    assert result.pending_question is not None
    assert result.pending_question.question_id == "repo_not_found"


@pytest.mark.asyncio
async def test_resume_discovery_resolves_issue_and_reassesses() -> None:
    wc = WorkingContext()
    wc.compatibility.original_request = "Fix the thing"
    wc.reasoning.blocking_issues = [_blocking_issue("repo_not_found")]

    with patch(
        "app.context_pipeline.reasoning_loop.invoke_llm_json",
        new=AsyncMock(return_value=_assessment_json(0.9)),
    ):
        result = await resume_discovery(
            working_context=wc,
            question_id="repo_not_found",
            answer="payment-service",
            model=None,
            stage="context_discovery",
        )

    assert result.working_context.reasoning.user_answers["repo_not_found"] == "payment-service"
    assert result.working_context.metadata.clarification_rounds == 1
    assert result.paused is False


@pytest.mark.asyncio
async def test_resume_discovery_performs_targeted_regather_for_named_repository() -> None:
    """Answering a repository question with a db available must trigger a
    real second graph query (not just re-assessment) — the actual
    'gather more evidence' step."""
    wc = WorkingContext()
    wc.compatibility.original_request = "Fix the thing"
    wc.reasoning.blocking_issues = [_blocking_issue("repo_ambiguous")]

    fake_graph_result = MagicMock()
    fake_graph_result.success = True
    fake_graph_result.data = {
        "indexed_repositories": [{"name": "payment-service"}],
        "components": [{"name": "RateLimiter", "repository": "payment-service"}],
        "kafka_topics": [],
        "ranked_repositories": ["payment-service"],
    }

    with (
        patch(
            "app.context_pipeline.reasoning_loop.GraphProvider"
        ) as mock_provider_cls,
        patch(
            "app.context_pipeline.reasoning_loop.invoke_llm_json",
            new=AsyncMock(return_value=_assessment_json(0.9)),
        ),
    ):
        mock_provider_cls.return_value.retrieve = AsyncMock(return_value=fake_graph_result)
        result = await resume_discovery(
            working_context=wc,
            question_id="repo_ambiguous",
            answer="payment-service",
            model=None,
            stage="context_discovery",
            db=AsyncMock(),
            user_id=None,
            graph_repo_override=None,
        )

    mock_provider_cls.return_value.retrieve.assert_awaited_once()
    assert result.working_context.knowledge.implementation_candidates == ["payment-service"]
    assert any(e.reference == "neo4j_graph_targeted" for e in result.evidence)


@pytest.mark.asyncio
async def test_resume_discovery_caps_rounds_and_stops_asking() -> None:
    """At MAX_CLARIFICATION_ROUNDS, the question just answered resolves
    normally, but any *other* still-unanswered blocking issue is forced
    into a final BLOCKED+exhausted state instead of producing another
    question — the loop stops asking, not stops being blocked."""
    wc = WorkingContext()
    wc.compatibility.original_request = "Fix the thing"
    wc.metadata.clarification_rounds = MAX_CLARIFICATION_ROUNDS - 1
    wc.reasoning.blocking_issues = [
        _blocking_issue("repo_ambiguous"),
        BlockingIssue(
            issue_id="jira_unresolved",
            type="jira_unresolved",
            severity="blocking",
            message="Connect Jira?",
            reason="not fetched",
            clarification_question=ClarificationQuestion(
                question_id="jira_unresolved", question="Connect Jira?", why="not fetched"
            ),
        ),
    ]

    result = await resume_discovery(
        working_context=wc,
        question_id="repo_ambiguous",
        answer="payment-service",
        model=None,
        stage="context_discovery",
    )

    assert result.working_context.metadata.clarification_rounds == MAX_CLARIFICATION_ROUNDS
    assert result.working_context.reasoning.user_answers["repo_ambiguous"] == "payment-service"
    assert result.working_context.reasoning.readiness == "BLOCKED"
    assert result.working_context.reasoning.exhausted is True
    assert result.pending_question is None
    assert result.paused is False
