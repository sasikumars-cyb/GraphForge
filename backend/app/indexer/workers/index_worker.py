"""The background-task entrypoint `POST /repositories/{id}/index` schedules
via FastAPI's `BackgroundTasks` — runs after the HTTP response is already
sent, so it opens its own DB session (the request-scoped one is gone by
then, same as any real out-of-process worker would need to).

FastAPI's BackgroundTasks stands in for a real task queue in this phase -
see ADR 0007 for what a real queue (Celery, arq, ...) would replace here
and why it wasn't needed yet.
"""

import logging
import uuid
from datetime import UTC, datetime

from app.database.session import AsyncSessionLocal
from app.indexer.services.indexing_service import run_indexing
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository

logger = logging.getLogger(__name__)


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
