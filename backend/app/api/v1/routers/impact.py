"""GET /repositories/{id}/impact — the fast, deterministic blast-radius
endpoint Impact Check's radial graph reads directly.

Deliberately bypasses `ImpactAnalysisAgent` (the LLM-narrated agent-run
flow `ImpactAnalysisPage.tsx` already uses) entirely: that flow is a slow,
narrative-generating LLM call appropriate for "explain this impact to me
in prose," not for driving an interactive visualization a user expects to
respond to clicking a different seed node in under a second.
`compute_blast_radius` (the same deterministic computation the agent
itself calls under the hood — see `ImpactAnalysisAgent.build_service_
requests`) is called directly here instead.

`direction` is intentionally not exposed as a request parameter here.
`ImpactAnalysisService.compute_blast_radius` does now genuinely honor its
own `direction: Literal["downstream", "upstream"]` argument — the gap this
comment used to describe (`graph_traversal.traverse` silently dropping it,
`get_neighborhood`'s Cypher being unconditionally undirected regardless of
what was asked for) was fixed directly in `get_neighborhood` itself. The
Impact lens's blast radius stays undirected on purpose, though: a change's
blast radius is everything that could be affected by touching the seed,
which includes both what it calls (downstream) and what calls it
(upstream) — collapsing that to one direction would silently under-report
risk, not just change the shape of the graph. (The Dependency lens is
where a direction toggle belongs — see `GET /repositories/{id}/graph/
nodes/{node_id}/neighbors`'s own `direction` param.)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.repository import Repository
from app.models.user import User
from app.schemas.impact import BlastRadiusResponse, RelationshipInsightResponse
from app.schemas.indexing import GraphResponse
from app.services.engineering_intelligence.contracts import EntityReference
from app.services.engineering_intelligence.impact_analysis_service import compute_blast_radius

router = APIRouter(prefix="/repositories", tags=["impact"])


async def _get_owned_repository(
    db: AsyncSession, repository_id: uuid.UUID, current_user: User
) -> Repository:
    result = await db.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.user_id == current_user.id
        )
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found.")
    return repository


@router.get("/{repository_id}/impact", response_model=BlastRadiusResponse)
async def get_blast_radius(
    repository_id: uuid.UUID,
    node_id: str | None = Query(
        None,
        description=(
            "The node to seed the blast radius from — must belong to this repository. "
            "Omit to seed from the repository's own node (the same default the "
            "impact_analysis agent uses)."
        ),
    ),
    max_hops: int = Query(
        2, ge=1, le=5, description="Same bound get_neighborhood itself enforces."
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> BlastRadiusResponse:
    repository = await _get_owned_repository(db, repository_id, current_user)
    seed_node_id = node_id or f"{repository.id}:repository"

    blast_radius = await compute_blast_radius(
        db,
        Neo4jGraphRepository(get_driver()),
        EntityReference(repository_id=str(repository.id), node_id=seed_node_id),
        max_hops=max_hops,
    )

    return BlastRadiusResponse(
        seed_node_id=seed_node_id,
        max_hops=max_hops,
        graph=GraphResponse(
            nodes=[
                {"id": n.id, "labels": n.labels, "properties": n.properties}
                for n in blast_radius.subgraph.nodes
            ],
            edges=[
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "type": e.type,
                    "properties": e.properties,
                }
                for e in blast_radius.subgraph.edges
            ],
        ),
        impacted_repositories=list(blast_radius.impacted_repositories),
        impacted_apis=list(blast_radius.impacted_apis),
        impacted_databases=list(blast_radius.impacted_databases),
        impacted_queues=list(blast_radius.impacted_queues),
        relationships=[
            RelationshipInsightResponse(
                relationship_type=r.relationship_type,
                source_entity=r.source_entity,
                target_entity=r.target_entity,
                confidence_state=r.confidence_state,
            )
            for r in blast_radius.relationships
        ],
    )
