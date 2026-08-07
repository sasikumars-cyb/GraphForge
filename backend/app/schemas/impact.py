"""Response schema for GET /repositories/{id}/impact — the fast,
deterministic blast-radius endpoint the Impact Check visualization reads
directly, bypassing the LLM-narrated `impact_analysis` agent entirely for
the graph itself (that agent's own narrative summary stays available as a
separate, on-demand action — see `ImpactAnalysisAgent`/`render_impact_
analysis`, unchanged by this endpoint).

Wraps `app.services.engineering_intelligence.contracts.BlastRadius`
directly — no new computation, this is `compute_blast_radius`'s existing
output reshaped for the API boundary.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.indexing import GraphResponse


class RelationshipInsightResponse(BaseModel):
    relationship_type: str
    source_entity: str
    target_entity: str
    # One of verified/highly_likely/likely/candidate/rejected/conflicting
    # — see EngineeringMemoryService/KnowledgeRelationshipRecord, never
    # re-derived here.
    confidence_state: str


class BlastRadiusResponse(BaseModel):
    seed_node_id: str
    max_hops: int
    # Every impacted node, with `hop_distance` already present in each
    # node's own `properties` (see `Neo4jGraphRepository.get_neighborhood`)
    # — this is what a radial layout groups nodes into rings by, without
    # a second request.
    graph: GraphResponse
    impacted_repositories: list[str]
    impacted_apis: list[str]
    impacted_databases: list[str]
    impacted_queues: list[str]
    relationships: list[RelationshipInsightResponse]
