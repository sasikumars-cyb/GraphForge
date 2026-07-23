"""Shared shapes for the Change Investigation Agent's reasoning loop.

Plain dataclasses, mirroring the style of `app.analysis.models.impact`, so
the reasoning log serialises trivially for the API response and for
debugging/demonstration - no framework-specific "agent message" types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.graph.models import TraversalHop
from app.analysis.models.impact import RiskLevel
from app.graph.models import GraphNode


@dataclass(frozen=True)
class Observation:
    """What a tool call returned, plus a short human-readable summary for
    the reasoning log - the log records *why* a call was worth making,
    not just its raw payload."""

    tool_name: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasoningStep:
    """One iteration of the agent's Plan -> Select Tool -> Execute ->
    Observe -> Decide loop, recorded verbatim for explainability.

    `tool_selected` is `None` for a step where the agent decided evidence
    wasn't needed - a skip is itself a recorded decision, not a gap in the
    log.
    """

    step_number: int
    goal: str
    plan: str
    tool_selected: str | None
    observation: Observation | None
    decision: str


@dataclass
class AgentState:
    """Mutable working memory threaded through the reasoning loop.

    Every field is either supplied at construction (from the pull
    request's real changed files) or populated by a tool's `Observation` -
    nothing here is invented by the agent itself. Shaped closely after
    what `ImpactAnalysisEngine` computes internally, since the agent's
    tools wrap the exact same reader methods - the difference is that the
    agent decides which of these get populated at all.
    """

    changed_files: list[str] = field(default_factory=list)
    pom_changed: bool = False

    direct_nodes: list[GraphNode] = field(default_factory=list)
    api_hops: list[TraversalHop] = field(default_factory=list)
    topic_hops: list[TraversalHop] = field(default_factory=list)
    same_repository_peer_hops: list[TraversalHop] = field(default_factory=list)
    cross_repository_peer_hops: list[TraversalHop] = field(default_factory=list)
    dependencies: list[GraphNode] = field(default_factory=list)

    risk: RiskLevel = RiskLevel.LOW
    impacted_repositories: list[dict[str, str]] = field(default_factory=list)
    diff_content: str = ""
    recent_file_authors: dict[str, list[str]] = field(default_factory=dict)

    reasoning_log: list[ReasoningStep] = field(default_factory=list)

    @property
    def direct_service_nodes(self) -> list[GraphNode]:
        """The subset of `direct_nodes` that are architecture-visible
        components (`Controller`/`Service`/`FeignClient`) rather than
        DTOs, config, or anything else the indexer doesn't track."""
        return [node for node in self.direct_nodes if "Component" in node.labels]

    @property
    def has_impacted_services(self) -> bool:
        """Whether there is any real service impact at all - directly
        changed, or reachable via a same-repository/cross-repository topic
        peer. Used both to decide whether a reviewer suggestion is worth
        grounding, and (on a low-confidence retry) whether git history is
        worth fetching."""
        return bool(
            self.direct_service_nodes
            or self.cross_repository_peer_hops
            or self.same_repository_peer_hops
        )
