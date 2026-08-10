"""`GraphInvestigator`'s explanation of *why* a tracked repository can't be
used — the Context Discovery half of the Graph Health rollout.

Before this, a repository that wasn't HEALTHY (GRAPH_MISSING, INDEXING,
NOT_INDEXED) simply didn't appear in `indexed_repositories` at all — "0
indexed repositories" looked identical whether nothing was tracked, an
indexing job was running, or a completed job's graph had gone missing.
`Neo4jGraphTool` now also returns `unhealthy_repositories` (id/name/owner/
status/latest_job_status per non-healthy repository — see
`app.graph.health` and `app.agents.planning.tools.GetIndexedRepositoriesTool`),
and `GraphInvestigator` turns that into deterministic, evidence-backed
narration instead of silence.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.investigators import (
    GraphInvestigator,
    _describe_unhealthy,
)
from app.context_pipeline.reasoning.ledger import Ledger
from app.tools.interfaces import ToolResult


def _session() -> SessionContext:
    return SessionContext(db=None, user_id=None)  # type: ignore[arg-type]


def _graph_result(indexed: list[str], unhealthy: list[dict[str, object]]) -> ToolResult:
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": n} for n in indexed],
            "unhealthy_repositories": unhealthy,
            "components": [],
            "kafka_topics": [],
            "context_text": "x",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "x",
            "_traverse_summary": "x",
        },
        summary="q",
    )


def _survey_action() -> InvestigationAction:
    return InvestigationAction(
        provider="graph",
        key="survey_architecture",
        intent="survey",
        targets="repository",
        params={"query": "add retry backoff", "search_terms": []},
    )


def _verify_action(claim: str) -> InvestigationAction:
    return InvestigationAction(
        provider="graph",
        key=f"verify_repository:{claim}",
        intent="verify",
        targets="repository",
        params={"claim": claim, "query": claim, "search_terms": []},
    )


# ---------------------------------------------------------------------------
# `_describe_unhealthy` — the pure template lookup.
# ---------------------------------------------------------------------------


def test_describe_graph_missing_names_the_repository_and_the_fix() -> None:
    text = _describe_unhealthy({"name": "payment-service", "status": "graph_missing"})
    assert "payment-service" in text
    assert "re-index" in text.lower()
    assert "completed" in text.lower()


def test_describe_indexing_says_still_in_progress() -> None:
    text = _describe_unhealthy({"name": "payment-service", "status": "indexing"})
    assert "payment-service" in text
    assert "indexed" in text.lower() or "indexing" in text.lower()


def test_describe_not_indexed_says_never_indexed() -> None:
    text = _describe_unhealthy({"name": "payment-service", "status": "not_indexed"})
    assert "payment-service" in text
    assert "never" in text.lower()


def test_describe_unknown_status_falls_back_to_generic_explanation() -> None:
    """Defensive: an unrecognized status string (e.g. a future
    GraphHealthStatus value this narration hasn't been taught yet) must
    still produce a named, non-crashing explanation rather than a KeyError."""
    text = _describe_unhealthy({"name": "payment-service", "status": "some_future_status"})
    assert "payment-service" in text


# ---------------------------------------------------------------------------
# `GraphInvestigator.run` — survey (focus=None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_survey_records_evidence_explaining_each_unhealthy_repository() -> None:
    ledger = Ledger()
    action = _survey_action()
    recorder = Recorder(ledger, action, iteration=1)

    graph_result = _graph_result(
        indexed=["payment-service"],
        unhealthy=[
            {
                "id": "r2",
                "name": "streaming-pipeline",
                "owner": "acme",
                "status": "graph_missing",
                "latest_job_status": "completed",
            },
            {
                "id": "r3",
                "name": "ingestion-framework",
                "owner": "acme",
                "status": "indexing",
                "latest_job_status": "running",
            },
        ],
    )

    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=graph_result),
    ):
        outcome = await GraphInvestigator().run(action, _session(), recorder)

    assert outcome.yielded is True

    evidence_summaries = [e.summary for e in ledger.evidence]
    graph_missing_evidence = [s for s in evidence_summaries if "streaming-pipeline" in s]
    indexing_evidence = [s for s in evidence_summaries if "ingestion-framework" in s]

    assert graph_missing_evidence, "GRAPH_MISSING repository must get its own evidence entry"
    assert "re-index" in graph_missing_evidence[0].lower()
    assert indexing_evidence, "INDEXING repository must get its own evidence entry"

    # The healthy repository is still correctly identified — the
    # explanations are additional, not a replacement for what did resolve.
    repo_facts = ledger.facts_of("repository")
    assert {f.subject for f in repo_facts} == {"payment-service"}

    # Neither unhealthy repository becomes a `repository` Fact — only
    # Evidence. Promoting a GRAPH_MISSING/INDEXING repo to a fact would let
    # it flow into `resync_repository_candidates` and be selected as if it
    # were usable, which is exactly what this must not do.
    assert "streaming-pipeline" not in {f.subject for f in repo_facts}
    assert "ingestion-framework" not in {f.subject for f in repo_facts}


@pytest.mark.asyncio
async def test_survey_with_zero_healthy_repos_explains_why_instead_of_just_reporting_zero() -> None:
    ledger = Ledger()
    action = _survey_action()
    recorder = Recorder(ledger, action, iteration=1)

    graph_result = _graph_result(
        indexed=[],
        unhealthy=[
            {
                "id": "r1",
                "name": "payment-service",
                "owner": "acme",
                "status": "graph_missing",
                "latest_job_status": "completed",
            }
        ],
    )

    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=graph_result),
    ):
        outcome = await GraphInvestigator().run(action, _session(), recorder)

    # The old behavior ("No repositories are indexed...") is preserved as a
    # prefix, but the observation no longer stops there.
    assert "No repositories are indexed" in outcome.observation
    assert "payment-service" in outcome.observation
    assert "tracked but not currently usable" in outcome.observation


@pytest.mark.asyncio
async def test_survey_with_no_tracked_repositories_at_all_keeps_the_original_message() -> None:
    """Regression guard: an account with nothing tracked at all (the
    "repository absent" case) must not gain a spurious "tracked but not
    usable" clause it has no data for."""
    ledger = Ledger()
    action = _survey_action()
    recorder = Recorder(ledger, action, iteration=1)

    graph_result = _graph_result(indexed=[], unhealthy=[])

    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=graph_result),
    ):
        outcome = await GraphInvestigator().run(action, _session(), recorder)

    assert outcome.observation == (
        "No repositories are indexed in the knowledge graph, so I can't tell which "
        "service this request belongs to."
    )


# ---------------------------------------------------------------------------
# `GraphInvestigator.run` — focused verify/scope action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_of_a_graph_missing_repository_explains_instead_of_generic_not_found() -> None:
    ledger = Ledger()
    action = _verify_action("streaming-pipeline")
    recorder = Recorder(ledger, action, iteration=1)

    graph_result = _graph_result(
        indexed=["payment-service"],
        unhealthy=[
            {
                "id": "r2",
                "name": "streaming-pipeline",
                "owner": "acme",
                "status": "graph_missing",
                "latest_job_status": "completed",
            }
        ],
    )

    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=graph_result),
    ):
        outcome = await GraphInvestigator().run(action, _session(), recorder)

    assert "streaming-pipeline" in outcome.observation
    assert "re-index" in outcome.observation.lower()
    assert "isn't among the indexed repositories" not in outcome.observation
    # No repository fact for the claimed-but-unhealthy repository — the
    # claim is explained, not silently accepted as usable.
    assert "streaming-pipeline" not in {f.subject for f in ledger.facts_of("repository")}


@pytest.mark.asyncio
async def test_verify_of_a_truly_unknown_repository_keeps_the_generic_message() -> None:
    """Regression guard: a claim that matches nothing at all (not even a
    tracked-but-unhealthy repository) must keep the original, generic
    "isn't among the indexed repositories" wording — there's no specific
    reason to give because the graph has never heard of it."""
    ledger = Ledger()
    action = _verify_action("totally-unknown-service")
    recorder = Recorder(ledger, action, iteration=1)

    graph_result = _graph_result(indexed=["payment-service"], unhealthy=[])

    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=graph_result),
    ):
        outcome = await GraphInvestigator().run(action, _session(), recorder)

    assert "isn't among the indexed repositories" in outcome.observation
