"""Planning Agent tools — own minimal tool-calling code.

NOT shared with the Review Agent's ToolRegistry. These tools are specific to the planning domain: gathering a
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


def _relevance(text: str, terms: list[str]) -> int:
    """How many capability search terms this text matches."""
    lowered = text.lower()
    return sum(1 for t in terms if t in lowered)


def format_graph_context(
    repos_observation: PlanningObservation,
    traverse_observation: PlanningObservation,
    relevance_terms: list[str] | None = None,
    max_repos: int = 4,
    max_components_per_repo: int = 5,
) -> str:
    """Format graph tool observations into a compact, LLM-readable context.

    When `relevance_terms` is supplied (the search terms derived from the
    detected capabilities), repositories and components are ranked by how well
    they match what the architecture actually needs, and only the top matches
    are included. This does three things at once:

    - better reuse recommendations, because the model sees the repositories
      that implement the required capabilities rather than an arbitrary slice;
    - less repository bias, because unrelated services never reach the prompt
      and so cannot be pattern-matched into the architecture;
    - fewer tokens, because the inventory shrinks to what is relevant.

    With no terms it degrades to the previous behaviour (first N, unranked),
    so callers that have not done capability analysis still work.
    """
    parts: list[str] = []
    terms = relevance_terms or []

    indexed_repos: list[dict[str, str]] = repos_observation.data.get(
        "indexed_repositories", []
    )
    if not indexed_repos:
        return "No repositories have been indexed into the Knowledge Graph yet."

    components: list[dict[str, Any]] = traverse_observation.data.get("components", [])

    # Score each repository by how many of its components match the required
    # capabilities. Repository name counts too — a repo called
    # "etl-customer-orders" is evidence in its own right.
    by_repo_all: dict[str, list[dict[str, Any]]] = {}
    for comp in components:
        by_repo_all.setdefault(comp["repository"], []).append(comp)

    def repo_score(name: str) -> int:
        if not terms:
            return 0
        score = _relevance(name, terms) * 2
        for comp in by_repo_all.get(name, []):
            score += _relevance(f"{comp['name']} {comp['type']}", terms)
        return score

    scored = sorted(
        ((repo_score(r["name"]), r["name"]) for r in indexed_repos),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if terms:
        # Drop repositories that match no required capability at all — they
        # are the ones that get pattern-matched into the architecture for no
        # reason. Only when something genuinely scored, so a brief we could
        # not score still sees an inventory rather than an empty list.
        positive = [name for score, name in scored if score > 0]
        shown = (positive or [name for _, name in scored])[:max_repos]
    else:
        shown = [name for _, name in scored]
    omitted = len(scored) - len(shown)

    header = f"**Relevant repositories**: {', '.join(shown)}"
    if omitted > 0:
        # Stated explicitly so the model knows the list is a filtered view and
        # does not assume these are the only repositories that exist.
        plural = "y" if omitted == 1 else "ies"
        header += (
            f" ({omitted} further indexed repositor{plural} "
            "less relevant to these capabilities)"
        )
    parts.append(header)

    if components:
        comp_lines = []
        for repo in shown:
            comps = by_repo_all.get(repo, [])
            if terms:
                comps = sorted(
                    comps,
                    key=lambda c: (-_relevance(f"{c['name']} {c['type']}", terms), c["name"]),
                )
            listed = [f"{c['name']} ({c['type']})" for c in comps[:max_components_per_repo]]
            if listed:
                comp_lines.append(f"  {repo}: {', '.join(listed)}")
        parts.append(
            "**Components**:\n" + "\n".join(comp_lines)
            if comp_lines
            else "**Components**: none in the relevant repositories"
        )
    else:
        parts.append("**Components**: none indexed yet")

    topics: list[dict[str, Any]] = traverse_observation.data.get("kafka_topics", [])
    if topics:
        topic_names = list({t["name"] for t in topics})[:12]
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
            kind="tool_call",
            reference=observation.tool_name,
            summary=f"FAILED: {observation.summary}",
        )
    return Evidence(
        kind=kind,
        reference=observation.tool_name,
        summary=observation.summary,
    )
