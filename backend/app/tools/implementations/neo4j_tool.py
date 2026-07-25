"""Neo4j Knowledge Graph Tool.

Wraps the existing GetIndexedRepositoriesTool + TraverseArchitectureGraphTool
from app.agents.planning.tools — the same graph reads, now exposed through
the Tool Platform contract so any agent can use them without importing the
Planning Agent's internal modules.

ToolInput.parameters expected keys:
    db             AsyncSession — required
    graph_repo     IGraphRepository — optional; created from get_driver() if absent
    relevance_terms list[str] — optional; used for relevance scoring in format_graph_context
"""

from __future__ import annotations

import logging

from app.agents.planning.tools import (
    GetIndexedRepositoriesTool,
    TraverseArchitectureGraphTool,
    format_graph_context,
)
from app.graph.interfaces import IGraphRepository
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.tools.interfaces import (
    ToolCategory,
    ToolHealth,
    ToolInput,
    ToolResult,
)

logger = logging.getLogger(__name__)


class Neo4jGraphTool:
    """Provides Knowledge Graph context: indexed repositories + component/topic graph.

    Enabled by default — uses the app-level Neo4j driver singleton,
    no external credentials needed beyond the driver being configured.
    """

    tool_id = "neo4j_graph"
    display_name = "Knowledge Graph (Neo4j)"
    description = (
        "Reads the Neo4j Knowledge Graph to find indexed repositories, "
        "software components (Controllers, Services, FeignClients), and "
        "Kafka topics. This is the primary source of ground-truth architecture "
        "facts for the Planning Agent."
    )
    category = ToolCategory.GRAPH
    capabilities = [
        "repository_discovery",
        "component_graph",
        "kafka_topics",
        "architecture_context",
    ]

    def __init__(self, config: dict) -> None:
        pass

    def requires_auth(self) -> bool:
        return False

    async def execute(self, input: ToolInput) -> ToolResult:
        db = input.parameters.get("db")
        if db is None:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="ToolInput.parameters['db'] (AsyncSession) is required.",
            )

        graph_repo: IGraphRepository = input.parameters.get(
            "graph_repo"
        ) or Neo4jGraphRepository(get_driver())

        relevance_terms: list[str] = input.parameters.get("relevance_terms") or []

        try:
            repos_tool = GetIndexedRepositoriesTool(db=db, graph_repository=graph_repo)
            repos_obs = await repos_tool.execute()

            indexed_repos: list[dict] = repos_obs.data.get("indexed_repositories", [])

            traverse_tool = TraverseArchitectureGraphTool(graph_repository=graph_repo)
            traverse_obs = await traverse_tool.execute(indexed_repos)

            context_text = format_graph_context(
                repos_obs, traverse_obs, relevance_terms=relevance_terms
            )

            component_count = len(traverse_obs.data.get("components", []))
            topic_count = len(traverse_obs.data.get("kafka_topics", []))

            evidence_items = [
                repos_obs.summary,
                traverse_obs.summary,
            ]

            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=repos_obs.succeeded,
                data={
                    "context_text": context_text,
                    "indexed_repositories": indexed_repos,
                    "components": traverse_obs.data.get("components", []),
                    "kafka_topics": traverse_obs.data.get("kafka_topics", []),
                    # Carry individual observations so the planning agent can
                    # still build its Evidence objects with the correct kinds.
                    "_repos_succeeded": repos_obs.succeeded,
                    "_traverse_succeeded": traverse_obs.succeeded,
                    "_repos_summary": repos_obs.summary,
                    "_traverse_summary": traverse_obs.summary,
                    "_component_count": component_count,
                    "_topic_count": topic_count,
                },
                summary=(
                    f"Knowledge Graph: {len(indexed_repos)} repositor"
                    f"{'y' if len(indexed_repos) == 1 else 'ies'}, "
                    f"{component_count} component{'s' if component_count != 1 else ''}, "
                    f"{topic_count} Kafka topic{'s' if topic_count != 1 else ''}."
                ),
                evidence_items=evidence_items,
                token_estimate=len(context_text) // 4,
            )

        except Exception as exc:
            logger.error("neo4j_graph_tool_failed error=%s", exc, exc_info=True)
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=str(exc),
            )

    async def health_check(self) -> ToolHealth:
        try:
            driver = get_driver()
            async with driver.session() as session:
                await session.run("RETURN 1")
            return ToolHealth.HEALTHY
        except Exception as exc:
            logger.warning("neo4j_health_check_failed error=%s", exc)
            return ToolHealth.OFFLINE
