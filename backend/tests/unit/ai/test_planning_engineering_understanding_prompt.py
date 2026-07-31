"""`_graph_context_text_from` (planning/agent.py) once `engineering_
understanding` is present — confirms Planning's prompt is now built
primarily from the synthesized understanding (per the Frontier-Class
Investigation Agent redesign), with the curated evidence package appended
only for traceability, and that everything still falls back exactly as
before when synthesis produced nothing.
"""

from __future__ import annotations

from app.agents.planning.agent import _graph_context_text_from
from app.context_pipeline.reasoning.curation import curate
from app.context_pipeline.reasoning.understanding import EngineeringUnderstanding


def _package_dict() -> dict:
    components = [
        {
            "id": "c1",
            "name": "ExactDeduplicator",
            "repository": "etl-core",
            "file_path": "src/etl_core/dedup/exact_dedup.py",
            "is_test": False,
        },
    ]
    package = curate(
        components=components,
        neighborhood_nodes=[{"id": "c1", "hop_distance": 0}],
        enriched_text="Fix the exact deduplicator implementation.",
        target_repositories=["etl-core"],
    )
    return package.model_dump()


def _understanding_dict(**overrides: object) -> dict:
    return EngineeringUnderstanding(
        business_objective="Prevent duplicate records.",
        primary_repository="etl-core",
        **overrides,
    ).model_dump()


def test_understanding_is_the_primary_text_with_evidence_appended_for_traceability():
    result = {
        "engineering_understanding": _understanding_dict(),
        "evidence_package": _package_dict(),
        "graph_context_text": "OLD RAW TEXT — should not be used",
    }
    text = _graph_context_text_from(result)

    assert text.index("Prevent duplicate records.") < text.index("ExactDeduplicator")
    assert "Supporting evidence" in text
    assert "OLD RAW TEXT" not in text


def test_understanding_alone_is_used_when_no_evidence_package_exists():
    result = {"engineering_understanding": _understanding_dict()}
    text = _graph_context_text_from(result)

    assert "Prevent duplicate records." in text
    assert "Supporting evidence" not in text


def test_falls_back_to_evidence_package_when_understanding_is_empty():
    result = {
        "engineering_understanding": {},
        "evidence_package": _package_dict(),
    }
    text = _graph_context_text_from(result)

    assert "ExactDeduplicator" in text
    assert "Prevent duplicate records." not in text


def test_falls_back_to_evidence_package_when_understanding_renders_to_nothing():
    # A well-formed but entirely blank EngineeringUnderstanding renders to "" —
    # must not win over a real evidence package.
    result = {
        "engineering_understanding": EngineeringUnderstanding().model_dump(),
        "evidence_package": _package_dict(),
    }
    text = _graph_context_text_from(result)

    assert "ExactDeduplicator" in text
    assert "Supporting evidence" not in text


def test_malformed_understanding_dict_falls_back_without_raising():
    result = {
        "engineering_understanding": {"confidence": "not-a-dict-should-fail-validation"},
        "evidence_package": _package_dict(),
    }
    text = _graph_context_text_from(result)

    assert "ExactDeduplicator" in text
