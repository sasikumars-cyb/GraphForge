"""Deterministic reproducer for the adversarial-review Finding #3 claim:

  "When multiple repositories belonging to the same account are indexed
  concurrently, relink_account() may permanently miss some repositories
  because concurrent callers return immediately after failing to acquire
  the advisory lock, and no later reconciliation is guaranteed."

Two independent AsyncSession objects (each bound to its own connection,
exactly like two independent `AsyncSessionLocal()` instances in production —
see `app.database.session.AsyncSessionLocal`, which is a plain
`async_sessionmaker(bind=engine)` with no shared connection or savepoint
join mode) simulate job A and job B.

`relink_account`'s advisory-lock acquisition is *blocking*
(`pg_advisory_xact_lock`, not `pg_try_advisory_xact_lock` — see
`app.indexer.graph.cross_repo_linker.relink_account`), matching how the two
jobs actually run in production: as independent asyncio tasks, neither
blocking the other's ability to make progress. Session B's call is
therefore driven via `asyncio.create_task` rather than a plain sequential
`await` — a plain sequential await, with session A's transaction still
open, would deadlock the single test coroutine against itself (nothing else
would ever run to commit session A and release the lock session B is
waiting on). This mirrors the real concurrency shape: two independent
tasks, one blocked on a lock the other holds, with the test explicitly
confirming the block actually happened (`task_b` is not done immediately)
before releasing it — no `sleep`-based guessing about ordering elsewhere in
the test, since the only genuinely timing-dependent step is exactly this
one, made explicit and asserted rather than assumed.

Deliberately does NOT use the `db_session` fixture other tests in this
package use: that fixture binds its session to a connection that already
has `conn.begin()` called on it, with `join_transaction_mode="create_savepoint"`
— every `session.commit()` there releases a SAVEPOINT, not the real
top-level Postgres transaction, so `pg_try_advisory_xact_lock` acquired
through it would never actually release until the fixture's own teardown
`conn.rollback()`. That doesn't match how `relink_account` really behaves
in production, where each `run_indexing_job` call owns a fully independent
`AsyncSessionLocal()` with a real commit boundary. This test builds its own
sessions the same way `AsyncSessionLocal` does, so the advisory lock's
acquire/release timing here is faithful to production.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import Base
from app.database.session import engine
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.graph.cross_repo_linker import relink_account
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio

# Same session construction as `app.database.session.AsyncSessionLocal` —
# bound directly to the engine, no pre-begun connection, no savepoint join
# mode — so `.commit()` here is a real top-level Postgres COMMIT, and an
# advisory lock acquired through one of these sessions is held until that
# session's own `.commit()`/`.rollback()`, exactly as in production.
_RealSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def _repository_payload(
    repository_id: str,
    *,
    feign_target: str | None = None,
    repository_properties: dict | None = None,
) -> GraphPayload:
    repo_node_id = f"{repository_id}:repository"
    nodes = [
        GraphNode(id=repo_node_id, labels=["Repository"], properties=repository_properties or {})
    ]
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
    return GraphPayload(nodes=nodes, edges=edges)


async def test_concurrent_indexing_can_permanently_drop_a_cross_repo_edge() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    graph_repository = Neo4jGraphRepository(get_driver())

    # --- Setup: one user, two repositories, committed for real so that two
    # independent later sessions (under READ COMMITTED) can both see them. ---
    setup = _RealSession()
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        full_name="Test User",
        auth_provider="local",
    )
    repo_a = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="repo-a",
        full_name="acme/repo-a",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/repo-a",
    )
    repo_b = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="repo-b",
        full_name="acme/repo-b",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/repo-b",
    )
    setup.add(user)
    await setup.flush()
    setup.add_all([repo_a, repo_b])
    await setup.commit()
    await setup.close()

    a_id, b_id = str(repo_a.id), str(repo_b.id)

    try:
        # T2: job A's clone+parse+build finishes -> A's Neo4j graph is
        # committed. A references B via a Feign client.
        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, feign_target="repo-b")
        )

        # T3: job A calls relink_account on its own independent session.
        # B is not indexed yet (has_graph(b) is False), so this pass can
        # only ever see A alone. We deliberately do NOT commit session_a
        # yet -- exactly like production, where `run_indexing` doesn't
        # commit until `run_indexing_job` reaches `job.status="completed"`
        # well after `relink_account` returns. The advisory lock this
        # acquires is therefore still held at this point in the test.
        session_a = _RealSession()
        await relink_account(graph_repository=graph_repository, db=session_a, user_id=user.id)

        # T8: job B's clone+parse+build finishes -> B's Neo4j graph is
        # committed.
        await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))

        # T9: job B calls relink_account on its own independent session, as
        # its own concurrent task -- session_a's transaction (and its
        # advisory lock) is still open, so this must genuinely block rather
        # than skip or deadlock the test.
        session_b = _RealSession()
        task_b = asyncio.create_task(
            relink_account(graph_repository=graph_repository, db=session_b, user_id=user.id)
        )
        await asyncio.sleep(0.2)
        assert not task_b.done(), (
            "expected job B's relink to still be blocked on job A's advisory "
            "lock -- if it already finished, this test isn't exercising "
            "contention at all"
        )

        # T11: job A "completes" -- its session commits, releasing the lock
        # job B has been waiting on, exactly as `run_indexing_job` does once
        # `run_indexing` (relink included) returns.
        await session_a.commit()
        await asyncio.wait_for(task_b, timeout=5)

        # T10': job B "completes" too, same as any other indexing job.
        await session_b.commit()
        await session_a.close()
        await session_b.close()

        # No third indexing event ever happens for this account. Both
        # repositories are fully indexed, and A has a real Feign reference
        # to B.
        edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
        assert edges, (
            "Finding #3 NOT reproduced: relink_account converged and "
            f"computed the A->B edge despite the interleaving. edges={edges}"
        )
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())


# ---------------------------------------------------------------------------
# RFC-0024 follow-up — a different, generic defect found while investigating
# why a real `IMPORTS_REPOSITORY` edge (correctly computed by
# `cross_repo_linker.compute_edges`) never showed up in Neo4j: `replace_
# repository_graph`'s `DETACH DELETE` removed the repository's own
# `Repository` node on every reindex, and Neo4j's `DETACH DELETE` cascades
# that into deleting every relationship touching it — including any
# cross-repository edge anchored there. Nothing here is specific to any one
# repository pair; any two repositories with a real cross-repo relationship
# are exposed to this the moment either one is reindexed.
# ---------------------------------------------------------------------------


async def _create_user_and_two_repos() -> tuple[uuid.UUID, str, str]:
    """Same setup shape as the Finding #3 reproducer above, factored out
    for reuse — one user, two committed repositories, returned as
    (user_id, repo_a_id, repo_b_id)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    setup = _RealSession()
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        full_name="Test User",
        auth_provider="local",
    )
    repo_a = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="repo-a",
        full_name="acme/repo-a",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/repo-a",
    )
    repo_b = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="repo-b",
        full_name="acme/repo-b",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/repo-b",
    )
    setup.add(user)
    await setup.flush()
    setup.add_all([repo_a, repo_b])
    await setup.commit()
    await setup.close()
    return user.id, str(repo_a.id), str(repo_b.id)


async def _relink(graph_repository: Neo4jGraphRepository, user_id: uuid.UUID) -> None:
    session = _RealSession()
    try:
        await relink_account(graph_repository=graph_repository, db=session, user_id=user_id)
        await session.commit()
    finally:
        await session.close()


async def test_cross_repo_edge_survives_source_repository_reindex() -> None:
    """1: A->B, established via a real `relink_account` pass, must still be
    there after A itself is reindexed (a fresh `replace_repository_graph`
    call for A, exactly what a normal re-index does)."""
    graph_repository = Neo4jGraphRepository(get_driver())
    user_id, a_id, b_id = await _create_user_and_two_repos()
    try:
        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, feign_target="repo-b")
        )
        await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))
        await _relink(graph_repository, user_id)
        assert await graph_repository.get_outgoing_cross_repository_edges(a_id), (
            "edge must exist right after relink"
        )

        # A is reindexed again (e.g. a later commit) -- no relink follows.
        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, feign_target="repo-b")
        )

        edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
        assert edges, f"A->B edge was lost when A was reindexed: edges={edges}"
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())


async def test_cross_repo_edge_survives_target_repository_reindex() -> None:
    """2: A->B must still be there after B (the *target* of the edge, not
    its source) is reindexed -- this is the exact shape found live:
    the edge is anchored on B's own `Repository` node, so B's reindex is
    the one that actually destroyed it."""
    graph_repository = Neo4jGraphRepository(get_driver())
    user_id, a_id, b_id = await _create_user_and_two_repos()
    try:
        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, feign_target="repo-b")
        )
        await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))
        await _relink(graph_repository, user_id)
        assert await graph_repository.get_outgoing_cross_repository_edges(a_id)

        # B is reindexed -- no relink follows.
        await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))

        edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
        assert edges, f"A->B edge was lost when B (the target) was reindexed: edges={edges}"
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())


async def test_repository_node_identity_stable_across_reindex() -> None:
    """3: the `Repository` node is the *same* node (same `id`) before and
    after a reindex -- merge-updated in place, never deleted and
    recreated -- and its properties still refresh normally."""
    graph_repository = Neo4jGraphRepository(get_driver())
    _user_id, a_id, _b_id = await _create_user_and_two_repos()
    try:
        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, repository_properties={"language": "python"})
        )
        before = await graph_repository.get_nodes_by_label(a_id, "Repository")
        assert len(before) == 1
        assert before[0].properties.get("language") == "python"

        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, repository_properties={"language": "java"})
        )
        after = await graph_repository.get_nodes_by_label(a_id, "Repository")
        assert len(after) == 1, "reindex must never leave more than one Repository node behind"
        assert after[0].id == before[0].id, "the Repository node's own id must stay stable"
        assert after[0].properties.get("language") == "java", (
            "properties must still refresh via MERGE even though the node itself is preserved"
        )
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())


async def test_incremental_subgraph_replace_preserves_cross_repo_edge() -> None:
    """4: the incremental (`KAN-32`) reindex path, `replace_repository_
    files_subgraph`, must preserve a cross-repository edge exactly like
    the full-reindex path does."""
    graph_repository = Neo4jGraphRepository(get_driver())
    user_id, a_id, b_id = await _create_user_and_two_repos()
    try:
        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, feign_target="repo-b")
        )
        await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))
        await _relink(graph_repository, user_id)
        assert await graph_repository.get_outgoing_cross_repository_edges(a_id)

        # An incremental reindex of A, touching one arbitrary changed file
        # -- no relink follows.
        await graph_repository.replace_repository_files_subgraph(
            a_id, ["some/changed_file.py"], _repository_payload(a_id, feign_target="repo-b")
        )

        edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
        assert edges, f"A->B edge was lost by an incremental reindex of A: edges={edges}"
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())


async def test_relink_then_target_reindex_race_shape_preserves_edge() -> None:
    """5: the exact race shape found live -- A's relink computes and
    writes A->B, and *then* B is reindexed, with no third relink ever
    happening afterward. Before the fix this permanently lost the edge;
    the fix means B's reindex no longer destroys the `Repository` node
    the edge is anchored on, so nothing needs to re-heal it."""
    graph_repository = Neo4jGraphRepository(get_driver())
    user_id, a_id, b_id = await _create_user_and_two_repos()
    try:
        await graph_repository.replace_repository_graph(
            a_id, _repository_payload(a_id, feign_target="repo-b")
        )
        await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))

        # A's relink runs first, alone -- computes and writes A->B.
        await _relink(graph_repository, user_id)
        assert await graph_repository.get_outgoing_cross_repository_edges(a_id), (
            "edge must exist right after A's relink"
        )

        # B is reindexed *after* -- the exact live sequence (two
        # repositories reindexed close together in time). No relink
        # follows this reindex.
        await graph_repository.replace_repository_graph(b_id, _repository_payload(b_id))

        edges = await graph_repository.get_outgoing_cross_repository_edges(a_id)
        assert edges, (
            f"the relink-then-target-reindex race destroyed the edge with no relink "
            f"to follow and repair it: edges={edges}"
        )
    finally:
        await graph_repository.replace_repository_graph(a_id, GraphPayload())
        await graph_repository.replace_repository_graph(b_id, GraphPayload())
