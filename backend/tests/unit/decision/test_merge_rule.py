"""Tests for app.decision.merge_rule — the deterministic verdict.

The rule is the one output a human acts on without necessarily reading the
evidence underneath it, so these tests cover every branch, the ordering
between branches, and — in `TestTheOriginalBug` — the specific real-world
situation whose hand-written rendering contradicted itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.decision.contracts import AffectedEntity, OpenQuestion, ReviewerAction
from app.decision.merge_rule import derive_merge_recommendation, in_scope_entities
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState


def _confidence(state: ConfidenceState) -> ConfidenceModel:
    sources: frozenset[str] = (
        frozenset({"code"}) if state != ConfidenceState.REJECTED else frozenset()
    )
    return ConfidenceModel(
        state=state,
        distinct_confirming_source_types=len(sources),
        confirming_source_types=sources,
        max_confirming_reliability_tier=2 if sources else 0,
        contradiction_count=(
            1 if state in (ConfidenceState.CONFLICTING, ConfidenceState.REJECTED) else 0
        ),
        computed_at=datetime(2026, 8, 6, tzinfo=UTC),
        formula_version="1.0.0",
    )


def _entity(name: str, state: ConfidenceState) -> AffectedEntity:
    return AffectedEntity(
        entity_id=f"repo-1:service:{name}",
        entity_type="service",
        entity_name=name,
        confidence=_confidence(state),
        origin="deterministic",
    )


def _action(action_id: str, *, blocking: bool, resolved: bool = False) -> ReviewerAction:
    return ReviewerAction(
        action_id=action_id,
        action="confirm_with_owning_team",
        target="inventory-team",
        reason="Confirm cache invalidation behavior",
        blocking=blocking,
        resolved=resolved,
    )


class TestApprove:
    def test_all_verified_approves(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(
                _entity("billing", ConfidenceState.VERIFIED),
                _entity("payments", ConfidenceState.VERIFIED),
            )
        )
        assert result.verdict == "approve"
        assert result.blocking_conditions == ()

    def test_highly_likely_is_good_enough_to_approve(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("billing", ConfidenceState.HIGHLY_LIKELY),)
        )
        assert result.verdict == "approve"

    def test_nothing_affected_approves(self) -> None:
        """A change touching nothing downstream is the correct approve, not an
        edge case to special-case at a call site."""
        result = derive_merge_recommendation(affected_entities=())
        assert result.verdict == "approve"
        assert "No downstream entities" in result.reasoning

    def test_non_safety_open_question_does_not_prevent_approval(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("billing", ConfidenceState.VERIFIED),),
            open_questions=(
                OpenQuestion(
                    question="Which team owns the legacy adapter?",
                    why_unknown="No CODEOWNERS entry found",
                    safety_relevant=False,
                ),
            ),
        )
        assert result.verdict == "approve"

    def test_resolved_blocking_action_does_not_hold_up_approval(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("billing", ConfidenceState.VERIFIED),),
            reviewer_actions=(_action("act-1", blocking=True, resolved=True),),
        )
        assert result.verdict == "approve"

    def test_singular_and_plural_reasoning_read_correctly(self) -> None:
        one = derive_merge_recommendation(
            affected_entities=(_entity("billing", ConfidenceState.VERIFIED),)
        )
        two = derive_merge_recommendation(
            affected_entities=(
                _entity("billing", ConfidenceState.VERIFIED),
                _entity("payments", ConfidenceState.VERIFIED),
            )
        )
        assert "1 affected entity is confirmed" in one.reasoning
        assert "2 affected entities are confirmed" in two.reasoning


class TestBlock:
    def test_conflicting_evidence_blocks(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(
                _entity("billing", ConfidenceState.VERIFIED),
                _entity("inventory", ConfidenceState.CONFLICTING),
            )
        )
        assert result.verdict == "block"
        assert result.blocking_conditions == ("repo-1:service:inventory",)
        assert "inventory" in result.reasoning

    def test_conflicting_outranks_an_unresolved_blocking_action(self) -> None:
        """Ordering matters: a contradiction cannot be cleared by a reviewer
        doing an assigned task, so it must not be reported as a condition."""
        result = derive_merge_recommendation(
            affected_entities=(_entity("inventory", ConfidenceState.CONFLICTING),),
            reviewer_actions=(_action("act-1", blocking=True),),
        )
        assert result.verdict == "block"


class TestApproveWithConditions:
    def test_unresolved_blocking_action_makes_approval_conditional(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("billing", ConfidenceState.VERIFIED),),
            reviewer_actions=(_action("act-1", blocking=True),),
        )
        assert result.verdict == "approve_with_conditions"
        assert result.blocking_conditions == ("act-1",)

    def test_non_blocking_action_does_not_make_approval_conditional(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("billing", ConfidenceState.VERIFIED),),
            reviewer_actions=(_action("act-1", blocking=False),),
        )
        assert result.verdict == "approve"


class TestRequestChanges:
    def test_under_confident_entity_with_no_assigned_action_requests_changes(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("inventory", ConfidenceState.CANDIDATE),)
        )
        assert result.verdict == "request_changes"
        assert result.blocking_conditions == ("repo-1:service:inventory",)
        assert "insufficient evidence" in result.reasoning

    def test_likely_is_not_confident_enough_for_unconditional_approval(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("inventory", ConfidenceState.LIKELY),)
        )
        assert result.verdict == "request_changes"

    def test_safety_relevant_unknown_requests_changes(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("billing", ConfidenceState.VERIFIED),),
            open_questions=(
                OpenQuestion(
                    question="Does this affect the notifications service?",
                    why_unknown="No graph edge exists, but two related incidents correlate",
                    safety_relevant=True,
                ),
            ),
        )
        assert result.verdict == "request_changes"
        assert "notifications" in result.reasoning


class TestRejectedEntitiesAreAuditRecordsNotRisks:
    def test_rejected_entity_is_excluded_from_scope(self) -> None:
        assert in_scope_entities((_entity("notifications", ConfidenceState.REJECTED),)) == ()

    def test_rejected_entity_does_not_prevent_approval(self) -> None:
        """'We checked and it is not affected' is the opposite of a risk
        signal — it must never drag the verdict down."""
        result = derive_merge_recommendation(
            affected_entities=(
                _entity("billing", ConfidenceState.VERIFIED),
                _entity("notifications", ConfidenceState.REJECTED),
            )
        )
        assert result.verdict == "approve"

    def test_a_decision_of_only_rejected_entities_approves(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(_entity("notifications", ConfidenceState.REJECTED),)
        )
        assert result.verdict == "approve"


class TestDeterminism:
    def test_same_inputs_produce_an_identical_recommendation(self) -> None:
        entities = (
            _entity("billing", ConfidenceState.VERIFIED),
            _entity("inventory", ConfidenceState.CANDIDATE),
        )
        assert derive_merge_recommendation(affected_entities=entities) == (
            derive_merge_recommendation(affected_entities=entities)
        )

    def test_every_verdict_is_computed_by_the_deterministic_rule(self) -> None:
        for entities in (
            (_entity("a", ConfidenceState.VERIFIED),),
            (_entity("a", ConfidenceState.CONFLICTING),),
            (_entity("a", ConfidenceState.CANDIDATE),),
        ):
            assert (
                derive_merge_recommendation(affected_entities=entities).computed_by
                == "deterministic_rule"
            )


class TestTheOriginalBug:
    """The situation whose hand-written rendering said 'Safe to Merge /
    Verified' at the top and 'Safe after Inventory approval' at the bottom:
    Billing and Payments confirmed, Inventory not yet, with the Inventory
    team assigned to confirm."""

    def test_the_verdict_is_conditional_not_a_bare_approval(self) -> None:
        result = derive_merge_recommendation(
            affected_entities=(
                _entity("billing", ConfidenceState.VERIFIED),
                _entity("payments", ConfidenceState.VERIFIED),
                _entity("inventory", ConfidenceState.CANDIDATE),
            ),
            reviewer_actions=(_action("act-inventory", blocking=True),),
        )
        assert result.verdict == "approve_with_conditions"
        assert result.verdict != "approve"
        assert result.blocking_conditions == ("act-inventory",)

    def test_the_headline_reasoning_names_the_condition_itself(self) -> None:
        """A renderer showing only `reasoning` still cannot present this as
        unconditionally safe — the condition travels with the verdict."""
        result = derive_merge_recommendation(
            affected_entities=(
                _entity("billing", ConfidenceState.VERIFIED),
                _entity("inventory", ConfidenceState.CANDIDATE),
            ),
            reviewer_actions=(_action("act-inventory", blocking=True),),
        )
        assert "conditional" in result.reasoning.lower()
        assert "cache invalidation" in result.reasoning
