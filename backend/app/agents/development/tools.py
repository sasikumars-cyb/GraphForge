"""Development Agent tools — own minimal tool-calling code.

NOT shared with the Planning or Review agents (per agent isolation pattern).
These tools are specific to the development/change-planning domain:
gathering detailed component, dependency, and relationship data from the
Knowledge Graph to produce an implementation blueprint.

Each tool wraps existing graph-read methods and returns an Observation.
No write operations — agents never write to the graph directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphNode
from app.models.repository import Repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observation — same pattern as PlanningObservation, separate type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DevelopmentObservation:
    """What a development tool call returned."""

    tool_name: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    succeeded: bool = True
    error: str = ""


# ---------------------------------------------------------------------------
# Development tools
# ---------------------------------------------------------------------------


class RepositoryDiscoveryTool:
    """Discover all indexed repositories and their basic metadata.

    Evidence kind: tool_call
    """

    name = "discover_repositories"

    def __init__(self, db: AsyncSession, graph_repository: IGraphRepository) -> None:
        self._db = db
        self._graph_repository = graph_repository

    async def execute(self) -> DevelopmentObservation:
        try:
            result = await self._db.execute(select(Repository))
            all_repos: list[Repository] = list(result.scalars().all())

            indexed: list[dict[str, str]] = []
            for repo in all_repos:
                if await self._graph_repository.has_graph(str(repo.id)):
                    indexed.append({
                        "id": str(repo.id),
                        "name": repo.name,
                        "owner": repo.owner,
                    })

            summary = (
                f"Discovered {len(indexed)} indexed repositor{'y' if len(indexed) == 1 else 'ies'} "
                f"out of {len(all_repos)} tracked."
            )

            return DevelopmentObservation(
                tool_name=self.name,
                summary=summary,
                data={"indexed_repositories": indexed, "total_tracked": len(all_repos)},
            )
        except Exception as exc:
            logger.warning("dev_tool_discover_repos_failed error=%s", str(exc))
            return DevelopmentObservation(
                tool_name=self.name,
                summary=f"Failed to discover repositories: {exc}",
                data={},
                succeeded=False,
                error=str(exc),
            )


class ComponentDiscoveryTool:
    """Discover all components across indexed repositories with their types,
    file paths, and relationships.

    Evidence kind: graph_traversal
    """

    name = "discover_components"

    _COMPONENT_LABELS = ("Component", "KafkaTopic")

    def __init__(self, graph_repository: IGraphRepository) -> None:
        self._graph_repository = graph_repository

    async def execute(
        self, repositories: list[dict[str, str]]
    ) -> DevelopmentObservation:
        if not repositories:
            return DevelopmentObservation(
                tool_name=self.name,
                summary="No indexed repositories — cannot discover components.",
                data={"components": [], "kafka_topics": [], "repository_count": 0},
            )

        all_components: list[dict[str, Any]] = []
        all_topics: list[dict[str, Any]] = []
        errors: list[str] = []

        for repo in repositories:
            repo_id = repo["id"]
            repo_name = repo["name"]
            try:
                components = await self._graph_repository.get_nodes_by_label(
                    repo_id, "Component"
                )
                for node in components:
                    all_components.append({
                        "id": node.id,
                        "name": node.properties.get("name", node.id),
                        "type": next(
                            (la for la in node.labels if la != "Component"),
                            "Component",
                        ),
                        "repository": repo_name,
                        "file_path": node.properties.get("file_path", ""),
                    })

                topics = await self._graph_repository.get_nodes_by_label(
                    repo_id, "KafkaTopic"
                )
                for node in topics:
                    all_topics.append({
                        "id": node.id,
                        "name": node.properties.get("name", node.id),
                        "repository": repo_name,
                    })

            except Exception as exc:
                errors.append(f"{repo_name}: {exc}")
                logger.warning(
                    "dev_tool_discover_components_failed repo=%s error=%s",
                    repo_name, str(exc),
                )

        summary = (
            f"Discovered {len(all_components)} component(s) and "
            f"{len(all_topics)} Kafka topic(s) across "
            f"{len(repositories)} repositor{'y' if len(repositories) == 1 else 'ies'}."
        )
        if errors:
            summary += f" {len(errors)} repositor{'y' if len(errors) == 1 else 'ies'} failed."

        all_failed = len(errors) == len(repositories)
        return DevelopmentObservation(
            tool_name=self.name,
            summary=summary,
            data={
                "components": all_components,
                "kafka_topics": all_topics,
                "repository_count": len(repositories),
            },
            succeeded=not all_failed,
            error="; ".join(errors) if all_failed else "",
        )


class DependencyTraversalTool:
    """Traverse edges in the Knowledge Graph to discover dependency
    relationships: CALLS, PRODUCES_TO, CONSUMES_FROM, DEPENDS_ON.

    Evidence kind: graph_traversal
    """

    name = "traverse_dependencies"

    def __init__(self, graph_repository: IGraphRepository) -> None:
        self._graph_repository = graph_repository

    async def execute(
        self, repositories: list[dict[str, str]]
    ) -> DevelopmentObservation:
        if not repositories:
            return DevelopmentObservation(
                tool_name=self.name,
                summary="No indexed repositories — cannot traverse dependencies.",
                data={"edges": [], "cross_repo_edges": []},
            )

        all_edges: list[dict[str, str]] = []
        cross_repo_edges: list[dict[str, str]] = []
        errors: list[str] = []

        for repo in repositories:
            repo_id = repo["id"]
            repo_name = repo["name"]
            try:
                graph_payload = await self._graph_repository.get_full_graph(repo_id)
                for edge in graph_payload.edges:
                    edge_data = {
                        "source": edge.source_id,
                        "target": edge.target_id,
                        "type": edge.type,
                        "repository": repo_name,
                    }
                    all_edges.append(edge_data)

            except Exception as exc:
                errors.append(f"{repo_name}: {exc}")
                logger.warning(
                    "dev_tool_traverse_deps_failed repo=%s error=%s",
                    repo_name, str(exc),
                )

        # Identify cross-repository edges (edges referencing nodes from different repos)
        # This is done by checking if topics/feign-clients are shared across repos
        topic_producers: dict[str, list[str]] = {}
        topic_consumers: dict[str, list[str]] = {}
        for edge in all_edges:
            if edge["type"] == "PRODUCES_TO":
                topic_producers.setdefault(edge["target"], []).append(edge["repository"])
            elif edge["type"] == "CONSUMES_FROM":
                topic_consumers.setdefault(edge["source"], []).append(edge["repository"])

        for topic_id, producers in topic_producers.items():
            consumers = topic_consumers.get(topic_id, [])
            for prod_repo in producers:
                for cons_repo in consumers:
                    if prod_repo != cons_repo:
                        cross_repo_edges.append({
                            "topic": topic_id,
                            "producer_repo": prod_repo,
                            "consumer_repo": cons_repo,
                            "type": "CROSS_REPO_KAFKA",
                        })

        summary = (
            f"Traversed {len(all_edges)} edge(s) across "
            f"{len(repositories)} repositor{'y' if len(repositories) == 1 else 'ies'}. "
            f"Found {len(cross_repo_edges)} cross-repository coupling(s)."
        )
        if errors:
            summary += f" {len(errors)} repositor{'y' if len(errors) == 1 else 'ies'} failed."

        all_failed = len(errors) == len(repositories) and len(repositories) > 0
        return DevelopmentObservation(
            tool_name=self.name,
            summary=summary,
            data={
                "edges": all_edges[:100],  # cap for prompt budget
                "cross_repo_edges": cross_repo_edges,
                "total_edges": len(all_edges),
            },
            succeeded=not all_failed,
            error="; ".join(errors) if all_failed else "",
        )


# ---------------------------------------------------------------------------
# Graph context formatter
# ---------------------------------------------------------------------------


def format_graph_context(
    repos_obs: DevelopmentObservation,
    components_obs: DevelopmentObservation,
    deps_obs: DevelopmentObservation,
) -> str:
    """Format all tool observations into a compact, LLM-readable context."""
    parts: list[str] = []

    indexed_repos = repos_obs.data.get("indexed_repositories", [])
    if not indexed_repos:
        return "No repositories have been indexed into the Knowledge Graph yet."

    repo_names = [r["name"] for r in indexed_repos]
    parts.append(f"**Indexed repositories**: {', '.join(repo_names)}")

    # Components grouped by repository
    components = components_obs.data.get("components", [])
    if components:
        by_repo: dict[str, list[str]] = {}
        for comp in components[:50]:
            repo = comp["repository"]
            comp_desc = f"{comp['name']} ({comp['type']})"
            if comp.get("file_path"):
                comp_desc += f" [{comp['file_path']}]"
            by_repo.setdefault(repo, []).append(comp_desc)
        comp_lines = []
        for repo, comps in by_repo.items():
            comp_lines.append(f"  {repo}: {', '.join(comps[:15])}")
        parts.append("**Components**:\n" + "\n".join(comp_lines))
    else:
        parts.append("**Components**: none indexed yet")

    # Kafka topics
    topics = components_obs.data.get("kafka_topics", [])
    if topics:
        topic_names = list({t["name"] for t in topics})[:20]
        parts.append(f"**Kafka topics**: {', '.join(topic_names)}")
    else:
        parts.append("**Kafka topics**: none indexed yet")

    # Dependency edges (summarized)
    edges = deps_obs.data.get("edges", [])
    if edges:
        edge_types: dict[str, int] = {}
        for e in edges:
            edge_types[e["type"]] = edge_types.get(e["type"], 0) + 1
        edge_summary = ", ".join(f"{k}: {v}" for k, v in sorted(edge_types.items()))
        parts.append(f"**Dependency edges**: {deps_obs.data.get('total_edges', len(edges))} total ({edge_summary})")

        # Show key relationships
        key_edges = [e for e in edges if e["type"] in ("CALLS", "PRODUCES_TO", "CONSUMES_FROM")][:20]
        if key_edges:
            rel_lines = []
            for e in key_edges:
                rel_lines.append(f"  {e['source']} —[{e['type']}]→ {e['target']} ({e['repository']})")
            parts.append("**Key relationships**:\n" + "\n".join(rel_lines))
    else:
        parts.append("**Dependency edges**: none indexed yet")

    # Cross-repo coupling
    cross_repo = deps_obs.data.get("cross_repo_edges", [])
    if cross_repo:
        coupling_lines = []
        for cr in cross_repo[:10]:
            coupling_lines.append(
                f"  {cr['producer_repo']} → [{cr['topic']}] → {cr['consumer_repo']}"
            )
        parts.append("**Cross-repository coupling**:\n" + "\n".join(coupling_lines))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Evidence builders
# ---------------------------------------------------------------------------


def to_evidence(observation: DevelopmentObservation, kind: str) -> Evidence:
    """Convert a DevelopmentObservation to a contract Evidence entry.

    If the observation failed, evidence kind is forced to "tool_call"
    with a failure-prefixed summary (never implies successful traversal).
    """
    if not observation.succeeded:
        return Evidence(
            kind="tool_call",  # type: ignore[arg-type]
            reference=observation.tool_name,
            summary=f"FAILED: {observation.summary}",
        )
    return Evidence(
        kind=kind,  # type: ignore[arg-type]
        reference=observation.tool_name,
        summary=observation.summary,
    )
