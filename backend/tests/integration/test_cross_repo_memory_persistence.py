"""ADR 0018 RFC-05 — `persist_cross_repo_relationships` end to end: real
Postgres writes/reads, proving cross-repository relationships actually
land in Engineering Memory with the same confidence mapping
`tests/unit/knowledge_engine/test_cross_repo_parity.py` already proved
(`structural` -> HIGHLY_LIKELY, `heuristic` -> LIKELY), and that a
non-matching pair persists nothing (no write amplification for the common
case).

Uses real, independently committed sessions rather than the rollback-
wrapped `db_session` fixture, because `persist_cross_repo_relationships`
opens its own `AsyncSessionLocal()` internally on a separate connection —
the same reason `test_finding3_concurrent_relink_repro.py` avoids
`db_session` for its own real-session assertions.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import engine
from app.graph.models import GraphNode
from app.indexer.graph.cross_repo_linker import RepoNodes
from app.indexer.graph.cross_repo_memory import persist_cross_repo_relationships
from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio

_RealSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def two_repositories() -> tuple[str, str]:
    session = _RealSession()
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
        name="ingestion-framework",
        full_name="acme/ingestion-framework",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/ingestion-framework",
    )
    repo_b = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="etl-core",
        full_name="acme/etl-core",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/etl-core",
    )
    session.add(user)
    await session.flush()
    session.add_all([repo_a, repo_b])
    await session.commit()
    a_id, b_id = str(repo_a.id), str(repo_b.id)
    await session.close()

    yield a_id, b_id

    cleanup = _RealSession()
    await cleanup.delete(await cleanup.get(Repository, repo_a.id))
    await cleanup.delete(await cleanup.get(Repository, repo_b.id))
    await cleanup.delete(await cleanup.get(User, user.id))
    await cleanup.commit()
    await cleanup.close()


def _repo_nodes(repository_id: str, name: str, *, feign_target: str | None = None) -> RepoNodes:
    feign_clients = []
    if feign_target is not None:
        feign_clients.append(
            GraphNode(
                id=f"{repository_id}:feign:x.TargetClient",
                labels=["Component", "FeignClient"],
                properties={"name": "TargetClient", "target_name": feign_target},
            )
        )
    return RepoNodes(
        repository_id=repository_id,
        name=name,
        feign_clients=feign_clients,
        maven_dependencies=[],
        python_dependencies=[],
        produces_topic_names=frozenset(),
        consumes_topic_names=frozenset(),
    )


async def test_matching_pair_persists_validated_relationship(
    two_repositories: tuple[str, str],
) -> None:
    a_id, b_id = two_repositories
    nodes_by_repo = {
        a_id: _repo_nodes(a_id, "ingestion-framework", feign_target="etl-core-service"),
        b_id: _repo_nodes(b_id, "etl-core"),
    }

    await persist_cross_repo_relationships(nodes_by_repo)

    query_session = _RealSession()
    try:
        memory = EngineeringMemoryService(query_session)
        relationships = await memory.get_current_relationships(uuid.UUID(a_id))
        assert len(relationships) == 1
        record = relationships[0]
        assert record.relationship_type == "CALLS_SERVICE"
        assert record.confidence_state == ConfidenceState.HIGHLY_LIKELY.value
        assert record.provenance
    finally:
        await query_session.close()


async def test_non_matching_pair_persists_nothing(two_repositories: tuple[str, str]) -> None:
    a_id, b_id = two_repositories
    nodes_by_repo = {
        a_id: _repo_nodes(a_id, "ingestion-framework"),
        b_id: _repo_nodes(b_id, "etl-core"),
    }

    await persist_cross_repo_relationships(nodes_by_repo)

    query_session = _RealSession()
    try:
        memory = EngineeringMemoryService(query_session)
        assert await memory.get_current_relationships(uuid.UUID(a_id)) == []
        assert await memory.get_current_relationships(uuid.UUID(b_id)) == []
    finally:
        await query_session.close()
