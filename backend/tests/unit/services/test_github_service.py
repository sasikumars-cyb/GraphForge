"""Unit tests for `get_decrypted_access_token` - the shared helper
extracted from what used to be duplicated `_get_access_token` methods in
`app.ai.agent.investigation_agent` and `app.analysis.engine.impact_analysis_engine`.

Uses the real transactional `db_session` fixture (real Postgres) and the
real `encrypt_secret`/`decrypt_secret` Fernet round-trip - not mocked, since
this helper's entire job is exercising that round-trip correctly.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.models.github_connection import GitHubConnection
from app.models.user import User
from app.services.github_service import get_decrypted_access_token

pytestmark = pytest.mark.asyncio


async def _create_user(db_session: AsyncSession) -> User:
    user = User(
        email=f"token-helper-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Token Helper Test",
        hashed_password="not-a-real-hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_returns_decrypted_token_when_connected(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    db_session.add(
        GitHubConnection(
            user_id=user.id,
            github_user_id="12345",
            github_username="octocat",
            encrypted_access_token=encrypt_secret("gho_realtoken"),
        )
    )
    await db_session.flush()

    token = await get_decrypted_access_token(db_session, user.id)

    assert token == "gho_realtoken"


async def test_returns_none_when_not_connected(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)

    token = await get_decrypted_access_token(db_session, user.id)

    assert token is None


async def test_returns_none_for_unknown_user_id(db_session: AsyncSession) -> None:
    token = await get_decrypted_access_token(db_session, uuid.uuid4())

    assert token is None
