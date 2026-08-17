"""`display_names()` tenant-boundary defense (M-3).

The audit found the fallback `Repository` lookup in `display_names()`
unscoped by `user_id` — `select(Repository).where(Repository.id.in_(...))`
would return *any* repository row in the table, not just the requesting
user's. Not exploitable at audit time (blast-radius node ids can currently
only ever come from this user's own scoped Neo4j subgraph — see
`cross_repo_linker.py`), but that safety is enforced entirely upstream of
this function, not by it.

This test does not rely on that upstream invariant holding. It calls
`display_names()` directly with a foreign repository's raw id sitting in
`blast_radius.impacted_repositories` — bypassing the graph traversal
entirely — to prove the function's *own* boundary holds even if whatever
calls it someday doesn't. Real DB (`db_session`), no mocking of the query
under test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphPayload
from app.models.repository import Repository
from app.models.user import User
from app.services import ask_grounding as ag
from app.services.engineering_intelligence.contracts import BlastRadius, EntityReference

pytestmark = pytest.mark.asyncio


async def _make_user_and_repository(
    db: AsyncSession, *, owner: str = "acme", name: str = "widgets"
) -> Repository:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        hashed_password="x",
        full_name="Test User",
    )
    db.add(user)
    await db.flush()

    repository = Repository(
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        private=False,
        default_branch="main",
        html_url=f"https://github.com/{owner}/{name}",
    )
    db.add(repository)
    await db.flush()
    return repository


def _blast_radius_naming(*repo_ids: uuid.UUID, seed_id: uuid.UUID) -> BlastRadius:
    """A blast radius whose `impacted_repositories` names the given raw
    repository ids directly — no graph node carries a `properties.name`,
    so every one of them is forced through `display_names()`'s DB
    fallback, which is exactly the code path under test."""
    return BlastRadius(
        seed=EntityReference(repository_id=str(seed_id), node_id=f"{seed_id}:repository"),
        direction="downstream",
        max_hops=2,
        impacted_repositories=tuple(f"{repo_id}:repository" for repo_id in repo_ids),
        impacted_apis=(),
        impacted_databases=(),
        impacted_queues=(),
        subgraph=GraphPayload(nodes=[], edges=[]),
    )


class TestDisplayNamesIsolation:
    async def test_a_foreign_repositorys_name_is_never_returned(
        self, db_session: AsyncSession
    ) -> None:
        """User A's blast radius somehow (bug, future regression) contains
        User B's raw repository id. `display_names()`, called as User A,
        must not resolve or leak User B's name."""
        repo_a = await _make_user_and_repository(db_session, owner="user-a", name="repo-a")
        repo_b = await _make_user_and_repository(db_session, owner="user-b", name="repo-b")

        blast_radius = _blast_radius_naming(repo_b.id, seed_id=repo_a.id)

        names = await ag.display_names(db_session, blast_radius, repo_a.user_id)

        assert f"{repo_b.id}:repository" not in names
        assert "user-b/repo-b" not in names.values()

    async def test_a_legitimate_own_repository_still_resolves(
        self, db_session: AsyncSession
    ) -> None:
        """The fix must not break the ordinary same-tenant case: a
        repository the caller actually owns still resolves to its real
        name through the same fallback path."""
        repo_a = await _make_user_and_repository(db_session, owner="user-a", name="repo-a")
        other_owned = await _make_user_and_repository(db_session, owner="user-a", name="repo-a2")
        # Same user_id as repo_a — simulate a second repo owned by the same
        # account, unresolved by the graph-node fast path.
        other_owned.user_id = repo_a.user_id
        await db_session.flush()

        blast_radius = _blast_radius_naming(other_owned.id, seed_id=repo_a.id)

        names = await ag.display_names(db_session, blast_radius, repo_a.user_id)

        assert names[f"{other_owned.id}:repository"] == other_owned.full_name

    async def test_the_seed_itself_resolves_only_when_owned(self, db_session: AsyncSession) -> None:
        """The seed id is looked up through the same fallback (see the
        `*blast_radius.impacted_repositories, blast_radius.seed.node_id`
        loop) — confirm it is equally subject to the `user_id` scope."""
        repo_a = await _make_user_and_repository(db_session, owner="user-a", name="repo-a")

        blast_radius = _blast_radius_naming(seed_id=repo_a.id)

        names = await ag.display_names(db_session, blast_radius, repo_a.user_id)

        assert names[f"{repo_a.id}:repository"] == repo_a.full_name
