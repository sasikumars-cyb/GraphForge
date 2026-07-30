"""The capability registry — what Context Discovery needs to know, declared
once per capability.

Everything about one capability lives in one `Capability` object: when it
applies, the weighted signals its confidence decomposes into, how to frame the
gap when it's unmet, what remediation to suggest, whether a human could be
usefully asked, and — critically — how to *verify* an answer if one is given.

This replaces a design where those concerns were scattered across three
places (an assessor function, a gap-template dict, and an if-chain of
question builders), so adding a capability meant three coordinated edits and
forgetting one failed silently. Adding `deployment_topology` or
`permissions` now means appending one entry to `CAPABILITIES`.

Two invariants the shape enforces:

**Confidence is derived, never reported.** A score is
`satisfied weight / total weight` over signals that are pure predicates on the
`Ledger`. No LLM supplies a number anywhere. Ask "why is architecture 65%?"
and the answer is the signal list:

    Architecture 65%
      ✓ Knowledge graph queried without errors (ev_a1b2c3)
      ✓ Architecture components discovered (ev_d4e5f6)
      ✗ Messaging topology discovered      — no Kafka topics found
      ✗ Scoped to the identified repository — repository not yet identified

**A capability is only askable if an answer can be verified.** `question` and
`verify` are declared together: no question may be asked whose answer could
not subsequently be corroborated by real investigation. That pairing is what
makes "verify before resolve" structural rather than a convention — a
capability cannot offer a question it has no way to check.

**Necessity is contextual.** A capability that cannot apply to this request is
`not_applicable` and is excluded from readiness and from overall confidence,
rather than scored 1.0 by default. Absent knowledge is absent, not perfect.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.context_pipeline.reasoning.ledger import Ledger

Necessity = Literal["required", "recommended", "not_applicable"]

# How much a capability's own score contributes to the single overall number
# surfaced to telemetry and the UI. Required counts double; `not_applicable`
# contributes nothing at all.
_NECESSITY_WEIGHT: dict[Necessity, float] = {
    "required": 2.0,
    "recommended": 1.0,
    "not_applicable": 0.0,
}

# A signal at or above this weight is load-bearing: readiness turns on it.
# Deliberately not a score threshold — "80% confident" and "the thing I
# actually needed is present" are different claims, and readiness should turn
# on the second.
LOAD_BEARING_WEIGHT = 2.0

_REPOSITORY_REFERENCE_TYPES = ("local_repository", "github_repository")

# The evidence action name the graph investigator records its traversal attempt
# under. `architecture` reads reachability from this record alone — see
# `_architecture_signals`.
GRAPH_TRAVERSAL_ACTION = "traverse_architecture_graph"


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


class ConfidenceSignal(BaseModel):
    """One decomposable reason a capability's confidence is what it is.

    `detail` must be meaningful when unsatisfied: it is what turns
    "✗ Graph available" — the entire explanation an earlier design offered for
    an unindexed graph — into something a user can act on.
    """

    label: str
    satisfied: bool
    weight: float
    detail: str = ""
    # Provenance: which investigations back this signal. Empty when
    # unsatisfied (nothing established it) — that asymmetry is informative and
    # the UI renders it as such.
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)


class CapabilityAssessment(BaseModel):
    """A capability's evidence-derived confidence plus its full decomposition.

    `score` is computed from `signals` in `from_signals` and never set
    independently, so the number and its explanation cannot drift apart.
    """

    capability: str
    label: str
    necessity: Necessity
    score: float
    signals: list[ConfidenceSignal] = Field(default_factory=list)

    @classmethod
    def from_signals(
        cls,
        *,
        capability: str,
        label: str,
        necessity: Necessity,
        signals: list[ConfidenceSignal],
    ) -> CapabilityAssessment:
        total = sum(s.weight for s in signals)
        satisfied = sum(s.weight for s in signals if s.satisfied)
        score = 0.0 if total == 0 else round(satisfied / total, 4)
        return cls(
            capability=capability,
            label=label,
            necessity=necessity,
            score=score,
            signals=signals,
        )

    @property
    def satisfied(self) -> bool:
        load_bearing = [s for s in self.signals if s.weight >= LOAD_BEARING_WEIGHT]
        return bool(load_bearing) and all(s.satisfied for s in load_bearing)

    @property
    def missing(self) -> list[ConfidenceSignal]:
        return [s for s in self.signals if not s.satisfied]

    def explanation(self) -> str:
        parts = [
            f"{'✓' if s.satisfied else '✗'} {s.label}"
            + (f" ({s.detail})" if s.detail and not s.satisfied else "")
            for s in self.signals
        ]
        return f"{self.score:.0%} — " + "; ".join(parts)


def signal(
    label: str,
    satisfied: bool,
    weight: float,
    *,
    detail: str = "",
    evidence_ids: list[str] | None = None,
    fact_ids: list[str] | None = None,
) -> ConfidenceSignal:
    """Build a signal, dropping provenance when unsatisfied — an unsatisfied
    signal has no evidence by definition, and carrying ids on one would imply
    the evidence supported a conclusion it doesn't."""
    return ConfidenceSignal(
        label=label,
        satisfied=satisfied,
        weight=weight,
        detail=detail,
        evidence_ids=(evidence_ids or []) if satisfied else [],
        fact_ids=(fact_ids or []) if satisfied else [],
    )


# ---------------------------------------------------------------------------
# Clarification
# ---------------------------------------------------------------------------


class ClarificationQuestion(BaseModel):
    """The single question discovery asks when investigation is exhausted.

    `why` is mandatory — a question without its reason makes the user guess at
    the engine's state.

    `options` are **real candidate values only** (repository names the graph
    actually contains), never instructions. Actions the user might take
    instead live in a gap's `recommended_action` and render separately,
    because an option that reads like a value but is really a UI verb is how
    an instruction label ends up being submitted as an answer.
    """

    question_id: str
    question: str
    why: str
    options: list[str] = Field(default_factory=list)
    # What was already tried before resorting to asking — shown with the
    # question so the user can see this wasn't the engine's first move.
    investigated: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class QuestionContext:
    """What a question builder gets to work with.

    Deliberately not the `KnowledgeGap` itself: gaps live in `memory`, which
    imports this module, so passing one would make the dependency circular.
    This carries the only parts a builder actually needs.
    """

    ledger: Ledger
    investigated: list[str]
    # Set when a previous answer was investigated and could not be
    # corroborated. A re-ask that ignores this reads as the engine having
    # forgotten the exchange.
    previous_claim: str | None = None

    @property
    def is_reask(self) -> bool:
        return self.previous_claim is not None


# ---------------------------------------------------------------------------
# The capability declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    """One thing discovery needs to know, fully specified.

    `question` and `verify` are a pair: a capability may only declare a
    question if it also declares how to check the answer. `_validate` enforces
    that at import time, so an unverifiable question cannot reach production.
    """

    key: str
    label: str
    # What this capability is for, in one line — used to frame the gap.
    gap_summary: str
    gap_why: str
    # All pure reads over the ledger.
    necessity: Callable[[Ledger], Necessity]
    signals: Callable[[Ledger], list[ConfidenceSignal]]
    remediation: Callable[[Ledger], list[str]]
    # None when no answer a human could give would help (nobody can index a
    # repository or connect Confluence by answering a question, and offering
    # those as answers is what let a UI verb be processed as a value).
    question: Callable[[QuestionContext], ClarificationQuestion | None] | None = None
    # Required whenever `question` is set: given the ledger *after* a
    # verification investigation ran, did the claim hold?
    verify: Callable[[Ledger, str], bool] | None = None

    def __post_init__(self) -> None:
        if (self.question is None) != (self.verify is None):
            raise ValueError(
                f"Capability {self.key!r} must declare `question` and `verify` together: a "
                "question may only be asked if its answer can be verified afterwards."
            )

    def assess(self, ledger: Ledger) -> CapabilityAssessment:
        return CapabilityAssessment.from_signals(
            capability=self.key,
            label=self.label,
            necessity=self.necessity(ledger),
            signals=self.signals(ledger),
        )

    @property
    def askable(self) -> bool:
        return self.question is not None


# ---------------------------------------------------------------------------
# work_item
# ---------------------------------------------------------------------------


def _jira_references(ledger: Ledger) -> list[str]:
    return [f.subject for f in ledger.facts_of("reference") if f.value.get("type") == "jira_issue"]


def _work_item_necessity(ledger: Ledger) -> Necessity:
    # Applicable only when the request actually references a work item —
    # otherwise there is no ticket to be confident or unconfident about.
    return "required" if _jira_references(ledger) else "not_applicable"


def _work_item_signals(ledger: Ledger) -> list[ConfidenceSignal]:
    refs = [f for f in ledger.facts_of("reference") if f.value.get("type") == "jira_issue"]
    work_items = ledger.facts_of("work_item")
    return [
        signal(
            "Work item content retrieved",
            bool(work_items),
            3.0,
            detail=(
                "the referenced ticket could not be fetched"
                if refs
                else "no work item referenced in the request"
            ),
            evidence_ids=[f.evidence_id for f in work_items],
            fact_ids=[f.fact_id for f in work_items],
        ),
        signal(
            "Work item reference recognized in the request",
            bool(refs),
            1.0,
            detail="no Jira-style reference found in the request text",
            evidence_ids=[f.evidence_id for f in refs],
            fact_ids=[f.fact_id for f in refs],
        ),
    ]


def _work_item_question(ctx: QuestionContext) -> ClarificationQuestion | None:
    referenced = _jira_references(ctx.ledger)
    named = referenced[0] if referenced else None
    if named is None:
        return None
    if ctx.is_reask:
        why = (
            f"'{ctx.previous_claim}' didn't resolve either. If Jira simply isn't reachable "
            "from here, no key will work and this needs connecting instead."
        )
    else:
        why = (
            f"{named} looks like a ticket reference, but fetching it returned nothing — "
            "either the key is wrong or Jira isn't reachable from here."
        )
    return ClarificationQuestion(
        question_id="gap_work_item",
        question=f"I couldn't retrieve {named}. If the key is wrong, what's the correct one?",
        why=why,
        options=[],
        investigated=ctx.investigated,
    )


def _verify_work_item(ledger: Ledger, claim: str) -> bool:
    return any(s.lower() == claim.lower() for s in ledger.subjects_of("work_item"))


# ---------------------------------------------------------------------------
# repository
# ---------------------------------------------------------------------------


def _repository_signals(ledger: Ledger) -> list[ConfidenceSignal]:
    repositories = ledger.facts_of("repository")
    candidates = ledger.live_inferences("repository_candidate")
    repo_names = {f.subject for f in repositories}
    matched_refs = [
        f
        for f in ledger.facts_of("reference")
        if f.value.get("type") in _REPOSITORY_REFERENCE_TYPES and f.subject in repo_names
    ]
    identified = len(candidates) == 1

    return [
        signal(
            "Indexed repositories available",
            bool(repositories),
            2.0,
            detail="no repositories are indexed in the knowledge graph",
            evidence_ids=ledger.evidence_for("repository"),
            fact_ids=[f.fact_id for f in repositories],
        ),
        signal(
            "Owning repository identified",
            identified,
            3.0,
            detail=(
                f"{len(candidates)} repositories are equally plausible"
                if len(candidates) > 1
                else "no repository could be matched to this request"
            ),
            evidence_ids=ledger.evidence_for("repository"),
            fact_ids=candidates[0].supporting_fact_ids if identified else [],
        ),
        signal(
            "Request names a repository that matched an indexed one",
            bool(matched_refs),
            1.0,
            detail="the request does not name a known repository",
            evidence_ids=[f.evidence_id for f in matched_refs],
            fact_ids=[f.fact_id for f in matched_refs],
        ),
    ]


def _repository_remediation(ledger: Ledger) -> list[str]:
    # "Select a repository" is sound advice when several are indexed and
    # ambiguous, and nonsense when none are — there is nothing to select from.
    # Advice the user cannot act on reads as the system not understanding its
    # own situation.
    if not ledger.has_fact("repository"):
        return ["Connect a repository", "Index the repository"]
    return ["Select a repository", "Index the repository"]


def _repository_question(ctx: QuestionContext) -> ClarificationQuestion | None:
    known = ctx.ledger.subjects_of("repository")
    if not known:
        # Nothing to choose between — a question here would be theatre. The
        # honest outcome is remediation, not an answer box.
        return None
    candidates = [c.statement for c in ctx.ledger.live_inferences("repository_candidate")]

    if ctx.is_reask:
        why = (
            f"I couldn't find '{ctx.previous_claim}' among the indexed repositories, so I "
            "still don't know which one to use. These are the ones I can actually see."
        )
        options = known
    elif len(candidates) > 1:
        why = (
            f"I narrowed it to {len(candidates)} equally-plausible repositories and can't "
            "separate them on the evidence I have."
        )
        options = candidates
    else:
        why = (
            "Nothing in the request matched any indexed repository strongly enough for me "
            "to choose."
        )
        options = known

    return ClarificationQuestion(
        question_id="gap_repository",
        question="Which repository should I use for this work?",
        why=why,
        options=options[:8],
        investigated=ctx.investigated,
    )


def _verify_repository(ledger: Ledger, claim: str) -> bool:
    # Corroboration means a *subsequent investigation* produced a candidate
    # for exactly this repository — never that the answer string looked
    # plausible.
    return any(
        c.statement.lower() == claim.lower() for c in ledger.live_inferences("repository_candidate")
    )


# ---------------------------------------------------------------------------
# architecture
# ---------------------------------------------------------------------------


def _architecture_signals(ledger: Ledger) -> list[ConfidenceSignal]:
    components = ledger.facts_of("component")
    topics = ledger.facts_of("topic")
    # Reachability means "we queried the graph and nothing failed" — NOT "we
    # successfully traversed something". Those are different claims and
    # collapsing them misreports both directions: a healthy graph with nothing
    # indexed yet is reachable (an indexing problem), while a Neo4j that refused
    # the connection is not (an infrastructure problem), and the remediation
    # differs completely.
    #
    # Note this deliberately does not key on "some graph evidence succeeded":
    # the repository list is read from Postgres and succeeds even when Neo4j is
    # down. Failure is what's decisive, so an observed failure anywhere in the
    # graph provider's work marks it unreachable.
    graph_evidence = [e for e in ledger.evidence if e.provider == "graph"]
    graph_failed = [e for e in graph_evidence if e.outcome == "failed"]
    graph_reached = [] if (not graph_evidence or graph_failed) else graph_evidence
    candidates = ledger.live_inferences("repository_candidate")
    identified = candidates[0].statement if len(candidates) == 1 else None
    scoped = [
        f for f in components if identified is not None and f.value.get("repository") == identified
    ]

    return [
        signal(
            # Named for exactly what the evidence establishes. "Reachable" claimed
            # slightly more than we know: with no indexed repositories there is
            # nothing to traverse, so Neo4j is never actually contacted and its
            # health is simply unobserved. "Queried without errors" is true in
            # both cases and still tells the user what they need.
            "Knowledge graph queried without errors",
            bool(graph_reached),
            2.0,
            detail="a graph query failed",
            evidence_ids=[e.evidence_id for e in graph_reached],
        ),
        signal(
            "Architecture components discovered",
            bool(components),
            3.0,
            # The diagnosis differs entirely depending on whether the graph
            # answered: an unreachable graph is an infrastructure problem, an
            # empty one is an indexing problem, and sending the user to fix
            # the wrong one wastes their time.
            detail=(
                "the graph is reachable but holds nothing for this request — the "
                "repository is likely not indexed yet"
                if graph_reached
                else "the graph could not be queried, so nothing could be discovered"
            ),
            evidence_ids=ledger.evidence_for("component"),
            fact_ids=[f.fact_id for f in components],
        ),
        signal(
            "Messaging topology discovered",
            bool(topics),
            1.0,
            detail="no Kafka topics were found for this request",
            evidence_ids=ledger.evidence_for("topic"),
            fact_ids=[f.fact_id for f in topics],
        ),
        signal(
            "Architecture scoped to the identified repository",
            bool(scoped),
            1.0,
            detail=(
                "no repository identified yet to scope against"
                if identified is None
                else (
                    "components were found, but none belong to the identified repository"
                    if components
                    else f"no components are indexed for {identified}"
                )
            ),
            evidence_ids=[f.evidence_id for f in scoped],
            fact_ids=[f.fact_id for f in scoped],
        ),
    ]


def _architecture_remediation(ledger: Ledger) -> list[str]:
    """Only suggest checking the graph connection when the graph is actually
    the problem. Telling someone to check infrastructure that just answered
    successfully sends them to debug a healthy system while the real cause —
    an unindexed repository — goes unaddressed."""
    graph_evidence = [e for e in ledger.evidence if e.provider == "graph"]
    reachable = bool(graph_evidence) and not any(e.outcome == "failed" for e in graph_evidence)
    if reachable:
        return ["Index the repository"]
    return ["Check the Neo4j connection", "Index the repository"]


# ---------------------------------------------------------------------------
# implementation_candidates
# ---------------------------------------------------------------------------


def _candidates_signals(ledger: Ledger) -> list[ConfidenceSignal]:
    candidates = ledger.live_inferences("repository_candidate")
    return [
        signal(
            "Candidate implementation sites ranked",
            bool(candidates),
            2.0,
            detail="no repository could be ranked as a candidate",
            evidence_ids=ledger.evidence_for("repository"),
            fact_ids=[fid for c in candidates for fid in c.supporting_fact_ids],
        ),
        signal(
            "A single leading candidate",
            len(candidates) == 1,
            1.0,
            detail=(
                f"{len(candidates)} candidates remain equally ranked"
                if len(candidates) > 1
                else "no candidate identified"
            ),
            evidence_ids=ledger.evidence_for("repository"),
        ),
    ]


# ---------------------------------------------------------------------------
# documentation
# ---------------------------------------------------------------------------


def _documentation_necessity(ledger: Ledger) -> Necessity:
    # Applicable only when there is something to be documented against — a
    # resolved work item, or an explicit documentation reference. A bare
    # freeform request has no missing documentation to complain about.
    has_anchor = ledger.has_fact("work_item") or any(
        f.value.get("type") == "confluence_page" for f in ledger.facts_of("reference")
    )
    return "recommended" if has_anchor else "not_applicable"


def _documentation_signals(ledger: Ledger) -> list[ConfidenceSignal]:
    documents = ledger.facts_of("document")
    reachable = [
        e
        for e in ledger.evidence
        if e.provider == "confluence" and e.outcome in ("success", "not_found")
    ]
    return [
        signal(
            "Design documentation retrieved",
            bool(documents),
            2.0,
            detail="no linked documentation was found for this work",
            evidence_ids=ledger.evidence_for("document"),
            fact_ids=[f.fact_id for f in documents],
        ),
        signal(
            "Documentation source reachable",
            bool(reachable),
            1.0,
            detail="Confluence is not connected",
            evidence_ids=[e.evidence_id for e in reachable],
        ),
    ]


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="work_item",
        label="Work item",
        gap_summary="The referenced work item could not be retrieved.",
        gap_why=(
            "The request points at a ticket, so its description is the most direct "
            "statement of what needs to change."
        ),
        necessity=_work_item_necessity,
        signals=_work_item_signals,
        remediation=lambda _l: ["Connect Jira", "Check the ticket key is correct"],
        question=_work_item_question,
        verify=_verify_work_item,
    ),
    Capability(
        key="repository",
        label="Repository",
        gap_summary="The repository this work belongs to could not be determined.",
        gap_why=(
            "Planning is scoped to one service — without knowing which, any plan would be "
            "about the wrong codebase."
        ),
        necessity=lambda _l: "required",
        signals=_repository_signals,
        remediation=_repository_remediation,
        question=_repository_question,
        verify=_verify_repository,
    ),
    Capability(
        key="architecture",
        label="Architecture",
        gap_summary="No architecture is available for this request.",
        gap_why=(
            "GraphForge reasons over the dependency graph of the affected service; with no "
            "indexed architecture there is nothing to reason over."
        ),
        necessity=lambda _l: "required",
        signals=_architecture_signals,
        remediation=_architecture_remediation,
        # No answer a human types can index a repository, so this is
        # remediation-only — and by the question/verify pairing rule it
        # therefore cannot offer a question at all.
    ),
    Capability(
        key="implementation_candidates",
        label="Implementation candidates",
        gap_summary="No implementation site could be ranked for this request.",
        gap_why=(
            "Knowing the likely implementation site lets Planning talk about real components "
            "instead of generic advice."
        ),
        necessity=lambda _l: "recommended",
        signals=_candidates_signals,
        remediation=lambda _l: ["Index the repository"],
    ),
    Capability(
        key="documentation",
        label="Documentation",
        gap_summary="No design documentation was found for this work.",
        gap_why=(
            "Design docs often carry constraints a ticket omits; without them Planning may "
            "miss a stated requirement."
        ),
        necessity=_documentation_necessity,
        signals=_documentation_signals,
        remediation=lambda _l: ["Connect Confluence", "Link a design page to the ticket"],
    ),
)

BY_KEY: dict[str, Capability] = {c.key: c for c in CAPABILITIES}


def get(key: str) -> Capability | None:
    return BY_KEY.get(key)


def assess(ledger: Ledger) -> list[CapabilityAssessment]:
    """Re-derive every capability's confidence from the ledger as it stands.

    Called at the top of every reasoning cycle, so assessments are always a
    fresh read of current knowledge rather than incrementally patched state
    that could fall out of sync with the facts. Returned in registry order so
    the UI's list is stable across cycles.
    """
    return [c.assess(ledger) for c in CAPABILITIES]


def overall_confidence(assessments: list[CapabilityAssessment]) -> float:
    """The single number surfaced to telemetry and the UI — necessity-weighted,
    excluding `not_applicable` capabilities entirely. Derived from the same
    assessments the explanation renders from, so there is no second path that
    could disagree with the breakdown."""
    weighted = [
        (a.score, _NECESSITY_WEIGHT[a.necessity])
        for a in assessments
        if a.necessity != "not_applicable"
    ]
    total = sum(w for _, w in weighted)
    if total == 0:
        return 0.0
    return round(sum(score * w for score, w in weighted) / total, 4)


def unmet(assessments: list[CapabilityAssessment]) -> list[CapabilityAssessment]:
    """Applicable capabilities that aren't satisfied yet, most important first
    — the engine's work queue for deciding what to investigate next."""
    order = {"required": 0, "recommended": 1}
    pending = [a for a in assessments if a.necessity != "not_applicable" and not a.satisfied]
    return sorted(pending, key=lambda a: (order[a.necessity], a.score))
