"""Contract tests for `app.control_plane.model` — construction-time shape
enforcement only; no pipeline evaluation happens here (that's
`test_control_plane_pipeline.py`)."""

from __future__ import annotations

import uuid

import pytest

from app.control_plane.model import (
    Action,
    ActionProposal,
    ConformanceResult,
    DenialStage,
    EligibilityResult,
    ModelError,
    Prediction,
)


def _prediction(**overrides: object) -> Prediction:
    defaults: dict[str, object] = {
        "target_observable": "data",
        "falsification_condition": "data is empty when a non-empty result was expected",
        "evaluation_procedure": "compare data.keys() against expected keys",
        "execution_context": {"snapshot": "abc"},
        "necessary_condition_rationale": "the PlanStep needs graph data to proceed",
    }
    defaults.update(overrides)
    return Prediction(**defaults)  # type: ignore[arg-type]


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


class TestPrediction:
    def test_valid_prediction_constructs(self) -> None:
        assert _prediction().target_observable == "data"

    @pytest.mark.parametrize(
        "field",
        [
            "target_observable",
            "falsification_condition",
            "evaluation_procedure",
            "necessary_condition_rationale",
        ],
    )
    def test_blank_required_field_rejected(self, field: str) -> None:
        with pytest.raises(ModelError):
            _prediction(**{field: "   "})


class TestAction:
    def test_valid_action_constructs(self) -> None:
        assert _action().capability_id == "query_knowledge_graph"

    def test_blank_capability_id_rejected(self) -> None:
        with pytest.raises(ModelError):
            _action(capability_id="")

    def test_zero_capability_version_rejected(self) -> None:
        with pytest.raises(ModelError):
            _action(capability_version=0)

    def test_expected_artifact_identity_defaults_to_none(self) -> None:
        assert _action().expected_artifact_identity is None


class TestActionProposal:
    def test_valid_proposal_constructs(self) -> None:
        proposal = ActionProposal(
            proposal_id=uuid.uuid4(),
            task_id=uuid.uuid4(),
            goal_id=uuid.uuid4(),
            proposing_role="dependency_query_agent",
            actions=(_action(),),
            engineering_state_snapshot_event_id=uuid.uuid4(),
        )
        assert len(proposal.actions) == 1

    def test_empty_actions_rejected(self) -> None:
        with pytest.raises(ModelError):
            ActionProposal(
                proposal_id=uuid.uuid4(),
                task_id=uuid.uuid4(),
                goal_id=uuid.uuid4(),
                proposing_role="role",
                actions=(),
                engineering_state_snapshot_event_id=None,
            )

    def test_duplicate_action_ids_rejected(self) -> None:
        shared_id = uuid.uuid4()
        with pytest.raises(ModelError):
            ActionProposal(
                proposal_id=uuid.uuid4(),
                task_id=uuid.uuid4(),
                goal_id=uuid.uuid4(),
                proposing_role="role",
                actions=(_action(action_id=shared_id), _action(action_id=shared_id)),
                engineering_state_snapshot_event_id=None,
            )

    def test_blank_proposing_role_rejected(self) -> None:
        with pytest.raises(ModelError):
            ActionProposal(
                proposal_id=uuid.uuid4(),
                task_id=uuid.uuid4(),
                goal_id=uuid.uuid4(),
                proposing_role="  ",
                actions=(_action(),),
                engineering_state_snapshot_event_id=None,
            )


class TestConformanceResult:
    def test_conformant_with_denial_is_rejected(self) -> None:
        with pytest.raises(ModelError):
            ConformanceResult(
                proposal_id=uuid.uuid4(),
                conformant=True,
                denial_stage=DenialStage.SCOPE_VIOLATION,
                denial_reason="whatever",
            )

    def test_non_conformant_without_denial_is_rejected(self) -> None:
        with pytest.raises(ModelError):
            ConformanceResult(
                proposal_id=uuid.uuid4(), conformant=False, denial_stage=None, denial_reason=None
            )

    def test_conformant_without_denial_is_valid(self) -> None:
        result = ConformanceResult(
            proposal_id=uuid.uuid4(), conformant=True, denial_stage=None, denial_reason=None
        )
        assert result.conformant


class TestEligibilityResult:
    def test_eligible_with_denial_is_rejected(self) -> None:
        with pytest.raises(ModelError):
            EligibilityResult(
                action_id=uuid.uuid4(),
                eligible=True,
                denial_stage=DenialStage.BUDGET_EXHAUSTED,
                denial_reason="whatever",
            )

    def test_ineligible_without_denial_is_rejected(self) -> None:
        with pytest.raises(ModelError):
            EligibilityResult(
                action_id=uuid.uuid4(), eligible=False, denial_stage=None, denial_reason=None
            )
