"""Contract tests for `app.control_plane.safety` — every branch must
either affirmatively confirm safety or deny; there is no default-True
path."""

from __future__ import annotations

from app.control_plane.safety import evaluate_safety_validity

_BASE_KWARGS: dict[str, object] = {
    "capability_id": "query_knowledge_graph",
    "side_effect_class": "read_only",
    "risk_class": "low",
    "lease_conflicts": False,
    "emergency_policy_active": False,
}


def test_known_safe_low_risk_read_only_capability_is_valid() -> None:
    result = evaluate_safety_validity(**_BASE_KWARGS)  # type: ignore[arg-type]
    assert result.valid is True


def test_unknown_capability_id_fails_closed() -> None:
    kwargs = dict(_BASE_KWARGS, capability_id="some_new_capability")
    result = evaluate_safety_validity(**kwargs)  # type: ignore[arg-type]
    assert result.valid is False
    assert "allowlist" in result.reason


def test_non_read_only_side_effect_fails_closed() -> None:
    kwargs = dict(_BASE_KWARGS, side_effect_class="external_write")
    result = evaluate_safety_validity(**kwargs)  # type: ignore[arg-type]
    assert result.valid is False


def test_non_low_risk_fails_closed() -> None:
    kwargs = dict(_BASE_KWARGS, risk_class="high")
    result = evaluate_safety_validity(**kwargs)  # type: ignore[arg-type]
    assert result.valid is False


def test_lease_conflict_fails_closed() -> None:
    kwargs = dict(_BASE_KWARGS, lease_conflicts=True)
    result = evaluate_safety_validity(**kwargs)  # type: ignore[arg-type]
    assert result.valid is False
    assert "lease" in result.reason


def test_emergency_policy_active_fails_closed_even_for_known_safe_capability() -> None:
    kwargs = dict(_BASE_KWARGS, emergency_policy_active=True)
    result = evaluate_safety_validity(**kwargs)  # type: ignore[arg-type]
    assert result.valid is False
    assert "emergency" in result.reason


def test_every_invalid_result_carries_a_reason() -> None:
    for override in (
        {"capability_id": "unknown"},
        {"side_effect_class": "external_write"},
        {"risk_class": "high"},
        {"lease_conflicts": True},
        {"emergency_policy_active": True},
    ):
        kwargs = dict(_BASE_KWARGS, **override)
        result = evaluate_safety_validity(**kwargs)
        assert result.valid is False
        assert result.reason.strip()
