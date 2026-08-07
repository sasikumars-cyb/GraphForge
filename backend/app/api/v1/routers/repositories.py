"""The user's persisted (selected) repositories, their ingested PRs, and
their architecture indexing jobs / discovered graph.
"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.api.v1.dependencies import get_current_user
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.database.session import get_db_session
from app.graph.models import GraphNode, GraphPayload
from app.graph.neo4j_common import _ALLOWED_REL_TYPES
from app.graph.neo4j_repository import _CROSS_REPO_REL_TYPES, Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.workers.index_worker import schedule_indexing_job
from app.models.indexing_job import IndexingJob
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User
from app.schemas.github import (
    LocalRepositoryCreateRequest,
    PullRequestResponse,
    RepositoryResponse,
    RepositorySelectionRequest,
    RepositoryUpdateRequest,
)
from app.schemas.indexing import (
    CrossRepositoryLinkResponse,
    GraphEdgeResponse,
    GraphResponse,
    IndexingJobResponse,
    NodeTypeCountsResponse,
)
from app.services.github_service import (
    list_repository_ids_for_user,
    list_tracked_repositories,
    set_selected_repositories,
)
from app.services.local_repository_service import create_local_repository

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
        truncated=graph.truncated,
        total_node_count=graph.total_node_count,
        next_cursor=graph.next_cursor,
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


@router.get("/cross-repository-links", response_model=list[CrossRepositoryLinkResponse])
async def get_all_cross_repository_links(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[CrossRepositoryLinkResponse]:
    """The same lightweight relationship metadata as
    `GET /{repository_id}/cross-repository-links`, for every tracked
    repository at once - one Neo4j relationship query instead of the
    overview issuing one HTTP request per repository.

    KAN-45: `find_cross_repository_topic_peers` takes a tenant-scoping
    allow-list, not an exclude-one-id filter - passing this user's own
    tracked repository ids means it returns every producer/consumer of
    every topic name collected across *this user's* tracked repositories,
    never another tenant's repository sharing a topic name by
    coincidence."""
    repositories = await list_tracked_repositories(db, current_user)
    if not repositories:
        return []

    driver = get_driver()
    graph_repository = Neo4jGraphRepository(driver)
    topic_name_sets = await asyncio.gather(
        *(graph_repository.get_nodes_by_label(str(repo.id), "KafkaTopic") for repo in repositories)
    )
    topic_names = {
        str(node.properties["name"])
        for nodes in topic_name_sets
        for node in nodes
        if "name" in node.properties
    }
    if not topic_names:
        return []

    allowed_repository_ids = {str(repo.id) for repo in repositories}
    impact_reader = Neo4jImpactGraphReader(driver)
    hops = await impact_reader.find_cross_repository_topic_peers(
        topic_names, allowed_repository_ids
    )

    repo_name_by_id = {str(repo.id): repo.full_name for repo in repositories}

    return [
        CrossRepositoryLinkResponse(
            repository_id=hop.from_node.properties["repository_id"],
            repository_name=repo_name_by_id.get(hop.from_node.properties["repository_id"], ""),
            component_id=hop.from_node.id,
            component_name=str(hop.from_node.properties.get("name", "")),
            relationship=hop.relationship,
            topic_name=str(hop.to_node.properties.get("name", "")),
        )
        for hop in hops
    ]


@router.get("/cross-repository-edges", response_model=list[GraphEdgeResponse])
async def get_all_cross_repository_edges(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[GraphEdgeResponse]:
    """Every structural repository-to-repository edge
    (`CALLS_SERVICE`/`SHARES_TOPIC`/`DEPENDS_ON_REPOSITORY`) computed by
    `app.indexer.graph.cross_repo_linker.relink_account` for this user's
    tracked repositories.

    Distinct from `/cross-repository-links` above: that endpoint only
    surfaces component-level Kafka producer/consumer overlap
    (`find_cross_repository_topic_peers`) and returns nothing for a Feign
    service call or a shared internal dependency. This endpoint reads the
    edges `cross_repo_linker` actually writes onto each repository's own
    `Repository` node (`get_outgoing_cross_repository_edges`) - previously
    computed and persisted on every indexing run but never read back by any
    API route, so the Architecture page could never render them.

    `source_id`/`target_id` are `"{repository_id}:repository"` - the same
    id `cross_repo_linker._repository_node_id` uses - so the frontend can
    match them against tracked repository ids directly.
    """
    repositories = await list_tracked_repositories(db, current_user)
    if not repositories:
        return []

    graph_repository = Neo4jGraphRepository(get_driver())
    edge_lists = await asyncio.gather(
        *(
            graph_repository.get_outgoing_cross_repository_edges(str(repo.id))
            for repo in repositories
        )
    )
    return [
        GraphEdgeResponse(
            source_id=edge.source_id,
            target_id=edge.target_id,
            type=edge.type,
            properties=edge.properties,
        )
        for edges in edge_lists
        for edge in edges
    ]


@router.post("", response_model=list[RepositoryResponse])
async def select_repositories(
    selection: RepositorySelectionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[Repository]:
    """Replaces the tracked set with exactly `selection.repositories`."""
    return await set_selected_repositories(db, current_user, selection)


@router.post("/local", response_model=RepositoryResponse, status_code=201)
async def add_local_repository(
    body: LocalRepositoryCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Repository:
    """Registers one local-filesystem folder as a tracked repository —
    additive (insert-one), unlike `select_repositories` above which
    replaces the entire GitHub-sourced set. See
    app.services.local_repository_service."""
    return await create_local_repository(db, current_user, name=body.name, path=body.path)


@router.delete("/{repository_id}", status_code=204)
async def remove_repository(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Permanently removes a repository: clears its Neo4j graph, then
    deletes the row (cascades to its pull requests, analyses, and
    indexing jobs via existing FK constraints). Clearing the graph first
    means a Neo4j failure leaves the repository intact and safe to retry,
    rather than an orphaned graph with no owning row."""
    repository = await _get_owned_repository(db, repository_id, current_user)
    await Neo4jGraphRepository(get_driver()).replace_repository_graph(
        str(repository.id), GraphPayload()
    )
    await db.delete(repository)
    await db.commit()


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
        # A repository accumulates one row per webhook-ingested PR forever
        # with no prior bound — most recent first, so this cap is "the
        # newest 500," not an arbitrary/misleading slice. Full pagination
        # (page/page_size/total, matching agent_runs.py/workflows.py) is
        # the real fix if a repository's PR history ever needs to exceed
        # this; not done here since it's a response-shape change the
        # frontend would need to follow.
        .limit(500)
    )
    return [PullRequestResponse.model_validate(pr) for pr in pr_result.scalars().all()]


@router.post("/{repository_id}/index", response_model=IndexingJobResponse, status_code=202)
async def trigger_indexing(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> IndexingJob:
    """Schedules an indexing run and returns immediately - the run itself
    happens on the durable job queue (see `app.indexer.workers.index_worker`,
    `app.orchestrator.job_queue`) and its progress is tracked via the
    returned job's `status`."""
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

    await schedule_indexing_job(db, job.id, repository.id)
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
    limit: int = Query(
        2000,
        ge=1,
        le=10_000,
        description=(
            "Max nodes to return, node-count-capped so a very large repository's graph "
            "can't blow up a single response — see GraphResponse.truncated/total_node_count "
            "for whether this cut anything off. Server enforces an absolute ceiling "
            "regardless of what's requested here."
        ),
    ),
    node_types: list[str] | None = Query(
        None,
        description=(
            "Restrict to nodes carrying at least one of these labels (e.g. Service, "
            "KafkaTopic) — filtered inside the query, not after fetching everything. "
            "Omit to include every type."
        ),
    ),
    after: str | None = Query(
        None,
        description=(
            "ADR 0023 — keyset cursor: the `next_cursor` from a previous truncated "
            "response, to fetch the following page. Omit for the first page."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GraphResponse:
    repository = await _get_owned_repository(db, repository_id, current_user)
    graph_repository = Neo4jGraphRepository(get_driver())
    try:
        graph = await graph_repository.get_full_graph(
            str(repository.id), limit=limit, node_types=node_types, after=after
        )
    except ValueError as exc:
        raise AppError(str(exc), status_code=400, error_code="invalid_node_type") from exc
    return _graph_response(graph)


@router.get("/{repository_id}/graph/types", response_model=NodeTypeCountsResponse)
async def get_repository_graph_types(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> NodeTypeCountsResponse:
    """ADR 0023 — real, untruncated per-label counts, server-side. Fixes
    the earlier UX audit's own flagged gap: the frontend previously had
    to derive filter-chip options from whatever labels happened to appear
    in a possibly-`limit`-truncated `/graph` load, so a type only present
    past the first `limit` nodes could never become a filter option at
    all."""
    repository = await _get_owned_repository(db, repository_id, current_user)
    graph_repository = Neo4jGraphRepository(get_driver())
    counts = await graph_repository.get_type_counts(str(repository.id))
    return NodeTypeCountsResponse(counts=counts)


@router.get("/{repository_id}/graph/nodes/{node_id}/neighbors", response_model=GraphResponse)
async def get_repository_graph_node_neighbors(
    repository_id: uuid.UUID,
    node_id: str,
    hops: int = Query(1, ge=1, le=5, description="How many hops out from this node to include."),
    edge_types: list[str] | None = Query(
        None,
        description=(
            "Relationship types to traverse. Omit to traverse every non-cross-repository "
            "type — a UI 'click to expand' interaction doesn't generally know which "
            "specific types matter the way a purpose-built caller (e.g. impact analysis) "
            "does."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GraphResponse:
    """ADR 0023 — lazy expand-on-click: the induced subgraph within
    `hops` of one node, not this repository's whole graph. Thin wrapper
    over the already-existing `get_neighborhood` (also used by impact
    analysis) — no new traversal logic, just a route exposing what
    already exists for a drill-down UI interaction."""
    repository = await _get_owned_repository(db, repository_id, current_user)
    graph_repository = Neo4jGraphRepository(get_driver())
    types = edge_types or sorted(_ALLOWED_REL_TYPES - _CROSS_REPO_REL_TYPES)
    try:
        graph = await graph_repository.get_neighborhood(
            str(repository.id), [node_id], types, hops
        )
    except ValueError as exc:
        raise AppError(str(exc), status_code=400, error_code="invalid_neighborhood_request") from exc
    return _graph_response(graph)


@router.patch("/{repository_id}", response_model=RepositoryResponse)
async def update_repository(
    repository_id: uuid.UUID,
    body: RepositoryUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Repository:
    """ADR 0023 — repository grouping. `domain` is currently the only
    mutable field; an empty string is rejected (a caller clearing the
    field sends `null` explicitly, not `""`, so an accidental blank isn't
    silently treated as "cleared")."""
    repository = await _get_owned_repository(db, repository_id, current_user)
    if body.domain is not None and not body.domain.strip():
        raise AppError(
            "domain cannot be an empty string — omit it or send null to clear it.",
            status_code=400,
            error_code="invalid_domain",
        )
    repository.domain = body.domain.strip() if body.domain is not None else None
    await db.commit()
    await db.refresh(repository)
    return repository


@router.get(
    "/{repository_id}/cross-repository-links", response_model=list[CrossRepositoryLinkResponse]
)
async def get_cross_repository_links(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[CrossRepositoryLinkResponse]:
    """Lightweight cross-repository relationship metadata - reuses the same
    topic-name matching the deterministic analysis engine uses
    (`find_cross_repository_topic_peers`), so discovering which other
    repositories are linked never requires downloading their full graphs.

    KAN-45: the allow-list passed is this user's own *other* tracked
    repositories (this repository's own id excluded, same as the old
    exclude-id behavior - same-repository peers are `find_same_repository_
    topic_peers`'s job elsewhere, not this one's) - never every repository
    in the graph, which would leak another tenant's component whenever a
    topic name happens to collide."""
    repository = await _get_owned_repository(db, repository_id, current_user)

    driver = get_driver()
    graph_repository = Neo4jGraphRepository(driver)
    topic_nodes = await graph_repository.get_nodes_by_label(str(repository.id), "KafkaTopic")
    topic_names = {
        str(node.properties["name"]) for node in topic_nodes if "name" in node.properties
    }
    if not topic_names:
        return []

    own_repository_ids = await list_repository_ids_for_user(db, current_user.id)
    allowed_repository_ids = own_repository_ids - {str(repository.id)}
    impact_reader = Neo4jImpactGraphReader(driver)
    hops = await impact_reader.find_cross_repository_topic_peers(
        topic_names, allowed_repository_ids
    )

    peer_repo_ids = {uuid.UUID(hop.from_node.properties["repository_id"]) for hop in hops}
    peers_result = await db.execute(select(Repository).where(Repository.id.in_(peer_repo_ids)))
    repo_name_by_id = {str(repo.id): repo.full_name for repo in peers_result.scalars().all()}

    return [
        CrossRepositoryLinkResponse(
            repository_id=hop.from_node.properties["repository_id"],
            repository_name=repo_name_by_id.get(hop.from_node.properties["repository_id"], ""),
            component_id=hop.from_node.id,
            component_name=str(hop.from_node.properties.get("name", "")),
            relationship=hop.relationship,
            topic_name=str(hop.to_node.properties.get("name", "")),
        )
        for hop in hops
    ]


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
