"""Contract tests for `app.control_plane.grant` — the three-state
crash-safe lifecycle and Cap §7's "never reused, never inherited"
guarantees."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.control_plane.grant import (
    AuthorizationGrant,
    GrantLifecycleError,
    GrantState,
    hash_action_parameters,
)


def _grant(**overrides: object) -> AuthorizationGrant:
    defaults: dict[str, object] = {
        "grant_id": uuid.uuid4(),
        "action_id": uuid.uuid4(),
        "capability_id": "query_knowledge_graph",
        "capability_version": 1,
        "action_parameters_hash": hash_action_parameters({"query": "find repos"}),
        "policy_version_id": "policy-abc",
        "scope": "neo4j read-only",
        "safety_validity_result": "ok",
        "safety_validity_valid": True,
        "novelty": "known",
        "human_approval_content_hash": None,
        "issued_at": datetime.now(UTC),
        "ttl_seconds": 60,
    }
    defaults.update(overrides)
    return AuthorizationGrant(**defaults)  # type: ignore[arg-type]


class TestLifecycleTransitions:
    def test_granted_to_consuming_succeeds(self) -> None:
        grant = _grant()
        consuming = grant.consuming()
        assert consuming.state == GrantState.CONSUMING
        # Frozen dataclass — the original object never mutates.
        assert grant.state == GrantState.GRANTED

    def test_consuming_to_consumed_succeeds(self) -> None:
        consuming = _grant().consuming()
        consumed = consuming.consumed()
        assert consumed.state == GrantState.CONSUMED

    def test_granted_to_consumed_directly_is_rejected(self) -> None:
        with pytest.raises(GrantLifecycleError):
            _grant().consumed()

    def test_consuming_to_consuming_again_is_rejected(self) -> None:
        consuming = _grant().consuming()
        with pytest.raises(GrantLifecycleError):
            consuming.consuming()

    def test_consumed_to_anything_is_rejected(self) -> None:
        consumed = _grant().consuming().consumed()
        with pytest.raises(GrantLifecycleError):
            consumed.consuming()
        with pytest.raises(GrantLifecycleError):
            consumed.consumed()


class TestExpiry:
    def test_not_expired_within_ttl(self) -> None:
        grant = _grant(issued_at=datetime.now(UTC), ttl_seconds=60)
        assert grant.is_expired(now=grant.issued_at + timedelta(seconds=10)) is False

    def test_expired_after_ttl(self) -> None:
        grant = _grant(issued_at=datetime.now(UTC), ttl_seconds=60)
        assert grant.is_expired(now=grant.issued_at + timedelta(seconds=61)) is True

    def test_expiry_is_derived_not_stored(self) -> None:
        """§7.1: "Expiry is derived ... MUST NOT be separately persisted."
        There is no settable field for it — `expires_at` is a computed
        property, checked here structurally via `AuthorizationGrant`'s own
        `__slots__` (no `expires_at` or `expired` field exists)."""
        assert "expires_at" not in AuthorizationGrant.__slots__
        assert "expired" not in AuthorizationGrant.__slots__


class TestUsability:
    def test_granted_and_unexpired_is_usable(self) -> None:
        grant = _grant(issued_at=datetime.now(UTC), ttl_seconds=60)
        assert grant.is_usable(now=grant.issued_at) is True

    def test_expired_is_not_usable(self) -> None:
        grant = _grant(issued_at=datetime.now(UTC), ttl_seconds=1)
        assert grant.is_usable(now=grant.issued_at + timedelta(seconds=5)) is False

    def test_consuming_state_is_not_usable(self) -> None:
        """Cap §7: "never be reused" — once consumption has begun, this
        Grant must never again look usable for a fresh dispatch attempt,
        even before it reaches CONSUMED."""
        consuming = _grant(issued_at=datetime.now(UTC), ttl_seconds=60).consuming()
        assert consuming.is_usable() is False

    def test_consumed_state_is_not_usable(self) -> None:
        consumed = _grant(issued_at=datetime.now(UTC), ttl_seconds=60).consuming().consumed()
        assert consumed.is_usable() is False


def test_zero_or_negative_ttl_rejected() -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        _grant(ttl_seconds=0)


def test_invalid_novelty_rejected() -> None:
    with pytest.raises(ValueError, match="novelty"):
        _grant(novelty="maybe")


class TestHashActionParameters:
    """Phase 3 exit-audit correction #2's binding primitive."""

    def test_identical_parameters_hash_identically(self) -> None:
        params = {"query": "find repos", "parameters": {"user_id": "u1"}}
        assert hash_action_parameters(params) == hash_action_parameters(dict(params))

    def test_key_order_does_not_affect_the_hash(self) -> None:
        a = {"query": "find repos", "parameters": {}}
        b = {"parameters": {}, "query": "find repos"}
        assert hash_action_parameters(a) == hash_action_parameters(b)

    def test_a_changed_value_changes_the_hash(self) -> None:
        a = {"query": "find repos"}
        b = {"query": "find something else"}
        assert hash_action_parameters(a) != hash_action_parameters(b)

    def test_an_added_key_changes_the_hash(self) -> None:
        a = {"query": "find repos"}
        b = {"query": "find repos", "extra": "escalated"}
        assert hash_action_parameters(a) != hash_action_parameters(b)

    def test_empty_parameters_hash_deterministically(self) -> None:
        assert hash_action_parameters({}) == hash_action_parameters({})
