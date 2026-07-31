"""Tests for TraverseArchitectureGraphTool's `repository_filter` — the fix
for a real retrieval-breadth defect: `GraphInvestigator.run()` folded a
known repository into relevance-scoring terms but never actually
restricted which repositories got a full Component-node fetch, so a
"scope" action (the owning repository is already known) still fetched
every OTHER indexed repository's full component list too, on every
reasoning cycle. `repository_filter` is what a "scope"/"verify" action
now sets to stop that; `None` (a genuine "survey", nothing known yet)
still traverses everything, which is correct — nothing can be ranked
without first seeing what exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.planning.tools import TraverseArchitectureGraphTool
from app.graph.models import GraphNode


def _repo(name: str) -> dict[str, str]:
    return {"id": f"{name}-id", "name": name}


def _fake_graph_repository(nodes_by_repo: dict[str, list[GraphNode]]) -> AsyncMock:
    repo = AsyncMock()

    async def get_nodes_by_label(repository_id: str, label: str) -> list[GraphNode]:
        return nodes_by_repo.get(repository_id, [])

    repo.get_nodes_by_label = AsyncMock(side_effect=get_nodes_by_label)
    return repo


@pytest.mark.asyncio
async def test_no_filter_traverses_every_repository() -> None:
    graph_repo = _fake_graph_repository(
        {
            "etl-core-id": [
                GraphNode(id="c1", labels=["Component", "Class"], properties={"name": "A"})
            ],
            "other-repo-id": [
                GraphNode(id="c2", labels=["Component", "Class"], properties={"name": "B"})
            ],
        }
    )
    tool = TraverseArchitectureGraphTool(graph_repository=graph_repo)

    result = await tool.execute([_repo("etl-core"), _repo("other-repo")])

    assert {c["name"] for c in result.data["components"]} == {"A", "B"}
    assert graph_repo.get_nodes_by_label.call_count == 4  # 2 repos x (Component + KafkaTopic)


@pytest.mark.asyncio
async def test_filter_restricts_traversal_to_named_repository_only() -> None:
    graph_repo = _fake_graph_repository(
        {
            "etl-core-id": [
                GraphNode(id="c1", labels=["Component", "Class"], properties={"name": "A"})
            ],
            "other-repo-id": [
                GraphNode(id="c2", labels=["Component", "Class"], properties={"name": "B"})
            ],
        }
    )
    tool = TraverseArchitectureGraphTool(graph_repository=graph_repo)

    result = await tool.execute(
        [_repo("etl-core"), _repo("other-repo")], repository_filter=["etl-core"]
    )

    assert {c["name"] for c in result.data["components"]} == {"A"}
    # Only etl-core's two label reads (Component + KafkaTopic) — other-repo
    # is never touched at all, not even queried and discarded.
    assert graph_repo.get_nodes_by_label.call_count == 2
    assert result.data["repository_count"] == 1


@pytest.mark.asyncio
async def test_filter_is_case_insensitive() -> None:
    graph_repo = _fake_graph_repository(
        {"etl-core-id": [GraphNode(id="c1", labels=["Component"], properties={"name": "A"})]}
    )
    tool = TraverseArchitectureGraphTool(graph_repository=graph_repo)

    result = await tool.execute([_repo("etl-core")], repository_filter=["ETL-CORE"])

    assert result.data["repository_count"] == 1


@pytest.mark.asyncio
async def test_filter_matching_nothing_returns_empty_without_querying() -> None:
    graph_repo = _fake_graph_repository({})
    tool = TraverseArchitectureGraphTool(graph_repository=graph_repo)

    result = await tool.execute([_repo("etl-core")], repository_filter=["nonexistent-repo"])

    assert result.data == {"components": [], "kafka_topics": [], "repository_count": 0}
    graph_repo.get_nodes_by_label.assert_not_called()
