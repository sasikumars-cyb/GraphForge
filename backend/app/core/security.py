"""Password hashing and JWT encode/decode.

The only module allowed to import `bcrypt` or `jwt` directly — every other
module goes through the functions here.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

# bcrypt only examines the first 72 bytes of the input and raises on longer
# input in bcrypt>=4.x. Truncating defensively means a very long (or
# multi-byte-heavy) password can never raise here - it just stops
# contributing to the hash past 72 bytes, same as bcrypt would do anyway.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(truncated, hashed_password.encode("utf-8"))


def create_access_token(
    subject: str, expires_delta: timedelta | None = None, purpose: str | None = None
) -> str:
    """Issue a signed JWT whose `sub` claim is `subject` (the user's id).

    `purpose`, when given, scopes this token to a specific single-use flow
    (e.g. "github_oauth_state") rather than general API authentication —
    `get_current_user` rejects any token carrying a `purpose` claim, so a
    token minted for one narrow flow can never double as a bearer token
    for the rest of the API even if it leaks (e.g. via a referrer header,
    a log line, browser history). Omit for a normal login access token.
    """
    settings = get_settings()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    if purpose is not None:
        payload["purpose"] = purpose
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT, raising UnauthorizedError if it's invalid or expired."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid or expired authentication token.") from exc
