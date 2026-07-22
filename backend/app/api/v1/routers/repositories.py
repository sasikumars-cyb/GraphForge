"""The user's persisted (selected) repositories, their ingested PRs, and
their architecture indexing jobs / discovered graph.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import ConflictError, NotFoundError
from app.database.session import get_db_session
from app.graph.models import GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.workers.index_worker import run_indexing_job
from app.models.indexing_job import IndexingJob
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User
from app.schemas.github import PullRequestResponse, RepositoryResponse, RepositorySelectionRequest
from app.schemas.indexing import GraphResponse, IndexingJobResponse
from app.services.github_service import list_tracked_repositories, set_selected_repositories

router = APIRouter(prefix="/repositories", tags=["repositories"])


async def _get_owned_repository(
    db: AsyncSession, repository_id: uuid.UUID, current_user: User
) -> Repository:
    result = await db.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.user_id == current_user.id
        )
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found.")
    return repository


def _graph_response(graph: GraphPayload) -> GraphResponse:
    return GraphResponse(
        nodes=[
            {"id": node.id, "labels": node.labels, "properties": node.properties}
            for node in graph.nodes
        ],
        edges=[
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "type": edge.type,
                "properties": edge.properties,
            }
            for edge in graph.edges
        ],
    )


def _nodes_response(nodes: list[GraphNode]) -> GraphResponse:
    return GraphResponse(
        nodes=[
            {"id": node.id, "labels": node.labels, "properties": node.properties} for node in nodes
        ],
        edges=[],
    )


@router.get("", response_model=list[RepositoryResponse])
async def list_repositories(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[Repository]:
    return await list_tracked_repositories(db, current_user)


@router.post("", response_model=list[RepositoryResponse])
async def select_repositories(
    selection: RepositorySelectionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[Repository]:
    """Replaces the tracked set with exactly `selection.repositories`."""
    return await set_selected_repositories(db, current_user, selection)


@router.get("/{repository_id}/pull-requests", response_model=list[PullRequestResponse])
async def list_pull_requests(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[PullRequestResponse]:
    repository = await _get_owned_repository(db, repository_id, current_user)

    pr_result = await db.execute(
        select(PullRequest)
        .where(PullRequest.repository_id == repository.id)
        .order_by(PullRequest.number.desc())
    )
    return [PullRequestResponse.model_validate(pr) for pr in pr_result.scalars().all()]


@router.post("/{repository_id}/index", response_model=IndexingJobResponse, status_code=202)
async def trigger_indexing(
    repository_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> IndexingJob:
    """Schedules an indexing run and returns immediately - the run itself
    happens in a background task (see `app.indexer.workers.index_worker`)
    and its progress is tracked via the returned job's `status`."""
    repository = await _get_owned_repository(db, repository_id, current_user)

    existing_result = await db.execute(
        select(IndexingJob).where(
            IndexingJob.repository_id == repository.id,
            IndexingJob.status.in_(["pending", "running"]),
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise ConflictError("An indexing job is already pending or running for this repository.")

    job = IndexingJob(repository_id=repository.id, status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(run_indexing_job, job.id, repository.id)
    return job


@router.get("/{repository_id}/index", response_model=IndexingJobResponse)
async def get_latest_indexing_job(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> IndexingJob:
    """The most recently created indexing job for this repository - lets a
    client that just called `POST .../index` poll for completion."""
    repository = await _get_owned_repository(db, repository_id, current_user)

    result = await db.execute(
        select(IndexingJob)
        .where(IndexingJob.repository_id == repository.id)
        .order_by(IndexingJob.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("No indexing job has been run for this repository yet.")
    return job


@router.get("/{repository_id}/graph", response_model=GraphResponse)
async def get_repository_graph(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GraphResponse:
    repository = await _get_owned_repository(db, repository_id, current_user)
    graph_repository = Neo4jGraphRepository(get_driver())
    graph = await graph_repository.get_full_graph(str(repository.id))
    return _graph_response(graph)


@router.get("/{repository_id}/services", response_model=GraphResponse)
async def get_repository_services(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GraphResponse:
    """Every discovered Component (Controller, Service, or FeignClient)."""
    repository = await _get_owned_repository(db, repository_id, current_user)
    graph_repository = Neo4jGraphRepository(get_driver())
    nodes = await graph_repository.get_nodes_by_label(str(repository.id), "Component")
    return _nodes_response(nodes)


@router.get("/{repository_id}/dependencies", response_model=GraphResponse)
async def get_repository_dependencies(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GraphResponse:
    repository = await _get_owned_repository(db, repository_id, current_user)
    graph_repository = Neo4jGraphRepository(get_driver())
    nodes = await graph_repository.get_nodes_by_label(str(repository.id), "MavenDependency")
    return _nodes_response(nodes)
