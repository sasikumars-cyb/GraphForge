"""Tests for app.decision.contracts — the EngineeringDecision contract.

These verify the invariants that make the contract worth having: claims cannot
be made without evidence, ids referenced by a verdict must exist, and an
"approve" cannot carry a condition. Each of these is a bug the platform has
either already shipped once in a hand-written rendering, or could ship the
moment a renderer is written by hand again.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from app.decision.contracts import (
    AffectedEntity,
    ChangeSummary,
    DiffStat,
    EngineeringDecision,
    EvidenceGap,
    GraphEdgeRef,
    MergeRecommendation,
    OpenQuestion,
    ReviewerAction,
)
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.evidence import EvidenceItem, EvidenceReference
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance


def _confidence(
    state: ConfidenceState = ConfidenceState.VERIFIED,
    *,
    sources: frozenset[str] = frozenset({"code", "openapi"}),
    contradictions: int = 0,
) -> ConfidenceModel:
    return ConfidenceModel(
        state=state,
        distinct_confirming_source_types=len(sources),
        confirming_source_types=sources,
        max_confirming_reliability_tier=2 if sources else 0,
        contradiction_count=contradictions,
        computed_at=datetime(2026, 8, 6, tzinfo=UTC),
        formula_version="1.0.0",
    )


def _evidence(locator: str = "src/OrderService.java") -> EvidenceItem:
    return EvidenceItem(
        id=f"ev-{locator}",
        kind="call_chain",
        source_type="code",
        reliability_tier=2,
        reference=EvidenceReference(
            repository_id="repo-1",
            source_type="code",
            locator=locator,
            line=142,
        ),
        raw_value="inventoryClient.invalidateCache(orderId)",
        provenance=Provenance(
            generator=GeneratorIdentity(kind="deterministic", name="java_parser", version="1.0.0"),
            produced_at=datetime(2026, 8, 6, tzinfo=UTC),
            pack_id="pack-1",
            pack_version="v1",
            run_id="run-1",
        ),
    )


def _entity(
    entity_id: str = "repo-1:service:inventory",
    *,
    state: ConfidenceState = ConfidenceState.VERIFIED,
    **overrides: object,
) -> AffectedEntity:
    defaults: dict[str, object] = dict(
        entity_id=entity_id,
        entity_type="service",
        entity_name="inventory-service",
        confidence=_confidence(state),
        origin="deterministic",
    )
    defaults.update(overrides)
    return AffectedEntity(**defaults)  # type: ignore[arg-type]


def _change_summary() -> ChangeSummary:
    return ChangeSummary(
        files_changed=("src/OrderService.java",),
        capabilities_touched=("repo-1:capability:place_order",),
        change_kind="modification",
        diff_stat=DiffStat(files_changed=1, lines_added=12, lines_removed=3),
    )


def _decision(**overrides: object) -> EngineeringDecision:
    defaults: dict[str, object] = dict(
        decision_id="dec-1",
        pull_request_id="pr-1",
        commit_sha="abc123",
        computed_at=datetime(2026, 8, 6, tzinfo=UTC),
        model_version="1.0.0",
        change_summary=_change_summary(),
        merge_recommendation=MergeRecommendation(verdict="approve", reasoning="All confirmed."),
    )
    defaults.update(overrides)
    return EngineeringDecision(**defaults)  # type: ignore[arg-type]


class TestImmutability:
    def test_decision_is_frozen(self) -> None:
        decision = _decision()
        with pytest.raises(FrozenInstanceError):
            decision.commit_sha = "def456"  # type: ignore[misc]

    def test_affected_entity_is_frozen(self) -> None:
        entity = _entity()
        with pytest.raises(FrozenInstanceError):
            entity.entity_name = "renamed"  # type: ignore[misc]


class TestEvidenceBacksEveryClaim:
    def test_risk_note_requires_evidence(self) -> None:
        """A risk claim with nothing to point at is the assertion this
        platform exists to refuse — enforced at construction, not review."""
        with pytest.raises(ValueError, match="requires at least one supporting or contradicting"):
            _entity(risk_note="Inventory cache may become stale")

    def test_risk_note_allowed_with_supporting_evidence(self) -> None:
        entity = _entity(
            risk_note="Inventory cache may become stale",
            supporting_evidence=(_evidence(),),
        )
        assert entity.risk_note == "Inventory cache may become stale"

    def test_risk_note_allowed_with_only_contradicting_evidence(self) -> None:
        """Evidence that argues against the claim still grounds it — a note
        explaining why a conclusion was reached *despite* something is exactly
        the case worth surfacing."""
        entity = _entity(
            risk_note="Contradicted by runtime telemetry; retained for reviewer judgement",
            contradicting_evidence=(_evidence("telemetry/inventory.json"),),
        )
        assert entity.contradicting_evidence

    def test_blank_risk_note_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be blank"):
            _entity(risk_note="   ", supporting_evidence=(_evidence(),))

    def test_open_question_requires_a_reason(self) -> None:
        with pytest.raises(ValueError, match="why_unknown must not be empty"):
            OpenQuestion(
                question="Does this affect notifications?", why_unknown="", safety_relevant=True
            )

    def test_reviewer_action_requires_a_reason(self) -> None:
        with pytest.raises(ValueError, match="reason must not be empty"):
            ReviewerAction(
                action_id="act-1",
                action="review_diff",
                target="alice",
                reason="",
                blocking=False,
            )


class TestVerdictSelfConsistency:
    def test_approve_cannot_carry_a_blocking_condition(self) -> None:
        """The exact shape of the bug found in a hand-written PR comment:
        an unconditional 'safe to merge' headline over a body naming a
        condition. Unrepresentable here."""
        with pytest.raises(ValueError, match="must have no blocking_conditions"):
            MergeRecommendation(
                verdict="approve",
                reasoning="Safe to merge",
                blocking_conditions=("repo-1:service:inventory",),
            )

    def test_conditional_verdict_may_carry_conditions(self) -> None:
        recommendation = MergeRecommendation(
            verdict="approve_with_conditions",
            reasoning="Pending inventory confirmation",
            blocking_conditions=("act-1",),
        )
        assert recommendation.blocking_conditions == ("act-1",)

    def test_computed_by_defaults_to_deterministic_rule(self) -> None:
        """A stored decision must state on its face that no model chose the
        verdict — otherwise every historical record is ambiguous."""
        assert MergeRecommendation(verdict="approve", reasoning="ok").computed_by == (
            "deterministic_rule"
        )


class TestReferentialIntegrity:
    def test_blocking_condition_must_reference_a_known_id(self) -> None:
        with pytest.raises(ValueError, match="reference ids not present"):
            _decision(
                merge_recommendation=MergeRecommendation(
                    verdict="approve_with_conditions",
                    reasoning="Pending something",
                    blocking_conditions=("act-does-not-exist",),
                ),
            )

    def test_blocking_condition_resolves_against_an_entity_id(self) -> None:
        decision = _decision(
            affected_entities=(_entity(state=ConfidenceState.CANDIDATE),),
            merge_recommendation=MergeRecommendation(
                verdict="request_changes",
                reasoning="Not confident enough",
                blocking_conditions=("repo-1:service:inventory",),
            ),
        )
        assert decision.merge_recommendation.blocking_conditions == ("repo-1:service:inventory",)

    def test_blocking_condition_resolves_against_an_action_id(self) -> None:
        decision = _decision(
            reviewer_actions=(
                ReviewerAction(
                    action_id="act-1",
                    action="confirm_with_owning_team",
                    target="inventory-team",
                    reason="Confirm cache invalidation",
                    blocking=True,
                ),
            ),
            merge_recommendation=MergeRecommendation(
                verdict="approve_with_conditions",
                reasoning="Pending inventory confirmation",
                blocking_conditions=("act-1",),
            ),
        )
        assert decision.merge_recommendation.verdict == "approve_with_conditions"

    def test_duplicate_entity_ids_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate entity_ids"):
            _decision(affected_entities=(_entity(), _entity()))

    def test_duplicate_action_ids_are_rejected(self) -> None:
        action = ReviewerAction(
            action_id="act-1",
            action="review_diff",
            target="alice",
            reason="Review the diff",
            blocking=False,
        )
        with pytest.raises(ValueError, match="duplicate action_ids"):
            _decision(reviewer_actions=(action, action))


class TestComposedTypes:
    def test_confidence_is_the_real_knowledge_engine_model(self) -> None:
        """Impact Check consumes the platform's confidence machine rather than
        restating it — this is what makes 'verified' mean one thing product-wide."""
        entity = _entity()
        assert isinstance(entity.confidence, ConfidenceModel)
        assert entity.confidence.state is ConfidenceState.VERIFIED

    def test_evidence_is_dereferenceable_to_a_locator(self) -> None:
        """Evidence must be a place a reviewer can open, not a category label
        like 'call chain' — the flaw in the original hand-written comment."""
        entity = _entity(supporting_evidence=(_evidence(),))
        reference = entity.supporting_evidence[0].reference
        assert reference.locator == "src/OrderService.java"
        assert reference.line == 142

    def test_relationship_path_records_the_actual_route(self) -> None:
        entity = _entity(
            relationship_path=(
                GraphEdgeRef(
                    from_node_id="repo-1:service:billing",
                    to_node_id="repo-1:service:inventory",
                    edge_type="CALLS_SERVICE",
                ),
            )
        )
        assert entity.relationship_path[0].edge_type == "CALLS_SERVICE"


class TestValueObjectValidation:
    def test_diff_stat_rejects_negative_counts(self) -> None:
        with pytest.raises(ValueError, match="lines_added must not be negative"):
            DiffStat(files_changed=1, lines_added=-1, lines_removed=0)

    def test_evidence_gap_requires_a_target(self) -> None:
        with pytest.raises(ValueError, match="target_entity_id must not be empty"):
            EvidenceGap(
                target_entity_id="",
                current_state="candidate",
                would_reach_state="verified",
                suggested_evidence_kind="runtime_telemetry",
            )

    def test_graph_edge_ref_requires_all_three_parts(self) -> None:
        with pytest.raises(ValueError, match="edge_type must not be empty"):
            GraphEdgeRef(from_node_id="a", to_node_id="b", edge_type="")

    def test_decision_requires_a_model_version(self) -> None:
        with pytest.raises(ValueError, match="model_version must not be empty"):
            _decision(model_version="")
