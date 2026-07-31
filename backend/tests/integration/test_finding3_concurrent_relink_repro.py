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


def _repository_payload(repository_id: str, *, feign_target: str | None = None) -> GraphPayload:
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
