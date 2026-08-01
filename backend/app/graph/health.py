"""Graph Health — the single authoritative answer to "is this repository's
architecture graph usable right now?"

Two independent call sites used to answer this question separately and
disagreed: Control Center trusted `IndexingJob.status` (Postgres, historical
intent — did an indexing attempt finish?) while Context Discovery trusted
`IGraphRepository.has_graph` (Neo4j, current reality — is there a graph to
query right now?). Nothing kept the two in sync, so a repository could
report "indexed" in one place and "not indexed" in the other whenever the
graph store and the job history drifted apart, for any reason (a partially
failed repository delete, an out-of-band Neo4j change, ...).

`GraphHealthService` is the one place this computation happens now — every
caller reads its result instead of re-deriving its own opinion from
`IndexingJob`/`has_graph` directly. It reads only data that already exists
(`repositories`, `indexing_jobs`, and a live Neo4j check) — no new tables,
no migrations, no background reconciliation. See the module docstring of
`app.api.v1.routers.system` for how this fits the "D Lite" scope: this is
a shared read path, not a durable, transactionally-written health record —
that upgrade (a `graph_synced_at` column written atomically with the graph
write, plus a background reconciler for drift that happens with no
application code in between) is the natural next step once this
abstraction exists, and is deliberately out of scope here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository

_IN_PROGRESS_STATUSES = frozenset({"pending", "running"})


class GraphHealthStatus(StrEnum):
    """What a repository's architecture graph is doing right now.

    Checked in this order, because a caller that wants to query the graph
    cares about whether it can, right now, more than it cares about job
    history:

    - HEALTHY — a graph exists in Neo4j and can be queried right now. This
      wins over everything else: a stale or failed `IndexingJob` row is
      irrelevant if a prior run's graph is still there.
    - INDEXING — no graph yet, but a job is currently pending/running.
    - GRAPH_MISSING — a job completed at some point, but the graph isn't
      there now. This is the state a completed job with no matching Neo4j
      data used to silently report as "indexed" — the exact drift this
      abstraction exists to surface instead of hide.
    - NOT_INDEXED — no completed job and no graph: never successfully
      indexed (whether never attempted, or only ever failed).
    """

    HEALTHY = "healthy"
    GRAPH_MISSING = "graph_missing"
    INDEXING = "indexing"
    NOT_INDEXED = "not_indexed"


@dataclass(frozen=True)
class RepositoryGraphHealth:
    """One repository's computed health, plus the raw signals it came
    from — so a caller that wants more than the status label (e.g. an
    evidence trail explaining *why*) doesn't have to re-query anything.

    `latest_job_status` is only looked up for repositories without a
    current graph (see `GraphHealthService.for_repositories`) — job
    history doesn't change a HEALTHY verdict, so it is left `None` there
    rather than spending a query on a fact the status didn't need.
    """

    repository_id: uuid.UUID
    status: GraphHealthStatus
    has_graph: bool
    latest_job_status: str | None


def _status_for(*, has_graph: bool, latest_job_status: str | None) -> GraphHealthStatus:
    if has_graph:
        return GraphHealthStatus.HEALTHY
    if latest_job_status in _IN_PROGRESS_STATUSES:
        return GraphHealthStatus.INDEXING
    if latest_job_status == "completed":
        return GraphHealthStatus.GRAPH_MISSING
    return GraphHealthStatus.NOT_INDEXED


class GraphHealthService:
    """Computes `RepositoryGraphHealth` for one or more repositories.

    Stateless and cheap to construct per-request, like the tools it
    replaces logic in — takes the same `db`/`graph_repository` handles
    every other repository-scoped read in this codebase already takes.
    """

    def __init__(self, db: AsyncSession, graph_repository: IGraphRepository) -> None:
        self._db = db
        self._graph_repository = graph_repository

    async def for_repository(self, repository: Repository) -> RepositoryGraphHealth:
        results = await self.for_repositories([repository])
        return results[0]

    async def for_repositories(
        self, repositories: Sequence[Repository]
    ) -> list[RepositoryGraphHealth]:
        if not repositories:
            return []

        has_graph_by_id: dict[uuid.UUID, bool] = {
            repo.id: await self._graph_repository.has_graph(str(repo.id))
            for repo in repositories
        }

        # Job history only changes the verdict for repositories with no
        # graph right now (see `_status_for`) — skip the query entirely
        # when every repository in this batch already has one, the common
        # case for an account whose indexing is healthy.
        needs_job_status = [repo.id for repo in repositories if not has_graph_by_id[repo.id]]
        latest_status_by_id = (
            await self._latest_job_status_by_repository(needs_job_status)
            if needs_job_status
            else {}
        )

        return [
            RepositoryGraphHealth(
                repository_id=repo.id,
                status=_status_for(
                    has_graph=has_graph_by_id[repo.id],
                    latest_job_status=latest_status_by_id.get(repo.id),
                ),
                has_graph=has_graph_by_id[repo.id],
                latest_job_status=latest_status_by_id.get(repo.id),
            )
            for repo in repositories
        ]

    async def _latest_job_status_by_repository(
        self, repository_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """The most recent `IndexingJob.status` per repository id, for
        exactly the ids in `repository_ids`. One query for the whole
        batch rather than one per repository — ordering by
        `(repository_id, created_at DESC)` puts each repository's newest
        job first within its group, so the first row seen per id (via
        `dict.setdefault`) is always the latest."""
        result = await self._db.execute(
            select(IndexingJob.repository_id, IndexingJob.status)
            .where(IndexingJob.repository_id.in_(repository_ids))
            .order_by(IndexingJob.repository_id, IndexingJob.created_at.desc())
        )
        latest: dict[uuid.UUID, str] = {}
        for repository_id, status in result.all():
            latest.setdefault(repository_id, status)
        return latest
