"""Planning Agent tools — own minimal tool-calling code.

NOT shared with the Review Agent's ToolRegistry (per TEAM_EXECUTION_PLAN.md
Section 1). These tools are specific to the planning domain: gathering a
high-level architecture overview from the Knowledge Graph to ground an
implementation plan.

Each tool wraps one or more existing deterministic graph-read methods and
returns an Observation describing what it found. No write operations here —
agents never write to the graph directly (GraphWriter rule from AGENT_FRAMEWORK.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphNode
from app.models.repository import Repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observation — mirrors the Review Agent's Observation shape but is
# intentionally a separate type (no shared framework between agents).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanningObservation:
    """What a planning tool call returned."""

    tool_name: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    succeeded: bool = True
    error: str = ""


# ---------------------------------------------------------------------------
# Planning tools
# ---------------------------------------------------------------------------


class GetIndexedRepositoriesTool:
    """Fetch all repositories that have been successfully indexed into the
    Knowledge Graph.

    Evidence kind: tool_call
    Data: list of repository names and their IDs.
    """

    name = "get_indexed_repositories"

    def __init__(self, db: AsyncSession, graph_repository: IGraphRepository) -> None:
        self._db = db
        self._graph_repository = graph_repository

    async def execute(self) -> PlanningObservation:
        try:
            result = await self._db.execute(select(Repository))
            all_repos: list[Repository] = list(result.scalars().all())

            indexed: list[dict[str, str]] = []
            for repo in all_repos:
                if await self._graph_repository.has_graph(str(repo.id)):
                    indexed.append({"id": str(repo.id), "name": repo.name, "owner": repo.owner})

            summary = (
                f"Found {len(indexed)} indexed repositor{'y' if len(indexed) == 1 else 'ies'} "
                f"out of {len(all_repos)} tracked."
            )
            if indexed:
                names = ", ".join(r["name"] for r in indexed[:10])
                summary += f" Indexed: {names}."

            logger.debug(
                "planning_tool_get_indexed_repos indexed=%d total=%d",
                len(indexed), len(all_repos),
            )

            return PlanningObservation(
                tool_name=self.name,
                summary=summary,
                data={"indexed_repositories": indexed, "total_tracked": len(all_repos)},
            )
        except Exception as exc:
            logger.warning(
                "planning_tool_get_indexed_repos_failed error=%s", str(exc)
            )
            return PlanningObservation(
                tool_name=self.name,
                summary=f"Failed to retrieve repositories: {exc}",
                data={},
                succeeded=False,
                error=str(exc),
            )


class TraverseArchitectureGraphTool:
    """Traverse the Knowledge Graph for a set of repositories: get all
    Components (Controllers, Services, FeignClients) and KafkaTopics.

    This is the graph-traversal step that grounds the planning agent's
    output in real architecture facts rather than LLM hallucinations.

    Evidence kind: graph_traversal
    Data: component list, kafka topic list, per-repository counts.
    """

    name = "traverse_architecture_graph"

    _ARCHITECTURE_LABELS = ("Component", "KafkaTopic")

    def __init__(self, graph_repository: IGraphRepository) -> None:
        self._graph_repository = graph_repository

    async def execute(
        self, repositories: list[dict[str, str]]
    ) -> PlanningObservation:
        if not repositories:
            return PlanningObservation(
                tool_name=self.name,
                summary="No indexed repositories to traverse.",
                data={"components": [], "kafka_topics": [], "repository_count": 0},
            )

        all_components: list[dict[str, Any]] = []
        all_topics: list[dict[str, Any]] = []
        errors: list[str] = []

        for repo in repositories:
            repo_id = repo["id"]
            repo_name = repo["name"]
            try:
                # Components: Controllers, Services, FeignClients
                components = await self._graph_repository.get_nodes_by_label(
                    repo_id, "Component"
                )
                for node in components:
                    all_components.append({
                        "id": node.id,
                        "name": node.properties.get("name", node.id),
                        "type": next(
                            (l for l in node.labels if l != "Component"),
                            "Component",
                        ),
                        "repository": repo_name,
                        "file_path": node.properties.get("file_path", ""),
                    })

                # Kafka topics
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
                    "planning_tool_traverse_failed_for_repo repo=%s error=%s",
                    repo_name, str(exc),
                )

        summary_parts = [
            f"Graph traversal across {len(repositories)} repositor{'y' if len(repositories) == 1 else 'ies'}",
            f"found {len(all_components)} component{'s' if len(all_components) != 1 else ''}",
            f"and {len(all_topics)} Kafka topic{'s' if len(all_topics) != 1 else ''}.",
        ]
        if errors:
            summary_parts.append(f"{len(errors)} repositor{'y' if len(errors) == 1 else 'ies'} failed.")
        summary = " ".join(summary_parts)

        logger.info(
            "planning_tool_traverse_architecture_graph components=%d topics=%d repos=%d",
            len(all_components), len(all_topics), len(repositories),
        )

        # Succeeded only if at least one repository was traversed without error.
        # All-failures means the graph was unreachable, not that it was empty.
        all_failed = len(errors) == len(repositories)
        return PlanningObservation(
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


# ---------------------------------------------------------------------------
# Graph context formatter — turns tool observations into LLM-readable text
# ---------------------------------------------------------------------------


def format_graph_context(
    repos_observation: PlanningObservation,
    traverse_observation: PlanningObservation,
) -> str:
    """Format graph tool observations into a compact, LLM-readable context
    string. Truncated to avoid exceeding prompt budget.
    """
    parts: list[str] = []

    indexed_repos: list[dict[str, str]] = repos_observation.data.get(
        "indexed_repositories", []
    )
    if not indexed_repos:
        return "No repositories have been indexed into the Knowledge Graph yet."

    repo_names = [r["name"] for r in indexed_repos]
    parts.append(f"**Indexed repositories**: {', '.join(repo_names)}")

    components: list[dict[str, Any]] = traverse_observation.data.get("components", [])
    if components:
        # Group by repository for readability; cap at 40 entries total
        by_repo: dict[str, list[str]] = {}
        for comp in components[:40]:
            repo = comp["repository"]
            comp_type = comp["type"]
            comp_name = comp["name"]
            by_repo.setdefault(repo, []).append(f"{comp_name} ({comp_type})")
        comp_lines = []
        for repo, comps in by_repo.items():
            comp_lines.append(f"  {repo}: {', '.join(comps[:10])}")
        parts.append("**Components**:\n" + "\n".join(comp_lines))
    else:
        parts.append("**Components**: none indexed yet")

    topics: list[dict[str, Any]] = traverse_observation.data.get("kafka_topics", [])
    if topics:
        topic_names = list({t["name"] for t in topics})[:20]
        parts.append(f"**Kafka topics**: {', '.join(topic_names)}")
    else:
        parts.append("**Kafka topics**: none indexed yet")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Evidence builders
# ---------------------------------------------------------------------------


def to_evidence(observation: PlanningObservation, kind: str) -> Evidence:
    """Convert a PlanningObservation to a contract Evidence entry.

    If the observation failed, the evidence kind is forced to
    ``"tool_call"`` with a failure-prefixed summary — never
    ``"graph_traversal"`` or the requested ``kind``, because that would
    imply a successful traversal/call that did not happen (P0-1).
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
