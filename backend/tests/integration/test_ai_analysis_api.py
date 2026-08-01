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
    MigrationAdvice,
    RegressionTest,
    ReleaseCoordinationPlan,
    SuggestedReviewer,
)
from app.core.crypto import encrypt_secret
from app.database.session import AsyncSessionLocal
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.github import GitHubApiError, GitHubVersionControlProvider, PostedComment
from app.integrations.interfaces import ChangedFile
from app.models.github_connection import GitHubConnection
from app.models.pull_request import PullRequest
from app.models.pull_request_ai_analysis import PullRequestAIAnalysis
from app.models.pull_request_analysis import PullRequestAnalysis
from app.models.repository import Repository
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
    # This fixture's repository is the only one tracked/indexed in
    # ai_test_setup - a real multi-repository plan is covered directly in
    # tests/unit/ai/test_schemas.py and tests/unit/ai/test_ai_analysis_service.py.
    # Here, deployment_order and repositories_to_notify are deliberately
    # empty, matching what a single-repository change should actually
    # produce (and what AIAnalysisService.grounded_in() would reduce a
    # self-referential plan to anyway).
    release_coordination_plan=ReleaseCoordinationPlan(
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
            "app.api.v1.routers.ai_analysis.StageAwareLLMProvider",
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
    # Single-repository change: no deployment order, nothing to notify.
    assert plan["deployment_order"] == []
    assert plan["repositories_to_notify"] == []
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


async def test_review_report_404_before_ai_analysis_exists(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    headers, _, pull_request_id = ai_test_setup

    response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/review-report", headers=headers
    )
    assert response.status_code == 404


async def test_review_report_renders_html_markdown_and_json(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    """After AI analysis runs, GET .../review-report renders each of the
    three supported formats from the persisted result, without invoking
    the LLM again."""
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
            "app.api.v1.routers.ai_analysis.StageAwareLLMProvider",
            return_value=mock_provider,
        ),
    ):
        post_response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/ai-analysis", headers=headers
        )
    assert post_response.status_code == 200

    html_response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/review-report", headers=headers
    )
    assert html_response.status_code == 200
    assert html_response.headers["content-type"].startswith("text/html")
    assert "This PR modifies the order event producer." in html_response.text
    assert "PR #42" in html_response.text
    assert "Not assessed" in html_response.text  # this fixture sets no merge_recommendation
    assert "<script>" in html_response.text

    md_response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/review-report?format=markdown",
        headers=headers,
    )
    assert md_response.status_code == 200
    assert md_response.headers["content-type"].startswith("text/markdown")
    assert "# PR Review Report" in md_response.text

    json_response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/review-report?format=json",
        headers=headers,
    )
    assert json_response.status_code == 200
    assert json_response.headers["content-type"].startswith("application/json")
    json_body = json_response.json()
    assert (
        json_body["executive_summary"]["summary"]
        == "This PR modifies the order event producer."
    )
    assert json_body["executive_summary"]["pull_request_number"] == 42


async def test_review_report_404s_for_another_users_pr(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    _, _, pull_request_id = ai_test_setup
    other_email = f"ai-report-other-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S105

    await client.post(
        "/api/v1/auth/register",
        json={"email": other_email, "password": password, "full_name": "Other User"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": other_email, "password": password}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/review-report", headers=other_headers
    )
    assert response.status_code == 404


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


async def test_post_investigate_returns_analysis_and_reasoning_log(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    """The Change Investigation Agent, run end-to-end through the real
    HTTP path: routing -> auth -> ownership -> agent -> persist -> response.
    Same fixture, same fake LLM result as the single-shot endpoint - only
    the extra `reasoning_log` and the agent's own tool decisions differ."""
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
        patch.object(
            GitHubVersionControlProvider,
            "get_diff",
            AsyncMock(return_value="--- a/x\n+++ b/x\n"),
        ),
        patch.object(
            GitHubVersionControlProvider,
            "get_recent_file_authors",
            AsyncMock(return_value={"OrderEventProducer.java": ["alice"]}),
        ),
        patch(
            "app.api.v1.routers.ai_analysis.StageAwareLLMProvider",
            return_value=mock_provider,
        ),
    ):
        response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/investigate", headers=headers
        )

    assert response.status_code == 200
    body = response.json()
    assert body["executive_summary"] == "This PR modifies the order event producer."
    assert body["confidence"]["score"] == 0.88

    reasoning_log = body["reasoning_log"]
    assert len(reasoning_log) >= 2
    tools_called = {step["tool_selected"] for step in reasoning_log if step["tool_selected"]}
    assert "read_dependency_graph" in tools_called
    assert "traverse_dependency_graph" in tools_called
    for step in reasoning_log:
        assert step["goal"]
        assert step["plan"]
        assert step["decision"]

    # Persists through the same path as POST .../ai-analysis, so GET
    # returns it too.
    get_response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/ai-analysis", headers=headers
    )
    assert get_response.status_code == 200
    assert get_response.json()["executive_summary"] == "This PR modifies the order event producer."


async def test_investigate_endpoint_404s_for_another_users_pr(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    _, _, pull_request_id = ai_test_setup
    other_email = f"investigate-other-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S105

    await client.post(
        "/api/v1/auth/register",
        json={"email": other_email, "password": password, "full_name": "Other User"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": other_email, "password": password}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.post(
        f"/api/v1/pull-requests/{pull_request_id}/investigate", headers=other_headers
    )

    assert response.status_code == 404


async def test_investigate_requires_authentication(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())

    response = await client.post(f"/api/v1/pull-requests/{fake_id}/investigate")

    assert response.status_code == 401


async def _connect_github(repository_id: str) -> None:
    """Inserts a real `GitHubConnection` row (real Fernet round-trip via
    `encrypt_secret`) for whichever user owns `repository_id` - no existing
    test in this file connects GitHub, so this is new."""
    async with AsyncSessionLocal() as session:
        repository = await session.get(Repository, uuid.UUID(repository_id))
        assert repository is not None
        session.add(
            GitHubConnection(
                user_id=repository.user_id,
                github_user_id="999",
                github_username="publish-review-tester",
                encrypted_access_token=encrypt_secret("gho_faketoken"),
            )
        )
        await session.commit()


async def _persist_ai_analysis(pull_request_id: str) -> None:
    """Writes a `PullRequestAIAnalysis` row directly - simulates an AI
    analysis (or Investigate run) having already completed, without
    actually invoking an LLM provider, matching `publish-review`'s own
    "never re-invoke the LLM" contract."""
    async with AsyncSessionLocal() as session:
        session.add(
            PullRequestAIAnalysis(
                pull_request_id=uuid.UUID(pull_request_id),
                executive_summary=_FAKE_AI_RESULT.executive_summary,
                breaking_changes=[bc.model_dump() for bc in _FAKE_AI_RESULT.breaking_changes],
                migration_advice=[ma.model_dump() for ma in _FAKE_AI_RESULT.migration_advice],
                suggested_reviewers=[sr.model_dump() for sr in _FAKE_AI_RESULT.suggested_reviewers],
                regression_tests=[rt.model_dump() for rt in _FAKE_AI_RESULT.regression_tests],
                release_coordination_plan=_FAKE_AI_RESULT.release_coordination_plan.model_dump(),
                confidence_score=_FAKE_AI_RESULT.confidence.score,
                confidence_reasoning=_FAKE_AI_RESULT.confidence.reasoning,
                prompt_version=_FAKE_AI_RESULT.prompt_version,
            )
        )
        await session.commit()


async def _persist_deterministic_analysis(pull_request_id: str) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            PullRequestAnalysis(
                pull_request_id=uuid.UUID(pull_request_id),
                risk="HIGH",
                directly_impacted_services=[
                    {
                        "id": "1",
                        "name": "order-service",
                        "node_type": "Component",
                        "repository_id": "1",
                    }
                ],
                indirectly_impacted_services=[
                    {
                        "id": "2",
                        "name": "payment-service",
                        "node_type": "Component",
                        "repository_id": "2",
                    }
                ],
            )
        )
        await session.commit()


async def test_publish_review_404_before_ai_analysis_exists(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    headers, repository_id, pull_request_id = ai_test_setup
    await _connect_github(repository_id)

    response = await client.post(
        f"/api/v1/pull-requests/{pull_request_id}/publish-review", headers=headers
    )

    assert response.status_code == 404


async def test_publish_review_401_when_github_not_connected(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    headers, _, pull_request_id = ai_test_setup
    await _persist_ai_analysis(pull_request_id)

    response = await client.post(
        f"/api/v1/pull-requests/{pull_request_id}/publish-review", headers=headers
    )

    assert response.status_code == 401


async def test_publish_review_happy_path_without_deterministic_analysis(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    """No PullRequestAnalysis row exists - the comment should still publish,
    gracefully degrading to risk="UNKNOWN" and empty impacted-service lists
    rather than blocking on the deterministic analysis being optional."""
    headers, repository_id, pull_request_id = ai_test_setup
    await _connect_github(repository_id)
    await _persist_ai_analysis(pull_request_id)

    mock_post = AsyncMock(
        return_value=PostedComment(
            id=42, html_url="https://github.com/local/ai-test-repo/pull/42#issuecomment-42"
        )
    )
    with patch.object(GitHubVersionControlProvider, "post_pull_request_comment", mock_post):
        response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/publish-review", headers=headers
        )

    assert response.status_code == 200
    body = response.json()
    assert body["comment_id"] == 42
    assert body["comment_url"] == "https://github.com/local/ai-test-repo/pull/42#issuecomment-42"

    mock_post.assert_awaited_once()
    call_kwargs = mock_post.await_args.kwargs
    assert call_kwargs["owner"] == "local"
    assert call_kwargs["repo"] == "ai-test-repo"
    assert call_kwargs["pull_number"] == 42
    assert call_kwargs["access_token"] == "gho_faketoken"
    assert "This PR modifies the order event producer." in call_kwargs["body"]
    assert "**UNKNOWN**" in call_kwargs["body"]
    assert "**Directly impacted:** None." in call_kwargs["body"]


async def test_publish_review_uses_deterministic_risk_and_impacted_services(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    headers, repository_id, pull_request_id = ai_test_setup
    await _connect_github(repository_id)
    await _persist_ai_analysis(pull_request_id)
    await _persist_deterministic_analysis(pull_request_id)

    mock_post = AsyncMock(
        return_value=PostedComment(id=1, html_url="https://github.com/local/ai-test-repo/pull/42")
    )
    with patch.object(GitHubVersionControlProvider, "post_pull_request_comment", mock_post):
        response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/publish-review", headers=headers
        )

    assert response.status_code == 200
    call_kwargs = mock_post.await_args.kwargs
    assert "**HIGH**" in call_kwargs["body"]
    assert "**Directly impacted:** order-service" in call_kwargs["body"]
    assert "**Indirectly impacted (cross-repository):** payment-service" in call_kwargs["body"]


async def test_publish_review_github_failure_is_not_swallowed(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    headers, repository_id, pull_request_id = ai_test_setup
    await _connect_github(repository_id)
    await _persist_ai_analysis(pull_request_id)

    mock_post = AsyncMock(side_effect=GitHubApiError("GitHub said no."))
    with patch.object(GitHubVersionControlProvider, "post_pull_request_comment", mock_post):
        response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/publish-review", headers=headers
        )

    assert response.status_code == 502


async def test_publish_review_404s_for_another_users_pr(
    client: AsyncClient, ai_test_setup: tuple[dict[str, str], str, str]
) -> None:
    _, repository_id, pull_request_id = ai_test_setup
    await _connect_github(repository_id)
    await _persist_ai_analysis(pull_request_id)

    other_email = f"publish-other-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S105
    await client.post(
        "/api/v1/auth/register",
        json={"email": other_email, "password": password, "full_name": "Other User"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": other_email, "password": password}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.post(
        f"/api/v1/pull-requests/{pull_request_id}/publish-review", headers=other_headers
    )

    assert response.status_code == 404


async def test_publish_review_requires_authentication(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())

    response = await client.post(f"/api/v1/pull-requests/{fake_id}/publish-review")

    assert response.status_code == 401
