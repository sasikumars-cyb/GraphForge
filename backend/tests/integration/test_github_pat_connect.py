"""PAT (personal access token) alternative to the OAuth 'Connect GitHub'
flow — POST /github/connection/pat.

No real network calls to github.com: `app.integrations.github`'s
module-level `fetch_user_profile`/`fetch_token_scopes` are patched, same
convention as `test_github_oauth.py`'s class-level `GitHubOAuthProvider`
patches (both now delegate to the same module-level functions - see
github.py's own docstring on why they were extracted).
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.integrations.github import GitHubApiError
from app.integrations.interfaces import OAuthUserProfile, RepositoryInfo

pytestmark = pytest.mark.asyncio

REGISTER_PAYLOAD = {
    "email": "grace@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Grace Hopper",
}

FAKE_PROFILE = OAuthUserProfile(
    provider_user_id="54321", email="grace@github.example", name="grace"
)


@pytest.fixture
def github_not_configured(monkeypatch: pytest.MonkeyPatch):
    """No GITHUB_CLIENT_ID/SECRET at all - the PAT flow must not need an
    OAuth App configured, unlike /github/connect. See github_service.
    connect_with_pat's docstring: it never calls _build_provider()."""
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


async def test_connect_with_pat_creates_a_connection(
    db_client: AsyncClient, github_not_configured: None
) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch(
            "app.services.github_service.fetch_user_profile",
            AsyncMock(return_value=FAKE_PROFILE),
        ),
        patch(
            "app.services.github_service.fetch_token_scopes",
            AsyncMock(return_value=["repo", "read:user"]),
        ),
    ):
        response = await db_client.post(
            "/api/v1/github/connection/pat", headers=headers, json={"token": "ghp_realtoken"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["github_username"] == "grace"
    assert body["auth_method"] == "pat"
    assert body["scope_warning"] is None

    status_response = await db_client.get("/api/v1/github/connection", headers=headers)
    status_body = status_response.json()
    assert status_body["connected"] is True
    assert status_body["auth_method"] == "pat"


async def test_listing_repositories_after_pat_connect_does_not_need_an_oauth_app(
    db_client: AsyncClient, github_not_configured: None
) -> None:
    """Regression test: listing repos only needs the access token (see
    `list_repositories`'s own module-level signature - no client_id/secret
    involved), so it must work for a PAT-only connection exactly like
    connecting itself does. `list_available_repositories` used to route
    through `_build_provider()` regardless, which raised
    'GitHub integration is not configured' even though nothing about
    listing repos actually needed an OAuth App - a real user hit this."""
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch(
            "app.services.github_service.fetch_user_profile",
            AsyncMock(return_value=FAKE_PROFILE),
        ),
        patch(
            "app.services.github_service.fetch_token_scopes",
            AsyncMock(return_value=["repo", "read:user"]),
        ),
    ):
        connect_response = await db_client.post(
            "/api/v1/github/connection/pat", headers=headers, json={"token": "ghp_realtoken"}
        )
    assert connect_response.status_code == 200

    fake_repos = [
        RepositoryInfo(
            provider_repo_id="1001",
            owner="grace",
            name="engine",
            full_name="grace/engine",
            private=False,
            default_branch="main",
            html_url="https://github.com/grace/engine",
        )
    ]
    with patch(
        "app.services.github_service.list_repositories",
        AsyncMock(return_value=fake_repos),
    ):
        repos_response = await db_client.get("/api/v1/github/repositories", headers=headers)

    assert repos_response.status_code == 200
    assert repos_response.json()[0]["full_name"] == "grace/engine"


async def test_connect_with_pat_rejects_an_invalid_token(
    db_client: AsyncClient, github_not_configured: None
) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with patch(
        "app.services.github_service.fetch_user_profile",
        AsyncMock(side_effect=GitHubApiError("Bad credentials")),
    ):
        response = await db_client.post(
            "/api/v1/github/connection/pat", headers=headers, json={"token": "not-a-real-token"}
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_github_token"

    status_response = await db_client.get("/api/v1/github/connection", headers=headers)
    assert status_response.json()["connected"] is False


async def test_connect_with_pat_rejects_an_empty_token(
    db_client: AsyncClient, github_not_configured: None
) -> None:
    token = await _register_and_get_token(db_client)

    response = await db_client.post(
        "/api/v1/github/connection/pat",
        headers={"Authorization": f"Bearer {token}"},
        json={"token": "   "},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_github_token"


async def test_connect_with_pat_warns_when_missing_repo_scope(
    db_client: AsyncClient, github_not_configured: None
) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch(
            "app.services.github_service.fetch_user_profile",
            AsyncMock(return_value=FAKE_PROFILE),
        ),
        patch(
            "app.services.github_service.fetch_token_scopes",
            AsyncMock(return_value=["read:user"]),
        ),
    ):
        response = await db_client.post(
            "/api/v1/github/connection/pat", headers=headers, json={"token": "ghp_narrowscope"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["scope_warning"] is not None
    assert "repo" in body["scope_warning"]


async def test_connect_with_pat_does_not_warn_for_a_fine_grained_token(
    db_client: AsyncClient, github_not_configured: None
) -> None:
    """fetch_token_scopes returns None for a fine-grained PAT/GitHub App
    token (no X-OAuth-Scopes header) - must not be misread as 'no scopes
    granted' and warned about."""
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch(
            "app.services.github_service.fetch_user_profile",
            AsyncMock(return_value=FAKE_PROFILE),
        ),
        patch("app.services.github_service.fetch_token_scopes", AsyncMock(return_value=None)),
    ):
        response = await db_client.post(
            "/api/v1/github/connection/pat",
            headers=headers,
            json={"token": "github_pat_finegrained"},
        )

    assert response.status_code == 200
    assert response.json()["scope_warning"] is None


async def test_reconnecting_via_oauth_after_pat_flips_auth_method_to_oauth(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the update-branch fix in handle_oauth_callback:
    reconnecting via OAuth after a PAT connection must not leave the row
    stuck reporting auth_method="pat"."""
    from app.integrations.github import GitHubOAuthProvider

    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-client-secret")
    get_settings.cache_clear()

    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch(
            "app.services.github_service.fetch_user_profile",
            AsyncMock(return_value=FAKE_PROFILE),
        ),
        patch(
            "app.services.github_service.fetch_token_scopes",
            AsyncMock(return_value=["repo", "read:user"]),
        ),
    ):
        await db_client.post(
            "/api/v1/github/connection/pat", headers=headers, json={"token": "ghp_realtoken"}
        )

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

    status_response = await db_client.get("/api/v1/github/connection", headers=headers)
    assert status_response.json()["auth_method"] == "oauth"

    get_settings.cache_clear()
