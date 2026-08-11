"""Enforces the rule that makes the decision model worth having: **no surface
may contain a fact the decision does not carry.**

Without this, "every renderer is a projection" is a convention, and conventions
erode the first time someone needs a PR comment to say one more thing. These
tests make erosion a test failure: a renderer that invents an entity name, a
verdict word, or an evidence locator fails here rather than in production, in
front of a reviewer, as a headline contradicting its own body.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from app.decision.contracts import (
    AffectedEntity,
    ChangeSummary,
    DiffStat,
    EngineeringDecision,
    EvidenceGap,
    OpenQuestion,
    ReviewerAction,
)
from app.decision.merge_rule import derive_merge_recommendation
from app.decision.renderers.check_run import render_check_run
from app.decision.renderers.pr_comment import render_pr_comment
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.evidence import EvidenceItem, EvidenceReference
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance


def _confidence(state: ConfidenceState, *, sources: int = 2) -> ConfidenceModel:
    chosen = frozenset(sorted({"code", "openapi", "telemetry"})[:sources])
    return ConfidenceModel(
        state=state,
        distinct_confirming_source_types=len(chosen),
        confirming_source_types=chosen,
        max_confirming_reliability_tier=2 if chosen else 0,
        contradiction_count=1 if state == ConfidenceState.CONFLICTING else 0,
        computed_at=datetime(2026, 8, 6, tzinfo=UTC),
        formula_version="1.0.0",
    )


def _evidence(locator: str, *, kind: str = "call_chain", line: int | None = 142) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev-{locator}",
        kind=kind,
        source_type="code",
        reliability_tier=2,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="code", locator=locator, line=line
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
    name: str,
    state: ConfidenceState,
    *,
    origin: str = "deterministic",
    supporting: tuple[EvidenceItem, ...] = (),
    contradicting: tuple[EvidenceItem, ...] = (),
    risk_note: str | None = None,
) -> AffectedEntity:
    return AffectedEntity(
        entity_id=f"repo-1:service:{name}",
        entity_type="service",
        entity_name=name,
        confidence=_confidence(state),
        origin=origin,  # type: ignore[arg-type]
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        risk_note=risk_note,
    )


def _decision(
    entities: tuple[AffectedEntity, ...],
    *,
    questions: tuple[OpenQuestion, ...] = (),
    gaps: tuple[EvidenceGap, ...] = (),
    actions: tuple[ReviewerAction, ...] = (),
) -> EngineeringDecision:
    """Builds a decision whose verdict is *derived*, never hand-set — the only
    construction path any production caller should use."""
    return EngineeringDecision(
        decision_id="dec-abc123",
        pull_request_id="pr-42",
        commit_sha="abc123def456",
        computed_at=datetime(2026, 8, 6, tzinfo=UTC),
        model_version="1.0.0",
        change_summary=ChangeSummary(
            files_changed=("src/OrderService.java",),
            capabilities_touched=("repo-1:capability:place_order",),
            change_kind="modification",
            diff_stat=DiffStat(files_changed=1, lines_added=12, lines_removed=3),
        ),
        affected_entities=entities,
        open_questions=questions,
        evidence_gaps=gaps,
        reviewer_actions=actions,
        merge_recommendation=derive_merge_recommendation(
            affected_entities=entities,
            open_questions=questions,
            reviewer_actions=actions,
        ),
    )


def _the_original_bug_decision() -> EngineeringDecision:
    """Billing and Payments confirmed, Inventory not — the situation whose
    hand-written rendering claimed 'Verified / Safe to Merge' while also
    saying 'Safe after Inventory approval'."""
    return _decision(
        entities=(
            _entity(
                "billing",
                ConfidenceState.VERIFIED,
                supporting=(_evidence("billing/src/OrderService.java"),),
            ),
            _entity(
                "payments",
                ConfidenceState.VERIFIED,
                supporting=(_evidence("payments/src/Ledger.java"),),
            ),
            _entity(
                "inventory",
                ConfidenceState.CANDIDATE,
                supporting=(_evidence("inventory/openapi.yaml", kind="api_contract", line=None),),
                risk_note="Inventory cache may become stale",
            ),
        ),
        questions=(
            OpenQuestion(
                question="Does this change affect the notifications service?",
                why_unknown="No graph edge exists, but two related incidents correlate",
                safety_relevant=False,
            ),
        ),
        gaps=(
            EvidenceGap(
                target_entity_id="repo-1:service:inventory",
                current_state="candidate",
                would_reach_state="verified",
                suggested_evidence_kind="runtime_telemetry",
            ),
        ),
        actions=(
            ReviewerAction(
                action_id="act-inventory",
                action="confirm_with_owning_team",
                target="inventory-team",
                reason="Confirm cache invalidation behavior",
                blocking=True,
            ),
            ReviewerAction(
                action_id="act-review",
                action="review_diff",
                target="alice",
                reason="Review the diff",
                blocking=False,
            ),
        ),
    )


class TestNoUndeclaredFacts:
    """Every proper noun a surface prints must be traceable to the decision."""

    def _declared_vocabulary(self, decision: EngineeringDecision) -> set[str]:
        vocabulary = {
            decision.decision_id,
            decision.pull_request_id,
            decision.commit_sha,
            decision.merge_recommendation.verdict,
        }
        for entity in decision.affected_entities:
            vocabulary |= {entity.entity_id, entity.entity_name, entity.confidence.state.value}
            for item in (*entity.supporting_evidence, *entity.contradicting_evidence):
                vocabulary |= {item.kind, item.reference.locator}
        for action in decision.reviewer_actions:
            vocabulary |= {action.action_id, action.target}
        for gap in decision.evidence_gaps:
            vocabulary |= {gap.target_entity_id, gap.suggested_evidence_kind}
        return vocabulary

    def test_pr_comment_invents_no_entity_name(self) -> None:
        decision = _the_original_bug_decision()
        rendered = render_pr_comment(decision)
        declared = self._declared_vocabulary(decision)

        # Every backticked token in the output is a machine-readable
        # identifier - a locator, an id, an evidence kind. None may be novel.
        for token in re.findall(r"`([^`]+)`", rendered):
            bare = token.split(":")[0] if token.count(":") > 2 else token
            assert any(
                bare in candidate or candidate in token for candidate in declared
            ), f"PR comment printed `{token}`, which is not derivable from the decision"

    def test_pr_comment_names_only_entities_on_the_decision(self) -> None:
        decision = _the_original_bug_decision()
        rendered = render_pr_comment(decision)
        assert "notifications-service" not in rendered
        for entity in decision.affected_entities:
            assert entity.entity_name in rendered

    def test_check_run_summary_is_the_decisions_own_reasoning_verbatim(self) -> None:
        """Not a re-worded version — a paraphrase is a second opinion."""
        decision = _the_original_bug_decision()
        assert render_check_run(decision).summary == decision.merge_recommendation.reasoning


class TestSurfacesAgreeWithEachOther:
    @pytest.mark.parametrize(
        ("state", "expected_conclusion"),
        [
            (ConfidenceState.VERIFIED, "success"),
            (ConfidenceState.CONFLICTING, "failure"),
            (ConfidenceState.CANDIDATE, "action_required"),
        ],
    )
    def test_check_run_conclusion_follows_the_verdict(
        self, state: ConfidenceState, expected_conclusion: str
    ) -> None:
        decision = _decision(entities=(_entity("inventory", state),))
        assert render_check_run(decision).conclusion == expected_conclusion

    def test_pr_comment_headline_and_check_run_never_disagree(self) -> None:
        """The core guarantee. A conditional verdict must not render as an
        unconditional pass on any surface."""
        decision = _the_original_bug_decision()
        comment = render_pr_comment(decision)
        check = render_check_run(decision)

        assert decision.merge_recommendation.verdict == "approve_with_conditions"
        assert comment.startswith("## ⚠️ Approve with conditions")
        assert check.conclusion == "neutral"
        assert check.gate_passed is False

    def test_a_conditional_approval_does_not_pass_the_gate(self) -> None:
        decision = _the_original_bug_decision()
        assert render_check_run(decision).gate_passed is False

    def test_a_clean_approval_passes_the_gate(self) -> None:
        decision = _decision(entities=(_entity("billing", ConfidenceState.VERIFIED),))
        assert render_check_run(decision).gate_passed is True


class TestTheOriginalBugCannotBeRendered:
    def test_headline_is_conditional_not_safe_to_merge(self) -> None:
        rendered = render_pr_comment(_the_original_bug_decision())
        headline = rendered.splitlines()[0]
        assert "Approve with conditions" in headline
        assert "Safe to merge" not in headline

    def test_the_condition_appears_above_the_fold_not_only_at_the_bottom(self) -> None:
        """The original comment buried 'safe after Inventory approval' beneath
        five sections. Here the condition travels in the verdict's own
        reasoning, which renders third line."""
        rendered = render_pr_comment(_the_original_bug_decision())
        head = "\n".join(rendered.splitlines()[:4])
        assert "cache invalidation" in head

    def test_evidence_renders_as_a_locator_not_a_category_label(self) -> None:
        """'call chain' alone tells a reviewer a kind of evidence exists
        without letting them check it."""
        rendered = render_pr_comment(_the_original_bug_decision())
        assert "`billing/src/OrderService.java:142`" in rendered
        assert "`inventory/openapi.yaml`" in rendered

    def test_unconfirmed_entity_is_visually_distinct_from_confirmed_ones(self) -> None:
        rendered = render_pr_comment(_the_original_bug_decision())
        assert "✓ **billing**" in rendered
        assert "⚠ **inventory**" in rendered

    def test_blocking_and_advisory_actions_are_partitioned(self) -> None:
        rendered = render_pr_comment(_the_original_bug_decision())
        assert "### Required before merge" in rendered
        assert "inventory-team" in rendered
        assert "### Suggested" in rendered
        assert "alice" in rendered


class TestRenderingTheHonestCases:
    def test_contradicting_evidence_renders_beside_the_claim(self) -> None:
        decision = _decision(
            entities=(
                _entity(
                    "inventory",
                    ConfidenceState.CONFLICTING,
                    supporting=(_evidence("inventory/src/Cache.java"),),
                    contradicting=(
                        _evidence("telemetry/inventory.json", kind="runtime_telemetry", line=None),
                    ),
                ),
            )
        )
        rendered = render_pr_comment(decision)
        assert "✗ contradicts:" in rendered
        assert "`telemetry/inventory.json`" in rendered

    def test_ruled_out_entities_are_partitioned_from_live_ones(self) -> None:
        decision = _decision(
            entities=(
                _entity("billing", ConfidenceState.VERIFIED),
                _entity("notifications", ConfidenceState.REJECTED),
            )
        )
        rendered = render_pr_comment(decision)
        assert "### Considered and ruled out" in rendered
        affected_section = rendered.split("### Considered and ruled out")[0]
        assert "notifications" not in affected_section

    def test_ai_inferred_claims_are_labeled_as_such(self) -> None:
        decision = _decision(
            entities=(_entity("inventory", ConfidenceState.CANDIDATE, origin="llm_inferred"),)
        )
        rendered = render_pr_comment(decision)
        assert "proposed by AI, not independently confirmed" in rendered

    def test_no_affected_entities_says_so_explicitly(self) -> None:
        rendered = render_pr_comment(_decision(entities=()))
        assert "_No downstream entities were found to be affected._" in rendered

    def test_footer_states_the_verdict_was_not_model_chosen(self) -> None:
        rendered = render_pr_comment(_decision(entities=()))
        assert "verdict computed by deterministic rule" in rendered


class TestPurity:
    def test_renderers_are_deterministic(self) -> None:
        decision = _the_original_bug_decision()
        assert render_pr_comment(decision) == render_pr_comment(decision)
        assert render_check_run(decision) == render_check_run(decision)
