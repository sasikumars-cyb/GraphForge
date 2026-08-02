"""Response schemas for the Graph Parity Engine REST API — a direct
Pydantic mirror of `app.knowledge_engine.parity.report`'s frozen
dataclasses (`ConfigDict(from_attributes=True)`, the same convention
`app.schemas.engineering_session` already uses for ORM/dataclass-backed
responses). No new report shape is invented here; this module only makes
the existing `ParityReport` JSON-serializable over HTTP.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PropertyDifferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    legacy_value: str | None
    materialized_value: str | None


class NodeMismatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    label_differences: tuple[str, ...]
    property_differences: tuple[PropertyDifferenceResponse, ...]


class EdgeSignatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    target_id: str
    type: str
    properties_json: str


class EdgePropertyMismatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    target_id: str
    type: str
    property_differences: tuple[PropertyDifferenceResponse, ...]


class DuplicateEntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    legacy_count: int
    materialized_count: int


class IgnoredDifferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_kind: str
    entity_key: str
    property_name: str
    reason: str


class NodeStatisticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    legacy_count: int
    materialized_count: int
    matched_count: int


class EdgeStatisticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    legacy_count: int
    materialized_count: int
    matched_count: int


class ParityReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    overall_result: str
    node_statistics: NodeStatisticsResponse
    edge_statistics: EdgeStatisticsResponse

    missing_nodes: tuple[str, ...]
    unexpected_nodes: tuple[str, ...]
    node_mismatches: tuple[NodeMismatchResponse, ...]
    duplicate_nodes: tuple[DuplicateEntityResponse, ...]

    missing_edges: tuple[EdgeSignatureResponse, ...]
    unexpected_edges: tuple[EdgeSignatureResponse, ...]
    edge_property_mismatches: tuple[EdgePropertyMismatchResponse, ...]
    duplicate_edges: tuple[DuplicateEntityResponse, ...]

    ignored_differences: tuple[IgnoredDifferenceResponse, ...]

    similarity_percentage: float
    summary: str
