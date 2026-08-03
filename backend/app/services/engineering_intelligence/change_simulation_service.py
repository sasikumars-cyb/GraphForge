"""`ChangeSimulationService` — never performs traversal itself (approved
design's explicit constraint). Every `simulate` call is a `change_type ->
direction` mapping followed by exactly one call to
`impact_analysis_service.compute_blast_radius`.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.services.engineering_intelligence import impact_analysis_service
from app.services.engineering_intelligence.contracts import (
    ChangeImpact,
    ChangeType,
    EntityReference,
)

# Which direction a change's blast radius is computed in — "downstream"
# means "who depends on this entity" (removing/renaming it breaks them);
# "upstream" means "what this entity depends on" (relevant only for
# dependency-upgrade style changes, where the risk is what's underneath,
# not what's on top).
_DIRECTION_BY_CHANGE_TYPE: dict[ChangeType, Literal["downstream", "upstream"]] = {
    "remove_endpoint": "downstream",
    "remove_topic": "downstream",
    "rename_api": "downstream",
    "migrate_database": "downstream",
    "upgrade_dependency": "upstream",
}

_RISK_LABEL_BY_CHANGE_TYPE: dict[ChangeType, str] = {
    "remove_endpoint": "Removing this endpoint",
    "remove_topic": "Removing this topic",
    "rename_api": "Renaming this API",
    "migrate_database": "Migrating this database",
    "upgrade_dependency": "Upgrading this dependency",
}


async def simulate(
    db: AsyncSession,
    graph_repository: IGraphRepository,
    entity: EntityReference,
    change_type: ChangeType,
    *,
    max_hops: int = 2,
) -> ChangeImpact:
    direction = _DIRECTION_BY_CHANGE_TYPE[change_type]
    blast_radius = await impact_analysis_service.compute_blast_radius(
        db, graph_repository, entity, direction=direction, max_hops=max_hops
    )

    affected_count = (
        len(blast_radius.impacted_repositories)
        + len(blast_radius.impacted_apis)
        + len(blast_radius.impacted_databases)
        + len(blast_radius.impacted_queues)
    )
    risk_summary = (
        f"{_RISK_LABEL_BY_CHANGE_TYPE[change_type]} affects "
        f"{affected_count} entity(ies) across {len(blast_radius.impacted_repositories)} "
        f"repository(ies)."
    )

    return ChangeImpact(
        entity=entity,
        change_type=change_type,
        blast_radius=blast_radius,
        risk_summary=risk_summary,
    )
