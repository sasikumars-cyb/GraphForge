"""`relink_account` end-to-end: real Neo4j writes/reads and a real Postgres
`Repository`/`User`/`IndexingJob` scoping query — no mocks anywhere in this
chain. Node/edge payloads are hand-built (rather than run through the full
clone+parse pipeline) so each rule's real-world signal (a Feign target name,
a shared Kafka topic, a matching dependency coordinate) can be constructed
directly; the parse/extraction step itself is already covered by
`test_indexing_pipeline.py`.

ADR 0010 (Theme C) coverage: batch fetch stays O(N) Neo4j round-trips
regardless of repository count, edges carry graph_version stamps, and a
concurrent relink for the same account is a no-op rather than a race.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import engine
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.graph.cross_repo_linker import relink_account
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def graph_repository() -> AsyncGenerator[Neo4jGraphRepository, None]:
    yield Neo4jGraphRepository(get_driver())


class _CountingGraphRepository:
    """Wraps a real `IGraphRepository`, counting calls per method — used
    only to assert the O(N) round-trip shape Theme C requires; every call
    still hits the real Neo4j instance underneath."""

    def __init__(self, inner: IGraphRepository) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        self._count("replace_repository_graph")
        await self._inner.replace_repository_graph(repository_id, graph)

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        self._count("get_full_graph")
        return await self._inner.get_full_graph(repository_id)

    async def get_nodes_by_label(self, repository_id: str, label: str) -> list[GraphNode]:
        self._count("get_nodes_by_label")
        return await self._inner.get_nodes_by_label(repository_id, label)

    async def has_graph(self, repository_id: str) -> bool:
        self._count("has_graph")
        return await self._inner.has_graph(repository_id)

    async def replace_cross_repository_edges(
        self, source_repository_id: str, edges: list[GraphEdge]
    ) -> None:
        self._count("replace_cross_repository_edges")
        await self._inner.replace_cross_repository_edges(source_repository_id, edges)

    async def get_outgoing_cross_repository_edges(self, repository_id: str) -> list[GraphEdge]:
        self._count("get_outgoing_cross_repository_edges")
        return await self._inner.get_outgoing_cross_repository_edges(repository_id)


def _repository_payload(
    repository_id: str,
    *,
    feign_target: str | None = None,
    produces_topic: str | None = None,
    consumes_topic: str | None = None,
    maven_artifact_id: str | None = None,
) -> GraphPayload:
    repo_node_id = f"{repository_id}:repository"
    nodes = [GraphNode(id=repo_node_id, labels=["Repository"], properties={})]
    edges: list[GraphEdge] = []

    if feign_target is not None:
        feign_id = f"{repository_id}:feign:x.TargetClient"
        nodes.append(
            GraphNode(
                id=feign_id,
                labels=["Component", "FeignClient"],
                properties={"name": "TargetClient", "target_name": feign_target},
            )
        )
        edges.append(GraphEdge(source_id=repo_node_id, target_id=feign_id, type="CONTAINS"))

    if produces_topic is not None:
        producer_id = f"{repository_id}:component:Producer"
        topic_id = f"{repository_id}:kafka-topic:{produces_topic}"
        nodes.append(
            GraphNode(id=producer_id, labels=["Component"], properties={"name": "Producer"})
        )
        nodes.append(
            GraphNode(id=topic_id, labels=["KafkaTopic"], properties={"name": produces_topic})
        )
        edges.append(GraphEdge(source_id=repo_node_id, target_id=producer_id, type="CONTAINS"))
        edges.append(GraphEdge(source_id=producer_id, target_id=topic_id, type="PRODUCES_TO"))

    if consumes_topic is not None:
        consumer_id = f"{repository_id}:component:Consumer"
        topic_id = f"{repository_id}:kafka-topic:{consumes_topic}"
        nodes.append(
            GraphNode(id=consumer_id, labels=["Component"], properties={"name": "Consumer"})
        )
        nodes.append(
            GraphNode(id=topic_id, labels=["KafkaTopic"], properties={"name": consumes_topic})
        )
        edges.append(GraphEdge(source_id=repo_node_id, target_id=consumer_id, type="CONTAINS"))
        edges.append(GraphEdge(source_id=consumer_id, target_id=topic_id, type="CONSUMES_FROM"))

    if maven_artifact_id is not None:
        dep_id = f"{repository_id}:dependency:com.acme:{maven_artifact_id}"
        nodes.append(
            GraphNode(
                id=dep_id,
                labels=["MavenDependency"],
                properties={"group_id": "com.acme", "artifact_id": maven_artifact_id},
            )
        )
        edges.append(GraphEdge(source_id=repo_node_id, target_id=dep_id, type="DEPENDS_ON"))

    return GraphPayload(nodes=nodes, edges=edges)


async def _make_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        full_name="Test User",
        auth_provider="local",
    )
    db.add(user)
    await db.flush()
    return user


async def _make_repository(db: AsyncSession, user: User, name: str) -> Repository:
    repo = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name=name,
        full_name=f"acme/{name}",
        private=False,
        default_branch="main",
        html_url=f"https://github.com/acme/{name}",
    )
    db.add(repo)
    await db.flush()
    return repo


async def _mark_indexed(db: AsyncSession, repository: Repository, finished_at: datetime) -> None:
    """A completed `IndexingJob` row — what `relink_account` reads to stamp
    `graph_version` onto every edge it computes (ADR 0010 §4)."""
    job = IndexingJob(
        id=uuid.uuid4(),
        repository_id=repository.id,
        status="completed",
        started_at=finished_at,
        finished_at=finished_at,
    )
    db.add(job)
    await db.flush()


async def test_feign_kafka_and_dependency_edges_are_written_and_readable(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    user = await _make_user(db_session)
    ingestion = await _make_repository(db_session, user, "ingestion-framework")
    etl_core = await _make_repository(db_session, user, "etl-core")
    await db_session.flush()

    ingestion_id = str(ingestion.id)
    etl_core_id = str(etl_core.id)

    # ingestion-framework calls etl-core via Feign, and produces a topic
    # etl-core consumes.
    await graph_repository.replace_repository_graph(
        ingestion_id,
        _repository_payload(
            ingestion_id, feign_target="etl-core-service", produces_topic="orders-created"
        ),
    )
    # etl-core consumes that topic, and declares a Maven dependency whose
    # artifact id matches ingestion-framework's own name.
    await graph_repository.replace_repository_graph(
        etl_core_id,
        _repository_payload(
            etl_core_id, consumes_topic="orders-created", maven_artifact_id="ingestion-framework"
        ),
    )

    try:
        await relink_account(graph_repository=graph_repository, db=db_session, user_id=user.id)

        ingestion_edges = await graph_repository.get_outgoing_cross_repository_edges(ingestion_id)
        by_type = {e.type: e for e in ingestion_edges}
        assert "CALLS_SERVICE" in by_type
        assert by_type["CALLS_SERVICE"].target_id == f"{etl_core_id}:repository"
        assert "SHARES_TOPIC" in by_type
        assert by_type["SHARES_TOPIC"].properties["topics"] == ["orders-created"]
        # ADR 0010 §4 — every edge is stamped, even with no IndexingJob rows
        # (graph_version is simply None in that case, not omitted).
        assert "computed_at" in by_type["CALLS_SERVICE"].properties

        etl_core_edges = await graph_repository.get_outgoing_cross_repository_edges(etl_core_id)
        by_type_other = {e.type: e for e in etl_core_edges}
        assert "DEPENDS_ON_REPOSITORY" in by_type_other
        assert by_type_other["DEPENDS_ON_REPOSITORY"].target_id == f"{ingestion_id}:repository"
        assert "SHARES_TOPIC" in by_type_other
    finally:
        await graph_repository.replace_repository_graph(ingestion_id, GraphPayload())
        await graph_repository.replace_repository_graph(etl_core_id, GraphPayload())


async def test_graph_version_is_stamped_from_the_latest_completed_indexing_job(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    user = await _make_user(db_session)
    repo_a = await _make_repository(db_session, user, "repo-a")
    repo_b = await _make_repository(db_session, user, "repo-b")
    await db_session.flush()

    a_id, b_id = str(repo_a.id), str(repo_b.id)
    finished = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    await _mark_indexed(db_session, repo_a, finished)

    await graph_repository.replace_repository_graph(
        a_id, _repository_payload(a_id, feign_target="repo-b")
    )
    await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))

    try:
        await relink_account(graph_repository=graph_repository, db=db_session, user_id=user.id)

        edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
        assert len(edges) == 1
        assert edges[0].properties["source_graph_version"] == finished.isoformat()
        # repo-b was never indexed via an IndexingJob row in this test — its
        # graph_version is honestly unknown. Neo4j drops a property set to
        # None entirely rather than storing a null, so the honest
        # "unknown" state is the key being absent, not present-and-null.
        assert "target_graph_version" not in edges[0].properties
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())


async def test_relink_account_computes_each_repositorys_edges_independently(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    """Each repository's own outgoing edge set, computed in the same
    `relink_account` batch, must be independently correct — repo A having no
    relationships at all must not affect repo B's real edge to repo C."""
    user = await _make_user(db_session)
    repo_a = await _make_repository(db_session, user, "repo-a")
    repo_b = await _make_repository(db_session, user, "repo-b")
    repo_c = await _make_repository(db_session, user, "repo-c")
    await db_session.flush()

    a_id, b_id, c_id = str(repo_a.id), str(repo_b.id), str(repo_c.id)

    await graph_repository.replace_repository_graph(a_id, _repository_payload(a_id))
    await graph_repository.replace_repository_graph(
        b_id, _repository_payload(b_id, feign_target="repo-c")
    )
    await graph_repository.replace_repository_graph(c_id, _repository_payload(c_id))

    try:
        await relink_account(graph_repository=graph_repository, db=db_session, user_id=user.id)

        assert await graph_repository.get_outgoing_cross_repository_edges(a_id) == []
        b_edges = await graph_repository.get_outgoing_cross_repository_edges(b_id)
        assert len(b_edges) == 1
        assert b_edges[0].target_id == f"{c_id}:repository"
        assert await graph_repository.get_outgoing_cross_repository_edges(c_id) == []

        # Idempotent: relinking again with nothing changed produces the same
        # edge set, not duplicates.
        await relink_account(graph_repository=graph_repository, db=db_session, user_id=user.id)
        assert len(await graph_repository.get_outgoing_cross_repository_edges(b_id)) == 1
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())
        await graph_repository.replace_repository_graph(c_id, GraphPayload())


async def test_cross_repository_edges_never_span_two_users(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    """A Feign target name matching another *user's* repository must never
    produce an edge — cross-repository linking is scoped to one user's own
    repositories by construction (same scoping `GetIndexedRepositoriesTool`
    already enforces for the single-repository read path)."""
    owner = await _make_user(db_session)
    other_user = await _make_user(db_session)
    mine = await _make_repository(db_session, owner, "my-service")
    someone_elses = await _make_repository(db_session, other_user, "shared-name")
    await db_session.flush()

    mine_id, theirs_id = str(mine.id), str(someone_elses.id)

    await graph_repository.replace_repository_graph(
        mine_id, _repository_payload(mine_id, feign_target="shared-name")
    )
    await graph_repository.replace_repository_graph(theirs_id, _repository_payload(theirs_id))

    try:
        await relink_account(graph_repository=graph_repository, db=db_session, user_id=owner.id)
        edges = await graph_repository.get_outgoing_cross_repository_edges(mine_id)
        assert edges == [], (
            "a Feign target matching another user's repository name must never link — "
            f"got: {edges}"
        )
    finally:
        await graph_repository.replace_repository_graph(mine_id, GraphPayload())
        await graph_repository.replace_repository_graph(theirs_id, GraphPayload())


async def test_relink_account_fetches_each_repositorys_nodes_at_most_once(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    """ADR 0010 Theme C: batch-fetch-then-evaluate must cost `O(N)` Neo4j
    round-trips for `N` repositories, not `O(N²)` — every repository's nodes
    fetched exactly once, regardless of how many other repositories it's
    compared against."""
    user = await _make_user(db_session)
    repos = [await _make_repository(db_session, user, f"repo-{i}") for i in range(4)]
    await db_session.flush()
    ids = [str(r.id) for r in repos]

    for repo_id in ids:
        await graph_repository.replace_repository_graph(repo_id, _repository_payload(repo_id))

    counting = _CountingGraphRepository(graph_repository)
    try:
        await relink_account(graph_repository=counting, db=db_session, user_id=user.id)

        # 4 node-label reads per repo (FeignClient/KafkaTopic/MavenDependency/
        # PythonDependency) + 1 get_full_graph per repo = 5*N, not 5*N*(N-1).
        assert counting.calls["get_nodes_by_label"] == 4 * len(repos)
        assert counting.calls["get_full_graph"] == len(repos)
        # One scoped write per repository — never more than N.
        assert counting.calls["replace_cross_repository_edges"] == len(repos)
    finally:
        for repo_id in ids:
            await graph_repository.replace_repository_graph(repo_id, GraphPayload())


async def test_relink_account_is_a_no_op_when_another_relink_holds_the_lock(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    """The single-flight guard: a concurrent relink for the same account
    (simulated here by holding the advisory lock on an independent
    connection) must make this call skip entirely rather than race it."""
    user = await _make_user(db_session)
    repo_a = await _make_repository(db_session, user, "repo-a")
    repo_b = await _make_repository(db_session, user, "repo-b")
    await db_session.flush()

    a_id, b_id = str(repo_a.id), str(repo_b.id)
    await graph_repository.replace_repository_graph(
        a_id, _repository_payload(a_id, feign_target="repo-b")
    )
    await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))

    try:
        async with engine.connect() as holder_conn:
            await holder_conn.execute(select(func.pg_advisory_lock(func.hashtext(str(user.id)))))
            try:
                await relink_account(
                    graph_repository=graph_repository, db=db_session, user_id=user.id
                )
                edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
                assert edges == [], (
                    "relink_account must skip entirely while another relink for the same "
                    "account holds the lock, not partially write"
                )
            finally:
                await holder_conn.execute(
                    select(func.pg_advisory_unlock(func.hashtext(str(user.id))))
                )
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())


# ---------------------------------------------------------------------------
# ADR 0010 §3 — relationship lifecycle regression tests
# ---------------------------------------------------------------------------


async def test_deleting_a_repository_removes_both_its_incoming_and_outgoing_edges(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    """`remove_repository`'s existing `replace_repository_graph(id,
    GraphPayload())` call issues `DETACH DELETE` on that repository's own
    node — Neo4j relationships cannot outlive either endpoint, so this must
    remove a deleted repository's outgoing *and* incoming cross-repo edges
    in one operation, with no separate cleanup code. Asserted here against
    a repository with both directions, not merely argued from reading the
    Cypher (ADR 0010 §3)."""
    user = await _make_user(db_session)
    repo_a = await _make_repository(db_session, user, "repo-a")
    repo_b = await _make_repository(db_session, user, "repo-b")
    repo_c = await _make_repository(db_session, user, "repo-c")
    await db_session.flush()

    a_id, b_id, c_id = str(repo_a.id), str(repo_b.id), str(repo_c.id)

    # repo-a -> repo-b (incoming to b), repo-b -> repo-c (outgoing from b)
    await graph_repository.replace_repository_graph(
        a_id, _repository_payload(a_id, feign_target="repo-b")
    )
    await graph_repository.replace_repository_graph(
        b_id, _repository_payload(b_id, feign_target="repo-c")
    )
    await graph_repository.replace_repository_graph(c_id, _repository_payload(c_id))

    try:
        await relink_account(graph_repository=graph_repository, db=db_session, user_id=user.id)

        # Confirm repo-b genuinely has both directions before deleting it.
        assert len(await graph_repository.get_outgoing_cross_repository_edges(a_id)) == 1
        assert len(await graph_repository.get_outgoing_cross_repository_edges(b_id)) == 1

        # Exactly what `remove_repository` does.
        await graph_repository.replace_repository_graph(b_id, GraphPayload())

        # repo-b's own outgoing edge (to repo-c) is gone...
        assert await graph_repository.get_outgoing_cross_repository_edges(b_id) == []
        # ...and so is repo-a's outgoing edge *to* repo-b — an edge cannot
        # survive the deletion of either endpoint, even though repo-a's own
        # node was never touched.
        assert await graph_repository.get_outgoing_cross_repository_edges(a_id) == []
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())
        await graph_repository.replace_repository_graph(c_id, GraphPayload())


async def test_orphaned_edges_are_structurally_impossible(
    db_session: AsyncSession, graph_repository: Neo4jGraphRepository
) -> None:
    """`_write_edges` MATCHes both endpoint nodes rather than MERGE-creating
    them — an edge whose target doesn't exist yet must silently fail to
    write, never produce a dangling relationship (ADR 0010 §3)."""
    user = await _make_user(db_session)
    repo_a = await _make_repository(db_session, user, "repo-a")
    await db_session.flush()
    a_id = str(repo_a.id)

    # repo-a's own node exists, but no repo-b node exists anywhere in Neo4j
    # at all (never indexed) — replace_cross_repository_edges must not
    # create a phantom edge or a phantom node for it.
    await graph_repository.replace_repository_graph(a_id, _repository_payload(a_id))
    try:
        phantom_edge = GraphEdge(
            source_id=f"{a_id}:repository",
            target_id="00000000-0000-0000-0000-000000000000:repository",
            type="CALLS_SERVICE",
            properties={"via": ["x"], "target_name": ["ghost"]},
        )
        await graph_repository.replace_cross_repository_edges(a_id, [phantom_edge])

        edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
        assert edges == [], "an edge to a nonexistent node must never be written"
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())


async def test_relink_failure_does_not_fail_the_triggering_indexing_job(
    db_session: AsyncSession,
) -> None:
    """`run_indexing` swallows a relink failure — the repository's own graph
    is already committed and usable regardless of whether relinking
    succeeded (ADR 0010 §3's relink failure policy)."""
    from unittest.mock import AsyncMock, patch

    from app.indexer.services import indexing_service

    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, "repo-a")
    await db_session.flush()

    with (
        patch.object(
            indexing_service,
            "index_repository",
            new=AsyncMock(return_value={"controllers": 0}),
        ),
        patch.object(
            indexing_service,
            "relink_account",
            new=AsyncMock(side_effect=RuntimeError("neo4j exploded")),
        ),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    # The repository's own indexing summary is returned normally — the
    # relink failure was logged and swallowed, not propagated.
    assert summary == {"controllers": 0}
