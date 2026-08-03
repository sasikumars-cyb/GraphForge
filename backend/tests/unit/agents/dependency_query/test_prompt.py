"""`build_dependency_query_prompt` — pure, no I/O."""

from __future__ import annotations

import json

from app.agents.dependency_query.prompt import build_dependency_query_prompt
from app.services.engineering_intelligence.contracts import QueryResult, RelationshipInsight

_REPO_ID = "repo-1"


def _insight(source: str, target: str, state: str) -> RelationshipInsight:
    return RelationshipInsight(
        relationship_key=f"{source}->{target}",
        relationship_type="CALLS_SERVICE",
        source_entity=source,
        target_entity=target,
        confidence_state=state,
        explanation=None,
    )


def test_prompt_splits_dependencies_from_consumers_by_direction() -> None:
    result = QueryResult(
        relationships=(
            _insight(f"{_REPO_ID}:svc:a", "repo-2:svc:b", "verified"),
            _insight("repo-3:svc:c", f"{_REPO_ID}:svc:d", "likely"),
        ),
        total_matched=2,
    )

    spec = build_dependency_query_prompt(_REPO_ID, result)
    payload = json.loads(spec.user_prompt)

    assert len(payload["direct_dependencies"]) == 1
    assert len(payload["downstream_consumers"]) == 1
    assert payload["repository_id"] == _REPO_ID
    assert spec.stage == "dependency_query"


def test_prompt_buckets_confidence_states() -> None:
    result = QueryResult(
        relationships=(
            _insight(f"{_REPO_ID}:a", "x", "verified"),
            _insight(f"{_REPO_ID}:b", "y", "candidate"),
            _insight(f"{_REPO_ID}:c", "z", "rejected"),
        ),
        total_matched=3,
    )

    spec = build_dependency_query_prompt(_REPO_ID, result)
    payload = json.loads(spec.user_prompt)

    assert len(payload["verified_relationships"]) == 1
    assert len(payload["medium_confidence_relationships"]) == 1
    assert len(payload["candidate_relationships"]) == 1


def test_prompt_system_prompt_instructs_grounding_only() -> None:
    spec = build_dependency_query_prompt(_REPO_ID, QueryResult())

    assert "FACTS" in spec.system_prompt
    assert "do not invent" in spec.system_prompt.lower()
