"""Unit tests for the human-override mechanism (Context Explorer's edit
affordance over Context Discovery's output — see the architecture review
for the sidecar design rationale).

Covers:
- get_stage_result(): plain read (no override), override merge, and that
  the base AgentStep.result is never mutated
- override_stage_result(): happy path, and NotFoundError when no
  completed run exists for the given stage

Uses plain in-memory Workflow/Run/AgentStep instances (no DB), following
the existing pattern in tests/unit/test_workflow.py — get_stage_result and
override_stage_result only ever read/write attributes, never issue their
own queries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.agents.git_ops._artifact_reader import get_stage_result
from app.core.exceptions import AppError, NotFoundError
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.models.workflow import Workflow
from app.services.workflow_service import override_stage_result


def _make_workflow_with_completed_stage(
    stage: str, result: dict, *, human_override: dict | None = None
) -> Workflow:
    workflow = Workflow(id=uuid.uuid4(), title="Test", current_stage="planning")
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="discover_context",
        status="completed",
        workflow_stage=stage,
        created_at=datetime.now(UTC),
    )
    step = AgentStep(
        id=uuid.uuid4(),
        run_id=run.id,
        agent_id=stage,
        status="completed",
        result=result,
        human_override=human_override,
    )
    run.steps = [step]
    workflow.runs = [run]
    return workflow


# ---------------------------------------------------------------------------
# get_stage_result
# ---------------------------------------------------------------------------


def test_get_stage_result_returns_none_when_no_completed_run() -> None:
    workflow = Workflow(id=uuid.uuid4(), title="Test", current_stage="context_discovery")
    workflow.runs = []
    assert get_stage_result(workflow, "context_discovery") is None


def test_get_stage_result_returns_base_result_when_no_override() -> None:
    workflow = _make_workflow_with_completed_stage(
        "context_discovery", {"indexed_repositories": [{"name": "order-service"}]}
    )
    result = get_stage_result(workflow, "context_discovery")
    assert result == {"indexed_repositories": [{"name": "order-service"}]}


def test_get_stage_result_merges_human_override_on_top_of_base_result() -> None:
    workflow = _make_workflow_with_completed_stage(
        "context_discovery",
        {"indexed_repositories": [{"name": "order-service"}], "graph_available": True},
        human_override={"indexed_repositories": [{"name": "corrected-service"}]},
    )

    effective = get_stage_result(workflow, "context_discovery")

    assert effective["indexed_repositories"] == [{"name": "corrected-service"}]
    # Untouched fields survive the merge unchanged.
    assert effective["graph_available"] is True


def test_get_stage_result_override_never_mutates_the_base_result() -> None:
    """The base AgentStep.result must stay exactly what the agent
    produced — confidence calibration checks a real AI output against the
    human decision, not an edited one."""
    workflow = _make_workflow_with_completed_stage(
        "context_discovery",
        {"indexed_repositories": [{"name": "order-service"}]},
        human_override={"indexed_repositories": [{"name": "corrected-service"}]},
    )
    step = workflow.runs[0].steps[0]
    original_result = step.result

    get_stage_result(workflow, "context_discovery")

    assert step.result is original_result
    assert step.result["indexed_repositories"] == [{"name": "order-service"}]


def test_get_stage_result_ignores_non_matching_stage() -> None:
    workflow = _make_workflow_with_completed_stage("planning", {"executive_summary": "A plan."})
    assert get_stage_result(workflow, "context_discovery") is None


def test_get_stage_result_ignores_incomplete_run_for_the_stage() -> None:
    workflow = Workflow(id=uuid.uuid4(), title="Test", current_stage="context_discovery")
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="discover_context",
        status="running",
        workflow_stage="context_discovery",
        created_at=datetime.now(UTC),
    )
    run.steps = []
    workflow.runs = [run]
    assert get_stage_result(workflow, "context_discovery") is None


def test_get_stage_result_picks_most_recent_completed_run_for_repeated_stage() -> None:
    """A workflow can re-run a stage (e.g. after a rejection); the most
    recent completed run's result must win."""
    workflow = Workflow(id=uuid.uuid4(), title="Test", current_stage="context_discovery")

    older_run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="discover_context",
        status="completed",
        workflow_stage="context_discovery",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    older_run.steps = [
        AgentStep(
            id=uuid.uuid4(),
            run_id=older_run.id,
            agent_id="context_discovery",
            status="completed",
            result={"version": "old"},
        )
    ]

    newer_run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="discover_context",
        status="completed",
        workflow_stage="context_discovery",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    newer_run.steps = [
        AgentStep(
            id=uuid.uuid4(),
            run_id=newer_run.id,
            agent_id="context_discovery",
            status="completed",
            result={"version": "new"},
        )
    ]

    workflow.runs = [older_run, newer_run]

    assert get_stage_result(workflow, "context_discovery") == {"version": "new"}


# ---------------------------------------------------------------------------
# override_stage_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_stage_result_sets_override_fields() -> None:
    workflow = _make_workflow_with_completed_stage(
        "context_discovery", {"graph_context_text": "original context"}
    )
    mock_db = AsyncMock()
    user_id = uuid.uuid4()
    override = {"graph_context_text": "corrected context"}

    await override_stage_result(mock_db, workflow, "context_discovery", override, user_id)

    step = workflow.runs[0].steps[0]
    assert step.human_override == override
    assert step.overridden_by_user_id == user_id
    assert step.overridden_at is not None
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_override_stage_result_effective_via_get_stage_result_afterwards() -> None:
    """The override must be immediately visible through get_stage_result —
    the same function Planning uses to consume this stage."""
    workflow = _make_workflow_with_completed_stage(
        "context_discovery", {"graph_context_text": "original context"}
    )
    mock_db = AsyncMock()
    override = {"graph_context_text": "corrected context"}

    await override_stage_result(mock_db, workflow, "context_discovery", override, uuid.uuid4())

    assert get_stage_result(workflow, "context_discovery") == override


@pytest.mark.asyncio
async def test_override_stage_result_raises_not_found_when_stage_never_completed() -> None:
    workflow = Workflow(id=uuid.uuid4(), title="Test", current_stage="context_discovery")
    workflow.runs = []
    mock_db = AsyncMock()

    with pytest.raises(NotFoundError):
        await override_stage_result(
            mock_db, workflow, "context_discovery", {"graph_context_text": "x"}, uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_override_stage_result_raises_not_found_for_wrong_stage() -> None:
    workflow = _make_workflow_with_completed_stage("planning", {"executive_summary": "A plan."})
    mock_db = AsyncMock()

    with pytest.raises(NotFoundError):
        await override_stage_result(
            mock_db, workflow, "context_discovery", {"graph_context_text": "x"}, uuid.uuid4()
        )


# ---------------------------------------------------------------------------
# The override allowlist — a human may correct prose, never the verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_cannot_forge_the_readiness_verdict() -> None:
    """The hole this allowlist closes: because `get_stage_result` merges the
    override over the stage result, `{"readiness": "READY"}` walked straight past
    the BLOCKED gate that the whole readiness model exists to enforce."""
    workflow = _make_workflow_with_completed_stage(
        "context_discovery", {"readiness": "BLOCKED", "confidence": 0.11}
    )
    mock_db = AsyncMock()

    with pytest.raises(AppError) as excinfo:
        await override_stage_result(
            mock_db,
            workflow,
            "context_discovery",
            {"readiness": "READY", "confidence": 1.0},
            uuid.uuid4(),
        )

    assert excinfo.value.error_code == "field_not_overridable"
    assert "readiness" in str(excinfo.value)
    # The real verdict is untouched, so the gate still sees BLOCKED.
    assert get_stage_result(workflow, "context_discovery")["readiness"] == "BLOCKED"


@pytest.mark.asyncio
async def test_override_cannot_inject_unevidenced_facts() -> None:
    """Forging `graph_components` put fabricated components into Planning's
    prompt with no provenance, while the Context Explorer kept showing the real
    evidence — the exact hallucination surface this architecture removes."""
    workflow = _make_workflow_with_completed_stage("context_discovery", {"graph_components": []})
    mock_db = AsyncMock()

    with pytest.raises(AppError) as excinfo:
        await override_stage_result(
            mock_db,
            workflow,
            "context_discovery",
            {"graph_components": [{"name": "TotallyFakeComponent"}]},
            uuid.uuid4(),
        )

    assert excinfo.value.error_code == "field_not_overridable"
    assert get_stage_result(workflow, "context_discovery")["graph_components"] == []


@pytest.mark.asyncio
async def test_override_cannot_rewrite_the_reasoning_record() -> None:
    workflow = _make_workflow_with_completed_stage(
        "context_discovery", {"discovery_report": {"readiness": "BLOCKED"}}
    )
    mock_db = AsyncMock()

    for forged in ({"discovery_report": {}}, {"working_memory": {}}, {"capability_confidence": {}}):
        with pytest.raises(AppError) as excinfo:
            await override_stage_result(
                mock_db, workflow, "context_discovery", forged, uuid.uuid4()
            )
        assert excinfo.value.error_code == "field_not_overridable"


@pytest.mark.asyncio
async def test_override_rejects_an_empty_correction() -> None:
    workflow = _make_workflow_with_completed_stage("context_discovery", {"graph_context_text": "x"})
    with pytest.raises(AppError) as excinfo:
        await override_stage_result(AsyncMock(), workflow, "context_discovery", {}, uuid.uuid4())
    assert excinfo.value.error_code == "empty_override"


@pytest.mark.asyncio
async def test_override_accepts_the_canonical_repositories_field() -> None:
    """ADR 0010 §2 — `repositories` is the one repository-shaped field a
    human correction may target. `RepositorySelector.tsx`'s save action
    sends exactly this shape."""
    workflow = _make_workflow_with_completed_stage(
        "context_discovery",
        {"repositories": [{"name": "payment-service", "source": "explicit", "selected": True}]},
    )
    mock_db = AsyncMock()
    override = {
        "repositories": [
            {"name": "payment-service", "source": "explicit", "selected": True},
            {"name": "billing-service", "source": "suggested", "selected": True},
        ]
    }

    await override_stage_result(mock_db, workflow, "context_discovery", override, uuid.uuid4())

    effective = get_stage_result(workflow, "context_discovery")
    assert effective["repositories"] == override["repositories"]


@pytest.mark.asyncio
async def test_override_rejects_every_repository_projection_field() -> None:
    """ADR 0010, invariant I6 — a human correction may never target a
    projection field directly; each is derived exclusively from
    `repositories` by `reasoning.projection.project_repositories`. Allowing
    any of these here would let an override silently disagree with the
    canonical field it's supposed to be derived from."""
    workflow = _make_workflow_with_completed_stage(
        "context_discovery", {"repositories": [], "ranked_repository_names": []}
    )
    mock_db = AsyncMock()

    for forged_field in (
        "selected_repositories",
        "explicit_repositories",
        "suggested_repositories",
        "ranked_repository_names",
        "implementation_candidates",
    ):
        with pytest.raises(AppError) as excinfo:
            await override_stage_result(
                mock_db, workflow, "context_discovery", {forged_field: []}, uuid.uuid4()
            )
        assert excinfo.value.error_code == "field_not_overridable", forged_field


@pytest.mark.asyncio
async def test_a_stage_with_no_declared_correctable_fields_accepts_nothing() -> None:
    """Fails closed: a stage nobody has thought about is not silently editable."""
    workflow = _make_workflow_with_completed_stage("planning", {"executive_summary": "A plan."})
    with pytest.raises(AppError) as excinfo:
        await override_stage_result(
            AsyncMock(), workflow, "planning", {"executive_summary": "forged"}, uuid.uuid4()
        )
    assert excinfo.value.error_code == "field_not_overridable"
    assert "no human-correctable fields" in str(excinfo.value)
