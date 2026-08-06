"""GitHub PR comment projection — the surface a reviewer actually reads.

Three rules this renderer follows that the hand-written comment it replaces
did not:

1. **The headline is the verdict.** Not a separate "safe / unsafe" badge
   computed alongside it. There is one `MergeRecommendation` and the first line
   of the comment renders it, so a conditional approval cannot be introduced by
   a headline that says otherwise.

2. **Evidence is a place, not a category.** Every evidence line renders
   `EvidenceReference.locator` (plus line/commit where present), because "call
   chain" as a bare label tells a reviewer a kind of evidence exists without
   letting them go look at it - which is indistinguishable, from the reader's
   side, from having no evidence at all.

3. **Contradicting evidence renders next to the claim it argues against**,
   never omitted and never relegated to a footnote. A conclusion reached
   despite something is exactly the case a reviewer most needs to see.
"""

from __future__ import annotations

from app.decision.contracts import (
    AffectedEntity,
    EngineeringDecision,
    EvidenceGap,
    MergeVerdict,
    OpenQuestion,
    ReviewerAction,
)
from app.decision.merge_rule import in_scope_entities
from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.evidence import EvidenceItem

_VERDICT_HEADLINE: dict[MergeVerdict, str] = {
    "approve": "✅ Safe to merge",
    "approve_with_conditions": "⚠️ Approve with conditions",
    "request_changes": "🔍 Not enough evidence to assess",
    "block": "⛔ Blocked — contradictory evidence",
}

# Only two customer-facing confidence markers, deliberately, even though the
# engine underneath tracks six states. A reviewer acts on one question - "can I
# rely on this, or do I need to check it myself" - and a six-way gradient
# invites them to invent their own threshold for an answer the platform should
# be giving them.
_CONFIRMED_STATES = frozenset({ConfidenceState.VERIFIED, ConfidenceState.HIGHLY_LIKELY})


def _confidence_marker(entity: AffectedEntity) -> str:
    if entity.confidence.state == ConfidenceState.CONFLICTING:
        return "⛔"
    return "✓" if entity.confidence.state in _CONFIRMED_STATES else "⚠"


def _confidence_label(entity: AffectedEntity) -> str:
    """Human-readable confidence, with the source count that produced it.

    The count is included because it is the actual reason the state is what it
    is - a reviewer who disagrees with "not confirmed" can see that only one
    source has reported, rather than having to trust the adjective.
    """
    state = entity.confidence.state
    sources = entity.confidence.distinct_confirming_source_types
    if state in _CONFIRMED_STATES:
        return f"Confirmed by {sources} independent source{'s' if sources != 1 else ''}"
    if state == ConfidenceState.CONFLICTING:
        return f"Sources disagree ({entity.confidence.contradiction_count} contradicting)"
    return f"Not confirmed ({sources} source{'s' if sources != 1 else ''} so far)"


def _origin_note(entity: AffectedEntity) -> str:
    """Whether a claim is graph-derived or model-proposed, stated plainly.

    Kept separate from confidence because they answer different questions and a
    reviewer weighs them differently: an AI-proposed claim confirmed by three
    deterministic sources deserves more trust than a graph-derived claim with
    one weak one.
    """
    return {
        "deterministic": "derived from code structure",
        "llm_inferred": "proposed by AI, not independently confirmed",
        "hybrid": "AI-summarized, structurally derived",
    }[entity.origin]


def _render_evidence_line(item: EvidenceItem, prefix: str) -> str:
    reference = item.reference
    locator = reference.locator
    if reference.line is not None:
        locator = f"{locator}:{reference.line}"
    elif reference.key is not None:
        locator = f"{locator}#{reference.key}"
    return f"  {prefix} `{locator}` — {item.kind}"


def _render_entity(entity: AffectedEntity) -> list[str]:
    lines = [
        f"{_confidence_marker(entity)} **{entity.entity_name}** — "
        f"{_confidence_label(entity)} ({_origin_note(entity)})"
    ]
    for item in entity.supporting_evidence:
        lines.append(_render_evidence_line(item, "→"))
    for item in entity.contradicting_evidence:
        lines.append(_render_evidence_line(item, "✗ contradicts:"))
    if entity.risk_note:
        lines.append(f"  _{entity.risk_note}_")
    return lines


def _render_open_questions(questions: tuple[OpenQuestion, ...]) -> list[str]:
    lines = ["", "### Open questions", ""]
    for question in questions:
        marker = "**Safety-relevant:** " if question.safety_relevant else ""
        lines.append(f"- {marker}{question.question}")
        lines.append(f"  _{question.why_unknown}_")
    return lines


def _render_evidence_gaps(gaps: tuple[EvidenceGap, ...]) -> list[str]:
    lines = ["", "### What would raise confidence", ""]
    for gap in gaps:
        lines.append(
            f"- `{gap.target_entity_id}`: add {gap.suggested_evidence_kind} "
            f"→ would move {gap.current_state} to {gap.would_reach_state}"
        )
    return lines


def _render_actions(actions: tuple[ReviewerAction, ...]) -> list[str]:
    blocking = [action for action in actions if action.blocking and not action.resolved]
    advisory = [action for action in actions if not (action.blocking and not action.resolved)]

    lines: list[str] = []
    if blocking:
        lines += ["", "### Required before merge", ""]
        for action in blocking:
            lines.append(f"- [ ] **{action.target}** — {action.reason}")
    if advisory:
        lines += ["", "### Suggested", ""]
        for action in advisory:
            done = "x" if action.resolved else " "
            lines.append(f"- [{done}] {action.target} — {action.reason}")
    return lines


def render_pr_comment(decision: EngineeringDecision) -> str:
    """Project a decision onto GitHub-flavored markdown. Pure."""
    recommendation = decision.merge_recommendation
    lines = [
        f"## {_VERDICT_HEADLINE[recommendation.verdict]}",
        "",
        recommendation.reasoning,
    ]

    summary = decision.change_summary
    lines += [
        "",
        f"**Changed:** {summary.diff_stat.files_changed} file(s), "
        f"+{summary.diff_stat.lines_added}/-{summary.diff_stat.lines_removed} "
        f"({summary.change_kind})",
    ]

    live = in_scope_entities(decision.affected_entities)
    ruled_out = tuple(
        entity
        for entity in decision.affected_entities
        if entity.confidence.state == ConfidenceState.REJECTED
    )

    if live:
        lines += ["", "### Affected", ""]
        for entity in live:
            lines += _render_entity(entity)
    else:
        lines += ["", "### Affected", "", "_No downstream entities were found to be affected._"]

    if ruled_out:
        # Shown, but partitioned away from live entities - a reader must not
        # have to work out which rows the verdict was computed from.
        lines += ["", "### Considered and ruled out", ""]
        for entity in ruled_out:
            lines.append(f"- {entity.entity_name} — evidence contradicted the initial hypothesis")

    if decision.open_questions:
        lines += _render_open_questions(decision.open_questions)
    if decision.evidence_gaps:
        lines += _render_evidence_gaps(decision.evidence_gaps)
    if decision.reviewer_actions:
        lines += _render_actions(decision.reviewer_actions)

    lines += [
        "",
        "---",
        f"<sub>GraphForge decision `{decision.decision_id}` · commit "
        f"`{decision.commit_sha[:8]}` · verdict computed by "
        f"{recommendation.computed_by.replace('_', ' ')}</sub>",
    ]
    return "\n".join(lines)
