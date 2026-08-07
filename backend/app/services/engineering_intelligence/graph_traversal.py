"""The ONLY graph-traversal helper in the Engineering Intelligence Service
Layer. Every service that needs to walk the graph (`ImpactAnalysisService`,
`ArchitectureInsightService`) calls `traverse` — none of them re-implements
hop expansion or edge filtering. `ChangeSimulationService` calls
`ImpactAnalysisService` instead of this module directly, so it never
duplicates traversal either (approved design's explicit constraint).

Deliberately a thin wrapper over `IGraphRepository.get_neighborhood`
(`app.graph.interfaces`), which does the real hop-bounded, cycle-safe
traversal in the graph store — this module never re-implements it, only
forwards `direction` to it and turns the result into the label-grouped
shape every caller here needs.

This module's own docstring used to claim it "owns direction handling"
because `get_neighborhood` was "direction-agnostic — both directions,
always" — found, while building the Dependency lens, to have never
actually been true: `traverse` had no direction logic of its own, so
`ImpactAnalysisService.compute_blast_radius`'s own `direction` argument
was silently inert the whole time. Fixed by giving `get_neighborhood`
itself a real `direction` parameter and forwarding it here, rather than
re-introducing direction-filtering logic in this module a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
    direction: Literal["any", "outgoing", "incoming"] = "any",
) -> Neighborhood:
    """The induced subgraph within `max_hops` of `seed_node_ids`, grouped
    by node label. An empty `seed_node_ids` or `edge_types` returns an
    empty neighborhood without querying — same short-circuit contract as
    `get_neighborhood` itself. `direction="any"` (the default) preserves
    every existing caller's exact behavior; forwarded, not re-implemented
    — see this module's own docstring."""
    if not seed_node_ids or not edge_types:
        return Neighborhood(payload=GraphPayload(), nodes_by_label={})

    payload = await graph_repository.get_neighborhood(
        repository_id, seed_node_ids, edge_types, max_hops, direction=direction
    )
    return Neighborhood(payload=payload, nodes_by_label=_group_by_label(payload))
