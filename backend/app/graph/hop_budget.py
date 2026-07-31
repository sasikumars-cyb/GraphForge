"""Hop-budget enforcement for `AgentManifest.max_graph_hops`.

The Knowledge Graph has no native "traverse N hops from a seed node"
primitive — every `IGraphRepository` read method (`get_full_graph`,
`get_nodes_by_label`) returns a whole slice of *one repository's* graph in
one call; there is no hop-by-hop walk to bound. The closest faithful
enforcement of the manifest's intent is therefore a **per-repository**
call budget, not a per-run one: every existing graph-reading agent
traverses exactly `max_graph_hops` calls per repository already —

- Planning (`max_graph_hops=2`): `get_nodes_by_label(repo, "Component")` +
  `get_nodes_by_label(repo, "KafkaTopic")` — 2 calls per repository.
- Development / Testing (`max_graph_hops=3`): the same two label reads
  plus one `get_full_graph(repo)` for dependency traversal — 3 calls per
  repository.

A per-run (cumulative across every repository) budget would reject any
multi-repository installation using these exact numbers on its second
repository — a regression, not an activation, of the manifest. Per
repository, the existing traversal pattern sits exactly at each manifest's
declared budget, which is why per-repository is the reading enforced here
rather than a global counter.

- `max_graph_hops=0` (code_generation, git_ops, engineering_review,
  documentation_planning) means "must not read the graph at all this
  run" — fully enforceable today regardless of interpretation, and the
  most important case: these are exactly the agents with no business
  touching the graph.

`GraphHopBudgetRepository` wraps a real `IGraphRepository` (e.g.
`Neo4jGraphRepository`) and raises `GraphHopBudgetExceeded` the instant a
call for a given repository would exceed the manifest's per-repository
budget. `replace_repository_graph` (the only write method) is deliberately
never budget-limited or exposed through here — the indexer, not an agent
run, owns graph writes.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.core.exceptions import AppError
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload

logger = logging.getLogger(__name__)


class GraphHopBudgetExceeded(AppError):
    """Raised when an agent's own graph reads against one repository would
    exceed its manifest's `max_graph_hops`. A hardcoded traversal depth
    inside an agent's tools can never work around this — the budget is
    enforced on the repository object itself, one level below any tool."""

    status_code = 422
    error_code = "graph_hop_budget_exceeded"


class GraphHopBudgetRepository(IGraphRepository):
    """Read-only proxy enforcing a per-repository graph-read budget.

    Constructed once per agent run (see
    `RunCoordinator.execute_run`/`build_hop_budgeted_repository`) from the
    dispatched agent's own `AgentManifest.max_graph_hops` — never a value
    an agent chooses for itself. Tracks calls per `repository_id`, so
    traversing 10 different (indexed) repositories in one run costs
    nothing extra per repository beyond its own budget.
    """

    def __init__(self, inner: IGraphRepository, max_hops: int, agent_id: str) -> None:
        self._inner = inner
        self._max_hops = max_hops
        self._agent_id = agent_id
        self._used_by_repo: dict[str, int] = defaultdict(int)

    def hops_used(self, repository_id: str) -> int:
        return self._used_by_repo[repository_id]

    def _consume(self, repository_id: str, tool_name: str) -> None:
        used = self._used_by_repo[repository_id]
        if used + 1 > self._max_hops:
            raise GraphHopBudgetExceeded(
                f"Agent '{self._agent_id}' exceeded its graph hop budget for "
                f"repository '{repository_id}' (max_graph_hops={self._max_hops}) "
                f"calling '{tool_name}'."
            )
        self._used_by_repo[repository_id] = used + 1
        logger.debug(
            "graph_hop_consumed agent_id=%s repository_id=%s tool=%s used=%d/%d",
            self._agent_id,
            repository_id,
            tool_name,
            self._used_by_repo[repository_id],
            self._max_hops,
        )

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        raise NotImplementedError(
            "GraphHopBudgetRepository is read-only — agents never write to the graph "
            "directly (see app.graph.interfaces.IGraphRepository's module docstring)."
        )

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        self._consume(repository_id, "get_full_graph")
        return await self._inner.get_full_graph(repository_id)

    async def get_nodes_by_label(self, repository_id: str, label: str) -> list[GraphNode]:
        self._consume(repository_id, "get_nodes_by_label")
        return await self._inner.get_nodes_by_label(repository_id, label)

    async def get_kafka_topic_edges(self, repository_id: str) -> list[GraphEdge]:
        self._consume(repository_id, "get_kafka_topic_edges")
        return await self._inner.get_kafka_topic_edges(repository_id)

    async def has_graph(self, repository_id: str) -> bool:
        # An indexing-status existence check, not a graph traversal — free
        # regardless of budget, same reasoning `verify_repository` already
        # relies on (see app.agents.code_generation.verification).
        return await self._inner.has_graph(repository_id)

    async def replace_cross_repository_edges(
        self, source_repository_id: str, edges: list[GraphEdge]
    ) -> None:
        raise NotImplementedError(
            "GraphHopBudgetRepository is read-only — cross-repository edges are written "
            "by the indexer's cross_repo_linker, never by an agent run."
        )

    async def get_outgoing_cross_repository_edges(self, repository_id: str) -> list[GraphEdge]:
        self._consume(repository_id, "get_outgoing_cross_repository_edges")
        return await self._inner.get_outgoing_cross_repository_edges(repository_id)


def build_hop_budgeted_repository(
    inner: IGraphRepository, max_hops: int, agent_id: str
) -> GraphHopBudgetRepository:
    """One place to construct a hop-budgeted repository from a manifest's
    `max_graph_hops` — used by the dispatcher (RunCoordinator) so every
    agent gets the same enforcement without agent-specific wiring."""
    return GraphHopBudgetRepository(inner, max_hops, agent_id)
