"""`_graph_context_text_from` (planning/agent.py) — confirms the curated
Evidence Package rendering is actually preferred over the old raw
`graph_context_text` when Context Discovery produced one, and that the
fallback still works for older/standalone runs that never produced one.
"""

from __future__ import annotations

from app.agents.planning.agent import _graph_context_text_from
from app.context_pipeline.reasoning.curation import curate


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


def test_prefers_curated_rendering_when_evidence_package_has_items():
    result = {
        "evidence_package": _package_dict(),
        "graph_context_text": "OLD RAW TEXT — should not be used",
    }
    text = _graph_context_text_from(result)
    assert "ExactDeduplicator" in text
    assert "OLD RAW TEXT" not in text


def test_falls_back_to_raw_text_when_evidence_package_is_empty():
    result = {"evidence_package": {}, "graph_context_text": "the old rendering"}
    assert _graph_context_text_from(result) == "the old rendering"


def test_falls_back_to_raw_text_when_evidence_package_has_no_items():
    result = {"evidence_package": {"items": []}, "graph_context_text": "the old rendering"}
    assert _graph_context_text_from(result) == "the old rendering"


def test_falls_back_to_empty_string_when_neither_is_present():
    assert _graph_context_text_from({}) == ""
