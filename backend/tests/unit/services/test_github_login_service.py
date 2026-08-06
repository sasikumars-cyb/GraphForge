"""Unit tests for KAN-34's "Sign in with GitHub" service. Uses the real
transactional `db_session` fixture (real Postgres) for user lookups/
creation; `GitHubOAuthProvider`'s network calls (`exchange_code_for_token`,
`fetch_user_profile`) are mocked - `test_github_service.py` and the
`github.py` integration module itself are where the real HTTP contract is
exercised.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.integrations.interfaces import OAuthUserProfile
from app.models.user import User
from app.services.github_login_service import (
    GitHubAccountIsLocalError,
    GitHubEmailUnavailableError,
    GitHubLoginNotConfiguredError,
    handle_login_callback,
)

pytestmark = pytest.mark.asyncio

_UNCONFIGURED_SETTINGS = SimpleNamespace(
    github_client_id=None,
    github_client_secret=None,
    github_login_redirect_uri="http://localhost:8000/api/v1/auth/github/callback",
)


def _valid_state() -> str:
    import uuid

    return create_access_token(
        subject=str(uuid.uuid4()),
        purpose="github_login_state",
    )


async def test_not_configured_raises_without_credentials(db_session: AsyncSession) -> None:
    with (
        patch(
            "app.services.github_login_service.get_oauth_app_credential",
            new=AsyncMock(return_value=(None, None)),
        ),
        patch(
            "app.services.github_login_service.get_settings", return_value=_UNCONFIGURED_SETTINGS
        ),
        pytest.raises(GitHubLoginNotConfiguredError),
    ):
        await handle_login_callback(db_session, code="abc", state=_valid_state())


async def test_creates_a_new_github_account_on_first_login(db_session: AsyncSession) -> None:
    profile = OAuthUserProfile(
        provider_user_id="999", email="new-github-user@example.com", name="New Github User"
    )
    with (
        patch("app.services.github_login_service.GitHubOAuthProvider") as provider_cls,
        patch(
            "app.services.github_login_service.get_oauth_app_credential",
            new=AsyncMock(return_value=("client-id", "client-secret")),
        ),
    ):
        provider = provider_cls.return_value
        provider.exchange_code_for_token = AsyncMock(return_value="gho_token")
        provider.fetch_user_profile = AsyncMock(return_value=profile)

        access_token = await handle_login_callback(db_session, code="abc", state=_valid_state())

    assert access_token
    result = await db_session.execute(
        User.__table__.select().where(User.email == "new-github-user@example.com")
    )
    row = result.mappings().one()
    assert row["auth_provider"] == "github"
    assert row["hashed_password"] is None
    assert row["full_name"] == "New Github User"


async def test_returning_github_user_logs_in_without_creating_a_duplicate(
    db_session: AsyncSession,
) -> None:
    existing = User(
        email="returning-github-user@example.com",
        full_name="Returning User",
        hashed_password=None,
        auth_provider="github",
    )
    db_session.add(existing)
    await db_session.flush()

    profile = OAuthUserProfile(
        provider_user_id="999", email="returning-github-user@example.com", name="Returning User"
    )
    with (
        patch("app.services.github_login_service.GitHubOAuthProvider") as provider_cls,
        patch(
            "app.services.github_login_service.get_oauth_app_credential",
            new=AsyncMock(return_value=("client-id", "client-secret")),
        ),
    ):
        provider = provider_cls.return_value
        provider.exchange_code_for_token = AsyncMock(return_value="gho_token")
        provider.fetch_user_profile = AsyncMock(return_value=profile)

        await handle_login_callback(db_session, code="abc", state=_valid_state())

    result = await db_session.execute(
        User.__table__.select().where(User.email == "returning-github-user@example.com")
    )
    rows = result.mappings().all()
    assert len(rows) == 1
    assert rows[0]["id"] == existing.id


async def test_email_already_owned_by_a_local_account_is_rejected(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        User(
            email="local-user@example.com",
            full_name="Local User",
            hashed_password="hashed",
            auth_provider="local",
        )
    )
    await db_session.flush()

    profile = OAuthUserProfile(
        provider_user_id="999", email="local-user@example.com", name="Local User"
    )
    with (
        patch("app.services.github_login_service.GitHubOAuthProvider") as provider_cls,
        patch(
            "app.services.github_login_service.get_oauth_app_credential",
            new=AsyncMock(return_value=("client-id", "client-secret")),
        ),
    ):
        provider = provider_cls.return_value
        provider.exchange_code_for_token = AsyncMock(return_value="gho_token")
        provider.fetch_user_profile = AsyncMock(return_value=profile)

        with pytest.raises(GitHubAccountIsLocalError):
            await handle_login_callback(db_session, code="abc", state=_valid_state())


async def test_missing_email_is_rejected(db_session: AsyncSession) -> None:
    profile = OAuthUserProfile(provider_user_id="999", email=None, name="No Email")
    with (
        patch("app.services.github_login_service.GitHubOAuthProvider") as provider_cls,
        patch(
            "app.services.github_login_service.get_oauth_app_credential",
            new=AsyncMock(return_value=("client-id", "client-secret")),
        ),
    ):
        provider = provider_cls.return_value
        provider.exchange_code_for_token = AsyncMock(return_value="gho_token")
        provider.fetch_user_profile = AsyncMock(return_value=profile)

        with pytest.raises(GitHubEmailUnavailableError):
            await handle_login_callback(db_session, code="abc", state=_valid_state())


async def test_invalid_state_purpose_is_rejected(db_session: AsyncSession) -> None:
    from app.core.exceptions import UnauthorizedError

    wrong_purpose_state = create_access_token(subject="not-a-real-user", purpose="something_else")

    with pytest.raises(UnauthorizedError):
        await handle_login_callback(db_session, code="abc", state=wrong_purpose_state)
