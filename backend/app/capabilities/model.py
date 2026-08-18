"""`CapabilityVersion` — the Phase 2 data model.

Every field below traces to a specific requirement in
`docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md` §3 (the
Primitive Capability contract) or §12 (composition governance). None is
speculative; each docstring cites what requires it. Fields that contract
requires as "declared, never self-evaluated" (`required_authorization`)
are recorded here as plain descriptive strings — nothing in Phase 2
evaluates them, because Policy/Control Plane don't exist yet (Cap §4:
"a Capability declares what authorization it requires; the Control Plane
alone decides whether that requirement is currently satisfied" — the
"alone decides" half is Phase 3's job, not this module's).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ReversibilityClass(StrEnum):
    """Cap §3.1's closed taxonomy — governs recovery obligations. A
    `COMPENSATABLE` Capability MUST declare a named compensator (Cap §3);
    `REVERSIBLE`/`IRREVERSIBLE` MUST NOT."""

    REVERSIBLE = "reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"


class SideEffectClass(StrEnum):
    """Cap §3's closed side-effect vocabulary — what Scope Validation and
    the security boundary depend on being machine-checkable, not prose."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    EXTERNAL_READ = "external_read"
    EXTERNAL_WRITE = "external_write"


class RiskClass(StrEnum):
    """Cap §3's risk floor — a composition MAY self-assess higher, never
    lower, than its constituent Capabilities' floors (enforced by future
    Composed Capability governance, Cap §10 — not this phase)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IsolationRequirement(StrEnum):
    """Cap §3 — "fresh Workspace / shared / none". The direct input to
    the (not-yet-built, Phase 4) Workspace boundary."""

    NONE = "none"
    SHARED_WORKSPACE = "shared_workspace"
    FRESH_WORKSPACE = "fresh_workspace"


class CapabilityKind(StrEnum):
    """Cap §12: "Ensure the distinction remains: Primitive Capability vs
    Composed Capability" and "the frozen contract requires composition to
    be independently represented and evaluated later." Phase 2 registers
    only `PRIMITIVE` Capabilities — `COMPOSED` is represented in the data
    model (so a later phase's registration is expressible without a
    model change) but nothing in this phase constructs or executes one."""

    PRIMITIVE = "primitive"
    COMPOSED = "composed"


class CapabilityModelError(ValueError):
    """Raised by `CapabilityVersion.__post_init__` for a shape violation
    — never for an authorization or Policy decision, which this module
    has no concept of."""


@dataclass(frozen=True)
class CapabilityVersion:
    """One immutable, versioned Capability declaration.

    Identity is the `(capability_id, version)` pair — see
    `CapabilityRegistry` for how immutability across re-registration is
    enforced; this dataclass only enforces the SHAPE of one version, not
    cross-version rules.
    """

    capability_id: str
    version: int
    description: str

    # Cap §3: "Input schema... MUST" / "Output schema... MUST" — kept as
    # a lightweight structural descriptor (field name -> type name),
    # sufficient for Phase 2's own scope (Parameter Validation and
    # Prediction admissibility are both Phase 3+ consumers of this same
    # field, not built here) without inventing a full JSON-Schema engine
    # this phase doesn't need.
    input_schema: dict[str, str]
    output_schema: dict[str, str]

    # Cap §3: "Scope ceiling... MUST — as a ceiling only. The maximum
    # boundary this Capability could ever touch." A free-text description
    # at this phase — structured Scope Validation is Phase 3's Control
    # Plane, not this model.
    scope_ceiling: str

    risk_class: RiskClass
    reversibility: ReversibilityClass
    # Cap §3.1: mandatory iff reversibility == COMPENSATABLE, forbidden
    # otherwise — enforced in __post_init__ below.
    compensating_capability_id: str | None

    # Cap §3: "External visibility... MUST — static, closed vocabulary."
    # A Capability is externally visible or it isn't; never self-assessed
    # per-invocation (that's exactly what this being a fixed, versioned
    # field prevents).
    external_visibility: bool
    side_effect_class: SideEffectClass

    # Cap §4: declared only — "a Capability declares what authorization
    # it requires... never states whether that's currently satisfied."
    # A plain descriptive string; Phase 3's Policy is what gives this
    # meaning, not this field's shape.
    required_authorization: str

    isolation_requirement: IsolationRequirement

    # Cap §3: "Execution Context requirements... MUST — which dimensions
    # of Execution Context this Capability's correctness depends on."
    # Empty is valid — not every Capability depends on any (e.g. a pure
    # graph-index read has no repository-revision dependency in the
    # Engineering State Execution Context sense).
    execution_context_requirements: tuple[str, ...]

    # Cap §3: "Artifact production declaration... MUST, if the Capability
    # produces a consumable artifact." A boolean is the whole of what
    # Phase 2 needs to record — the full artifact-identity mechanism
    # (Cap §14: content digest bound to Execution Context) is an
    # execution-time concern with no producer yet in this phase; see
    # this module's own note on the representative Capability, which
    # sets this False because it produces nothing.
    produces_artifact: bool

    # The Tool binding — Cap §1: "Capability is the interface... Tool is
    # one swappable implementation of it." Must name a `tool_id` real and
    # currently registered in `app.tools.registry.ToolRegistry` — checked
    # by `CapabilityRegistry.register()`, not here (this dataclass has no
    # access to the Tool registry and shouldn't need one to be internally
    # well-formed).
    tool_id: str

    # Provenance — Cap §3: "Registration provenance... MUST." Which
    # trusted module registered this, per Phase 2 §11's registration-
    # authority requirement.
    registered_by: str

    # Cap §12 — kept PRIMITIVE for every Phase 2 registration; the field
    # exists so a later phase's COMPOSED registration needs no model
    # change.
    kind: CapabilityKind = CapabilityKind.PRIMITIVE
    composed_of: tuple[str, ...] | None = None

    # Phase 9 (runtime dependency injection design) — Cap §3's declared-
    # metadata pattern, applied to a distinct question: which
    # `ToolInput.parameters` keys does this Capability's Tool need that
    # are NOT part of what the Reasoning Plane proposes, but are instead
    # supplied by the Control Plane at dispatch time (a live request
    # `AsyncSession`, the task's owning `user_id`)? Declared here, never
    # inferred, so `ControlPlane._consume_and_dispatch` never needs
    # Tool-specific knowledge hardcoded into its generically-named
    # dispatch path (Cap §1: "Capability ≠ Tool"). Deliberately NOT a
    # general dependency-injection framework — see
    # `ControlPlane._resolve_runtime_parameter`'s own docstring for the
    # two, and only two, keys this phase's resolver understands.
    # Defaults to empty: most Capabilities need nothing runtime-injected.
    runtime_injected_parameters: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise CapabilityModelError("capability_id must be non-empty.")
        if self.version < 1:
            raise CapabilityModelError("version must be >= 1.")

        if self.reversibility == ReversibilityClass.COMPENSATABLE:
            if self.compensating_capability_id is None:
                raise CapabilityModelError(
                    f"{self.capability_id} v{self.version} is COMPENSATABLE "
                    "but declares no compensating_capability_id (Cap §3)."
                )
        elif self.compensating_capability_id is not None:
            raise CapabilityModelError(
                f"{self.capability_id} v{self.version} is "
                f"{self.reversibility.value}, which MUST NOT declare a "
                "compensating_capability_id (Cap §3.1 — only COMPENSATABLE "
                "Capabilities compensate)."
            )

        if self.kind == CapabilityKind.COMPOSED:
            if not self.composed_of:
                raise CapabilityModelError(
                    f"{self.capability_id} v{self.version} is COMPOSED but "
                    "declares no composed_of Capabilities (Cap §12)."
                )
        elif self.composed_of:
            raise CapabilityModelError(
                f"{self.capability_id} v{self.version} is PRIMITIVE and "
                "MUST NOT declare composed_of (Cap §12 — composition is "
                "independently represented, never implicit on a primitive)."
            )
