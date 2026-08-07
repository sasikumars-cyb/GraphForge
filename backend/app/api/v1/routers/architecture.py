"""GET /architecture/summary (ADR 0023) — the org-scale replacement for
ArchitecturePage.tsx's per-repository GET /repositories/{id}/index
fan-out (`useQueries`, one HTTP request per tracked repository).

User-scoped (this user's own tracked repositories), unlike the admin-only
`/calibration/summary`/`/investigation-intelligence/summary` precedent
this otherwise mirrors (direct queries, no service layer, read-only
aggregation) — a regular user needs this to load their own Architecture
page, not an admin dashboard.

Two queries total, never N: one Postgres query for every tracked
repository's latest `IndexingJob` (a `DISTINCT ON` per-repository-id
pick, not N `ORDER BY ... LIMIT 1` calls), and one Neo4j aggregate query
across every tracked repository's nodes at once
(`Neo4jGraphRepository.get_type_counts_for_repositories`), using the
existing `graph_node_repository_id` index.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.database.session import get_db_session
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository
from app.models.user import User
from app.schemas.architecture import (
    ArchitectureSummaryResponse,
    DomainSummary,
    RepositorySummary,
)
from app.services.github_service import list_tracked_repositories

router = APIRouter(prefix="/architecture", tags=["architecture"])

# A repository whose last successful index is older than this is flagged
# `is_stale` — matches the earlier UX audit's own mockup ("4 graphs stale
# (>30d)"). Never indexed at all is also stale (there's nothing fresher
# to compare against), same as it's also `unindexed`.
_STALE_THRESHOLD_DAYS = 30


async def _latest_indexing_jobs(
    db: AsyncSession, repository_ids: list[uuid.UUID]
) -> dict[uuid.UUID, IndexingJob]:
    """One repository -> its most recently created `IndexingJob`, for
    every id in `repository_ids` at once. Postgres `DISTINCT ON` (per
    `repository_id`, most recent `created_at` first) — the bulk
    equivalent of `GET /repositories/{id}/index`'s own
    `ORDER BY created_at DESC LIMIT 1`, run once instead of N times."""
    if not repository_ids:
        return {}
    result = await db.execute(
        select(IndexingJob)
        .where(IndexingJob.repository_id.in_(repository_ids))
        .distinct(IndexingJob.repository_id)
        .order_by(IndexingJob.repository_id, IndexingJob.created_at.desc())
    )
    return {job.repository_id: job for job in result.scalars().all()}


@router.get("/summary", response_model=ArchitectureSummaryResponse)
async def get_architecture_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ArchitectureSummaryResponse:
    repositories = await list_tracked_repositories(db, current_user)
    if not repositories:
        return ArchitectureSummaryResponse(
            total_repositories=0,
            total_nodes=0,
            total_cross_repository_edges=0,
            repositories=[],
            domains=[],
            unindexed_count=0,
            stale_count=0,
        )

    repo_ids = [repo.id for repo in repositories]
    graph_repository = Neo4jGraphRepository(get_driver())

    latest_jobs, type_counts_by_repo, cross_repo_edge_lists = await asyncio.gather(
        _latest_indexing_jobs(db, repo_ids),
        graph_repository.get_type_counts_for_repositories([str(rid) for rid in repo_ids]),
        asyncio.gather(
            *(
                graph_repository.get_outgoing_cross_repository_edges(str(repo.id))
                for repo in repositories
            )
        ),
    )

    stale_cutoff = datetime.now(UTC) - timedelta(days=_STALE_THRESHOLD_DAYS)

    summaries: list[RepositorySummary] = []
    unindexed_count = 0
    stale_count = 0
    total_nodes = 0
    domain_repo_counts: dict[str | None, int] = {}
    domain_node_counts: dict[str | None, int] = {}

    for repo in repositories:
        job = latest_jobs.get(repo.id)
        type_counts = type_counts_by_repo.get(str(repo.id), {})
        node_count = sum(type_counts.values())
        total_nodes += node_count

        # `Repository.last_indexed_at` (not `job.finished_at`) — already
        # maintained by the indexing service, set only on a genuinely
        # successful run (see the model's own docstring). Using the
        # latest job's `finished_at` instead would misreport "stale" for
        # a repository with an indexing run currently `pending`/`running`
        # (whose `finished_at` is still null) even though its last
        # *successful* index might be recent.
        last_indexed_at = repo.last_indexed_at
        # "Unindexed" means never *successfully* indexed — a repository
        # whose only job so far failed still has `last_indexed_at=None`
        # even though a job row exists, and that's exactly the case that
        # should count as unindexed, not be excluded by it.
        is_unindexed = last_indexed_at is None
        is_stale = is_unindexed or last_indexed_at < stale_cutoff
        if is_unindexed:
            unindexed_count += 1
        if is_stale:
            stale_count += 1

        domain_repo_counts[repo.domain] = domain_repo_counts.get(repo.domain, 0) + 1
        domain_node_counts[repo.domain] = domain_node_counts.get(repo.domain, 0) + node_count

        summaries.append(
            RepositorySummary(
                repository_id=repo.id,
                name=repo.name,
                full_name=repo.full_name,
                domain=repo.domain,
                indexing_status=job.status if job is not None else None,
                last_indexed_at=last_indexed_at,
                node_count=node_count,
                node_counts_by_label=type_counts,
                is_stale=is_stale,
            )
        )

    domains = [
        DomainSummary(
            domain=domain,
            repository_count=domain_repo_counts[domain],
            node_count=domain_node_counts[domain],
        )
        # Ungrouped (None) last — every named domain first, alphabetically.
        for domain in sorted(domain_repo_counts, key=lambda d: (d is None, d or ""))
    ]

    total_cross_repository_edges = sum(len(edges) for edges in cross_repo_edge_lists)

    return ArchitectureSummaryResponse(
        total_repositories=len(repositories),
        total_nodes=total_nodes,
        total_cross_repository_edges=total_cross_repository_edges,
        repositories=summaries,
        domains=domains,
        unindexed_count=unindexed_count,
        stale_count=stale_count,
    )
