"""`POST /pull-requests/{id}/ai-analysis` and `GET /pull-requests/{id}/ai-analysis`.

Reuses the same `indexed_repository_with_pr` fixture from the deterministic
tests, and patches the LLM provider to avoid real OpenAI calls while testing
the full HTTP path: routing → auth → ownership → service → persist → response.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.ai.schemas.analysis_result import (
    AIAnalysisResult,
    BreakingChange,
    ConfidenceScore,
    DeploymentStep,
    MigrationAdvice,
    RegressionTest,
    ReleaseCoordinationPlan,
    RepositoryToNotify,
    SuggestedReviewer,
)
from app.database.session import AsyncSessionLocal
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.github import GitHubVersionControlProvider
from app.integrations.interfaces import ChangedFile
from app.models.pull_request import PullRequest
from app.models.user import User

pytestmark = pytest.mark.asyncio

_FAKE_AI_RESULT = AIAnalysisResult(
    executive_summary="This PR modifies the order event producer.",
    breaking_changes=[
        BreakingChange(
            component="OrderEventProducer",
            description="Kafka topic name changed",
            severity="high",
            confidence=ConfidenceScore(score=0.9, reasoning="Topic constant renamed"),
        ),
    ],
    migration_advice=[
        MigrationAdvice(
            component="OrderEventProducer",
            advice="Update all consumers to use new topic name",
            priority="high",
        ),
    ],
    suggested_reviewers=[
        SuggestedReviewer(
            reviewer="alice",
            reason="Primary owner of messaging infrastructure",
            confidence=ConfidenceScore(score=0.85, reasoning="Commit history analysis"),
        ),
    ],
    regression_tests=[
        RegressionTest(
            component="OrderEventProducer",
            test_description="Verify event delivery on new topic",
            priority="high",
            confidence=ConfidenceScore(score=0.8, reasoning="Critical path"),
        ),
    ],
    release_coordination_plan=ReleaseCoordinationPlan(
        deployment_order=[
            DeploymentStep(
                order=1,
                repository="ai-test-repo",
                action="Deploy first",
                reason="Producer of the changed topic",
            ),
        ],
        repositories_to_notify=[
            RepositoryToNotify(
                repository="ai-test-repo",
                reason="Owns the changed producer",
                urgency="before deployment",
            ),
        ],
        rollout_strategy="Ship behind a feature flag.",
        backward_compatibility_advice="Keep the Kafka payload schema backward compatible.",
        communication_summary="No other tracked repository consumes this topic.",
        rollout_risks=["Kafka deserialization failures during rollout"],
    ),
    confidence=ConfidenceScore(score=0.88, reasoning="High confidence analysis"),
    prompt_version="1.0.0",
)


@pytest.fixture
async def ai_test_setup(
    client: AsyncClient, spring_boot_git_repo: Path
) -> AsyncGenerator[tuple[dict[str, str], str, str], None]:
    """A tracked, indexed repository with one PR row.

    Same pattern as the deterministic test fixture: creates a real user,
    repository, indexing job, and PR. Cleans up after.
    """
    email = f"ai-analyst-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S105

    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "AI Analyst"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    select_response = await client.post(
        "/api/v1/repositories",
        headers=headers,
        json={
            "repositories": [
                {
                    "provider_repo_id": "ai-test-1",
                    "owner": "local",
                    "name": "ai-test-repo",
                    "full_name": "local/ai-test-repo",
                    "private": False,
                    "default_branch": "main",
                    "html_url": str(spring_boot_git_repo),
                }
            ]
        },
    )
    repository_id = select_response.json()[0]["id"]

    index_response = await client.post(
        f"/api/v1/repositories/{repository_id}/index", headers=headers
    )
    assert index_response.status_code == 202

    async with AsyncSessionLocal() as session:
        pull_request = PullRequest(
            repository_id=uuid.UUID(repository_id),
            github_pr_id="8001",
            number=42,
            title="Refactor producer",
            state="open",
            is_draft=False,
            author_login="tester",
            html_url="https://example.invalid/pr/42",
            head_ref="feature/refactor",
            head_sha="cafebabe",
            base_ref="main",
            github_created_at=datetime.now(UTC),
            github_updated_at=datetime.now(UTC),
        )
        session.add(pull_request)
        await session.commit()
        await session.refresh(pull_request)
        pull_request_id = str(pull_request.id)

    yield headers, repository_id, pull_request_id

    async with AsyncSessionLocal() as session:
        await session.execute(delete(User).where(User.email == email))
        await session.commit()
    await Neo4jGraphRepository(get_driver()).replace_repository_graph(repository_id, GraphPayload())


async def test_get_ai_analysis_before_running_is_404(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    headers, _, pull_request_id = ai_test_setup

    response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/ai-analysis", headers=headers
    )
    assert response.status_code == 404


async def test_post_ai_analysis_then_get(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    headers, _, pull_request_id = ai_test_setup

    mock_provider = AsyncMock()
    mock_provider.analyze = AsyncMock(return_value=_FAKE_AI_RESULT)

    with (
        patch.object(
            GitHubVersionControlProvider,
            "list_changed_files",
            AsyncMock(
                return_value=[
                    ChangedFile(
                        path="src/main/java/com/example/orders/OrderEventProducer.java",
                        status="modified",
                    )
                ]
            ),
        ),
        patch(
            "app.api.v1.routers.ai_analysis.create_llm_provider",
            return_value=mock_provider,
        ),
    ):
        post_response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/ai-analysis", headers=headers
        )

    assert post_response.status_code == 200
    body = post_response.json()
    assert body["executive_summary"] == "This PR modifies the order event producer."
    assert len(body["breaking_changes"]) == 1
    assert body["breaking_changes"][0]["component"] == "OrderEventProducer"
    assert body["confidence"]["score"] == 0.88
    assert body["prompt_version"] == "1.0.0"

    # Release Coordination Plan is AI-enriched output, only returned live -
    # never persisted this iteration (see ADR 0009 / this feature's scope).
    plan = body["release_coordination_plan"]
    assert len(plan["deployment_order"]) == 1
    assert plan["deployment_order"][0]["repository"] == "ai-test-repo"
    assert len(plan["repositories_to_notify"]) == 1
    assert plan["rollout_strategy"] == "Ship behind a feature flag."
    assert plan["rollout_risks"] == ["Kafka deserialization failures during rollout"]

    # GET returns the persisted analysis, which does NOT include the
    # release coordination plan - it's ephemeral, regenerated fresh on
    # every POST rather than stored.
    get_response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/ai-analysis", headers=headers
    )
    assert get_response.status_code == 200
    get_body = get_response.json()
    assert get_body["executive_summary"] == "This PR modifies the order event producer."
    assert get_body["confidence_score"] == 0.88
    assert get_body["prompt_version"] == "1.0.0"
    assert "id" in get_body
    assert get_body["pull_request_id"] == pull_request_id
    assert "release_coordination_plan" not in get_body


async def test_ai_analysis_endpoint_404s_for_another_users_pr(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    _, _, pull_request_id = ai_test_setup
    other_email = f"ai-other-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S105

    await client.post(
        "/api/v1/auth/register",
        json={"email": other_email, "password": password, "full_name": "Other User"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": other_email, "password": password}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    post_response = await client.post(
        f"/api/v1/pull-requests/{pull_request_id}/ai-analysis", headers=other_headers
    )
    get_response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/ai-analysis", headers=other_headers
    )

    assert post_response.status_code == 404
    assert get_response.status_code == 404


async def test_ai_analysis_requires_authentication(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())

    post_response = await client.post(f"/api/v1/pull-requests/{fake_id}/ai-analysis")
    get_response = await client.get(f"/api/v1/pull-requests/{fake_id}/ai-analysis")

    assert post_response.status_code == 401
    assert get_response.status_code == 401
