"""Resolves cross-repository metadata for AI context building.

Extracted from `AIAnalysisService` so the Change Investigation Agent
(`app.ai.agent`) can reuse the exact same lookup instead of duplicating it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository


async def resolve_impacted_repositories(
    db: AsyncSession,
    repository: Repository,
    indirectly_impacted_services: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Resolve the current repository plus every repository id already
    present in the deterministic engine's cross-repository impact
    (``indirectly_impacted_services``) to human-readable metadata.

    A Postgres primary-key lookup on ids the deterministic engine already
    produced — never a new Neo4j traversal, never dependency discovery.
    Unresolvable ids (e.g. a repository since removed) are silently
    skipped rather than treated as an error.
    """
    downstream_ids: set[uuid.UUID] = set()
    for node in indirectly_impacted_services:
        raw_id = node.get("repository_id")
        if not raw_id or raw_id == str(repository.id):
            continue
        try:
            downstream_ids.add(uuid.UUID(raw_id))
        except ValueError:
            continue

    resolved = [
        {
            "id": str(repository.id),
            "owner": repository.owner,
            "name": repository.name,
            "full_name": repository.full_name,
            "relation": "current",
        }
    ]

    if downstream_ids:
        stmt = select(Repository).where(Repository.id.in_(downstream_ids))
        result = await db.execute(stmt)
        for repo in result.scalars().all():
            resolved.append(
                {
                    "id": str(repo.id),
                    "owner": repo.owner,
                    "name": repo.name,
                    "full_name": repo.full_name,
                    "relation": "downstream",
                }
            )

    return resolved
