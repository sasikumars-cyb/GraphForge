"""TestRail project listing and the case-sync pipeline: fetch -> build
graph -> persist -> track job status. Mirrors `app.indexer.services.
indexing_service` / `app.indexer.workers.index_worker`'s shape, simplified
since a TestRail sync is one external API plus one graph write, not the
code indexer's multi-stage clone/detect/parse pipeline.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.database.session import AsyncSessionLocal
from app.graph.session import get_driver
from app.graph.test_case_repository import Neo4jTestCaseGraphRepository
from app.indexer.graph.testrail_builder import TestRailProjectData, build_graph
from app.models.testrail_sync_job import TestRailSyncJob
from app.tools import get_tool_registry
from app.tools.implementations.testrail_tool import TestRailTool

logger = logging.getLogger(__name__)


class TestRailNotConfiguredError(AppError):
    """Raised when no TestRail Knowledge Connection has been configured
    (Settings -> Integrations -> TestRail)."""

    status_code = 503
    error_code = "testrail_not_configured"


def _get_configured_tool() -> TestRailTool:
    tool = get_tool_registry().get_tool("testrail")
    if not isinstance(tool, TestRailTool):
        raise TestRailNotConfiguredError(
            "TestRail is not configured. Connect it in Settings -> Integrations."
        )
    return tool


async def list_available_projects(db: AsyncSession) -> list[dict[str, object]]:
    """Live project list from TestRail, cross-referenced against each
    project's most recent sync job — mirrors github_service.
    list_available_repositories's "live list + is_selected/status overlay"
    shape."""
    tool = _get_configured_tool()
    projects = await tool.list_projects()

    latest_jobs: dict[int, TestRailSyncJob] = {}
    if projects:
        result = await db.execute(
            select(TestRailSyncJob).order_by(TestRailSyncJob.created_at.desc())
        )
        for row in result.scalars().all():
            latest_jobs.setdefault(row.testrail_project_id, row)

    overview: list[dict[str, object]] = []
    for project in projects:
        job = latest_jobs.get(project["id"])
        overview.append(
            {
                "id": project["id"],
                "name": project["name"],
                "last_sync_status": job.status if job else None,
                "last_synced_at": job.finished_at if job and job.status == "completed" else None,
                "case_count": (job.result_summary or {}).get("cases") if job else None,
            }
        )
    return overview


async def sync_project_cases(project_id: int, project_name: str) -> dict[str, int]:
    """The DB-independent core: fetch this project's full hierarchy from
    TestRail, build a GraphPayload, and persist it — replacing any prior
    sync for this project. Takes plain values rather than an ORM job
    object for the same testability reason indexing_service.
    index_repository does."""
    tool = _get_configured_tool()

    suites = await tool.list_suites(project_id)
    # Single-suite projects (suite_mode=1) commonly 400 on get_suites, or
    # return an empty list depending on TestRail version - either way,
    # fall back to "no suites", which testrail_builder synthesizes a
    # Master suite for.
    sections: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    if suites:
        for suite in suites:
            sections.extend(await tool.list_sections(project_id, suite["id"]))
            cases.extend(await tool.list_cases(project_id, suite["id"]))
    else:
        sections = await tool.list_sections(project_id, None)
        cases = await tool.list_cases(project_id, None)

    graph = build_graph(
        TestRailProjectData(
            project_id=project_id,
            project_name=project_name,
            suites=suites,
            sections=sections,
            cases=cases,
        )
    )

    graph_repository = Neo4jTestCaseGraphRepository(get_driver())
    await graph_repository.replace_project_test_cases(str(project_id), graph)

    return {"suites": len(suites), "sections": len(sections), "cases": len(cases)}


async def create_sync_job(db: AsyncSession, project_id: int, project_name: str) -> TestRailSyncJob:
    job = TestRailSyncJob(
        id=uuid.uuid4(),
        testrail_project_id=project_id,
        project_name=project_name,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def run_sync_job(job_id: uuid.UUID) -> None:
    """Background-task entrypoint — opens its own DB session, same as
    `index_worker.run_indexing_job` (the request-scoped session is gone
    by the time a BackgroundTasks callback actually runs)."""
    async with AsyncSessionLocal() as db:
        job = await db.get(TestRailSyncJob, job_id)
        if job is None:
            logger.error("testrail_sync_job_vanished job_id=%s", job_id)
            return

        job.status = "running"
        job.started_at = datetime.now(UTC)
        await db.commit()

        try:
            summary = await sync_project_cases(job.testrail_project_id, job.project_name)
        except Exception as exc:
            logger.exception(
                "testrail_sync_failed job_id=%s project_id=%s", job_id, job.testrail_project_id
            )
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            job.finished_at = datetime.now(UTC)
            await db.commit()
            return

        job.status = "completed"
        job.result_summary = summary
        job.finished_at = datetime.now(UTC)
        await db.commit()
        logger.info(
            "testrail_sync_completed job_id=%s project_id=%s summary=%s",
            job_id,
            job.testrail_project_id,
            summary,
        )


async def get_latest_sync_job(db: AsyncSession, project_id: int) -> TestRailSyncJob | None:
    result = await db.execute(
        select(TestRailSyncJob)
        .where(TestRailSyncJob.testrail_project_id == project_id)
        .order_by(TestRailSyncJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_pending_or_running_job(db: AsyncSession, project_id: int) -> TestRailSyncJob | None:
    result = await db.execute(
        select(TestRailSyncJob).where(
            TestRailSyncJob.testrail_project_id == project_id,
            TestRailSyncJob.status.in_(["pending", "running"]),
        )
    )
    return result.scalar_one_or_none()
