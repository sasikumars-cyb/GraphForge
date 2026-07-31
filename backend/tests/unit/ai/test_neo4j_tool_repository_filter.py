"""`Neo4jGraphTool.execute`'s `repository_filter` — the same retrieval-
breadth fix as `TraverseArchitectureGraphTool` (see
test_traverse_architecture_graph_scoping.py), applied to the
cross-repository-edges loop too: it used to call
`get_outgoing_cross_repository_edges` for EVERY indexed repository
regardless of whether a "scope"/"verify" action already knew which one
repository this run was about.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.implementations.neo4j_tool import Neo4jGraphTool
from app.tools.interfaces import ToolInput


def _indexed_repos() -> list[dict[str, str]]:
    return [
        {"id": "repo-a-id", "name": "etl-core", "owner": "acme"},
        {"id": "repo-b-id", "name": "other-repo", "owner": "acme"},
    ]


@pytest.mark.asyncio
async def test_no_filter_fetches_cross_repo_edges_for_every_repository() -> None:
    graph_repo = AsyncMock()
    graph_repo.get_outgoing_cross_repository_edges = AsyncMock(return_value=[])

    with patch(
        "app.tools.implementations.neo4j_tool.GetIndexedRepositoriesTool"
    ) as repos_tool_cls, patch(
        "app.tools.implementations.neo4j_tool.TraverseArchitectureGraphTool"
    ) as traverse_tool_cls:
        repos_tool_cls.return_value.execute = AsyncMock(
            return_value=type(
                "Obs", (), {"data": {"indexed_repositories": _indexed_repos()}, "succeeded": True}
            )()
        )
        traverse_tool_cls.return_value.execute = AsyncMock(
            return_value=type(
                "Obs",
                (),
                {"data": {"components": [], "kafka_topics": []}, "succeeded": True},
            )()
        )

        tool = Neo4jGraphTool({})
        await tool.execute(
            ToolInput(
                query="",
                parameters={"db": object(), "user_id": "u1", "graph_repo": graph_repo},
            )
        )

    assert graph_repo.get_outgoing_cross_repository_edges.call_count == 2


@pytest.mark.asyncio
async def test_repository_filter_restricts_cross_repo_edges_to_named_repository() -> None:
    graph_repo = AsyncMock()
    graph_repo.get_outgoing_cross_repository_edges = AsyncMock(return_value=[])

    with patch(
        "app.tools.implementations.neo4j_tool.GetIndexedRepositoriesTool"
    ) as repos_tool_cls, patch(
        "app.tools.implementations.neo4j_tool.TraverseArchitectureGraphTool"
    ) as traverse_tool_cls:
        repos_tool_cls.return_value.execute = AsyncMock(
            return_value=type(
                "Obs", (), {"data": {"indexed_repositories": _indexed_repos()}, "succeeded": True}
            )()
        )
        traverse_tool_cls.return_value.execute = AsyncMock(
            return_value=type(
                "Obs",
                (),
                {"data": {"components": [], "kafka_topics": []}, "succeeded": True},
            )()
        )

        tool = Neo4jGraphTool({})
        await tool.execute(
            ToolInput(
                query="",
                parameters={
                    "db": object(),
                    "user_id": "u1",
                    "graph_repo": graph_repo,
                    "repository_filter": ["etl-core"],
                },
            )
        )

    assert graph_repo.get_outgoing_cross_repository_edges.call_count == 1
    graph_repo.get_outgoing_cross_repository_edges.assert_called_once_with("repo-a-id")
