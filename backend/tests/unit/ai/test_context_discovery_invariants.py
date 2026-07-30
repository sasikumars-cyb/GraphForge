"""Cross-cutting invariants over Context Discovery's persisted output.

Every other test in this area asserts a specific behaviour. These assert
properties that must hold in *every* state the engine can reach — the kind of
thing that catches a contradiction introduced three refactors from now, when a
projection field and the report it is derived from quietly drift apart.

`assert_self_consistent` is applied to a fresh BLOCKED run, a verified answer, a
refuted answer, and the exhausted round-cap state.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.context_pipeline.reasoning.engine import MAX_CLARIFICATION_ROUNDS, discover, resume
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import GraphInvestigator
from app.context_pipeline.reasoning.projection import build_result, restore
from app.tools.interfaces import ToolResult


def _session() -> SessionContext:
    return SessionContext(db=None, user_id=None)  # type: ignore[arg-type]


def _ambiguous_graph() -> ToolResult:
    """Two repositories owning identically-named components, so relevance
    ranking genuinely cannot separate them."""
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": "payment-service"}, {"name": "billing-service"}],
            "components": [
                {"name": "RetryHandler", "repository": "payment-service", "type": "service"},
                {"name": "RetryHandler", "repository": "billing-service", "type": "service"},
            ],
            "kafka_topics": [],
            "context_text": "two candidates",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "2 repos",
            "_traverse_summary": "2 components",
        },
        summary="queried",
    )


def assert_self_consistent(result: dict[str, Any]) -> None:
    """The persisted result must never contradict itself or assert anything the
    fact ledger does not support."""
    report = result["discovery_report"]

    # The flat verdict and the report are two views of one assessment.
    assert result["readiness"] == report["readiness"]
    assert result["confidence"] == pytest.approx(report["confidence"])

    applicable = [c for c in report["confidence_breakdown"] if c["necessity"] != "not_applicable"]
    unmet = [c for c in applicable if not c["satisfied"]]
    unmet_required = [c for c in unmet if c["necessity"] == "required"]

    if result["readiness"] == "READY":
        assert not unmet, f"READY while {[c['capability'] for c in unmet]} are unmet"
        assert not result["unresolved_questions"], "asking a question while READY"
    if result["readiness"] == "BLOCKED":
        assert unmet_required, "BLOCKED with every required capability satisfied"
    if result["readiness"] == "PARTIAL":
        assert not unmet_required, "PARTIAL while a required capability is unmet"

    # Every name the result asserts must trace to a real fact.
    repositories = {
        item["subject"]
        for group in report["findings"]
        if group["kind"] == "repository"
        for item in group["items"]
    }
    components = {
        item["subject"]
        for group in report["findings"]
        if group["kind"] == "component"
        for item in group["items"]
    }
    assert not set(result["implementation_candidates"]) - repositories, "candidate with no fact"
    assert not set(result["ranked_repository_names"]) - repositories, "ranked name with no fact"
    assert (
        not {c.get("name") for c in result["graph_components"]} - components
    ), "component with no fact"

    # An uncorroborated human claim must never be presented as knowledge.
    unverified = {
        item["subject"]
        for group in report["findings"]
        for item in group["items"]
        if not item["verified"]
    }
    leaked = unverified & (
        set(result["implementation_candidates"]) | set(result["ranked_repository_names"])
    )
    assert not leaked, f"unverified claim used as knowledge: {leaked}"

    # Working memory exists exactly when the run is resumable.
    assert bool(result["working_memory"]) == bool(result["unresolved_questions"])


@pytest.fixture
def graph_patch():  # type: ignore[no-untyped-def]
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=_ambiguous_graph()),
    ):
        yield


@pytest.mark.asyncio
async def test_a_blocked_run_is_self_consistent(graph_patch: None) -> None:
    state = await discover(
        request="Add retry backoff to the handler",
        session=_session(),
        investigators=[GraphInvestigator()],
    )
    result = build_result(state)
    assert result["readiness"] == "BLOCKED"
    assert_self_consistent(result)


@pytest.mark.asyncio
async def test_a_verified_answer_leaves_a_self_consistent_result(graph_patch: None) -> None:
    state = await discover(
        request="Add retry backoff to the handler",
        session=_session(),
        investigators=[GraphInvestigator()],
    )
    resumed = await resume(
        state=restore(build_result(state)),
        question_id="gap_repository",
        answer="billing-service",
        session=_session(),
        investigators=[GraphInvestigator()],
    )
    result = build_result(resumed)
    assert result["readiness"] == "READY"
    assert result["implementation_candidates"] == ["billing-service"]
    assert_self_consistent(result)


@pytest.mark.asyncio
async def test_refuted_answers_stay_self_consistent_through_the_round_cap(
    graph_patch: None,
) -> None:
    state = await discover(
        request="Add retry backoff to the handler",
        session=_session(),
        investigators=[GraphInvestigator()],
    )
    current = restore(build_result(state))
    for attempt in range(MAX_CLARIFICATION_ROUNDS):
        current = await resume(
            state=current,
            question_id="gap_repository",
            answer=f"ghost-service-{attempt}",
            session=_session(),
            investigators=[GraphInvestigator()],
        )
        result = build_result(current)
        assert result["readiness"] == "BLOCKED", "an unverifiable answer must not unblock anything"
        assert_self_consistent(result)
        if result["working_memory"]:
            current = restore(result)

    # Past the cap: still blocked, but no longer asking.
    assert not build_result(current)["unresolved_questions"]


@pytest.mark.asyncio
async def test_resume_rejects_a_question_that_is_not_pending(graph_patch: None) -> None:
    state = await discover(
        request="Add retry backoff to the handler",
        session=_session(),
        investigators=[GraphInvestigator()],
    )
    with pytest.raises(ValueError, match="No pending question"):
        await resume(
            state=restore(build_result(state)),
            question_id="gap_does_not_exist",
            answer="x",
            session=_session(),
        )
