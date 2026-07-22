"""Request/response schemas for the indexing job API and the architecture
graph it produces. The graph schemas mirror `app.graph.models` (`GraphNode`
/`GraphEdge`/`GraphPayload`) directly - Pydantic shapes for the same generic
vocabulary, not indexer-specific.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class IndexingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    status: str
    error_message: str | None = None
    result_summary: dict[str, int] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class GraphNodeResponse(BaseModel):
    id: str
    labels: list[str]
    properties: dict[str, Any]


class GraphEdgeResponse(BaseModel):
    source_id: str
    target_id: str
    type: str
    properties: dict[str, Any]


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


class CrossRepositoryLinkResponse(BaseModel):
    """One component in another repository sharing a Kafka topic with the
    requested repository - lightweight relationship metadata only, no
    graph/nodes/edges, so discovering cross-repository links never requires
    downloading another repository's full graph."""

    repository_id: str
    repository_name: str
    component_id: str
    component_name: str
    relationship: str
    topic_name: str
