"""Tests for app.services.auth_service — the TOCTOU race on registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.schemas.auth import UserRegisterRequest
from app.services import auth_service

pytestmark = pytest.mark.asyncio


async def test_register_user_creates_account(db_session: AsyncSession) -> None:
    request = UserRegisterRequest(
        email="race-fixture@example.com", password="correct-horse-battery-staple", full_name="Ada"
    )
    user = await auth_service.register_user(db_session, request)
    assert user.email == request.email


async def test_register_user_concurrent_duplicate_raises_conflict_not_a_crash(
    db_session: AsyncSession,
) -> None:
    """Regression test: two concurrent registrations for the same email can
    both pass the `existing is None` check before either commits — the
    second INSERT then violates users.email's unique constraint. Simulated
    here by forcing `get_user_by_email` to report "no existing user" (the
    exact race window) even though one is about to be committed for real,
    proving the resulting IntegrityError becomes a ConflictError (409),
    not an unhandled crash surfacing as a generic 500."""
    request = UserRegisterRequest(
        email="race-duplicate@example.com",
        password="correct-horse-battery-staple",
        full_name="Ada",
    )
    await auth_service.register_user(db_session, request)

    with (
        patch.object(auth_service, "get_user_by_email", new=AsyncMock(return_value=None)),
        pytest.raises(ConflictError),
    ):
        await auth_service.register_user(db_session, request)

    # The session must still be usable afterward — a failed commit that
    # isn't rolled back leaves it unusable for the rest of the request.
    still_works = await auth_service.get_user_by_email(db_session, request.email)
    assert still_works is not None
