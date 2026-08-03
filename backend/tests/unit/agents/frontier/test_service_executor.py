"""Unit tests for `ServiceExecutor` — mocks each of the six Engineering
Intelligence Service entry points to verify dispatch, not their own
behavior (already covered by
`tests/integration/test_engineering_intelligence_*.py`)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.frontier.service_executor import (
    ArchitectureInsightCall,
    ChangeSimulationCall,
    DependencyQueryCall,
    ImpactAnalysisCall,
    RepositoryProfileCall,
    execute,
)
from app.services.engineering_intelligence.contracts import EntityReference

pytestmark = pytest.mark.asyncio

_REPO_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_ENTITY = EntityReference(repository_id=str(_REPO_ID), node_id=f"{_REPO_ID}:svc:checkout")


async def test_execute_dispatches_repository_profile_call() -> None:
    sentinel = object()
    with patch(
        "app.agents.frontier.service_executor.repository_profile_service.get_profile",
        new=AsyncMock(return_value=sentinel),
    ) as mock_call:
        result = await execute(object(), object(), [RepositoryProfileCall(repository_id=_REPO_ID)])

    mock_call.assert_awaited_once()
    assert result.results == (sentinel,)
    assert result.errors == ()


async def test_execute_dispatches_impact_analysis_call() -> None:
    sentinel = object()
    with patch(
        "app.agents.frontier.service_executor.impact_analysis_service.compute_blast_radius",
        new=AsyncMock(return_value=sentinel),
    ) as mock_call:
        result = await execute(
            object(),
            object(),
            [ImpactAnalysisCall(entity=_ENTITY, direction="downstream", max_hops=3)],
        )

    _, kwargs = mock_call.call_args
    assert kwargs["direction"] == "downstream"
    assert kwargs["max_hops"] == 3
    assert result.results == (sentinel,)


async def test_execute_dispatches_change_simulation_call() -> None:
    sentinel = object()
    with patch(
        "app.agents.frontier.service_executor.change_simulation_service.simulate",
        new=AsyncMock(return_value=sentinel),
    ) as mock_call:
        result = await execute(
            object(),
            object(),
            [ChangeSimulationCall(entity=_ENTITY, change_type="remove_endpoint")],
        )

    mock_call.assert_awaited_once()
    assert result.results == (sentinel,)


async def test_execute_dispatches_non_graph_calls_without_graph_repository() -> None:
    with (
        patch(
            "app.agents.frontier.service_executor.dependency_query_service.search",
            new=AsyncMock(return_value="deps"),
        ),
        patch(
            "app.agents.frontier.service_executor.architecture_insight_service.detect_findings",
            new=AsyncMock(return_value="findings"),
        ),
    ):
        result = await execute(
            object(),
            None,
            [
                DependencyQueryCall(repository_ids=(_REPO_ID,)),
                ArchitectureInsightCall(repository_ids=(_REPO_ID,)),
            ],
        )

    assert result.results == ("deps", "findings")
    assert result.errors == ()


async def test_execute_raises_clear_error_when_graph_repository_missing_for_graph_call() -> None:
    result = await execute(object(), None, [RepositoryProfileCall(repository_id=_REPO_ID)])

    assert result.results == (None,)
    assert len(result.errors) == 1
    assert "graph_repository" in result.errors[0]


async def test_execute_one_failure_does_not_discard_other_results() -> None:
    with (
        patch(
            "app.agents.frontier.service_executor.dependency_query_service.search",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "app.agents.frontier.service_executor.architecture_insight_service.detect_findings",
            new=AsyncMock(return_value="findings"),
        ),
    ):
        result = await execute(
            object(),
            None,
            [
                DependencyQueryCall(repository_ids=(_REPO_ID,)),
                ArchitectureInsightCall(repository_ids=(_REPO_ID,)),
            ],
        )

    assert result.results == (None, "findings")
    assert len(result.errors) == 1
    assert "[0] dependency_query" in result.errors[0]


async def test_execute_returns_empty_result_for_no_calls() -> None:
    result = await execute(object(), None, [])
    assert result.calls == ()
    assert result.results == ()
    assert result.errors == ()
