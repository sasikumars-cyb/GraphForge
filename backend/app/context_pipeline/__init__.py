"""Context Resolution Pipeline — see `pipeline.ContextResolutionPipeline`.

Turns a raw user prompt into an `EnrichedPlanningRequest`: reference
detection, provider capability resolution, context retrieval, and
normalization, all separated from the Planning Agent's own reasoning.
"""

from app.context_pipeline.models import (
    EnrichedPlanningRequest,
    ProviderCapability,
    Reference,
    ReferenceType,
    ResolvedArtifact,
)
from app.context_pipeline.pipeline import ContextResolutionPipeline

__all__ = [
    "ContextResolutionPipeline",
    "EnrichedPlanningRequest",
    "ProviderCapability",
    "Reference",
    "ReferenceType",
    "ResolvedArtifact",
]
