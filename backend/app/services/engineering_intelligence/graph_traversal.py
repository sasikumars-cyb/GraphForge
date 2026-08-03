"""The ONLY graph-traversal helper in the Engineering Intelligence Service
Layer. Every service that needs to walk the graph (`ImpactAnalysisService`,
`ArchitectureInsightService`) calls `traverse` — none of them re-implements
hop expansion or edge filtering. `ChangeSimulationService` calls
`ImpactAnalysisService` instead of this module directly, so it never
duplicates traversal either (approved design's explicit constraint).

Deliberately a thin wrapper over `IGraphRepository.get_neighborhood`
(`app.graph.interfaces`), which already does the real hop-bounded,
cycle-safe traversal in the graph store. This module owns direction
handling (`get_neighborhood` is direction-agnostic — both directions,
always) and turning its `GraphPayload` output into the label-grouped
shape every caller here needs, not a second traversal algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphPayload


@dataclass(frozen=True)
class Neighborhood:
    """The induced subgraph plus one grouping every caller in this layer
    needs — nodes bucketed by their graph label (`Endpoint`, `DataTable`,
    `KafkaTopic`, `Repository`, ...) so a caller never re-scans
    `payload.nodes` itself."""

    payload: GraphPayload
    nodes_by_label: dict[str, tuple[str, ...]]


def _group_by_label(payload: GraphPayload) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for node in payload.nodes:
        for label in node.labels:
            grouped.setdefault(label, []).append(node.id)
    return {label: tuple(sorted(set(ids))) for label, ids in sorted(grouped.items())}


async def traverse(
    graph_repository: IGraphRepository,
    *,
    repository_id: str,
    seed_node_ids: list[str],
    edge_types: list[str],
    max_hops: int,
) -> Neighborhood:
    """The induced subgraph within `max_hops` of `seed_node_ids`, grouped
    by node label. An empty `seed_node_ids` or `edge_types` returns an
    empty neighborhood without querying — same short-circuit contract as
    `get_neighborhood` itself."""
    if not seed_node_ids or not edge_types:
        return Neighborhood(payload=GraphPayload(), nodes_by_label={})

    payload = await graph_repository.get_neighborhood(
        repository_id, seed_node_ids, edge_types, max_hops
    )
    return Neighborhood(payload=payload, nodes_by_label=_group_by_label(payload))
