"""The indexing-job entrypoint `POST /repositories/{id}/index` schedules.

As of KAN-18, scheduled via the durable queue (`app.orchestrator.job_queue`),
not FastAPI `BackgroundTasks` — see `app.orchestrator.background_execution`'s
module docstring for the durability rationale, which applies identically
here: a process crash mid-index used to silently drop the job with the
`IndexingJob` row stuck at "running" forever; now the row survives in
`background_jobs` and a `Worker` (this process's own, or another) retries it
after the lease on an abandoned attempt expires.

`run_indexing_job` itself is unchanged — still opens its own session,
re-fetches both rows by id, and is safe to re-run from scratch (it always
starts `run_indexing` over rather than trying to resume a partial one),
which is exactly what "safe to retry after a crash" requires.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.indexer.services.indexing_service import run_indexing
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository
from app.orchestrator.job_queue import JobQueue
from app.orchestrator.worker import register_handler

logger = logging.getLogger(__name__)

JOB_TYPE_INDEX_REPOSITORY = "index_repository"


async def run_indexing_job(job_id: uuid.UUID, repository_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(IndexingJob, job_id)
        repository = await db.get(Repository, repository_id)
        if job is None or repository is None:
            logger.error(
                "Indexing job or repository vanished before running: job=%s repository=%s",
                job_id,
                repository_id,
            )
            return

        job.status = "running"
        job.started_at = datetime.now(UTC)
        await db.commit()

        try:
            summary = await run_indexing(db, repository)
        except Exception as exc:
            logger.exception("Indexing failed for repository %s", repository_id)
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            job.finished_at = datetime.now(UTC)
            await db.commit()
            return

        job.status = "completed"
        job.result_summary = summary
        job.finished_at = datetime.now(UTC)
        await db.commit()
        logger.info("Indexing completed for repository %s: %s", repository_id, summary)


async def _index_repository_handler(payload: dict[str, Any]) -> None:
    await run_indexing_job(
        job_id=uuid.UUID(payload["job_id"]), repository_id=uuid.UUID(payload["repository_id"])
    )


async def schedule_indexing_job(
    db: AsyncSession, job_id: uuid.UUID, repository_id: uuid.UUID
) -> uuid.UUID:
    """Durably enqueue an indexing run. Commits on its own — see
    `JobQueue.enqueue`'s docstring; call after the `IndexingJob` row itself
    is committed, matching `trigger_indexing`'s existing structure."""
    job = await JobQueue(db).enqueue(
        JOB_TYPE_INDEX_REPOSITORY,
        {"job_id": str(job_id), "repository_id": str(repository_id)},
        correlation_id=str(job_id),
    )
    return job.id


register_handler(JOB_TYPE_INDEX_REPOSITORY, _index_repository_handler)
