"""Migration Assistant — a "migration"-mode conversation on the same
`POST /conversations` endpoints Ask GraphForge already uses. See
`app.services.migration_grounding` and `ConversationService`'s own
docstrings for what's grounded (real graph) vs. reasoned (LLM over
already-gathered facts) each turn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "ada@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada",
}
USER_B = {
    "email": "grace@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Grace",
}

REPO_INGESTION = {
    "provider_repo_id": "4001",
    "owner": "ada",
    "name": "customer-ingestion",
    "full_name": "ada/customer-ingestion",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/customer-ingestion",
}
REPO_REPORTING = {
    "provider_repo_id": "4002",
    "owner": "ada",
    "name": "customer-reporting",
    "full_name": "ada/customer-reporting",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/customer-reporting",
}


def _relationship(rel_type: str, source: str, target: str) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        id=f"rel-{source}-{target}",
        relationship_type=rel_type,
        source_entity=source,
        target_entity=target,
        confidence=ConfidenceModel(
            state=ConfidenceState.LIKELY,
            distinct_confirming_source_types=1,
            confirming_source_types=frozenset({"code_annotation_literal"}),
            max_confirming_reliability_tier=3,
            contradiction_count=0,
            computed_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
            formula_version="v1",
        ),
        hypothesis_ids=("hyp-1",),
        provenance=(
            Provenance(
                generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
                produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
                pack_id="pack-1",
                pack_version="v1",
                run_id="run-1",
            ),
        ),
    )


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


async def _select_repo(
    db_client: AsyncClient, headers: dict[str, str], repo: dict[str, str]
) -> str:
    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [repo]}
    )
    return str(select_response.json()[0]["id"])


async def _select_repos(
    db_client: AsyncClient, headers: dict[str, str], repos: list[dict[str, str]]
) -> list[str]:
    """Selects every repo in a single request — a second `POST
    /repositories` call against the same `db_session`-backed test client
    otherwise leaves the first call's row invisible to a direct read on
    that session afterward (a quirk of the transactional test fixture's
    savepoint handling, not of the endpoint itself)."""
    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": repos}
    )
    return [str(r["id"]) for r in select_response.json()]


async def _seed_postgres_dependency(db_session: AsyncSession, repo_id: str) -> None:
    """A relationship whose entity text mentions PostgreSQL, in
    Engineering Memory (Postgres) — what `migration_grounding.
    _direct_repository_ids` token-matches against. Separate data source
    from the Neo4j graph (see that module's own docstring)."""
    memory = EngineeringMemoryService(db_session)
    await memory.store_relationship(
        uuid.UUID(repo_id),
        _relationship(
            "DEPENDS_ON",
            f"{repo_id}:database:postgresql",
            f"{repo_id}:service:customer-ingestion-api",
        ),
    )


async def _seed_graph_chain(repo_id: str, downstream_repo_id: str | None = None) -> None:
    """`{repo_id}:repository` -CALLS_SERVICE-> `{downstream_repo_id}:repository`
    when a downstream repo is given — `CALLS_SERVICE` is one of
    `impact_analysis_service._IMPACT_EDGE_TYPES`, the whitelist
    `compute_blast_radius`'s traversal actually follows (unlike e.g.
    `CONTAINS`, which it ignores); a repo with no downstream still gets a
    lone `:repository` node so `compute_blast_radius` has something to
    seed from without erroring."""
    nodes = [GraphNode(id=f"{repo_id}:repository", labels=["GraphNode", "Repository"])]
    edges: list[GraphEdge] = []
    if downstream_repo_id:
        nodes.append(
            GraphNode(id=f"{downstream_repo_id}:repository", labels=["GraphNode", "Repository"])
        )
        edges.append(
            GraphEdge(
                source_id=f"{repo_id}:repository",
                target_id=f"{downstream_repo_id}:repository",
                type="CALLS_SERVICE",
            )
        )
    graph_repository = Neo4jGraphRepository(get_driver())
    await graph_repository.replace_repository_graph(repo_id, GraphPayload(nodes=nodes, edges=edges))
    if downstream_repo_id:
        await graph_repository.replace_repository_graph(
            downstream_repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(
                        id=f"{downstream_repo_id}:repository", labels=["GraphNode", "Repository"]
                    )
                ],
                edges=[],
            ),
        )


class TestMigrationAssistant:
    async def test_basic_migration_scope_is_dependency_graph_grounded(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id, reporting_id = await _select_repos(
            db_client, headers, [REPO_INGESTION, REPO_REPORTING]
        )

        await _seed_postgres_dependency(db_session, repo_id)
        await _seed_graph_chain(repo_id, downstream_repo_id=reporting_id)

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "question": (
                    "We want to migrate the customer ingestion database from "
                    "PostgreSQL to BigQuery. What will be affected?"
                ),
                "mode": "migration",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["mode"] == "migration"
        assistant = body["messages"][-1]
        assert assistant["role"] == "assistant"
        payload = assistant["payload"]
        assert payload is not None
        migration = payload["migration"]
        assert migration is not None
        assert migration["source_technology"].lower() == "postgresql"
        assert any("ingestion" in name.lower() for name in migration["direct"])
        # dependency analysis: direct vs indirect are correctly distinguished
        assert any("reporting" in name.lower() for name in migration["indirect"])
        assert set(migration["direct"]).isdisjoint(migration["indirect"])
        # evidence references the dependency graph, not a fabricated source
        assert any(e["source"] == "Dependency Graph" for e in payload["evidence"])
        assert all(e["provenance"] in {"fact", "derived"} for e in payload["evidence"])
        return body["id"]

    async def test_multi_turn_constraint_updates_the_assessment(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id, reporting_id = await _select_repos(
            db_client, headers, [REPO_INGESTION, REPO_REPORTING]
        )
        await _seed_postgres_dependency(db_session, repo_id)
        await _seed_graph_chain(repo_id, downstream_repo_id=reporting_id)

        start = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "question": "Migrate the customer ingestion service from PostgreSQL to BigQuery.",
                "mode": "migration",
            },
        )
        conversation_id = start.json()["id"]

        follow_up = await db_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"message": "What if we migrate Reporting first?"},
        )

        assert follow_up.status_code == 200
        body = follow_up.json()
        assert len(body["messages"]) == 4
        last = body["messages"][-1]
        assert last["role"] == "assistant"
        assert last["content"].strip() != ""
        # A pure follow-up must not re-run migration grounding — the scope
        # carries forward from state rather than being silently dropped.
        assert last["payload"]["migration"] is not None
        assert last["payload"]["migration"]["source_technology"].lower() == "postgresql"

    async def test_ambiguous_migration_asks_for_clarification_instead_of_guessing(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "Help me plan a migration.", "mode": "migration"},
        )

        assert response.status_code == 201
        body = response.json()
        assistant = body["messages"][-1]
        assert assistant["payload"]["intent"] == "clarification"
        assert assistant["payload"]["migration"] is None
        assert "what are you migrating" in assistant["content"].lower()

    async def test_empty_result_is_reported_honestly_not_fabricated(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        await _select_repo(db_client, headers, REPO_INGESTION)
        # No relationship seeded anywhere that mentions "Oracle" — nothing
        # in the graph to ground a migration scope on.

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "Migrate our Oracle database to Snowflake.", "mode": "migration"},
        )

        assert response.status_code == 201
        body = response.json()
        assistant = body["messages"][-1]
        assert assistant["payload"]["migration"] is None
        assert assistant["payload"]["impact"] is None
        assert "couldn't find" in assistant["content"].lower()

    async def test_does_not_expose_another_users_repositories(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        repo_id = await _select_repo(db_client, headers_a, REPO_INGESTION)
        await _seed_postgres_dependency(db_session, repo_id)
        await _seed_graph_chain(repo_id)

        token_b = await _register_and_get_token(db_client, USER_B)
        headers_b = {"Authorization": f"Bearer {token_b}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers_b,
            json={
                "question": "Migrate the customer ingestion database from PostgreSQL to BigQuery.",
                "mode": "migration",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assistant = body["messages"][-1]
        # User B has no indexed repositories at all — Migration Assistant
        # must not find User A's PostgreSQL relationship on their behalf.
        assert assistant["payload"]["migration"] is None
        assert "couldn't find" in assistant["content"].lower()

    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(
            "/api/v1/conversations",
            json={"question": "Migrate PostgreSQL to BigQuery.", "mode": "migration"},
        )
        assert response.status_code == 401
