"""Unit test proving `ChangeSimulationService` performs zero traversal of
its own — it must call `impact_analysis_service.compute_blast_radius`
exactly once and use its result verbatim."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.engineering_intelligence.change_simulation_service import simulate
from app.services.engineering_intelligence.contracts import BlastRadius, EntityReference

pytestmark = pytest.mark.asyncio


async def test_simulate_delegates_entirely_to_impact_analysis_service() -> None:
    entity = EntityReference(repository_id="repo-1", node_id="repo-1:endpoint:get:/orders")
    fake_blast_radius = BlastRadius(
        seed=entity,
        direction="downstream",
        max_hops=2,
        impacted_repositories=("repo-2",),
        impacted_apis=("repo-2:endpoint:get:/x",),
    )

    with patch(
        "app.services.engineering_intelligence.impact_analysis_service.compute_blast_radius",
        new=AsyncMock(return_value=fake_blast_radius),
    ) as mock_compute:
        impact = await simulate(object(), object(), entity, "remove_endpoint")

    mock_compute.assert_awaited_once()
    _, kwargs = mock_compute.call_args
    assert kwargs["direction"] == "downstream"
    assert impact.blast_radius is fake_blast_radius
    assert "1" in impact.risk_summary or "repo-2" in impact.risk_summary


async def test_simulate_uses_upstream_direction_for_dependency_upgrades() -> None:
    entity = EntityReference(repository_id="repo-1", node_id="repo-1:dep:commons")
    fake_blast_radius = BlastRadius(seed=entity, direction="upstream", max_hops=2)

    with patch(
        "app.services.engineering_intelligence.impact_analysis_service.compute_blast_radius",
        new=AsyncMock(return_value=fake_blast_radius),
    ) as mock_compute:
        await simulate(object(), object(), entity, "upgrade_dependency")

    _, kwargs = mock_compute.call_args
    assert kwargs["direction"] == "upstream"
