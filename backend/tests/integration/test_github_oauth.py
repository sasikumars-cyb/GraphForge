"""'Connect GitHub' OAuth flow.

No real network calls to github.com — GitHubOAuthProvider's methods are
patched at the class level so we can exercise our own routes/services/DB
without needing an actual GitHub OAuth App or account.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.integrations.github import GitHubOAuthProvider
from app.integrations.interfaces import OAuthUserProfile, RepositoryInfo

pytestmark = pytest.mark.asyncio

REGISTER_PAYLOAD = {
    "email": "ada@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada Lovelace",
}

FAKE_PROFILE = OAuthUserProfile(provider_user_id="12345", email="ada@github.example", name="ada")

FAKE_REPOS = [
    RepositoryInfo(
        provider_repo_id="1001",
        owner="ada",
        name="engine",
        full_name="ada/engine",
        private=False,
        default_branch="main",
        html_url="https://github.com/ada/engine",
    ),
    RepositoryInfo(
        provider_repo_id="1002",
        owner="ada",
        name="notes",
        full_name="ada/notes",
        private=True,
        default_branch="main",
        html_url="https://github.com/ada/notes",
    ),
]


@pytest.fixture
def github_configured(monkeypatch: pytest.MonkeyPatch):
    """Configures GITHUB_CLIENT_ID/SECRET for the duration of one test and
    restores the (unconfigured) default afterward - get_settings() is
    lru_cache'd, so the cache must be cleared on both ends."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-client-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def github_not_configured(monkeypatch: pytest.MonkeyPatch):
    """Forces GITHUB_CLIENT_ID/SECRET unset for one test, regardless of
    whatever real values a developer's own backend/.env has (a real GitHub
    OAuth App is expected to be configured there for local "Connect GitHub"
    testing) - explicit env vars override .env file values in
    pydantic-settings, so this is reliable independent of .env's contents."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _register_and_get_token(db_client: AsyncClient) -> str:
    await db_client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    login_response = await db_client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    return str(login_response.json()["access_token"])


async def test_connect_returns_503_when_not_configured(
    db_client: AsyncClient, github_not_configured: None
) -> None:
    token = await _register_and_get_token(db_client)

    response = await db_client.get(
        "/api/v1/github/connect", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_not_configured"


async def test_connect_returns_authorization_url_when_configured(
    db_client: AsyncClient, github_configured: None
) -> None:
    token = await _register_and_get_token(db_client)

    response = await db_client.get(
        "/api/v1/github/connect", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    url = response.json()["authorization_url"]
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=test-client-id" in url
    assert "state=" in url


async def test_connection_status_before_and_after_connecting(
    db_client: AsyncClient, github_configured: None
) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    before = await db_client.get("/api/v1/github/connection", headers=headers)
    assert before.json() == {"connected": False, "github_username": None, "connected_at": None}

    connect_response = await db_client.get("/api/v1/github/connect", headers=headers)
    state = connect_response.json()["authorization_url"].split("state=")[1]

    with (
        patch.object(
            GitHubOAuthProvider,
            "exchange_code_for_token",
            AsyncMock(return_value="gh-access-token"),
        ),
        patch.object(
            GitHubOAuthProvider, "fetch_user_profile", AsyncMock(return_value=FAKE_PROFILE)
        ),
    ):
        callback_response = await db_client.get(
            f"/api/v1/github/callback?code=fake-code&state={state}", follow_redirects=False
        )

    assert callback_response.status_code in (302, 307)
    assert "github=connected" in callback_response.headers["location"]

    after = await db_client.get("/api/v1/github/connection", headers=headers)
    body = after.json()
    assert body["connected"] is True
    assert body["github_username"] == "ada"


async def test_callback_with_invalid_state_redirects_with_error(
    db_client: AsyncClient, github_configured: None
) -> None:
    response = await db_client.get(
        "/api/v1/github/callback?code=fake-code&state=not-a-real-token",
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "github=error" in response.headers["location"]


async def test_disconnect_removes_the_connection(
    db_client: AsyncClient, github_configured: None
) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}
    connect_response = await db_client.get("/api/v1/github/connect", headers=headers)
    state = connect_response.json()["authorization_url"].split("state=")[1]

    with (
        patch.object(
            GitHubOAuthProvider,
            "exchange_code_for_token",
            AsyncMock(return_value="gh-access-token"),
        ),
        patch.object(
            GitHubOAuthProvider, "fetch_user_profile", AsyncMock(return_value=FAKE_PROFILE)
        ),
    ):
        await db_client.get(f"/api/v1/github/callback?code=fake-code&state={state}")

    delete_response = await db_client.delete("/api/v1/github/connection", headers=headers)
    assert delete_response.status_code == 204

    status_response = await db_client.get("/api/v1/github/connection", headers=headers)
    assert status_response.json()["connected"] is False


async def test_list_available_repositories_marks_already_selected_ones(
    db_client: AsyncClient, github_configured: None
) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}
    connect_response = await db_client.get("/api/v1/github/connect", headers=headers)
    state = connect_response.json()["authorization_url"].split("state=")[1]

    with (
        patch.object(
            GitHubOAuthProvider,
            "exchange_code_for_token",
            AsyncMock(return_value="gh-access-token"),
        ),
        patch.object(
            GitHubOAuthProvider, "fetch_user_profile", AsyncMock(return_value=FAKE_PROFILE)
        ),
    ):
        await db_client.get(f"/api/v1/github/callback?code=fake-code&state={state}")

    await db_client.post(
        "/api/v1/repositories",
        headers=headers,
        json={
            "repositories": [
                {
                    "provider_repo_id": "1001",
                    "owner": "ada",
                    "name": "engine",
                    "full_name": "ada/engine",
                    "private": False,
                    "default_branch": "main",
                    "html_url": "https://github.com/ada/engine",
                }
            ]
        },
    )

    with patch.object(GitHubOAuthProvider, "list_repositories", AsyncMock(return_value=FAKE_REPOS)):
        response = await db_client.get("/api/v1/github/repositories", headers=headers)

    assert response.status_code == 200
    by_name = {repo["full_name"]: repo for repo in response.json()}
    assert by_name["ada/engine"]["is_selected"] is True
    assert by_name["ada/notes"]["is_selected"] is False


async def test_repositories_endpoint_requires_connection(
    db_client: AsyncClient, github_configured: None
) -> None:
    token = await _register_and_get_token(db_client)

    response = await db_client.get(
        "/api/v1/github/repositories", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
