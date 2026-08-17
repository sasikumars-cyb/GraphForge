"""`ImpactAnalysisService` — blast-radius computation. Owns no traversal
logic of its own: every hop of graph walking goes through
`graph_traversal.traverse`, every confidence/explanation lookup through
`relationship_lookup.fetch_with_confidence`. `ChangeSimulationService`
calls this service rather than duplicating any of it (approved design's
explicit constraint).
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.services.engineering_intelligence import graph_traversal, relationship_lookup
from app.services.engineering_intelligence.contracts import (
    BlastRadius,
    EntityReference,
    RelationshipInsight,
)

# Edge types a blast-radius walk follows in either direction — the same
# vocabulary `app.indexer.graph.builder`/`cross_repo_linker` write.
_IMPACT_EDGE_TYPES = [
    "CALLS",
    "CALLS_SERVICE",
    "EXPOSES",
    "PRODUCES_TO",
    "CONSUMES_FROM",
    "DEPENDS_ON",
    "DEPENDS_ON_REPOSITORY",
    "SHARES_TOPIC",
    "READS_FROM",
    "WRITES_TO",
]


_DIRECTION_TO_NEO4J: dict[str, Literal["outgoing", "incoming"]] = {
    "downstream": "outgoing",
    "upstream": "incoming",
}


def _repository_uuid_of(node_id: str) -> str:
    """The repository UUID a graph node id belongs to. Every node written
    by `app.indexer.graph.builder` is namespaced `<repository_uuid>:<kind>
    [:<rest>]`, so the leading segment is the owning repository. Returns
    the whole string unchanged when there's no separator — a malformed or
    differently-shaped id then simply never equal-matches a real UUID,
    which is the safe direction to fail (it stays in the impacted set
    rather than being silently dropped)."""
    return node_id.split(":", 1)[0]


async def compute_blast_radius(
    db: AsyncSession,
    graph_repository: IGraphRepository,
    entity: EntityReference,
    *,
    direction: Literal["downstream", "upstream"] = "downstream",
    max_hops: int = 2,
) -> BlastRadius:
    """`direction` is now real — previously accepted but silently
    ignored, since `graph_traversal.traverse` had no direction logic of
    its own despite its docstring's claim (found while building the
    Dependency lens; fixed at the source — `get_neighborhood` — not
    re-implemented here). "downstream" (the default — "what does this
    change affect") now correctly walks only outgoing edges from the
    seed; "upstream" ("what would affect this if it changed") walks only
    incoming ones. Every existing caller left at the default sees a
    narrower, more correct blast radius than before (outgoing-only,
    rather than every direction mixed together and labeled "downstream"
    regardless)."""
    neighborhood = await graph_traversal.traverse(
        graph_repository,
        repository_id=entity.repository_id,
        seed_node_ids=[entity.node_id],
        edge_types=_IMPACT_EDGE_TYPES,
        max_hops=max_hops,
        direction=_DIRECTION_TO_NEO4J[direction],
    )

    impacted_repositories: set[str] = set()
    for node in neighborhood.payload.nodes:
        if "Repository" in node.labels:
            impacted_repositories.add(node.id)
    for edge in neighborhood.payload.edges:
        if edge.type == "DEPENDS_ON_REPOSITORY":
            impacted_repositories.add(edge.target_id)

    # The seed is never its own downstream impact. A traversal centred on
    # a repository necessarily walks back into that repository's own
    # Repository node, so without this every blast radius reported at
    # least one "impacted repository" — itself — which made a repository
    # with zero real dependents indistinguishable from one with a real
    # one, and made `_severity`'s "low" band (total == 0) unreachable.
    #
    # Compared on the repository UUID rather than the node id so a
    # Repository node addressed by any id shape still resolves to the same
    # repository; `all_involved_repository_ids` below re-adds the seed
    # explicitly, so relationship lookup is unaffected by this exclusion.
    impacted_repositories = {
        node_id
        for node_id in impacted_repositories
        if _repository_uuid_of(node_id) != entity.repository_id
    }

    all_involved_repository_ids: set[uuid.UUID] = {uuid.UUID(entity.repository_id)}
    for repo_node_id in impacted_repositories:
        try:
            all_involved_repository_ids.add(uuid.UUID(repo_node_id.split(":")[0]))
        except (ValueError, IndexError):
            continue

    relationships: list[RelationshipInsight] = []
    for repository_uuid in sorted(all_involved_repository_ids, key=str):
        relationships.extend(await relationship_lookup.fetch_with_confidence(db, repository_uuid))

    return BlastRadius(
        seed=entity,
        direction=direction,
        max_hops=max_hops,
        impacted_repositories=tuple(sorted(impacted_repositories)),
        impacted_apis=neighborhood.nodes_by_label.get("Endpoint", ()),
        impacted_databases=neighborhood.nodes_by_label.get("DataTable", ()),
        impacted_queues=neighborhood.nodes_by_label.get("KafkaTopic", ()),
        relationships=tuple(sorted(relationships, key=lambda r: r.relationship_key)),
        subgraph=neighborhood.payload,
    )
