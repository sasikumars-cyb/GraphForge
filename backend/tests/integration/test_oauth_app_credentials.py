"""Admin-managed OAuth App credentials (GitHub, Google Drive) - see
app.services.oauth_app_config_service. Covers the admin gate, the
database-over-environment precedence, and that /github/connect and
/google-drive/connect actually pick up a stored override.
"""

import uuid
from collections.abc import Generator

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.oauth_app_credential import OAuthAppCredential
from app.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def clean_oauth_app_credentials(db_session: AsyncSession) -> None:
    """This suite runs against the same database as the running dev app -
    db_session only wraps and rolls back THIS test's own transaction, it
    doesn't erase rows already committed by real usage (an admin actually
    configuring a real OAuth App through the UI). Deletes any pre-existing
    github/google_drive rows at the start of this transaction so every
    test here starts from a real "unset" baseline regardless of what's
    genuinely configured - the rollback at teardown restores whatever was
    really there, nothing is lost."""
    await db_session.execute(
        delete(OAuthAppCredential).where(
            OAuthAppCredential.provider_key.in_(["github", "google_drive"])
        )
    )


@pytest.fixture
def no_env_credentials(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Forces every provider's env-var credentials unset, regardless of a
    developer's own backend/.env - same convention as
    test_github_oauth.py's github_not_configured fixture."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _register_and_get_token(db_client: AsyncClient, email: str) -> str:
    await db_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple", "full_name": "Test User"},
    )
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery-staple"}
    )
    return str(login.json()["access_token"])


async def _promote_to_admin(db_session: AsyncSession, email: str) -> None:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = "admin"
    await db_session.commit()


async def test_non_admin_cannot_read_or_write_oauth_app_credentials(
    db_client: AsyncClient, no_env_credentials: None
) -> None:
    token = await _register_and_get_token(db_client, f"user+{uuid.uuid4()}@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    assert (await db_client.get("/api/v1/oauth-apps", headers=headers)).status_code == 403
    assert (
        await db_client.put(
            "/api/v1/oauth-apps/github",
            headers=headers,
            json={"client_id": "x", "client_secret": "y"},
        )
    ).status_code == 403


async def test_admin_sees_unset_status_when_nothing_configured(
    db_client: AsyncClient, db_session: AsyncSession, no_env_credentials: None
) -> None:
    email = f"admin+{uuid.uuid4()}@example.com"
    token = await _register_and_get_token(db_client, email)
    await _promote_to_admin(db_session, email)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.get("/api/v1/oauth-apps", headers=headers)

    assert response.status_code == 200
    by_key = {row["provider_key"]: row for row in response.json()}
    assert by_key["github"] == {
        "provider_key": "github",
        "configured": False,
        "source": "unset",
        "client_id": None,
    }
    assert by_key["google_drive"]["source"] == "unset"


async def test_admin_can_store_a_credential_and_it_takes_precedence_over_env(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "env-client-secret")
    get_settings.cache_clear()

    email = f"admin+{uuid.uuid4()}@example.com"
    token = await _register_and_get_token(db_client, email)
    await _promote_to_admin(db_session, email)
    headers = {"Authorization": f"Bearer {token}"}

    put_response = await db_client.put(
        "/api/v1/oauth-apps/github",
        headers=headers,
        json={"client_id": "stored-client-id", "client_secret": "stored-client-secret"},
    )
    assert put_response.status_code == 200
    assert put_response.json() == {
        "provider_key": "github",
        "configured": True,
        "source": "database",
        "client_id": "stored-client-id",
    }

    connect_response = await db_client.get("/api/v1/github/connect", headers=headers)
    assert connect_response.status_code == 200
    assert "client_id=stored-client-id" in connect_response.json()["authorization_url"]

    get_settings.cache_clear()


async def test_clearing_a_stored_credential_falls_back_to_env(
    db_client: AsyncClient, db_session: AsyncSession, no_env_credentials: None
) -> None:
    email = f"admin+{uuid.uuid4()}@example.com"
    token = await _register_and_get_token(db_client, email)
    await _promote_to_admin(db_session, email)
    headers = {"Authorization": f"Bearer {token}"}

    await db_client.put(
        "/api/v1/oauth-apps/google_drive",
        headers=headers,
        json={"client_id": "temp-id", "client_secret": "temp-secret"},
    )

    delete_response = await db_client.delete("/api/v1/oauth-apps/google_drive", headers=headers)

    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "provider_key": "google_drive",
        "configured": False,
        "source": "unset",
        "client_id": None,
    }
    # env still unset (no_env_credentials) - Connect should 503 again.
    connect_response = await db_client.get("/api/v1/google-drive/connect", headers=headers)
    assert connect_response.status_code == 503


async def test_unknown_provider_key_returns_404(
    db_client: AsyncClient, db_session: AsyncSession, no_env_credentials: None
) -> None:
    email = f"admin+{uuid.uuid4()}@example.com"
    token = await _register_and_get_token(db_client, email)
    await _promote_to_admin(db_session, email)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.put(
        "/api/v1/oauth-apps/bitbucket",
        headers=headers,
        json={"client_id": "x", "client_secret": "y"},
    )

    assert response.status_code == 404
