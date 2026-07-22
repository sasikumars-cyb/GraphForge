"""Tools the Change Investigation Agent may call.

Each tool wraps exactly one existing deterministic reader method (or the
same small, fixed bundle of them `ImpactAnalysisEngine` already uses) -
zero new graph-traversal, risk-classification, or business logic lives
here. The agent (`app.ai.agent.investigation_agent`) decides *whether* and
*when* to call each one; none of them run unconditionally, and none of
them are ever invoked by the deterministic engine or the original
single-shot AI analysis path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.models import AgentState, Observation
from app.ai.services.repository_resolution import resolve_impacted_repositories
from app.analysis.graph.interfaces import IImpactGraphReader
from app.graph.interfaces import IGraphRepository
from app.integrations.interfaces import IVersionControlProvider
from app.models.repository import Repository

# Roughly ~2k tokens worth of diff text - enough to show real code without
# risking the prompt exceeding the model's context budget on a large PR.
_MAX_DIFF_CHARS = 8_000

_GRAPH_VISIBLE_LABELS = (
    "Controller",
    "Service",
    "FeignClient",
    "Endpoint",
    "KafkaTopic",
    "MavenDependency",
)


class Tool(ABC):
    """A single unit of evidence-gathering the agent may choose to call."""

    name: str

    @abstractmethod
    async def execute(self, state: AgentState) -> Observation:
        """Populate `state` with whatever this tool discovers and return
        an `Observation` describing what happened, for the reasoning log."""
        raise NotImplementedError


class ReadDependencyGraphTool(Tool):
    """Maps the pull request's changed files to indexed graph nodes -
    wraps `IImpactGraphReader.find_nodes_by_file_paths`. This is the same
    first step `ImpactAnalysisEngine` always performs; the agent always
    calls it too, since without it there is no way to know whether the
    change touches the architecture graph at all."""

    name = "read_dependency_graph"

    def __init__(self, impact_graph_reader: IImpactGraphReader, repository_id: str) -> None:
        self._reader = impact_graph_reader
        self._repository_id = repository_id

    async def execute(self, state: AgentState) -> Observation:
        nodes = await self._reader.find_nodes_by_file_paths(
            self._repository_id, set(state.changed_files)
        )
        state.direct_nodes = nodes
        return Observation(
            tool_name=self.name,
            summary=(
                f"{len(nodes)} of {len(state.changed_files)} changed file(s) matched an "
                f"indexed graph node ({len(state.direct_service_nodes)} of those are "
                "architecture-visible components)."
            ),
            data={"node_count": len(nodes), "service_node_count": len(state.direct_service_nodes)},
        )


class TraverseDependencyGraphTool(Tool):
    """Expands from the directly-changed nodes to their downstream impact -
    wraps `find_downstream_apis`, `find_downstream_topics`,
    `find_same_repository_topic_peers`, `find_cross_repository_topic_peers`,
    and (only when a `pom.xml` changed) `get_dependencies` - the same
    traversal sequence `ImpactAnalysisEngine` always runs, run here only
    when the agent has decided the direct nodes found are worth expanding
    from."""

    name = "traverse_dependency_graph"

    def __init__(self, impact_graph_reader: IImpactGraphReader, repository_id: str) -> None:
        self._reader = impact_graph_reader
        self._repository_id = repository_id

    async def execute(self, state: AgentState) -> Observation:
        direct_ids = {node.id for node in state.direct_nodes}

        state.api_hops = await self._reader.find_downstream_apis(self._repository_id, direct_ids)
        state.topic_hops = await self._reader.find_downstream_topics(
            self._repository_id, direct_ids
        )

        topic_ids = {hop.to_node.id for hop in state.topic_hops}
        topic_names = {
            str(hop.to_node.properties["name"])
            for hop in state.topic_hops
            if hop.to_node.properties.get("name")
        }

        state.same_repository_peer_hops = (
            await self._reader.find_same_repository_topic_peers(
                self._repository_id, topic_ids, direct_ids
            )
            if topic_ids
            else []
        )
        state.cross_repository_peer_hops = (
            await self._reader.find_cross_repository_topic_peers(topic_names, self._repository_id)
            if topic_names
            else []
        )
        state.dependencies = (
            await self._reader.get_dependencies(self._repository_id) if state.pom_changed else []
        )

        return Observation(
            tool_name=self.name,
            summary=(
                f"{len(state.api_hops)} downstream API(s), {len(state.topic_hops)} Kafka "
                f"topic(s), {len(state.cross_repository_peer_hops)} cross-repository "
                "peer(s)."
            ),
            data={
                "api_hop_count": len(state.api_hops),
                "topic_hop_count": len(state.topic_hops),
                "cross_repository_peer_count": len(state.cross_repository_peer_hops),
            },
        )


class ReadIndexingInformationTool(Tool):
    """Summarizes how much of the repository's architecture graph is
    actually indexed - wraps `IGraphRepository.get_nodes_by_label`. Called
    only when the agent found zero directly-impacted nodes, as a sanity
    check on *why*: a genuinely non-architectural change (docs, config)
    versus a graph that simply hasn't indexed much yet."""

    name = "read_indexing_information"

    def __init__(self, graph_repository: IGraphRepository, repository_id: str) -> None:
        self._graph_repository = graph_repository
        self._repository_id = repository_id

    async def execute(self, state: AgentState) -> Observation:
        counts: dict[str, int] = {}
        for label in _GRAPH_VISIBLE_LABELS:
            nodes = await self._graph_repository.get_nodes_by_label(self._repository_id, label)
            counts[label] = len(nodes)

        total = sum(counts.values())
        breakdown = ", ".join(f"{count} {label}" for label, count in counts.items() if count)
        return Observation(
            tool_name=self.name,
            summary=(
                f"Repository graph has {total} architecture-visible node(s) indexed"
                + (f" ({breakdown})" if breakdown else "")
                + " - confirms the empty match isn't due to a sparse or stale index."
            ),
            data={"indexed_node_counts": counts},
        )


class RetrieveRepositoryMetadataTool(Tool):
    """Resolves cross-repository metadata for the coordination plan -
    wraps the same `resolve_impacted_repositories` lookup
    `AIAnalysisService` uses. Called only when traversal actually found
    cross-repository impact; otherwise there is nothing to resolve beyond
    the current repository itself."""

    name = "retrieve_repository_metadata"

    def __init__(self, db: AsyncSession, repository: Repository) -> None:
        self._db = db
        self._repository = repository

    async def execute(self, state: AgentState) -> Observation:
        indirectly_impacted = [
            {"repository_id": str(hop.from_node.properties.get("repository_id", ""))}
            for hop in state.cross_repository_peer_hops
        ]
        state.impacted_repositories = await resolve_impacted_repositories(
            self._db, self._repository, indirectly_impacted
        )
        downstream_count = len(state.impacted_repositories) - 1
        return Observation(
            tool_name=self.name,
            summary=f"Resolved {downstream_count} downstream repository(ies) by name.",
            data={"downstream_repository_count": downstream_count},
        )


class ReadGitDiffTool(Tool):
    """Fetches the pull request's unified diff - wraps
    `IVersionControlProvider.get_diff`. Called only when the risk level is
    non-trivial, so the LLM can reason about the actual code change rather
    than just which nodes it touches. Truncated to a fixed character
    budget - a deterministic, zero-cost stand-in for token-counting - so a
    large diff can't blow out the prompt's context budget."""

    name = "read_git_diff"

    def __init__(
        self,
        version_control_provider: IVersionControlProvider,
        owner: str,
        repo: str,
        pull_number: int,
        access_token: str | None,
    ) -> None:
        self._provider = version_control_provider
        self._owner = owner
        self._repo = repo
        self._pull_number = pull_number
        self._access_token = access_token

    async def execute(self, state: AgentState) -> Observation:
        diff = await self._provider.get_diff(
            owner=self._owner,
            repo=self._repo,
            pull_number=self._pull_number,
            access_token=self._access_token,
        )
        truncated = len(diff) > _MAX_DIFF_CHARS
        if truncated:
            diff = diff[:_MAX_DIFF_CHARS] + "\n... (diff truncated for prompt budget) ..."
        state.diff_content = diff

        return Observation(
            tool_name=self.name,
            summary=f"Fetched diff ({len(diff)} chars{', truncated' if truncated else ''}).",
            data={"diff_chars": len(diff), "truncated": truncated},
        )


class ReadGitHistoryTool(Tool):
    """Fetches real recent commit authorship per changed file - wraps
    `IVersionControlProvider.get_recent_file_authors`. Called only when
    there is an actual service impacted (so a reviewer suggestion is
    about to be made), to ground that suggestion in real history instead
    of letting the model guess a name."""

    name = "read_git_history"

    def __init__(
        self,
        version_control_provider: IVersionControlProvider,
        owner: str,
        repo: str,
        access_token: str | None,
    ) -> None:
        self._provider = version_control_provider
        self._owner = owner
        self._repo = repo
        self._access_token = access_token

    async def execute(self, state: AgentState) -> Observation:
        authors = await self._provider.get_recent_file_authors(
            owner=self._owner,
            repo=self._repo,
            file_paths=set(state.changed_files),
            access_token=self._access_token,
        )
        state.recent_file_authors = authors
        files_with_authors = sum(1 for names in authors.values() if names)
        return Observation(
            tool_name=self.name,
            summary=f"Found authorship for {files_with_authors} of {len(authors)} changed file(s).",
            data={"files_with_authors": files_with_authors},
        )


@dataclass(frozen=True)
class ToolRegistry:
    """Name-addressable lookup for the tools available in a single
    investigation - constructed fresh per request since several tools
    close over per-repository/per-PR identifiers."""

    tools: dict[str, Tool]

    def get(self, name: str) -> Tool:
        return self.tools[name]


class ToolExecutor:
    """Executes a tool by name and records that it was called - a thin
    seam so `InvestigationAgent` never touches `ToolRegistry` internals
    directly."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_name: str, state: AgentState) -> Observation:
        tool = self._registry.get(tool_name)
        return await tool.execute(state)
