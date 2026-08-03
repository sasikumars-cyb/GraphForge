"""`DependencyQueryService` — filtered relationship search. Reuses
`relationship_lookup.fetch_with_confidence` for every repository in
scope; does not query Postgres directly and does not add any method to
`EngineeringMemoryRepository` (frozen, no redesign).

Scoping note: the frozen `EngineeringMemoryRepository.get_current_relationships`
is repository-scoped — there is no existing "every relationship across
every repository" query to build a true organization-wide search on
without adding persistence-layer code, which is out of this RFC's scope.
`search` therefore takes an explicit `repository_ids` list rather than an
implicit "empty means everything" — callers (agents) decide which
repositories are in scope, same as every other service in this layer.

`Dependency Explorer` and `Engineering Search` (the two agents the
approved design flagged as needing "the same underlying capability") both
call this one function — the resolution of that flagged duplication risk.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.engineering_intelligence import relationship_lookup
from app.services.engineering_intelligence.contracts import QueryResult, RelationshipInsight


def _matches(
    insight: RelationshipInsight, *, relationship_type: str | None, keyword: str | None
) -> bool:
    if relationship_type is not None and insight.relationship_type != relationship_type:
        return False
    if keyword is not None:
        haystack = f"{insight.source_entity} {insight.target_entity}".lower()
        if keyword.lower() not in haystack:
            return False
    return True


async def search(
    db: AsyncSession,
    repository_ids: list[uuid.UUID],
    *,
    relationship_type: str | None = None,
    keyword: str | None = None,
) -> QueryResult:
    matched: list[RelationshipInsight] = []
    for repository_id in repository_ids:
        insights = await relationship_lookup.fetch_with_confidence(db, repository_id)
        matched.extend(
            insight
            for insight in insights
            if _matches(insight, relationship_type=relationship_type, keyword=keyword)
        )

    ordered = tuple(sorted(matched, key=lambda insight: insight.relationship_key))
    return QueryResult(relationships=ordered, total_matched=len(ordered))
