"""`app.core.redact.redact_secrets` — Phase 8 adds a new caller
(`app.services.engineering_task_service._observation_view`) but reuses
this existing, already-in-production mechanism unmodified. These tests
did not exist before Phase 8; added here per the Phase 8 security
requirement to prove representative existing patterns are actually
redacted, not merely assumed to work because the module already exists.
"""

from __future__ import annotations

from app.core.redact import redact_secrets


def test_returns_falsy_text_unchanged() -> None:
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None  # type: ignore[arg-type]


def test_text_with_no_credential_shape_is_unchanged() -> None:
    text = "the query returned 3 repositories and 12 components"
    assert redact_secrets(text) == text


def test_aws_style_credential_is_redacted() -> None:
    text = "connection failed: aws_access_key_id=AKIAABCDEFGHIJKLMNOP"
    redacted = redact_secrets(text)
    assert "AKIAABCDEFGHIJKLMNOP" not in redacted
    assert "[REDACTED:aws_access_key]" in redacted


def test_generic_token_secret_password_assignment_is_redacted() -> None:
    for keyword, raw_value in [
        ("api_key", "sk-live-abcdef1234567890"),
        ("secret", "s3cr3t-value-1234567890"),
        ("password", "hunter2-not-a-real-password"),
    ]:
        text = f"{keyword}: '{raw_value}'"
        redacted = redact_secrets(text)
        assert raw_value not in redacted, f"{keyword} value leaked: {redacted!r}"
        assert "[REDACTED:generic_credential_assignment]" in redacted


def test_jwt_shaped_token_is_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ"
    text = f"Authorization: Bearer {jwt}"
    redacted = redact_secrets(text)
    assert jwt not in redacted
    assert "[REDACTED:jwt]" in redacted


def test_private_key_block_is_redacted() -> None:
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz\n"
        "-----END RSA PRIVATE KEY-----"
    )
    redacted = redact_secrets(text)
    assert "MIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "[REDACTED:private_key_block]" in redacted


def test_multiple_secrets_in_the_same_text_are_all_redacted() -> None:
    text = (
        "connection failed: aws_access_key_id=AKIAABCDEFGHIJKLMNOP "
        "api_key: 'sk-live-abcdef1234567890'"
    )
    redacted = redact_secrets(text)
    assert "AKIAABCDEFGHIJKLMNOP" not in redacted
    assert "sk-live-abcdef1234567890" not in redacted
    assert "[REDACTED:aws_access_key]" in redacted
    assert "[REDACTED:generic_credential_assignment]" in redacted


def test_redaction_is_idempotent_on_already_redacted_text() -> None:
    once = redact_secrets("aws_access_key_id=AKIAABCDEFGHIJKLMNOP")
    twice = redact_secrets(once)
    assert once == twice
