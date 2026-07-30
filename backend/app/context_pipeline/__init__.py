"""Context acquisition — reference detection, provider adapters, and the
reasoning engine that drives them.

`reasoning` is the entry point: `reasoning.engine.discover()` investigates a
request and returns the `WorkingContext` everything downstream reads. The
modules beside it are the passive parts it uses — `reference_detection`
(recognizing Jira keys, GitHub URLs and repository names in text), `providers`
(the Jira/Confluence/GitHub/Graph transport adapters) and `models` (the
`Reference`/`ResolvedArtifact` shapes they exchange).

There is deliberately no pipeline object here any more. Context used to be
acquired by `ContextResolutionPipeline.resolve()`, a fixed
Jira -> Confluence -> GitHub -> Graph sequence, and the reasoning engine was
layered on top of its output. That inverted: reasoning now owns orchestration
and chooses which provider to consult next, so a fixed sequence has nothing
left to do. See `reasoning/__init__.py`.
"""

from app.context_pipeline.models import (
    ProviderCapability,
    Reference,
    ReferenceType,
    ResolvedArtifact,
)

__all__ = [
    "ProviderCapability",
    "Reference",
    "ReferenceType",
    "ResolvedArtifact",
]
