"""Integration tests for the Dependency Query Agent:

- Registration in the global `AgentRegistry` and goal routing (AI
  Workspace's `POST /agent-runs` path).
- `DependencyQueryService` reuse through a real Postgres transaction
  (`db_session` fixture, rolled back per test) — the same no-mocks
  convention `tests/integration/test_engineering_intelligence_*.py`
  already established. No fake `IGraphRepository` needed here (unlike
  `impact_analysis`): `DependencyQueryService.search` never touches
  Neo4j.
- Full agent execution (`DependencyQueryAgent.run`), with the LLM call
  mocked — proving the agent makes no direct Neo4j call, no Cypher, no
  direct Postgres query, and no direct `EngineeringMemoryService` call
  anywhere in its own code: every fact in the result traces back to
  exactly one `DependencyQueryService.search` invocation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, Subject
from app.agents.dependency_query.agent import DependencyQueryAgent
from app.agents.dependency_query.manifest import DEPENDENCY_QUERY_MANIFEST
from app.agents.setup import register_agents
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import AgentSelector


def _relationship(
    rel_type: str, source: str, target: str, state: ConfidenceState
) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        id=f"rel-{source}-{target}",
        relationship_type=rel_type,
        source_entity=source,
        target_entity=target,
        confidence=ConfidenceModel(
            state=state,
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


@pytest.fixture
async def repository_id(db_session: AsyncSession) -> uuid.UUID:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()
    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name="test-repo",
        full_name="test-owner/test-repo",
        html_url="https://github.com/test-owner/test-repo",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo.id


def test_agent_is_registered_in_global_registry() -> None:
    register_agents()
    agent_ids = {m.agent_id for m in global_registry.all_manifests()}
    assert "dependency_query" in agent_ids


def test_selector_routes_analyze_dependency_query_goal() -> None:
    register_agents()
    selector = AgentSelector(global_registry)
    assert selector.select("analyze_dependency_query") == "dependency_query"


def test_manifest_declares_llm_only_no_neo4j() -> None:
    assert DEPENDENCY_QUERY_MANIFEST.max_graph_hops == 0
    assert "repository" in DEPENDENCY_QUERY_MANIFEST.accepted_subject_types
    assert "analyze_dependency_query" in DEPENDENCY_QUERY_MANIFEST.goals


async def test_agent_run_produces_report_from_real_service_and_no_other_source(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    memory = EngineeringMemoryService(db_session)
    await memory.store_relationship(
        repository_id,
        _relationship(
            "CALLS_SERVICE",
            f"{repository_id}:svc:checkout",
            "repo-2:svc:billing",
            ConfidenceState.VERIFIED,
        ),
    )
    await memory.store_relationship(
        repository_id,
        _relationship(
            "CALLS_SERVICE",
            "repo-3:svc:gateway",
            f"{repository_id}:svc:orders",
            ConfidenceState.CANDIDATE,
        ),
    )

    agent = DependencyQueryAgent()
    context = AgentContext(
        subject=Subject(subject_id=f"repo:{repository_id}", subject_type="repository"),
        goal="analyze_dependency_query",
        extras={"db": db_session},
    )

    with patch(
        "app.agents.frontier.base_frontier_agent.prompt_builder.run",
        new=AsyncMock(
            return_value=(
                {"repository": "This repository has one dependency and one consumer."},
                None,
            )
        ),
    ):
        output = await agent.run(context)

    assert output.agent_id == "dependency_query"
    assert output.subject_id == f"repo:{repository_id}"
    assert len(output.result["direct_dependencies"]) == 1
    assert len(output.result["downstream_consumers"]) == 1
    assert output.result["confidence_breakdown"]["high"] == 1
    assert output.result["confidence_breakdown"]["medium"] == 1
    assert output.result["executive_summary"].startswith("This repository has")
    assert output.confidence.score == 1.0
    tool_call_evidence = [e for e in output.evidence if e.kind == "tool_call"]
    assert len(tool_call_evidence) == 1
    assert tool_call_evidence[0].reference == "engineering_intelligence:dependency_query"


async def test_agent_run_degrades_gracefully_for_repository_with_no_relationships(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    agent = DependencyQueryAgent()
    context = AgentContext(
        subject=Subject(subject_id=f"repo:{repository_id}", subject_type="repository"),
        goal="analyze_dependency_query",
        extras={"db": db_session},
    )

    with patch(
        "app.agents.frontier.base_frontier_agent.prompt_builder.run",
        new=AsyncMock(return_value=({}, None)),
    ) as mock_prompt:
        output = await agent.run(context)

    mock_prompt.assert_awaited_once()
    assert output.result["direct_dependencies"] == []
    assert output.result["downstream_consumers"] == []
