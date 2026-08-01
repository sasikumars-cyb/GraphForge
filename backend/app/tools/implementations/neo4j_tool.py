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
from typing import Any

from app.agents.planning.tools import (
    GetIndexedRepositoriesTool,
    TraverseArchitectureGraphTool,
    format_graph_context,
    rank_repositories,
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

    def __init__(self, config: dict[str, Any]) -> None:
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

        # Required, and validated before any query runs: repository rows are
        # per-user (see GetIndexedRepositoriesTool), so executing without a
        # user id would read every account's repositories into this run's
        # prompt and evidence. Failing the tool is the safe outcome — an
        # unscoped read is worse than no graph context at all.
        user_id = input.parameters.get("user_id")
        if user_id is None:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="ToolInput.parameters['user_id'] is required to scope repository access.",
            )

        graph_repo: IGraphRepository = input.parameters.get("graph_repo") or Neo4jGraphRepository(
            get_driver()
        )

        relevance_terms: list[str] = input.parameters.get("relevance_terms") or []
        # Restricts traversal to specific repositories by name — set by
        # GraphInvestigator when the owning repository is already known
        # (a "scope" or "verify" action), so this run doesn't re-fetch
        # every Component node of every OTHER indexed repository just to
        # refresh a ranking that's already settled. None (the default,
        # used for a genuine "survey" with no repository known yet) still
        # traverses everything — see TraverseArchitectureGraphTool.execute.
        repository_filter: list[str] | None = input.parameters.get("repository_filter")

        try:
            repos_tool = GetIndexedRepositoriesTool(
                db=db, graph_repository=graph_repo, user_id=user_id
            )
            repos_obs = await repos_tool.execute()

            indexed_repos: list[dict[str, Any]] = repos_obs.data.get("indexed_repositories", [])
            # Tracked repositories that aren't HEALTHY, with the specific
            # reason (see `GraphHealthStatus`) — forwarded so callers (e.g.
            # `GraphInvestigator`) can explain *why* a repository can't be
            # used instead of it just silently not appearing above.
            unhealthy_repos: list[dict[str, Any]] = repos_obs.data.get(
                "unhealthy_repositories", []
            )

            traverse_tool = TraverseArchitectureGraphTool(graph_repository=graph_repo)
            traverse_obs = await traverse_tool.execute(
                indexed_repos, repository_filter=repository_filter
            )

            context_text = format_graph_context(
                repos_obs, traverse_obs, relevance_terms=relevance_terms
            )

            component_count = len(traverse_obs.data.get("components", []))
            topic_count = len(traverse_obs.data.get("kafka_topics", []))

            # Real cross-repository relationships (see
            # app.indexer.graph.cross_repo_linker) — one Neo4j read per
            # repository considered, each already-cheap relative to the
            # component/topic traversal above. Only ever connects repos
            # already in `indexed_repos` (the same user's own), by
            # construction of the linker that wrote them.
            #
            # Scoped by the same `repository_filter` as the component
            # traversal above, for the same reason: this used to loop
            # over EVERY indexed repository regardless of whether a
            # "scope"/"verify" action already knew which one repository
            # this run was about, spending that repository's own
            # hop-budget allowance for every OTHER repository too.
            id_to_name = {repo["id"]: repo["name"] for repo in indexed_repos}
            if repository_filter is not None:
                wanted = {n.lower() for n in repository_filter}
                edge_source_repos = [r for r in indexed_repos if r["name"].lower() in wanted]
            else:
                edge_source_repos = indexed_repos
            cross_repository_edges: list[dict[str, Any]] = []
            for repo in edge_source_repos:
                edges = await graph_repo.get_outgoing_cross_repository_edges(repo["id"])
                for edge in edges:
                    target_repository_id = edge.target_id.rsplit(":repository", 1)[0]
                    target_name = id_to_name.get(target_repository_id)
                    if target_name is None:
                        continue
                    cross_repository_edges.append(
                        {
                            "source_repository": repo["name"],
                            "target_repository": target_name,
                            "type": edge.type,
                            "properties": dict(edge.properties),
                        }
                    )

            # Best-first repository names by the same deterministic score
            # `format_graph_context` used to build `context_text` — exposed as
            # its own field so callers (e.g. the entity/tenant mismatch check
            # in app.agents.verification) know exactly which repository was
            # actually selected, without re-deriving or guessing it from the
            # rendered markdown text.
            ranked_repositories = [
                name
                for _, name in rank_repositories(
                    indexed_repos, traverse_obs.data.get("components", []), relevance_terms
                )
            ]

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
                    "unhealthy_repositories": unhealthy_repos,
                    "ranked_repositories": ranked_repositories,
                    "components": traverse_obs.data.get("components", []),
                    "kafka_topics": traverse_obs.data.get("kafka_topics", []),
                    "cross_repository_edges": cross_repository_edges,
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
