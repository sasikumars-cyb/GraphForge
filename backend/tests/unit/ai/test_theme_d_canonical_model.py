"""ADR 0010 §7 P2 (Theme D) regression tests — the canonical `repositories`
model and its read-only compatibility projections.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.context_discovery.schemas import ContextDiscoveryResult
from app.context_pipeline.reasoning.engine import discover
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import (
    GraphInvestigator,
    RequestParseInvestigator,
)
from app.context_pipeline.reasoning.projection import (
    RepositoryCandidate,
    build_result,
    project_repositories,
)
from app.tools.interfaces import ToolResult

# Every repository-shaped field `ContextDiscoveryResult` may carry. A sixth
# one appearing here without a matching update to `project_repositories`
# and this allowlist is exactly the accidental-field-sprawl ADR 0010 §6
# warns about — this test exists to make that impossible to do silently.
_REPOSITORY_FIELDS = frozenset(
    {
        "repositories",
        "ranked_repository_names",
        "implementation_candidates",
        "explicit_repositories",
        "suggested_repositories",
        "selected_repositories",
    }
)


def _session() -> SessionContext:
    return SessionContext(db=None, user_id=None)  # type: ignore[arg-type]


def _graph_tool_result(repositories: list[str], components: list[tuple[str, str]]) -> ToolResult:
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": n} for n in repositories],
            "components": [{"name": c, "repository": r, "type": "service"} for c, r in components],
            "kafka_topics": [],
            "context_text": "x",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "x",
            "_traverse_summary": "x",
        },
        summary="q",
    )


def test_context_discovery_result_has_exactly_the_documented_repository_fields() -> None:
    # `indexed_repositories` is deliberately excluded: it's raw graph-query
    # output (every indexed repo, unfiltered), not a repository-*candidate*
    # shaped field this ADR's canonical model or its projections cover.
    fields = {
        name
        for name in ContextDiscoveryResult.model_fields
        if name != "indexed_repositories"
        and ("repositor" in name or name == "implementation_candidates")
    }
    assert fields == _REPOSITORY_FIELDS


# ---------------------------------------------------------------------------
# `project_repositories` — pure, no ledger, no I/O
# ---------------------------------------------------------------------------


def test_project_repositories_splits_by_source_and_selection() -> None:
    repos = [
        RepositoryCandidate(name="ingestion-framework", source="explicit", selected=True, rank=0),
        RepositoryCandidate(name="etl-core", source="explicit", selected=True, rank=1),
        RepositoryCandidate(
            name="streaming-pipeline", source="suggested", selected=False, rank=None
        ),
    ]

    projected = project_repositories(repos)

    assert projected["ranked_repository_names"] == ["ingestion-framework", "etl-core"]
    assert {r["name"] for r in projected["explicit_repositories"]} == {
        "ingestion-framework",
        "etl-core",
    }
    assert {r["name"] for r in projected["suggested_repositories"]} == {"streaming-pipeline"}
    assert {r["name"] for r in projected["selected_repositories"]} == {
        "ingestion-framework",
        "etl-core",
    }
    assert set(projected["implementation_candidates"]) == {
        "ingestion-framework",
        "etl-core",
        "streaming-pipeline",
    }


def test_project_repositories_falls_back_to_unranked_order_when_nothing_was_ranked() -> None:
    repos = [RepositoryCandidate(name="only-repo", source="suggested", selected=True, rank=None)]
    projected = project_repositories(repos)
    assert projected["ranked_repository_names"] == ["only-repo"]


def test_project_repositories_of_an_empty_list_is_all_empty() -> None:
    projected = project_repositories([])
    assert all(v == [] for v in projected.values())


# ---------------------------------------------------------------------------
# `build_result` — the canonical field is the only one populated directly;
# every legacy field is byte-identical to the pre-Theme-D formula
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ranked_repository_names_and_implementation_candidates_are_unchanged() -> None:
    """Equivalence test required by ADR 0010 §2: the projected legacy
    fields must match what the pre-refactor, independently-computed
    formulas produced, for the exact scenario used throughout this test
    suite (two explicit repositories, one indexed but unrelated third)."""
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["ingestion-framework", "etl-core", "streaming-pipeline"],
                [("SchemaMerger", "ingestion-framework"), ("DeltaWriter", "etl-core")],
            )
        ),
    ):
        state = await discover(
            request="Enable Delta Lake mergeSchema. Repo: ingestion-framework, etl-core",
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    result = build_result(state)

    # Pre-refactor formula: full relevance ordering, best first.
    assert set(result["ranked_repository_names"]) == {
        "ingestion-framework",
        "etl-core",
        "streaming-pipeline",
    }
    # Pre-refactor formula: every live candidate's name, unfiltered.
    assert set(result["implementation_candidates"]) == {
        i["name"] for i in result["repositories"]
    }
    assert set(result["implementation_candidates"]) == {"ingestion-framework", "etl-core"}


def test_build_result_never_leaves_ranked_repository_names_empty_when_repos_are_indexed() -> None:
    """A ledger with indexed repositories but zero live candidates (nothing
    matched, nothing explicit) must still populate `ranked_repository_names`
    from the raw repository facts — `project_repositories` alone can't do
    this (it only ever sees `repositories`, which is empty here), so
    `build_result` must apply this fallback itself."""
    from app.context_pipeline.reasoning.ledger import Ledger
    from app.context_pipeline.reasoning.memory import WorkingContext

    state = WorkingContext(ledger=Ledger())
    ev = state.ledger.add_evidence(
        provider="graph", action="survey", outcome="success", summary="s"
    )
    state.ledger.add_fact(
        kind="repository", subject="unrelated-repo", provider="graph", evidence_id=ev.evidence_id
    )
    state.refresh_assessments()

    result = build_result(state)
    assert result["repositories"] == []
    assert result["ranked_repository_names"] == ["unrelated-repo"]
