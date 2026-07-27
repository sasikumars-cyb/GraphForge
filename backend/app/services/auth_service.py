"""Registration and authentication against locally-stored (email/password)
accounts.

This is deliberately NOT behind an interface the way `graph`/`ai`/
`integrations` are: it's built-in functionality, not a swappable external
adapter. A future GitHub-OAuth login is a different code path (see
`app.integrations.interfaces.IOAuthProvider`) that creates or looks up a
User with `auth_provider="github"`, then issues a token the same way this
module does for local accounts.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.auth import UserRegisterRequest


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def register_user(db: AsyncSession, request: UserRegisterRequest) -> User:
    existing = await get_user_by_email(db, request.email)
    if existing is not None:
        raise ConflictError("An account with this email already exists.")

    user = User(
        email=request.email,
        full_name=request.full_name,
        hashed_password=hash_password(request.password),
        auth_provider="local",
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # TOCTOU: two concurrent registrations for the same email can both
        # pass the `existing is None` check above before either commits —
        # the second one's INSERT then violates users.email's unique
        # constraint. Without this, that surfaces as an unhandled
        # IntegrityError → a generic 500 instead of the 409 a duplicate
        # email is supposed to produce. The session must be rolled back
        # before it can be used again (a failed commit leaves it unusable).
        await db.rollback()
        raise ConflictError("An account with this email already exists.")
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    user = await get_user_by_email(db, email)

    # Also raised (not just on a missing user) when hashed_password is None,
    # e.g. a future OAuth-only account with no local password to check.
    if (
        user is None
        or user.hashed_password is None
        or not verify_password(password, user.hashed_password)
    ):
        raise UnauthorizedError("Incorrect email or password.")

    if not user.is_active:
        raise UnauthorizedError("This account has been deactivated.")

    return user
