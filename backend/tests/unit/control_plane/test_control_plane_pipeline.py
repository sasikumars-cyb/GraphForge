"""Contract tests for the proposal-level and per-Action pipeline stages of
`app.control_plane.control_plane.ControlPlane` that need no database:
`check_conformance` and `check_eligibility`. The final-gate/Grant/dispatch
path (`authorize_and_execute`) needs real Postgres for its Engineering
State writes and is covered by
`tests/integration/test_control_plane_authorization_integration.py`
instead.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from app.capabilities.model import (
    CapabilityKind,
    CapabilityVersion,
    IsolationRequirement,
    ReversibilityClass,
    RiskClass,
    SideEffectClass,
)
from app.capabilities.registry import CapabilityRegistry
from app.control_plane.control_plane import ControlPlane
from app.control_plane.model import Action, ActionProposal, DenialStage, Prediction
from app.control_plane.policy import PolicyStore
from app.repositories.engineering_event_repository import EngineeringEventRepository
from app.tools.executor import ToolExecutor
from app.tools.interfaces import ToolCategory, ToolHealth, ToolInput, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec


class _FakeGraphTool:
    tool_id = "neo4j_graph"
    display_name = "Fake Graph"
    description = "d"
    category = ToolCategory.GRAPH
    capabilities: list[str] = []

    def __init__(self, config: dict[str, Any]) -> None:
        pass

    async def execute(self, input: ToolInput) -> ToolResult:
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            # Phase 10 fix: `summary` is a TOP-LEVEL `ToolResult` field,
            # never nested inside `data` (matches the real Neo4jGraphTool).
            data={},
            summary="ok",
        )

    async def health_check(self) -> ToolHealth:
        return ToolHealth.HEALTHY

    def requires_auth(self) -> bool:
        return False


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            tool_id="neo4j_graph",
            display_name="Fake Graph",
            description="d",
            category=ToolCategory.GRAPH,
            capabilities=[],
            factory=lambda cfg: _FakeGraphTool(cfg),
            requires_auth=False,
            default_enabled=True,
        )
    )
    return registry


def _capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry(tool_registry=_tool_registry())
    registry.register(
        CapabilityVersion(
            capability_id="query_knowledge_graph",
            version=1,
            description="d",
            input_schema={"query": "str", "parameters": "dict"},
            output_schema={"data": "dict", "summary": "str", "evidence_items": "list[str]"},
            scope_ceiling="the single Neo4j instance",
            risk_class=RiskClass.LOW,
            reversibility=ReversibilityClass.REVERSIBLE,
            compensating_capability_id=None,
            external_visibility=False,
            side_effect_class=SideEffectClass.READ_ONLY,
            required_authorization="none",
            isolation_requirement=IsolationRequirement.NONE,
            execution_context_requirements=(),
            produces_artifact=False,
            tool_id="neo4j_graph",
            registered_by="test",
            kind=CapabilityKind.PRIMITIVE,
            composed_of=None,
        )
    )
    return registry


def _control_plane() -> ControlPlane:
    return ControlPlane(
        capability_registry=_capability_registry(),
        tool_executor=ToolExecutor(registry=_tool_registry()),
        policy_store=PolicyStore(),
        event_repository=cast(EngineeringEventRepository, None),
    )


def _prediction(target_observable: str = "data") -> Prediction:
    return Prediction(
        target_observable=target_observable,
        falsification_condition="data is empty",
        evaluation_procedure="check keys",
        execution_context={},
        necessary_condition_rationale="needed for the plan step",
    )


def _action(**overrides: object) -> Action:
    defaults: dict[str, object] = {
        "action_id": uuid.uuid4(),
        "capability_id": "query_knowledge_graph",
        "capability_version": 1,
        "parameters": {"query": "find repos"},
        "prediction": _prediction(),
        "plan_step_id": uuid.uuid4(),
    }
    defaults.update(overrides)
    return Action(**defaults)  # type: ignore[arg-type]


def _proposal(*actions: Action) -> ActionProposal:
    return ActionProposal(
        proposal_id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        goal_id=uuid.uuid4(),
        proposing_role="test_role",
        actions=actions or (_action(),),
        engineering_state_snapshot_event_id=None,
    )


class TestConformance:
    def test_well_formed_proposal_is_conformant(self) -> None:
        result = _control_plane().check_conformance(_proposal())
        assert result.conformant is True

    def test_unknown_capability_is_a_capability_gap(self) -> None:
        proposal = _proposal(_action(capability_id="does_not_exist"))
        result = _control_plane().check_conformance(proposal)
        assert result.conformant is False
        assert result.denial_stage == DenialStage.CAPABILITY_GAP

    def test_unknown_capability_version_is_a_capability_gap(self) -> None:
        proposal = _proposal(_action(capability_version=99))
        result = _control_plane().check_conformance(proposal)
        assert result.denial_stage == DenialStage.CAPABILITY_GAP

    def test_parameter_outside_declared_input_schema_is_scope_violation(self) -> None:
        proposal = _proposal(_action(parameters={"query": "x", "arbitrary_field": "escape"}))
        result = _control_plane().check_conformance(proposal)
        assert result.conformant is False
        assert result.denial_stage == DenialStage.SCOPE_VIOLATION

    def test_prediction_target_not_in_output_schema_is_inadmissible(self) -> None:
        proposal = _proposal(_action(prediction=_prediction(target_observable="not_a_real_field")))
        result = _control_plane().check_conformance(proposal)
        assert result.conformant is False
        assert result.denial_stage == DenialStage.PREDICTION_INADMISSIBLE

    def test_conformance_grants_nothing_by_itself(self) -> None:
        """Cap §8 state 1 — conformant does not imply eligible, let alone
        authorized. Structural: `ConformanceResult` has no field a caller
        could misread as a Grant."""
        result = _control_plane().check_conformance(_proposal())
        assert not hasattr(result, "grant_id")
        assert not hasattr(result, "authorized")


class TestEligibility:
    def test_all_conditions_met_is_eligible(self) -> None:
        result = _control_plane().check_eligibility(
            _action(),
            budget_available=True,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=True,
        )
        assert result.eligible is True

    def test_prior_halt_blocks_eligibility(self) -> None:
        result = _control_plane().check_eligibility(
            _action(),
            budget_available=True,
            lease_held=True,
            prior_action_halted=True,
            preconditions_hold=True,
        )
        assert result.eligible is False
        assert result.denial_stage == DenialStage.PRECONDITION_INVALIDATED

    def test_stale_precondition_blocks_eligibility(self) -> None:
        result = _control_plane().check_eligibility(
            _action(),
            budget_available=True,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=False,
        )
        assert result.eligible is False

    def test_no_budget_blocks_eligibility(self) -> None:
        result = _control_plane().check_eligibility(
            _action(),
            budget_available=False,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=True,
        )
        assert result.denial_stage == DenialStage.BUDGET_EXHAUSTED

    def test_no_lease_blocks_eligibility(self) -> None:
        result = _control_plane().check_eligibility(
            _action(),
            budget_available=True,
            lease_held=False,
            prior_action_halted=False,
            preconditions_hold=True,
        )
        assert result.denial_stage == DenialStage.LEASE_CONFLICT

    def test_eligibility_grants_nothing_by_itself(self) -> None:
        result = _control_plane().check_eligibility(
            _action(),
            budget_available=True,
            lease_held=True,
            prior_action_halted=False,
            preconditions_hold=True,
        )
        assert not hasattr(result, "grant_id")
