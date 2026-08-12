"""RFC-07 — `materializer._promoted_single_repo_edges`: the promotion gate
for a single-repository relationship a generator proposed without also
pre-baking its own `graph_edge` evidence item (the deterministic
generator always does; a new generator, e.g. the generic-language LLM
fallback, does not). Pure unit tests, no database - `KnowledgeRelationshipRecord`
is a plain in-memory ORM object here, never persisted."""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.materializer import _promoted_single_repo_edges
from app.models.knowledge_relationship import KnowledgeRelationshipRecord

_NOW = datetime.now(UTC)


def _record(
    *,
    relationship_type: str = "IMPORTS",
    source_entity: str = "repo-1:source-file:a.go",
    target_entity: str = "repo-1:source-file:b.go",
    confidence_state: str = "highly_likely",
    provenance: list[dict] | None = None,
    explanation: dict | None = None,
) -> KnowledgeRelationshipRecord:
    return KnowledgeRelationshipRecord(
        relationship_key=f"{relationship_type}:{source_entity}:{target_entity}",
        repository_id="00000000-0000-0000-0000-000000000001",
        relationship_type=relationship_type,
        source_entity=source_entity,
        target_entity=target_entity,
        hypothesis_ids=["hyp:1"],
        confidence_state=confidence_state,
        distinct_confirming_source_types=0,
        confirming_source_types=[],
        max_confirming_reliability_tier=0,
        contradiction_count=0,
        confidence_formula_version="1.0",
        confidence_computed_at=_NOW,
        provenance=provenance or [],
        explanation=explanation,
    )


def test_highly_likely_relationship_not_already_covered_is_promoted() -> None:
    record = _record(confidence_state="highly_likely")
    edges = _promoted_single_repo_edges([record], already_covered=frozenset())
    assert len(edges) == 1
    assert edges[0].type == "IMPORTS"
    assert edges[0].source_id == "repo-1:source-file:a.go"
    assert edges[0].target_id == "repo-1:source-file:b.go"
    assert edges[0].properties["confidence"] == "highly_likely"


def test_verified_relationship_is_promoted() -> None:
    record = _record(confidence_state="verified")
    edges = _promoted_single_repo_edges([record], already_covered=frozenset())
    assert len(edges) == 1


def test_candidate_relationship_is_not_promoted() -> None:
    # The core trust boundary: an unvalidated LLM hypothesis lands at
    # CANDIDATE and must never silently become authoritative graph truth.
    record = _record(confidence_state="candidate")
    edges = _promoted_single_repo_edges([record], already_covered=frozenset())
    assert edges == []


def test_likely_relationship_is_not_promoted() -> None:
    record = _record(confidence_state="likely")
    edges = _promoted_single_repo_edges([record], already_covered=frozenset())
    assert edges == []


def test_rejected_and_conflicting_are_not_promoted() -> None:
    for state in ("rejected", "conflicting"):
        edges = _promoted_single_repo_edges([_record(confidence_state=state)], already_covered=frozenset())
        assert edges == [], state


def test_already_covered_relationship_is_not_duplicated() -> None:
    # A relationship the pack's own graph_edge items already produced
    # (the deterministic generator's normal case) must never be doubled.
    record = _record(confidence_state="highly_likely")
    covered = frozenset({("IMPORTS", "repo-1:source-file:a.go", "repo-1:source-file:b.go")})
    edges = _promoted_single_repo_edges([record], already_covered=covered)
    assert edges == []


def test_cross_repo_relationship_types_are_never_promoted_here() -> None:
    # Cross-repo edges have their own dedicated path (`_cross_repo_edges`)
    # with cross-repo-specific property recovery - this function must
    # never double them.
    record = _record(relationship_type="CALLS_SERVICE", confidence_state="verified")
    edges = _promoted_single_repo_edges([record], already_covered=frozenset())
    assert edges == []


def test_provenance_and_explanation_are_surfaced_on_the_edge() -> None:
    record = _record(
        confidence_state="verified",
        provenance=[{"generator": {"kind": "llm", "name": "generic_language_llm:test-model"}}],
        explanation={"summary": "confirmed by two validators"},
    )
    edges = _promoted_single_repo_edges([record], already_covered=frozenset())
    assert edges[0].properties["generator"] == "generic_language_llm:test-model"
    assert edges[0].properties["extraction_method"] == "llm"
    assert edges[0].properties["explanation"] == "confirmed by two validators"


def test_missing_provenance_and_explanation_degrade_gracefully() -> None:
    record = _record(confidence_state="verified", provenance=[], explanation=None)
    edges = _promoted_single_repo_edges([record], already_covered=frozenset())
    assert edges[0].properties["confidence"] == "verified"
    assert "generator" not in edges[0].properties
    assert "explanation" not in edges[0].properties
