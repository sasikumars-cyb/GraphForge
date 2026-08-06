"""The `EngineeringDecision` contract and its component types.

Design rule this module exists to enforce: **a claim may not be made without
the evidence that backs it travelling alongside it.** Every type below binds
its assertions to `EvidenceItem`s at the point the assertion is made, rather
than collecting evidence into one pile at the document level. A per-document
evidence list cannot answer "which of these facts supports *that* specific
claim" — and a renderer given such a list has no choice but to guess, which
is how a UI ends up displaying evidence that argues for a different
conclusion than the one printed above it.

The second rule: **`MergeRecommendation` is computed, never supplied.**
Nothing in this module lets a caller construct an `EngineeringDecision` with
a hand-chosen verdict — see `app.decision.merge_rule.derive_merge_recommendation`,
the only supported way to produce one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.knowledge_engine.contracts.confidence import ConfidenceModel
from app.knowledge_engine.contracts.evidence import EvidenceItem

# Where a claim came from. `deterministic` = derived from the indexer/graph
# with no model involved; `llm_inferred` = a model proposed it and no
# independent non-LLM source has confirmed it; `hybrid` = a model narrated
# or ranked something whose underlying relationship is graph-derived.
#
# This is deliberately separate from `ConfidenceModel.state`: a claim can be
# `llm_inferred` and still be `VERIFIED` (a model proposed it, deterministic
# validators later confirmed it), and a claim can be `deterministic` and only
# `CANDIDATE` (structurally derived but only one weak source so far). Reviewers
# ask both questions and they have different answers.
ClaimOrigin = Literal["deterministic", "llm_inferred", "hybrid"]

EntityType = Literal["service", "repository", "capability", "database", "topic"]

ChangeKind = Literal["addition", "modification", "deletion", "rename", "config_only"]

# What the reviewer is being asked to do. Kept a closed vocabulary (unlike
# `EvidenceItem.kind`, which ADR 0018 deliberately leaves open) because every
# renderer — a Slack button, a Check Run annotation, a UI checklist — needs to
# map each action to a concrete affordance. An open vocabulary here would mean
# a renderer silently falling back to plain text for an action it can't handle.
ReviewerActionKind = Literal[
    "review_diff",
    "confirm_with_owning_team",
    "run_test",
    "approve_gate",
    "escalate",
]

MergeVerdict = Literal["approve", "approve_with_conditions", "request_changes", "block"]


@dataclass(frozen=True)
class DiffStat:
    """Plain counting facts about the diff. No claim, no inference — this is
    the one part of a decision that is true by observation alone."""

    files_changed: int
    lines_added: int
    lines_removed: int

    def __post_init__(self) -> None:
        if self.files_changed < 0:
            raise ValueError("DiffStat.files_changed must not be negative")
        if self.lines_added < 0:
            raise ValueError("DiffStat.lines_added must not be negative")
        if self.lines_removed < 0:
            raise ValueError("DiffStat.lines_removed must not be negative")


@dataclass(frozen=True)
class GraphEdgeRef:
    """One traversed edge on the path from the change to an affected entity.

    Carried as a list on `AffectedEntity` rather than summarized into prose so
    a reviewer can see *the actual route* the reasoning took — "billing calls
    inventory via CALLS_SERVICE, which SHARES_TOPIC with notifications" — and
    disagree with a specific hop rather than with an opaque conclusion.
    """

    from_node_id: str
    to_node_id: str
    edge_type: str

    def __post_init__(self) -> None:
        if not self.from_node_id.strip():
            raise ValueError("GraphEdgeRef.from_node_id must not be empty")
        if not self.to_node_id.strip():
            raise ValueError("GraphEdgeRef.to_node_id must not be empty")
        if not self.edge_type.strip():
            raise ValueError("GraphEdgeRef.edge_type must not be empty")


@dataclass(frozen=True)
class ChangeSummary:
    """What changed — question 1, and the only section guaranteed to contain
    zero inference.

    `capabilities_touched` holds graph capability-node ids the diff maps onto,
    resolved by the indexer from the parsed AST, not proposed by a model. If a
    change cannot be mapped to a capability deterministically, that absence is
    reported as an `OpenQuestion` on the decision — it is never papered over
    with a model's guess about what the change "probably" touches, because a
    guess recorded here would be indistinguishable from a fact to every
    downstream consumer.
    """

    files_changed: tuple[str, ...]
    capabilities_touched: tuple[str, ...]
    change_kind: ChangeKind
    diff_stat: DiffStat


@dataclass(frozen=True)
class AffectedEntity:
    """One thing the change could affect, with everything needed to judge that
    claim attached to the claim itself — questions 2 through 6.

    `contradicting_evidence` is a required part of the shape rather than an
    optional extra. A system that only reports evidence agreeing with its own
    conclusion is not doing analysis, it is doing advocacy; keeping the field
    structural means a renderer cannot quietly omit the inconvenient half, and
    a reviewer can see that a conclusion was reached *despite* something.

    `risk_note` is guarded by `__post_init__`: prose asserting a risk may only
    exist where evidence exists to point at. This is the platform's "evidence
    over assertion" principle enforced at construction time rather than left
    to reviewer discipline — the same reason `ConfidenceModel` validates its
    own counters instead of trusting callers.
    """

    entity_id: str
    entity_type: EntityType
    entity_name: str
    confidence: ConfidenceModel
    origin: ClaimOrigin
    relationship_path: tuple[GraphEdgeRef, ...] = field(default_factory=tuple)
    supporting_evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    contradicting_evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    risk_note: str | None = None

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("AffectedEntity.entity_id must not be empty")
        if not self.entity_name.strip():
            raise ValueError("AffectedEntity.entity_name must not be empty")
        if self.risk_note is not None:
            if not self.risk_note.strip():
                raise ValueError(
                    "AffectedEntity.risk_note must not be blank — omit it (None) instead"
                )
            if not self.supporting_evidence and not self.contradicting_evidence:
                raise ValueError(
                    "AffectedEntity.risk_note requires at least one supporting or "
                    "contradicting EvidenceItem — a risk claim with no evidence to "
                    "point at is exactly the assertion this platform refuses to make"
                )


@dataclass(frozen=True)
class OpenQuestion:
    """What remains unknown — question 7.

    Present as a populated list entry, never as silence. A reviewer must be
    able to distinguish "we checked Notifications and it is unaffected" from
    "we never established whether Notifications is affected"; a low confidence
    score on an entity that was never surfaced cannot express the second one,
    because there is no entity to carry the score.

    `safety_relevant` is read directly by the merge rule: an unknown that could
    hide a real consequence blocks an unconditional approval, while an unknown
    that is merely incomplete context does not.
    """

    question: str
    why_unknown: str
    safety_relevant: bool
    related_entity_id: str | None = None

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("OpenQuestion.question must not be empty")
        if not self.why_unknown.strip():
            raise ValueError(
                "OpenQuestion.why_unknown must not be empty — an unknown without a "
                "stated reason gives a reviewer nothing to act on"
            )


@dataclass(frozen=True)
class EvidenceGap:
    """What additional evidence would raise confidence — question 8.

    Deliberately actionable rather than descriptive: each gap names the
    specific entity it would move and the state it would move it *to*, so
    "add an integration test" carries the reason it is worth doing. Aggregated
    across decisions, the `suggested_evidence_kind` distribution is also the
    honest priority signal for which extractor or indexer capability to build
    next — the platform's own roadmap, derived from where it keeps running out
    of evidence, rather than from what is interesting to build.
    """

    target_entity_id: str
    current_state: str
    would_reach_state: str
    suggested_evidence_kind: str

    def __post_init__(self) -> None:
        if not self.target_entity_id.strip():
            raise ValueError("EvidenceGap.target_entity_id must not be empty")
        if not self.suggested_evidence_kind.strip():
            raise ValueError("EvidenceGap.suggested_evidence_kind must not be empty")


@dataclass(frozen=True)
class ReviewerAction:
    """What the reviewer should do — question 9.

    An action, a target, and the reason it exists — not a bare list of names.
    `reason` should reference the `entity_id` or question that produced it so
    every surface renders the same justification instead of each one inventing
    its own phrasing for why Alice was tagged.

    `blocking` is the single field the merge rule reads from here, which is
    why it is a plain bool rather than a severity scale: a scale would push the
    "does this stop the merge" judgement into whoever renders it, and two
    renderers would eventually draw the line differently.
    """

    action_id: str
    action: ReviewerActionKind
    target: str
    reason: str
    blocking: bool
    resolved: bool = False

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("ReviewerAction.action_id must not be empty")
        if not self.target.strip():
            raise ValueError("ReviewerAction.target must not be empty")
        if not self.reason.strip():
            raise ValueError(
                "ReviewerAction.reason must not be empty — an action a reviewer "
                "cannot trace to a finding is one they cannot prioritize"
            )


@dataclass(frozen=True)
class MergeRecommendation:
    """The merge verdict — question 10, and the only field in this contract
    that is *derived* rather than observed or measured.

    `computed_by` is a one-value Literal on purpose. It is not extension room
    for a future `"llm_judgment"`: it exists so that a stored decision, read
    back years later from the ledger, states on its face that no model chose
    this verdict. A field that could hold either value would make every
    historical record ambiguous about which mechanism produced it.

    `blocking_conditions` holds `ReviewerAction.action_id` and
    `AffectedEntity.entity_id` values, never free text, so a gate checking
    whether conditions have cleared resolves them against the decision itself
    rather than by parsing prose.
    """

    verdict: MergeVerdict
    reasoning: str
    blocking_conditions: tuple[str, ...] = field(default_factory=tuple)
    computed_by: Literal["deterministic_rule"] = "deterministic_rule"

    def __post_init__(self) -> None:
        if not self.reasoning.strip():
            raise ValueError("MergeRecommendation.reasoning must not be empty")
        if self.verdict == "approve" and self.blocking_conditions:
            raise ValueError(
                "MergeRecommendation with verdict 'approve' must have no "
                "blocking_conditions — an unconditional approval that names a "
                "condition is the self-contradiction this contract exists to "
                "make unrepresentable"
            )


@dataclass(frozen=True)
class EngineeringDecision:
    """The canonical record: one per (pull_request_id, commit_sha).

    Immutable and superseded rather than mutated — a new commit produces a new
    decision, and the prior one stays readable in the ledger. "Why did we block
    this six months ago" is answered by reading the decision that blocked it,
    not by reconstructing intent from whatever a chat surface happened to
    retain.

    `model_version` pins this contract's own shape, for the same reason
    `ConfidenceModel.formula_version` pins the aggregation logic: a consumer
    reading a stored decision needs to know whether its field semantics match
    the ones it was written under.
    """

    decision_id: str
    pull_request_id: str
    commit_sha: str
    computed_at: datetime
    model_version: str
    change_summary: ChangeSummary
    merge_recommendation: MergeRecommendation
    affected_entities: tuple[AffectedEntity, ...] = field(default_factory=tuple)
    open_questions: tuple[OpenQuestion, ...] = field(default_factory=tuple)
    evidence_gaps: tuple[EvidenceGap, ...] = field(default_factory=tuple)
    reviewer_actions: tuple[ReviewerAction, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            raise ValueError("EngineeringDecision.decision_id must not be empty")
        if not self.pull_request_id.strip():
            raise ValueError("EngineeringDecision.pull_request_id must not be empty")
        if not self.commit_sha.strip():
            raise ValueError("EngineeringDecision.commit_sha must not be empty")
        if not self.model_version.strip():
            raise ValueError("EngineeringDecision.model_version must not be empty")

        entity_ids = [entity.entity_id for entity in self.affected_entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError(
                "EngineeringDecision.affected_entities must not contain duplicate "
                "entity_ids — two rows for one entity would let two renderers "
                "disagree about its confidence"
            )

        action_ids = [action.action_id for action in self.reviewer_actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError(
                "EngineeringDecision.reviewer_actions must not contain duplicate action_ids"
            )

        # Every blocking condition must name something in this decision. A
        # condition pointing at an id that is not here cannot be resolved by
        # any consumer — the merge gate would wait forever on a condition it
        # has no way to look up.
        known_ids = set(entity_ids) | set(action_ids)
        unknown = [
            condition
            for condition in self.merge_recommendation.blocking_conditions
            if condition not in known_ids
        ]
        if unknown:
            raise ValueError(
                "EngineeringDecision.merge_recommendation.blocking_conditions "
                f"reference ids not present in this decision: {sorted(unknown)} — "
                "a condition no consumer can resolve can never be cleared"
            )
