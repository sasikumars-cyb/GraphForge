"""`ServiceExecutor` — the one place a Frontier agent's service calls get
dispatched. Calls the six Engineering Intelligence Services directly
(`repository_profile_service`, `impact_analysis_service`,
`dependency_query_service`, `architecture_insight_service`,
`change_simulation_service`) — unmodified, no new persistence, no new
traversal.

Deliberately not routed through
`app.services.engineering_intelligence.organization_knowledge_service
.compose` — `compose` only dispatches four of the six services (it has no
`change_simulation` case, and its own docstring scopes it to answering
already-decided `ServiceRequest`s, not to being every agent's call path).
Extending the frozen `ComposedAnswer`/`ServiceRequest` contracts to add a
fifth case would mean modifying `app.services.engineering_intelligence
.contracts` — explicitly frozen for this RFC. `ServiceExecutor` is
therefore the (additive, frontier-package-local) superset dispatcher every
Frontier agent uses; `OrganizationKnowledgeService.compose` remains
available unchanged for any caller that specifically wants its narrower,
four-service composition.

Calls run concurrently (`asyncio.gather`) since each targets an
independent read against Postgres/Neo4j with no ordering dependency
between them — the same "no ordering dependency" property the approved
Service Layer design already relies on for `OrganizationKnowledgeService`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.services.engineering_intelligence import (
    architecture_insight_service,
    change_simulation_service,
    dependency_query_service,
    impact_analysis_service,
    repository_profile_service,
)
from app.services.engineering_intelligence.contracts import ChangeType, EntityReference


@dataclass(frozen=True)
class RepositoryProfileCall:
    repository_id: uuid.UUID
    service: Literal["repository_profile"] = "repository_profile"


@dataclass(frozen=True)
class ImpactAnalysisCall:
    entity: EntityReference
    direction: Literal["downstream", "upstream"] = "downstream"
    max_hops: int = 2
    service: Literal["impact_analysis"] = "impact_analysis"


@dataclass(frozen=True)
class DependencyQueryCall:
    repository_ids: tuple[uuid.UUID, ...]
    relationship_type: str | None = None
    keyword: str | None = None
    service: Literal["dependency_query"] = "dependency_query"


@dataclass(frozen=True)
class ArchitectureInsightCall:
    repository_ids: tuple[uuid.UUID, ...]
    service: Literal["architecture_insight"] = "architecture_insight"


@dataclass(frozen=True)
class ChangeSimulationCall:
    entity: EntityReference
    change_type: ChangeType
    max_hops: int = 2
    service: Literal["change_simulation"] = "change_simulation"


ServiceCall = (
    RepositoryProfileCall
    | ImpactAnalysisCall
    | DependencyQueryCall
    | ArchitectureInsightCall
    | ChangeSimulationCall
)


@dataclass(frozen=True)
class ExecutionResult:
    """Positionally aligned with the `calls` list passed to `execute` —
    same alignment discipline `ComposedAnswer.results` already uses, so a
    caller never has to re-derive which result came from which call."""

    calls: tuple[ServiceCall, ...]
    results: tuple[object, ...]
    errors: tuple[str, ...] = field(default_factory=tuple)


async def _run_one(
    db: AsyncSession, graph_repository: IGraphRepository | None, call: ServiceCall
) -> object:
    if isinstance(call, RepositoryProfileCall):
        if graph_repository is None:
            raise ValueError("repository_profile requires a graph_repository (max_graph_hops > 0)")
        return await repository_profile_service.get_profile(
            db, graph_repository, call.repository_id
        )
    if isinstance(call, ImpactAnalysisCall):
        if graph_repository is None:
            raise ValueError("impact_analysis requires a graph_repository (max_graph_hops > 0)")
        return await impact_analysis_service.compute_blast_radius(
            db, graph_repository, call.entity, direction=call.direction, max_hops=call.max_hops
        )
    if isinstance(call, DependencyQueryCall):
        return await dependency_query_service.search(
            db,
            list(call.repository_ids),
            relationship_type=call.relationship_type,
            keyword=call.keyword,
        )
    if isinstance(call, ArchitectureInsightCall):
        return await architecture_insight_service.detect_findings(db, list(call.repository_ids))
    if isinstance(call, ChangeSimulationCall):
        if graph_repository is None:
            raise ValueError("change_simulation requires a graph_repository (max_graph_hops > 0)")
        return await change_simulation_service.simulate(
            db, graph_repository, call.entity, call.change_type, max_hops=call.max_hops
        )
    raise ValueError(f"Unknown service call: {call!r}")


async def execute(
    db: AsyncSession, graph_repository: IGraphRepository | None, calls: list[ServiceCall]
) -> ExecutionResult:
    """One failing call never discards the others — same partial-result
    discipline `OrganizationKnowledgeService.compose` established."""
    outcomes = await asyncio.gather(
        *(_run_one(db, graph_repository, call) for call in calls), return_exceptions=True
    )

    results: list[object] = []
    errors: list[str] = []
    for index, outcome in enumerate(outcomes):
        if isinstance(outcome, BaseException):
            results.append(None)
            errors.append(f"[{index}] {calls[index].service}: {outcome}")
        else:
            results.append(outcome)

    return ExecutionResult(calls=tuple(calls), results=tuple(results), errors=tuple(errors))
