"""Request/response schemas for Phase 7's deterministic pull request
impact analysis. Field names mirror
`app.analysis.models.impact.ImpactAnalysisResult` and its nested
dataclasses exactly, so the JSON persisted on `PullRequestAnalysis`
(written via `dataclasses.asdict`) validates against these without any
translation.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ImpactedNodeResponse(BaseModel):
    id: str
    name: str
    node_type: str
    repository_id: str


class DependencyPathStepResponse(BaseModel):
    node_id: str
    node_name: str
    node_type: str
    relationship: str | None = None


class DependencyPathResponse(BaseModel):
    steps: list[DependencyPathStepResponse]


class PullRequestAnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pull_request_id: uuid.UUID
    risk: str
    directly_impacted_services: list[ImpactedNodeResponse]
    indirectly_impacted_services: list[ImpactedNodeResponse]
    impacted_apis: list[ImpactedNodeResponse]
    impacted_topics: list[ImpactedNodeResponse]
    impacted_libraries: list[ImpactedNodeResponse]
    dependency_paths: list[DependencyPathResponse]
    analyzed_at: datetime
