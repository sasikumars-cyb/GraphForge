"""Tests for `reasoning.understanding.synthesize_engineering_understanding` —
the cognitive reasoning layer above retrieval. Confirms it degrades
gracefully (never raises, never blocks discovery) on any LLM failure, short-
circuits deterministically when there's nothing to synthesize over, and
produces the two distinct objects (scratch `InvestigationWorkspace`, the
validated `EngineeringUnderstanding` Planning actually reads) from a
well-formed response.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.ledger import Ledger
from app.context_pipeline.reasoning.memory import WorkingContext
from app.context_pipeline.reasoning.understanding import (
    Contradiction,
    EngineeringUnderstanding,
    InvestigationWorkspace,
    capability_priority,
    render_engineering_understanding_text,
    synthesize_engineering_understanding,
)


def _empty_session() -> SessionContext:
    return SessionContext(db=None, user_id=None, model="test-model")  # type: ignore[arg-type]


def _state_with_ticket_and_components() -> WorkingContext:
    ledger = Ledger()
    ev = ledger.add_evidence(
        provider="jira", action="fetch_work_item", outcome="success", summary="ok"
    )
    ledger.add_fact(
        kind="work_item",
        subject="NPT-29",
        provider="jira",
        evidence_id=ev.evidence_id,
        value={
            "title": "Duplicate records",
            "sections": {
                "problem": "Duplicate records appear after checkpoint replay.",
                "business_goal": "Prevent duplicate downstream records.",
                "acceptance_criteria": "No duplicates after replay.",
            },
        },
        text="Duplicate records appear after checkpoint replay.",
    )
    graph_ev = ledger.add_evidence(
        provider="graph", action="traverse_architecture_graph", outcome="success", summary="ok"
    )
    ledger.add_fact(
        kind="component",
        subject="SCDType2Merger",
        provider="graph",
        evidence_id=graph_ev.evidence_id,
        value={
            "id": "etl-core:class:SCDType2Merger",
            "name": "SCDType2Merger",
            "repository": "etl-core",
            "file_path": "src/etl_core/merge/scd2_merger.py",
            "is_test": False,
        },
    )
    state = WorkingContext()
    state.ledger = ledger
    state.derived["original_request"] = "Fix duplicate records in NPT-29"
    state.derived["enriched_text"] = "Fix duplicate records in NPT-29"
    state.derived["evidence_package"] = {
        "items": [
            {
                "name": "SCDType2Merger",
                "repository": "etl-core",
                "path": "src/etl_core/merge/scd2_merger.py",
                "symbol_type": "class",
                "component_type": "class",
                "is_test": False,
                "is_test_confidence": 0.0,
                "tier": "must_modify",
                "relevance_score": 0.9,
                "proximity_score": 1.0,
                "repository_bonus": 0.15,
                "test_penalty": 0.0,
                "composite_score": 2.05,
                "confidence": 1.0,
                "hop_distance": 0,
                "reason": "Named directly in the request.",
            }
        ],
        "excluded_count": 0,
        "total_candidates": 1,
    }
    return state


@pytest.mark.asyncio
async def test_empty_investigation_short_circuits_without_calling_the_llm():
    state = WorkingContext()
    state.derived["evidence_package"] = {}

    with patch(
        "app.context_pipeline.reasoning.understanding.invoke_llm_json", new=AsyncMock()
    ) as mock_invoke:
        await synthesize_engineering_understanding(state, _empty_session())

    mock_invoke.assert_not_called()
    understanding = state.derived["engineering_understanding"]
    assert understanding["business_objective"] == ""
    assert understanding["primary_repository"] == ""
    assert understanding["confidence"] == {"overall": 0.0}
    assert any("did not run" in u for u in understanding["remaining_unknowns"])
    workspace = state.derived["investigation_workspace"]
    assert workspace["hypotheses"] == []
    assert workspace["contradictions"] == []
    assert workspace["next_investigation_candidates"] == []
    assert workspace["information_gain_estimates"] == {}
    assert len(workspace["investigation_history"]) == 1
    # The task graph is planned even in the empty/degraded path (it's pure
    # Python, no LLM cost) — its first ready task's capability is what
    # populates investigation_priority here, not an LLM-derived signal.
    assert workspace["investigation_graph"], "a task graph is seeded even with no evidence yet"
    assert workspace["engineering_strategy"]
    assert state.derived["investigation_priority"]


@pytest.mark.asyncio
async def test_successful_synthesis_populates_both_workspace_and_understanding():
    state = _state_with_ticket_and_components()
    raw_response = """{
        "workspace": {
            "hypotheses": [
                {"description": "Merge logic double-counts on replay",
                 "supporting_evidence": ["SCDType2Merger"],
                 "contradicting_evidence": [],
                 "confidence": 0.7, "status": "supported"},
                {"description": "Ingestion re-publishes the same record",
                 "supporting_evidence": [],
                 "contradicting_evidence": ["No ingestion component in evidence"],
                 "confidence": 0.2, "status": "rejected"}
            ],
            "open_questions": ["Does checkpoint replay call merge twice?"],
            "unknowns": ["No test coverage found for SCDType2Merger"],
            "dead_ends": ["Considered a config-mismatch hypothesis; no config evidence exists"],
            "candidate_repositories": ["etl-core"],
            "candidate_architecture": ["SCDType2Merger owns the merge behavior"],
            "reasoning_notes": ["Only one component was retrieved; kept scope narrow"]
        },
        "understanding": {
            "business_objective": "Prevent duplicate downstream records.",
            "current_behavior": "SCDType2Merger re-applies merges on checkpoint replay.",
            "desired_behavior": "No duplicates after replay.",
            "primary_repository": "etl-core",
            "supporting_repositories": [],
            "implementation_ownership": [
                "SCDType2Merger (etl-core, src/etl_core/merge/scd2_merger.py)"
            ],
            "architecture_relationships": [],
            "reusable_components": [],
            "dependencies": [],
            "risks": ["No test currently protects this merge path"],
            "constraints": [],
            "validated_assumptions": ["SCDType2Merger owns the merge behavior"],
            "rejected_assumptions": ["Ingestion re-publishes the same record"],
            "remaining_unknowns": ["No test coverage found for SCDType2Merger"],
            "confidence": {"business_objective": 0.8, "current_behavior": 0.7, "overall": 0.75},
            "engineering_insights": ["This change is isolated to a single repository."]
        }
    }"""

    with patch(
        "app.context_pipeline.reasoning.understanding.invoke_llm_json",
        new=AsyncMock(return_value=raw_response),
    ) as mock_invoke:
        await synthesize_engineering_understanding(state, _empty_session())

    mock_invoke.assert_called_once()
    call_kwargs = mock_invoke.call_args.kwargs
    assert call_kwargs["purpose"] == "synthesis"
    assert "SCDType2Merger" in call_kwargs["user_prompt"]

    workspace = state.derived["investigation_workspace"]
    assert len(workspace["hypotheses"]) == 2
    assert workspace["hypotheses"][0]["status"] == "supported"
    assert workspace["hypotheses"][1]["status"] == "rejected"

    understanding = state.derived["engineering_understanding"]
    assert understanding["primary_repository"] == "etl-core"
    assert understanding["business_objective"] == "Prevent duplicate downstream records."
    assert "SCDType2Merger" in understanding["implementation_ownership"][0]


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_deterministic_summary_instead_of_raising():
    state = _state_with_ticket_and_components()

    with patch(
        "app.context_pipeline.reasoning.understanding.invoke_llm_json",
        new=AsyncMock(side_effect=RuntimeError("provider unreachable")),
    ):
        await synthesize_engineering_understanding(state, _empty_session())  # must not raise

    understanding = state.derived["engineering_understanding"]
    assert understanding["primary_repository"] == "etl-core"
    assert any("SCDType2Merger" in item for item in understanding["implementation_ownership"])
    unknowns = understanding["remaining_unknowns"]
    assert any("synthesis" in u.lower() and "failed" in u.lower() for u in unknowns)


@pytest.mark.asyncio
async def test_malformed_json_response_also_degrades_gracefully():
    state = _state_with_ticket_and_components()

    with patch(
        "app.context_pipeline.reasoning.understanding.invoke_llm_json",
        new=AsyncMock(return_value="not valid json at all"),
    ):
        await synthesize_engineering_understanding(state, _empty_session())  # must not raise

    understanding = state.derived["engineering_understanding"]
    assert understanding["primary_repository"] == "etl-core"
    assert any("failed" in u.lower() for u in understanding["remaining_unknowns"])


def test_render_engineering_understanding_text_is_empty_for_a_blank_object():
    assert render_engineering_understanding_text(EngineeringUnderstanding()) == ""


def test_render_engineering_understanding_text_includes_populated_sections():
    understanding = EngineeringUnderstanding(
        business_objective="Prevent duplicate records.",
        primary_repository="etl-core",
        risks=["No test currently protects this merge path"],
    )
    text = render_engineering_understanding_text(understanding)
    assert "Business objective" in text
    assert "Prevent duplicate records." in text
    assert "Primary repository" in text
    assert "Risks" in text
    assert "No test currently protects this merge path" in text


# ---------------------------------------------------------------------------
# capability_priority — the deterministic bridge from workspace to
# engine._select's priority_boost (understanding actively driving the next
# investigation, without action selection itself becoming an LLM call).
# ---------------------------------------------------------------------------


def test_capability_priority_maps_known_labels_from_information_gain_estimates():
    workspace = InvestigationWorkspace(
        information_gain_estimates={"architecture": 0.8, "documentation": 0.2}
    )
    priority = capability_priority(workspace)
    assert priority == {"architecture": 0.8, "documentation": 0.2}


def test_capability_priority_ignores_labels_outside_the_known_capability_set():
    workspace = InvestigationWorkspace(
        information_gain_estimates={"github": 0.9, "tests": 0.7, "repository": 0.5}
    )
    priority = capability_priority(workspace)
    assert priority == {"repository": 0.5}


def test_capability_priority_clamps_out_of_range_gain_estimates():
    workspace = InvestigationWorkspace(information_gain_estimates={"architecture": 5.0})
    assert capability_priority(workspace)["architecture"] == 1.0


def test_capability_priority_falls_back_to_next_investigation_candidates():
    workspace = InvestigationWorkspace(next_investigation_candidates=["documentation", "github"])
    priority = capability_priority(workspace)
    assert priority == {"documentation": 0.3}


def test_capability_priority_boosts_architecture_for_an_unresolved_contradiction():
    workspace = InvestigationWorkspace(
        contradictions=[
            Contradiction(
                description="Merge logic conflicts with the architecture doc", resolved=False
            )
        ]
    )
    priority = capability_priority(workspace)
    assert priority["architecture"] >= 0.4


def test_capability_priority_ignores_a_resolved_contradiction():
    workspace = InvestigationWorkspace(
        contradictions=[
            Contradiction(description="Already explained", resolved=True, resolution_note="ok")
        ]
    )
    assert capability_priority(workspace) == {}


@pytest.mark.asyncio
async def test_synthesize_populates_investigation_priority_from_the_llm_response():
    state = _state_with_ticket_and_components()
    raw_response = """{
        "workspace": {
            "hypotheses": [],
            "contradictions": [],
            "next_investigation_candidates": ["architecture"],
            "information_gain_estimates": {"architecture": 0.9}
        },
        "understanding": {"primary_repository": "etl-core"}
    }"""

    with patch(
        "app.context_pipeline.reasoning.understanding.invoke_llm_json",
        new=AsyncMock(return_value=raw_response),
    ):
        await synthesize_engineering_understanding(state, _empty_session())

    # The LLM's own gain estimate combines (via max, per capability) with
    # whatever the deterministic task graph's own ready task contributes —
    # here, the seeded "understand_objective" task (work_item, 0.85) is
    # still the only ready task, since it has no unmet dependencies.
    priority = state.derived["investigation_priority"]
    assert priority["architecture"] == 0.9
    assert priority["work_item"] == 0.85
    assert len(state.derived["investigation_workspace"]["investigation_history"]) == 1


@pytest.mark.asyncio
async def test_second_synthesis_call_carries_investigation_history_forward_and_detects_flip():
    state = _state_with_ticket_and_components()
    first_response = """{
        "workspace": {"hypotheses": [{"description": "Merge logic owns the bug",
                                        "confidence": 0.6, "status": "supported"}]},
        "understanding": {"primary_repository": "etl-core"}
    }"""
    second_response = """{
        "workspace": {"hypotheses": [{"description": "Merge logic owns the bug",
                                        "confidence": 0.1, "status": "rejected"}]},
        "understanding": {"primary_repository": "etl-core"}
    }"""

    with patch(
        "app.context_pipeline.reasoning.understanding.invoke_llm_json",
        new=AsyncMock(return_value=first_response),
    ):
        await synthesize_engineering_understanding(state, _empty_session())

    with patch(
        "app.context_pipeline.reasoning.understanding.invoke_llm_json",
        new=AsyncMock(return_value=second_response),
    ):
        await synthesize_engineering_understanding(state, _empty_session())

    history = state.derived["investigation_workspace"]["investigation_history"]
    assert len(history) == 2
    assert "moved supported -> rejected" in history[1]
