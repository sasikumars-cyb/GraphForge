"""KAN-33 — cross-user isolation for `/api/v1/pull-requests/{pull_request_id}
/{ai-analysis,investigate,publish-review,review-report}` (`ai_analysis.py`).

Every endpoint on this router calls `_get_owned_pull_request` as its
first line, before any LLM call, Neo4j read, or GitHub write — so a 404
test proves the gate without needing to exercise (or mock) any of that.
`run_ai_analysis`/`investigate_pull_request`'s success paths genuinely
invoke an LLM provider and are out of scope for an ownership test either
way; `publish_review`/`get_review_report`'s success paths need a
persisted `PullRequestAIAnalysis` row this file doesn't attempt to
construct, since the ownership gate is the only thing being verified
here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pull_request import PullRequest
from app.models.repository import Repository

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "ai-analysis-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "ai-analysis-intruder-b@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Intruder B",
}


async def _register_and_login(db_client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    await db_client.post("/api/v1/auth/register", json=payload)
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _owner_user_id(db_client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    me = await db_client.get("/api/v1/auth/me", headers=headers)
    return uuid.UUID(me.json()["id"])


@pytest.fixture
async def user_a_headers(db_client: AsyncClient) -> dict[str, str]:
    return await _register_and_login(db_client, USER_A)


@pytest.fixture
async def user_b_headers(db_client: AsyncClient) -> dict[str, str]:
    return await _register_and_login(db_client, USER_B)


@pytest.fixture
async def owned_pull_request(
    db_client: AsyncClient, db_session: AsyncSession, user_a_headers: dict[str, str]
) -> AsyncGenerator[PullRequest, None]:
    owner_id = await _owner_user_id(db_client, user_a_headers)
    repo = Repository(
        id=uuid.uuid4(),
        user_id=owner_id,
        github_repo_id="789012",
        source="github",
        owner="acme",
        name="sprockets",
        full_name="acme/sprockets",
        default_branch="main",
        html_url="https://github.com/acme/sprockets",
    )
    db_session.add(repo)
    await db_session.flush()

    now = datetime.now(UTC)
    pr = PullRequest(
        id=uuid.uuid4(),
        repository_id=repo.id,
        github_pr_id="pr-1",
        number=1,
        title="Add a sprocket",
        state="open",
        author_login="octocat",
        html_url="https://github.com/acme/sprockets/pull/1",
        head_ref="feature/sprocket",
        head_sha="abc123",
        base_ref="main",
        github_created_at=now,
        github_updated_at=now,
    )
    db_session.add(pr)
    await db_session.flush()
    yield pr


async def test_run_ai_analysis_404s_for_another_users_pull_request(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_pull_request: PullRequest
) -> None:
    resp = await db_client.post(
        f"/api/v1/pull-requests/{owned_pull_request.id}/ai-analysis", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_get_ai_analysis_404s_for_another_users_pull_request(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_pull_request: PullRequest
) -> None:
    resp = await db_client.get(
        f"/api/v1/pull-requests/{owned_pull_request.id}/ai-analysis", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_investigate_404s_for_another_users_pull_request(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_pull_request: PullRequest
) -> None:
    resp = await db_client.post(
        f"/api/v1/pull-requests/{owned_pull_request.id}/investigate", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_publish_review_404s_for_another_users_pull_request(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_pull_request: PullRequest
) -> None:
    resp = await db_client.post(
        f"/api/v1/pull-requests/{owned_pull_request.id}/publish-review", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_get_review_report_404s_for_another_users_pull_request(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_pull_request: PullRequest
) -> None:
    resp = await db_client.get(
        f"/api/v1/pull-requests/{owned_pull_request.id}/review-report", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_get_ai_analysis_404s_when_none_has_been_run_yet_for_the_owner(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_pull_request: PullRequest
) -> None:
    """Distinguishes "not your PR" from "your PR, but nothing's been
    analyzed yet" - both 404, for different reasons, and both must stay
    404 (never leak into a 403 or 200) regardless of which is true."""
    resp = await db_client.get(
        f"/api/v1/pull-requests/{owned_pull_request.id}/ai-analysis", headers=user_a_headers
    )
    assert resp.status_code == 404


async def test_unauthenticated_requests_are_401(
    db_client: AsyncClient, owned_pull_request: PullRequest
) -> None:
    resp = await db_client.get(f"/api/v1/pull-requests/{owned_pull_request.id}/ai-analysis")
    assert resp.status_code == 401
