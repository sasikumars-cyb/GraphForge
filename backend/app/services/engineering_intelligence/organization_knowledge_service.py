"""`OrganizationKnowledgeService` — composes results from other services in
this layer. It never parses natural language and never calls an LLM
(approved design's explicit constraint — those are the calling agent's
job, not this service's).

`compose` takes an explicit `list[ServiceRequest]` the agent has already
decided to make (not a raw question string) — the boundary that keeps
"which service(s) does this question need" out of the service layer,
where it would otherwise have to embed a classification prompt.
"""

from __future__ import annotations

import uuid
from typing import Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.services.engineering_intelligence import (
    architecture_insight_service,
    dependency_query_service,
    impact_analysis_service,
    repository_profile_service,
)
from app.services.engineering_intelligence.contracts import (
    ComposedAnswer,
    EntityReference,
    ServiceRequest,
)


async def _execute(
    db: AsyncSession, graph_repository: IGraphRepository, request: ServiceRequest
) -> object:
    args = request.arguments
    if request.service == "repository_profile":
        return await repository_profile_service.get_profile(
            db, graph_repository, uuid.UUID(str(args["repository_id"]))
        )
    if request.service == "impact_analysis":
        direction = cast(
            "Literal['downstream', 'upstream']", str(args.get("direction", "downstream"))
        )
        return await impact_analysis_service.compute_blast_radius(
            db,
            graph_repository,
            EntityReference(repository_id=str(args["repository_id"]), node_id=str(args["node_id"])),
            direction=direction,
            max_hops=int(str(args.get("max_hops", 2))),
        )
    if request.service == "dependency_query":
        repository_ids = cast("list[object]", args["repository_ids"])
        relationship_type = args.get("relationship_type")
        keyword = args.get("keyword")
        return await dependency_query_service.search(
            db,
            [uuid.UUID(str(rid)) for rid in repository_ids],
            relationship_type=str(relationship_type) if relationship_type is not None else None,
            keyword=str(keyword) if keyword is not None else None,
        )
    if request.service == "architecture_insight":
        repository_ids = cast("list[object]", args["repository_ids"])
        return await architecture_insight_service.detect_findings(
            db, [uuid.UUID(str(rid)) for rid in repository_ids]
        )
    raise ValueError(f"Unknown service request: {request.service}")


async def compose(
    db: AsyncSession, graph_repository: IGraphRepository, requests: list[ServiceRequest]
) -> ComposedAnswer:
    """`results[i]` is `None` on failure rather than being omitted — kept
    positionally aligned with `requests` so a caller can always match a
    result (or its absence) back to the request that produced it, instead
    of re-deriving alignment from `errors`' text."""
    results: list[object] = []
    errors: list[str] = []
    for index, request in enumerate(requests):
        try:
            results.append(await _execute(db, graph_repository, request))
        except Exception as exc:  # noqa: BLE001 - a partial result for one
            # request must never abort the rest; the failure itself is
            # part of the composed answer, not a reason to discard it.
            results.append(None)
            errors.append(f"[{index}] {request.service}: {exc}")

    return ComposedAnswer(requests=tuple(requests), results=tuple(results), errors=tuple(errors))
