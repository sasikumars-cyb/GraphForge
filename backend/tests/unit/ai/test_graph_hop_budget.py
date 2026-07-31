"""Unit tests for app.graph.hop_budget — AgentManifest.max_graph_hops
enforcement (Part 2 / Part 4).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.graph.hop_budget import (
    GraphHopBudgetExceeded,
    GraphHopBudgetRepository,
    build_hop_budgeted_repository,
)
from app.graph.models import GraphPayload


def _make_inner() -> AsyncMock:
    inner = AsyncMock()
    inner.get_full_graph = AsyncMock(return_value=GraphPayload())
    inner.get_nodes_by_label = AsyncMock(return_value=[])
    inner.get_neighborhood = AsyncMock(return_value=GraphPayload())
    inner.has_graph = AsyncMock(return_value=True)
    return inner


@pytest.mark.asyncio
async def test_zero_budget_blocks_get_nodes_by_label() -> None:
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=0, agent_id="code_generation")

    with pytest.raises(GraphHopBudgetExceeded):
        await repo.get_nodes_by_label("repo-1", "Component")

    inner.get_nodes_by_label.assert_not_called()


@pytest.mark.asyncio
async def test_zero_budget_blocks_get_full_graph() -> None:
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=0, agent_id="engineering_review")

    with pytest.raises(GraphHopBudgetExceeded):
        await repo.get_full_graph("repo-1")


@pytest.mark.asyncio
async def test_zero_budget_still_allows_has_graph() -> None:
    """has_graph is an indexing-status check, not a traversal — always
    free regardless of budget (see module docstring)."""
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=0, agent_id="code_generation")

    result = await repo.has_graph("repo-1")

    assert result is True
    inner.has_graph.assert_called_once()


@pytest.mark.asyncio
async def test_budget_allows_exactly_n_calls_per_repository() -> None:
    """Planning's max_graph_hops=2 matches its own traversal pattern
    exactly: 2 get_nodes_by_label calls per repository."""
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=2, agent_id="planning")

    await repo.get_nodes_by_label("repo-1", "Component")
    await repo.get_nodes_by_label("repo-1", "KafkaTopic")

    assert repo.hops_used("repo-1") == 2

    with pytest.raises(GraphHopBudgetExceeded):
        await repo.get_nodes_by_label("repo-1", "SomethingElse")


@pytest.mark.asyncio
async def test_budget_is_tracked_per_repository_not_globally() -> None:
    """A multi-repository run must not be penalized for traversing more
    than one repository — each repository gets its own budget."""
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=2, agent_id="planning")

    await repo.get_nodes_by_label("repo-1", "Component")
    await repo.get_nodes_by_label("repo-1", "KafkaTopic")
    # A second, independent repository should not be blocked by repo-1's
    # already-exhausted budget.
    await repo.get_nodes_by_label("repo-2", "Component")
    await repo.get_nodes_by_label("repo-2", "KafkaTopic")

    assert repo.hops_used("repo-1") == 2
    assert repo.hops_used("repo-2") == 2


@pytest.mark.asyncio
async def test_write_method_is_disabled() -> None:
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=3, agent_id="development")

    with pytest.raises(NotImplementedError):
        await repo.replace_repository_graph("repo-1", GraphPayload())


def test_class_implements_igraph_repository() -> None:
    from app.graph.interfaces import IGraphRepository

    assert issubclass(GraphHopBudgetRepository, IGraphRepository)


@pytest.mark.asyncio
async def test_get_neighborhood_counts_as_a_single_call_regardless_of_max_hops() -> None:
    """The whole point of this primitive: its internal traversal is
    genuinely hop-bounded by Neo4j itself, so it costs the same one call
    as get_full_graph (which has no depth bound at all) — a caller should
    always prefer it over get_full_graph once a seed set is available."""
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=1, agent_id="planning")

    await repo.get_neighborhood("repo-1", ["seed-1"], ["CALLS"], max_hops=5)

    assert repo.hops_used("repo-1") == 1
    inner.get_neighborhood.assert_called_once_with("repo-1", ["seed-1"], ["CALLS"], 5)


@pytest.mark.asyncio
async def test_zero_budget_blocks_get_neighborhood() -> None:
    inner = _make_inner()
    repo = build_hop_budgeted_repository(inner, max_hops=0, agent_id="documentation_planning")

    with pytest.raises(GraphHopBudgetExceeded):
        await repo.get_neighborhood("repo-1", ["seed-1"], ["CALLS"], max_hops=2)
