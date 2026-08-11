"""Security regression coverage — cross-tenant repository exposure (P0).

Root cause (see the read-only investigation this fixes): `RepositoryDiscoveryTool`
(app.agents.development.tools) and `TestRepositoryDiscoveryTool`
(app.agents.testing.tools) issued `select(Repository)` with no `user_id`
filter, so a standalone Development or Testing run (no prior
`context_discovery` workflow stage) returned *every* tracked repository from
*every* user — names, owners, and their Neo4j architecture data all reached
the LLM prompt and the run's visible result for whichever user happened to
run the agent.

This file proves the fix at three levels, matching the deliverables asked
for in the investigation follow-up:

1. DB-level (`test_repository_discovery_tool_is_tenant_scoped_at_the_db_level`,
   `test_test_repository_discovery_tool_is_tenant_scoped_at_the_db_level`) —
   a real, non-mocked Postgres session with two users' repositories, proving
   the `WHERE user_id = ...` clause is actually applied. Unit tests that mock
   `AsyncSession.execute` cannot prove this — they pass regardless of what
   `select(...)` was constructed with, which is exactly how the original bug
   shipped with 100% green tests.
2. Agent-level standalone isolation (`test_standalone_development_run_...`,
   `test_standalone_testing_run_...`) — runs the real agent against the real
   DB, with only Neo4j and the LLM mocked, and asserts the foreign
   repository's id is never even passed to `graph_repository.get_nodes_by_label`/
   `get_full_graph` — i.e. the boundary is enforced *before* graph traversal,
   not filtered out of the result afterward.
3. Workflow-path regression
   (`test_workflow_development_and_testing_stages_never_see_a_foreign_repository`)
   — protects the existing, already-correct `context_discovery`-reuse branch
   (see `DevelopmentAgent`/`TestingAgent`'s own docstrings) from ever
   regressing to the unscoped fallback path.

Direct-repository-authorization coverage (a malicious user requesting another
tenant's repository by id) already exists for repository-scoped endpoints —
see `test_api_intelligence_cross_user_isolation.py`,
`test_documentation_cross_user_isolation.py`, and the `/repositories/{id}/impact`
check added here as `test_direct_repository_id_access_is_rejected_across_tenants`.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, Subject
from app.agents.development.agent import DevelopmentAgent
from app.agents.development.tools import RepositoryDiscoveryTool
from app.agents.testing.agent import TestPlanningAgent
from app.agents.testing.tools import TestRepositoryDiscoveryTool
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared fixtures — two real users, one real repository each
# ---------------------------------------------------------------------------


def _make_user(email: str) -> User:
    return User(id=uuid.uuid4(), email=email, full_name=email.split("@")[0], role="user")


def _make_repository(*, user_id: uuid.UUID, owner: str, name: str) -> Repository:
    return Repository(
        id=uuid.uuid4(),
        user_id=user_id,
        github_repo_id=f"gh-{uuid.uuid4()}",
        source="github",
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        private=False,
        default_branch="main",
        html_url=f"https://github.com/{owner}/{name}",
    )


@pytest.fixture
async def two_tenants(
    db_session: AsyncSession,
) -> tuple[User, Repository, User, Repository]:
    """User A / Repository A (the requesting tenant) and User B / Repository B
    (the foreign tenant that must never be visible to A)."""
    user_a = _make_user(f"tenant-a-{uuid.uuid4()}@example.com")
    user_b = _make_user(f"tenant-b-{uuid.uuid4()}@example.com")
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    repo_a = _make_repository(user_id=user_a.id, owner="tenant-a", name="widgets-service")
    repo_b = _make_repository(user_id=user_b.id, owner="tenant-b", name="secret-service")
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    return user_a, repo_a, user_b, repo_b


def _always_healthy_graph_repo() -> AsyncMock:
    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    return mock_graph_repo


# ---------------------------------------------------------------------------
# 1. DB-level — the non-mocked regression test the original bug needed
# ---------------------------------------------------------------------------


async def test_repository_discovery_tool_is_tenant_scoped_at_the_db_level(
    db_session: AsyncSession,
    two_tenants: tuple[User, Repository, User, Repository],
) -> None:
    user_a, repo_a, user_b, repo_b = two_tenants

    tool = RepositoryDiscoveryTool(
        db=db_session, graph_repository=_always_healthy_graph_repo(), user_id=user_a.id
    )
    obs = await tool.execute()

    assert obs.succeeded is True
    names = {r["name"] for r in obs.data["indexed_repositories"]}
    assert names == {repo_a.name}
    assert repo_b.name not in names
    assert repo_b.owner not in {r["owner"] for r in obs.data["indexed_repositories"]}
    # Also proves the "N tracked" count itself no longer leaks the global
    # total across every account — it must reflect only user_a's own rows.
    assert obs.data["total_tracked"] == 1


async def test_test_repository_discovery_tool_is_tenant_scoped_at_the_db_level(
    db_session: AsyncSession,
    two_tenants: tuple[User, Repository, User, Repository],
) -> None:
    user_a, repo_a, user_b, repo_b = two_tenants

    tool = TestRepositoryDiscoveryTool(
        db=db_session, graph_repository=_always_healthy_graph_repo(), user_id=user_a.id
    )
    obs = await tool.execute()

    assert obs.succeeded is True
    names = {r["name"] for r in obs.data["indexed_repositories"]}
    assert names == {repo_a.name}
    assert repo_b.name not in names
    assert obs.data["total_tracked"] == 1


async def test_repository_discovery_tool_scopes_symmetrically_for_the_other_tenant(
    db_session: AsyncSession,
    two_tenants: tuple[User, Repository, User, Repository],
) -> None:
    """Same tool, run as User B — proves this isn't one-directional luck
    (e.g. an accidental ordering/limit that happens to favor whichever user
    was seeded first)."""
    user_a, repo_a, user_b, repo_b = two_tenants

    tool = RepositoryDiscoveryTool(
        db=db_session, graph_repository=_always_healthy_graph_repo(), user_id=user_b.id
    )
    obs = await tool.execute()

    names = {r["name"] for r in obs.data["indexed_repositories"]}
    assert names == {repo_b.name}
    assert repo_a.name not in names


# ---------------------------------------------------------------------------
# 2. Agent-level standalone isolation — proves the boundary is enforced
#    BEFORE graph traversal / evidence / the LLM prompt, not filtered out
#    of the final result afterward.
# ---------------------------------------------------------------------------

_DEVELOPMENT_LLM_RESPONSE = json.dumps(
    {
        "executive_summary": "A blueprint.",
        "repositories": [],
        "components": [],
        "dependencies": [],
        "reusable_implementations": [],
        "implementation_phases": [],
        "risks": [],
        "graph_context_used": True,
    }
)

_TESTING_LLM_RESPONSE = json.dumps(
    {
        "executive_summary": "A test strategy.",
        "test_scope": {"in_scope": [], "out_of_scope": []},
        "affected_repositories": [],
        "affected_components": [],
        "regression_tests": [],
        "integration_tests": [],
        "edge_cases": [],
        "environment_requirements": [],
        "execution_order": [],
        "automation_candidates": [],
        "manual_validations": [],
        "risks": [],
        "recommendations": [],
        "graph_context_used": True,
    }
)


def _agent_context(*, goal: str, db: AsyncSession, user_id: uuid.UUID) -> AgentContext:
    subject = Subject(
        subject_id=f"freetext:{uuid.uuid4()}",
        subject_type="freetext",
        display_name="Add rate limiting to the public API",
    )
    return AgentContext(subject=subject, goal=goal, extras={"db": db, "user_id": user_id})


async def test_standalone_development_run_never_reaches_a_foreign_repository(
    db_session: AsyncSession,
    two_tenants: tuple[User, Repository, User, Repository],
) -> None:
    user_a, repo_a, user_b, repo_b = two_tenants
    context = _agent_context(goal="develop_change_plan", db=db_session, user_id=user_a.id)

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_DEVELOPMENT_LLM_RESPONSE),
        ),
    ):
        output = await DevelopmentAgent().run(context)

    # The requesting tenant's own repository is the only one that ever
    # reached graph traversal.
    for call in mock_graph_repo.get_nodes_by_label.await_args_list:
        assert call.args[0] == str(repo_a.id)
        assert call.args[0] != str(repo_b.id)
    for call in mock_graph_repo.get_full_graph.await_args_list:
        assert call.args[0] == str(repo_a.id)
        assert call.args[0] != str(repo_b.id)

    # Never surfaced in the run's visible result or evidence either.
    result_text = json.dumps(output.result)
    evidence_text = json.dumps([e.model_dump() for e in output.evidence])
    assert repo_b.name not in result_text
    assert repo_b.owner not in result_text
    assert str(repo_b.id) not in result_text
    assert repo_b.name not in evidence_text
    assert str(repo_b.id) not in evidence_text
    assert repo_b.full_name not in result_text


async def test_standalone_testing_run_never_reaches_a_foreign_repository(
    db_session: AsyncSession,
    two_tenants: tuple[User, Repository, User, Repository],
) -> None:
    user_a, repo_a, user_b, repo_b = two_tenants
    context = _agent_context(goal="plan_tests", db=db_session, user_id=user_a.id)

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))

    with (
        patch("app.agents.testing.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.testing.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.testing.agent._call_llm",
            new=AsyncMock(return_value=_TESTING_LLM_RESPONSE),
        ),
    ):
        output = await TestPlanningAgent().run(context)

    for call in mock_graph_repo.get_nodes_by_label.await_args_list:
        assert call.args[0] == str(repo_a.id)
        assert call.args[0] != str(repo_b.id)
    for call in mock_graph_repo.get_full_graph.await_args_list:
        assert call.args[0] == str(repo_a.id)
        assert call.args[0] != str(repo_b.id)

    result_text = json.dumps(output.result)
    evidence_text = json.dumps([e.model_dump() for e in output.evidence])
    assert repo_b.name not in result_text
    assert repo_b.owner not in result_text
    assert str(repo_b.id) not in result_text
    assert repo_b.name not in evidence_text
    assert str(repo_b.id) not in evidence_text


async def test_standalone_development_run_still_sees_its_own_repository(
    db_session: AsyncSession,
    two_tenants: tuple[User, Repository, User, Repository],
) -> None:
    """The fix must not regress the authenticated user's own data — the
    agent should still discover and traverse the requesting tenant's own
    repository exactly as before."""
    user_a, repo_a, user_b, repo_b = two_tenants
    context = _agent_context(goal="develop_change_plan", db=db_session, user_id=user_a.id)

    component_node = GraphNode(
        id="c1",
        labels=["Component", "Controller"],
        properties={"name": "RateLimiterController", "file_path": "src/RateLimiter.java"},
    )
    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: [component_node] if label == "Component" else []
    )
    mock_graph_repo.get_full_graph = AsyncMock(
        return_value=GraphPayload(
            nodes=[], edges=[GraphEdge(source_id="c1", target_id="t1", type="CALLS", properties={})]
        )
    )

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_DEVELOPMENT_LLM_RESPONSE),
        ),
    ):
        output = await DevelopmentAgent().run(context)

    assert output.result["repositories_consulted"] == [repo_a.full_name]
    assert mock_graph_repo.get_nodes_by_label.await_count > 0
    tool_call_evidence = [e for e in output.evidence if e.reference == "discover_repositories"]
    assert tool_call_evidence
    assert "1 indexed repository out of 1 tracked" in tool_call_evidence[0].summary


# ---------------------------------------------------------------------------
# 3. Workflow-path regression — the context_discovery-reuse branch must
#    stay scoped, protecting it from ever falling through to the fixed
#    (but still user-supplied) fallback tools above.
# ---------------------------------------------------------------------------


async def test_workflow_development_and_testing_stages_never_see_a_foreign_repository(
    db_session: AsyncSession,
    two_tenants: tuple[User, Repository, User, Repository],
) -> None:
    """When Development/Testing run as part of a workflow, they read
    `context_discovery`'s already-computed result instead of calling
    RepositoryDiscoveryTool/TestRepositoryDiscoveryTool at all (see both
    agents' own docstrings). This proves that reused result — built here by
    hand, standing in for a real `context_discovery` stage output — cannot
    smuggle a foreign repository through either downstream stage, and that
    neither stage falls back to a fresh (unscoped-in-spirit) discovery call
    when workflow context is present.
    """
    user_a, repo_a, user_b, repo_b = two_tenants

    # A context_discovery stage result exactly like ContextDiscoveryAgent's
    # own (correctly `user_id`-scoped, via GetIndexedRepositoriesTool) would
    # produce for user_a alone.
    context_discovery_result: dict[str, object] = {
        "indexed_repositories": [
            {
                "id": str(repo_a.id),
                "name": repo_a.name,
                "owner": repo_a.owner,
                "full_name": repo_a.full_name,
            }
        ],
        "graph_components": [],
        "graph_topics": [],
    }

    class _FakeWorkflow:
        pass

    fake_workflow = _FakeWorkflow()

    subject = Subject(
        subject_id=f"freetext:{uuid.uuid4()}",
        subject_type="freetext",
        display_name="Add rate limiting to the public API",
    )

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))

    def _fake_get_stage_result(_workflow: object, stage: str) -> dict[str, object] | None:
        return context_discovery_result if stage == "context_discovery" else None

    dev_context = AgentContext(
        subject=subject,
        goal="develop_change_plan",
        extras={"db": db_session, "user_id": user_a.id, "workflow": fake_workflow},
    )
    test_context = AgentContext(
        subject=subject,
        goal="plan_tests",
        extras={"db": db_session, "user_id": user_a.id, "workflow": fake_workflow},
    )

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_DEVELOPMENT_LLM_RESPONSE),
        ),
        patch(
            "app.agents.development.agent.get_stage_result",
            side_effect=_fake_get_stage_result,
        ),
    ):
        dev_output = await DevelopmentAgent().run(dev_context)

    with (
        patch("app.agents.testing.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.testing.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.testing.agent._call_llm",
            new=AsyncMock(return_value=_TESTING_LLM_RESPONSE),
        ),
        patch(
            "app.agents.testing.agent.get_stage_result",
            side_effect=_fake_get_stage_result,
        ),
    ):
        test_output = await TestPlanningAgent().run(test_context)

    for output in (dev_output, test_output):
        result_text = json.dumps(output.result)
        evidence_text = json.dumps([e.model_dump() for e in output.evidence])
        assert repo_b.name not in result_text
        assert repo_b.name not in evidence_text
        assert str(repo_b.id) not in result_text

    assert dev_output.result["repositories_consulted"] == [repo_a.full_name]
    # Both stages must have read the context_discovery result rather than
    # falling back to their own discovery tool — the tool_call evidence
    # reference name is the observable difference between the two paths.
    dev_refs = {e.reference for e in dev_output.evidence}
    test_refs = {e.reference for e in test_output.evidence}
    assert "read_context_discovery_stage" in dev_refs
    assert "discover_repositories" not in dev_refs
    assert "read_context_discovery_stage" in test_refs
    assert "discover_test_repositories" not in test_refs


# ---------------------------------------------------------------------------
# Direct repository-id authorization — a malicious user probing another
# tenant's repository id directly, not via the discovery agents above.
# ---------------------------------------------------------------------------


async def _register_and_login(client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json=payload)
    login = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_direct_repository_id_access_is_rejected_across_tenants(
    db_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A malicious User A cannot fetch User B's blast radius (or any other
    repository-scoped resource) just by knowing/guessing B's repository id
    — `_get_owned_repository` 404s before any Neo4j call happens. This is
    the API-authorization boundary already confirmed intact by the
    investigation; this test is the concrete reproduction the investigation
    asked to keep verified alongside the two fixes above.
    """
    owner_payload = {
        "email": f"tenant-owner-{uuid.uuid4()}@example.com",
        "password": "correct-horse-battery-staple",
        "full_name": "Repo Owner",
    }
    intruder_payload = {
        "email": f"tenant-intruder-{uuid.uuid4()}@example.com",
        "password": "correct-horse-battery-staple",
        "full_name": "Repo Intruder",
    }
    owner_headers = await _register_and_login(db_client, owner_payload)
    intruder_headers = await _register_and_login(db_client, intruder_payload)

    me = await db_client.get("/api/v1/auth/me", headers=owner_headers)
    owner_id = uuid.UUID(me.json()["id"])

    repo = _make_repository(user_id=owner_id, owner="owner-org", name="private-repo")
    db_session.add(repo)
    await db_session.flush()

    resp = await db_client.get(f"/api/v1/repositories/{repo.id}/impact", headers=intruder_headers)
    assert resp.status_code == 404

    # A repository id that does not exist at all gets the same 404, not a
    # 500 or a silent empty success — same ownership check, same code path.
    missing_resp = await db_client.get(
        f"/api/v1/repositories/{uuid.uuid4()}/impact", headers=owner_headers
    )
    assert missing_resp.status_code == 404
