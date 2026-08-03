"""`render_dependency_query` — pure formatting, no I/O."""

from __future__ import annotations

from app.agents.dependency_query.renderer import render_dependency_query
from app.services.engineering_intelligence.contracts import QueryResult, RelationshipInsight

_REPO_ID = "repo-1"


def _insight(source: str, target: str, state: str) -> RelationshipInsight:
    return RelationshipInsight(
        relationship_key=f"{source}->{target}-{state}",
        relationship_type="CALLS_SERVICE",
        source_entity=source,
        target_entity=target,
        confidence_state=state,
        explanation=None,
    )


def _result() -> QueryResult:
    return QueryResult(
        relationships=(
            _insight(f"{_REPO_ID}:svc:a", "repo-2:svc:b", "verified"),
            _insight("repo-3:svc:c", f"{_REPO_ID}:svc:d", "candidate"),
            _insight(f"{_REPO_ID}:svc:e", "repo-4:svc:f", "rejected"),
        ),
        total_matched=3,
    )


def test_render_uses_narrative_fields_when_present() -> None:
    narrative = {
        "repository": "repo-1 depends on two services.",
        "direct_dependencies": "Calls repo-2 and repo-4.",
        "downstream_consumers": "Called by repo-3.",
    }

    rendered = render_dependency_query(_REPO_ID, _result(), narrative)

    assert rendered["executive_summary"].startswith("repo-1 depends on two services.")
    assert rendered["direct_dependencies_summary"] == "Calls repo-2 and repo-4."
    assert rendered["downstream_consumers_summary"] == "Called by repo-3."
    assert len(rendered["direct_dependencies"]) == 2
    assert len(rendered["downstream_consumers"]) == 1
    assert rendered["confidence_breakdown"] == {"high": 1, "medium": 1, "low": 1}
    assert len(rendered["verified_relationships"]) == 1
    assert len(rendered["candidate_relationships"]) == 1
    assert "# Dependency Query Report" in rendered["markdown"]


def test_render_falls_back_to_computed_summary_when_narrative_is_empty() -> None:
    rendered = render_dependency_query(_REPO_ID, _result(), {})

    assert "found for repo-1" in rendered["executive_summary"]
    assert rendered["total_matched"] == 3


def test_render_handles_empty_query_result() -> None:
    rendered = render_dependency_query(_REPO_ID, QueryResult(), {})

    assert rendered["direct_dependencies"] == []
    assert rendered["downstream_consumers"] == []
    assert rendered["confidence_breakdown"] == {"high": 0, "medium": 0, "low": 0}
