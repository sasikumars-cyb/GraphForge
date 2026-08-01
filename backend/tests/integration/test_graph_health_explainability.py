"""Context Discovery explainability end-to-end: a repository whose indexing
job completed in Postgres but whose graph is missing from Neo4j (the exact
live drift the Graph Health investigation found) must produce a specific,
evidence-backed explanation — not just "0 indexed repositories" — through
the real `Neo4jGraphTool` -> `GraphHealthService` -> `GraphInvestigator`
pipeline. Real Postgres and real Neo4j, no mocked DB or graph store.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_pipeline.reasoning.engine import discover
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import GraphInvestigator
from app.graph.models import GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


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


async def _make_tracked_repository(db: AsyncSession, user: User, name: str) -> Repository:
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


async def _clear_graph(repository_id: str) -> None:
    graph_repository = Neo4jGraphRepository(get_driver())
    await graph_repository.replace_repository_graph(repository_id, GraphPayload())


async def test_graph_missing_repository_gets_a_specific_explanation(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    repo = await _make_tracked_repository(db_session, user, "streaming-pipeline")
    db_session.add(
        IndexingJob(id=uuid.uuid4(), repository_id=repo.id, status="completed")
    )
    await db_session.flush()
    # Deliberately no Neo4j graph written — the exact GRAPH_MISSING state
    # found live in the investigation: a completed job, no matching graph.

    state = await discover(
        request="Investigate streaming-pipeline for a retry bug",
        session=SessionContext(db=db_session, user_id=user.id),
        investigators=[GraphInvestigator()],
    )

    evidence_summaries = [e.summary for e in state.ledger.evidence]
    explanation = [s for s in evidence_summaries if "streaming-pipeline" in s]
    assert explanation, (
        f"expected an evidence entry naming streaming-pipeline; got {evidence_summaries}"
    )
    assert any("re-index" in s.lower() for s in explanation)
    assert any("completed" in s.lower() for s in explanation)

    # The repository must not be silently treated as usable — no
    # `repository` fact for it, so it can never become a candidate.
    assert "streaming-pipeline" not in {f.subject for f in state.ledger.facts_of("repository")}


async def test_healthy_repository_is_unaffected_by_the_new_narration(
    db_session: AsyncSession,
) -> None:
    """Regression guard: a genuinely healthy repository must still resolve
    exactly as before — the explainability additions are for repositories
    that AREN'T usable, not a change to the ones that are."""
    user = await _make_user(db_session)
    repo = await _make_tracked_repository(db_session, user, "payment-service")
    db_session.add(
        IndexingJob(id=uuid.uuid4(), repository_id=repo.id, status="completed")
    )
    await db_session.flush()

    graph_repository = Neo4jGraphRepository(get_driver())
    repository_id = str(repo.id)
    await graph_repository.replace_repository_graph(
        repository_id,
        GraphPayload(
            nodes=[
                GraphNode(
                    id=f"{repository_id}:repository",
                    labels=["Repository"],
                    properties={"repository_id": repository_id},
                )
            ]
        ),
    )
    try:
        state = await discover(
            request="Investigate payment-service for a retry bug",
            session=SessionContext(db=db_session, user_id=user.id),
            investigators=[GraphInvestigator()],
        )

        assert "payment-service" in {f.subject for f in state.ledger.facts_of("repository")}
        evidence_summaries = [e.summary for e in state.ledger.evidence]
        assert not any("re-index" in s.lower() for s in evidence_summaries)
    finally:
        await _clear_graph(repository_id)
