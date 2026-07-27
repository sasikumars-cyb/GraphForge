"""Tests for app.core.security — password hashing and JWT encode/decode.

Previously only exercised indirectly via the auth integration tests (real
register/login flows) — this covers the primitives directly: token
expiry, bad-signature rejection, the bcrypt-truncation edge case, and the
`purpose` claim that scopes a token to one flow (e.g. GitHub OAuth state)
instead of general API authentication.
"""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_correct_password_verifies(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed)

    def test_wrong_password_does_not_verify(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert not verify_password("wrong-password", hashed)

    def test_bcrypt_72_byte_truncation_is_consistent(self):
        """bcrypt only examines the first 72 bytes — hash_password truncates
        defensively so it never raises on longer input; verify_password
        must truncate identically or a >72-byte password could never
        verify against its own hash."""
        long_password = "a" * 100
        hashed = hash_password(long_password)
        assert verify_password("a" * 100, hashed)
        assert verify_password("a" * 72, hashed)
        # Byte 73 onward never contributed to the hash, so a password that
        # only differs beyond the truncation point still verifies — this
        # documents the actual (bcrypt-inherent) behavior, not a bug.
        assert verify_password("a" * 72 + "different-tail", hashed)


class TestAccessTokens:
    def test_round_trips_subject(self):
        token = create_access_token(subject="user-123")
        payload = decode_access_token(token)
        assert payload["sub"] == "user-123"

    def test_expired_token_is_rejected(self):
        token = create_access_token(subject="user-123", expires_delta=timedelta(seconds=-1))
        with pytest.raises(UnauthorizedError):
            decode_access_token(token)

    def test_token_with_wrong_signature_is_rejected(self):
        token = create_access_token(subject="user-123")
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(UnauthorizedError):
            decode_access_token(tampered)

    def test_token_signed_with_a_different_key_is_rejected(self):
        settings = get_settings()
        forged = jwt.encode(
            {"sub": "attacker"}, "a-completely-different-secret", algorithm=settings.jwt_algorithm
        )
        with pytest.raises(UnauthorizedError):
            decode_access_token(forged)

    def test_no_purpose_claim_by_default(self):
        """A normal login token must carry no `purpose` claim — that's what
        lets get_current_user distinguish it from a single-use, scoped
        token like a GitHub OAuth `state` value."""
        token = create_access_token(subject="user-123")
        payload = decode_access_token(token)
        assert "purpose" not in payload

    def test_purpose_claim_is_included_when_given(self):
        """Regression test: the GitHub OAuth `state` value used to be
        created via this same function with no distinguishing claim at
        all — functionally a full 10-minute bearer token for the user's
        whole API if it ever leaked (e.g. via a referrer header or log
        line), not just the OAuth-connect flow it was meant for."""
        token = create_access_token(subject="user-123", purpose="github_oauth_state")
        payload = decode_access_token(token)
        assert payload["purpose"] == "github_oauth_state"
