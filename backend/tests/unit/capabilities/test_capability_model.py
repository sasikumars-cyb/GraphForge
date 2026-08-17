"""Contract tests for `app.capabilities.model.CapabilityVersion` — shape
validation only, no registry, no database."""

from __future__ import annotations

import pytest

from app.capabilities.model import (
    CapabilityKind,
    CapabilityModelError,
    CapabilityVersion,
    IsolationRequirement,
    ReversibilityClass,
    RiskClass,
    SideEffectClass,
)


def _base_kwargs(**overrides: object) -> dict:
    kwargs = dict(
        capability_id="test_capability",
        version=1,
        description="d",
        input_schema={},
        output_schema={},
        scope_ceiling="s",
        risk_class=RiskClass.LOW,
        reversibility=ReversibilityClass.REVERSIBLE,
        compensating_capability_id=None,
        external_visibility=False,
        side_effect_class=SideEffectClass.READ_ONLY,
        required_authorization="none",
        isolation_requirement=IsolationRequirement.NONE,
        execution_context_requirements=(),
        produces_artifact=False,
        tool_id="some_tool",
        registered_by="test",
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_reversible_capability_constructs() -> None:
    CapabilityVersion(**_base_kwargs())


def test_capability_id_must_be_non_empty() -> None:
    with pytest.raises(CapabilityModelError, match="capability_id"):
        CapabilityVersion(**_base_kwargs(capability_id=""))


def test_version_must_be_positive() -> None:
    with pytest.raises(CapabilityModelError, match="version"):
        CapabilityVersion(**_base_kwargs(version=0))


def test_compensatable_requires_a_named_compensator() -> None:
    with pytest.raises(CapabilityModelError, match="COMPENSATABLE"):
        CapabilityVersion(
            **_base_kwargs(
                reversibility=ReversibilityClass.COMPENSATABLE,
                compensating_capability_id=None,
            )
        )


def test_compensatable_with_a_named_compensator_is_valid() -> None:
    CapabilityVersion(
        **_base_kwargs(
            reversibility=ReversibilityClass.COMPENSATABLE,
            compensating_capability_id="revert_x",
        )
    )


@pytest.mark.parametrize(
    "reversibility", [ReversibilityClass.REVERSIBLE, ReversibilityClass.IRREVERSIBLE]
)
def test_non_compensatable_must_not_declare_a_compensator(reversibility) -> None:
    with pytest.raises(CapabilityModelError, match="MUST NOT declare"):
        CapabilityVersion(
            **_base_kwargs(reversibility=reversibility, compensating_capability_id="x")
        )


def test_composed_requires_composed_of() -> None:
    with pytest.raises(CapabilityModelError, match="COMPOSED"):
        CapabilityVersion(**_base_kwargs(kind=CapabilityKind.COMPOSED, composed_of=None))


def test_primitive_must_not_declare_composed_of() -> None:
    with pytest.raises(CapabilityModelError, match="PRIMITIVE"):
        CapabilityVersion(**_base_kwargs(kind=CapabilityKind.PRIMITIVE, composed_of=("a", "b")))


def test_composed_with_composed_of_is_valid() -> None:
    """The data model does not make future composition impossible (Cap
    §12) — a well-formed COMPOSED declaration constructs cleanly, even
    though Phase 2 registers none and nothing executes one yet."""
    CapabilityVersion(**_base_kwargs(kind=CapabilityKind.COMPOSED, composed_of=("a", "b")))
