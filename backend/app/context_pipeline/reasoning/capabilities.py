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

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.planning.tools import (
    DEFAULT_CANDIDATE_LIMIT,
    _TEST_RELEVANCE_FACTOR,
    _is_test_component,
    _match_text,
)
from app.agents.text_relevance import tokenize
from app.context_pipeline.reasoning.ledger import Inference, Ledger

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
    # Explicit candidates (the request itself named the repository, or a
    # human's claim was corroborated) always satisfy "identified" on their
    # own, however many *suggested* candidates also exist — two repositories
    # the request named together is not ambiguity. Ambiguity is otherwise
    # among however many candidates remain once uncorroborated ranking
    # survivors are excluded — exactly the pre-existing `len(candidates) ==
    # 1` fallback, just no longer counting a candidate this module cannot
    # yet trust on its own.
    explicit_candidates = [c for c in candidates if c.value.get("source") == "explicit"]
    # RFC-0011 (repository candidate verification) — the fix for "a lone
    # lexical-ranking survivor was treated as equivalent to an explicitly
    # identified repository": only a candidate `resync_ranked_candidates`
    # itself tagged `basis: "ranking"` (a genuine multi-repository ranking
    # leader) is held to the corroboration requirement below. Every other
    # candidate — explicit, the sole-indexed-repository case, or anything
    # constructed without a `basis` at all (e.g. a fixture/test predating
    # this field, or a future candidate source that hasn't adopted it) —
    # keeps its original, unambiguous behavior unchanged: there was no real
    # ranking competition to have won unverified in the first place.
    # Checking `== "ranking"` specifically, rather than trying to enumerate
    # every *other* legitimate origin, is deliberately the conservative,
    # backward-compatible reading.
    ranking_candidates = [c for c in candidates if c.value.get("basis") == "ranking"]
    # A ranking candidate must be independently corroborated before it
    # counts as identified — a real cross-repository graph relationship, or
    # fetched source content that mentions this request's own specific
    # vocabulary — see `_corroborated_ranking_candidates`.
    corroborated_candidates = _corroborated_ranking_candidates(ledger)
    uncorroborated_ranking_candidates = [c for c in ranking_candidates if c not in corroborated_candidates]
    # Every candidate this signal can trust right now: everything except an
    # uncorroborated ranking survivor.
    trustworthy_candidates = [c for c in candidates if c not in uncorroborated_ranking_candidates]

    # Which trustworthy candidate(s) actually decide "is there exactly one
    # answer" — deliberately NOT just `trustworthy_candidates` itself.
    # `resync_corroborated_candidates` can promote a candidate for *either*
    # endpoint of a real cross-repository relationship (RFC-0011's
    # bidirectional evidence — a caller and its dependency corroborate each
    # other regardless of which one the ticket's own ranking favored), so a
    # corroborated ranking leader (e.g. the sibling that actually owns the
    # ticket) can end up alongside a freshly-promoted candidate for the
    # *other* endpoint (e.g. a shared library it merely depends on). Those
    # two are not competing hypotheses for "which repository owns this
    # ticket" — one is the answer, the other is corroborating context — so
    # counting both toward ambiguity would be wrong. A corroborated ranking
    # leader is decisive on its own; only when there is no such leader does
    # any other trustworthy candidate (a freshly-promoted corroboration, the
    # sole-indexed-repository case, etc.) get to stand on its own merits.
    decisive_candidates = (
        explicit_candidates
        or corroborated_candidates
        or trustworthy_candidates
    )

    identified = bool(explicit_candidates) or len(decisive_candidates) == 1
    identified_fact_ids = (
        [fid for c in explicit_candidates for fid in c.supporting_fact_ids]
        if explicit_candidates
        else (decisive_candidates[0].supporting_fact_ids if identified else [])
    )
    repo_names = {f.subject for f in repositories}
    matched_refs = [
        f
        for f in ledger.facts_of("reference")
        if f.value.get("type") in _REPOSITORY_REFERENCE_TYPES and f.subject in repo_names
    ]
    # ADR 0010 (Theme E) — a repository the request names that the user
    # tracks but hasn't indexed (`RequestParseInvestigator.
    # _match_tracked_repository_names`, `value["indexed"] is False`) is a
    # different, more actionable situation than "the request doesn't name
    # a known repository at all" — this signal stays unsatisfied either
    # way (an unindexed repository still can't be used), but the detail
    # text tells the user specifically what to do.
    unindexed_refs = [
        f
        for f in ledger.facts_of("reference")
        if f.value.get("type") in _REPOSITORY_REFERENCE_TYPES and f.value.get("indexed") is False
    ]

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
                f"{len(uncorroborated_ranking_candidates)} repositories rank closely against "
                "this request but none is independently corroborated by a cross-repository "
                "relationship or matching source content yet"
                if uncorroborated_ranking_candidates and not identified
                else (
                    f"{len(decisive_candidates)} repositories are equally plausible"
                    if len(decisive_candidates) > 1
                    else "no repository could be matched to this request"
                )
            ),
            evidence_ids=ledger.evidence_for("repository"),
            fact_ids=identified_fact_ids,
        ),
        signal(
            "Suggested candidate corroborated by independent evidence",
            # Deliberately *not* a second independent readiness gate — it
            # mirrors `identified`'s own truth value rather than inventing a
            # separate one, so an empty ledger or a not-yet-ranked request
            # reports unsatisfied here too (no false "trivially satisfied"
            # credit — see the regression this fixed: `overall_confidence`
            # of a brand-new, evidence-free ledger must stay exactly 0.0).
            # What this signal adds beyond `identified` is visibility: once
            # true, its `detail`/evidence specifically say *how* — explicit
            # reference, unambiguous suggestion, or genuine corroboration —
            # which `identified` alone doesn't distinguish.
            identified,
            1.0,
            detail=(
                f"{len(uncorroborated_ranking_candidates)} ranked candidate(s) have not been "
                "confirmed by a real cross-repository relationship or by fetched source "
                "content mentioning this request's own vocabulary"
                if uncorroborated_ranking_candidates
                else "no repository has been identified or independently corroborated yet"
            ),
            evidence_ids=(
                ledger.evidence_for("repository_relationship", "pull_request", "source_file")
                if corroborated_candidates
                else ledger.evidence_for("repository")
            ),
            fact_ids=(
                [fid for c in corroborated_candidates for fid in c.supporting_fact_ids]
                if corroborated_candidates
                else identified_fact_ids
            ),
        ),
        signal(
            "Request names a repository that matched an indexed one",
            bool(matched_refs),
            1.0,
            detail=(
                f"'{unindexed_refs[0].subject}' was mentioned but hasn't been indexed yet"
                if unindexed_refs
                else "the request does not name a known repository"
            ),
            evidence_ids=[f.evidence_id for f in matched_refs],
            fact_ids=[f.fact_id for f in matched_refs],
        ),
        # Subsumes the retired `implementation_candidates` capability's
        # "ranked" signal (ADR 0010, Theme B) — `repository` is now the sole
        # owner of every "do we know which repository/repositories this
        # work touches" signal, so this and "Owning repository identified"
        # can never drift into two different definitions of the same thing
        # again. Deliberately distinct from "identified": a request can have
        # live candidates (this signal) without any of them being explicit
        # or a lone survivor (that signal) — the genuine-ambiguity case.
        signal(
            "Candidate implementation sites found",
            bool(candidates),
            1.0,
            detail="no repository could be ranked or suggested as a candidate",
            evidence_ids=ledger.evidence_for("repository"),
            fact_ids=[fid for c in candidates for fid in c.supporting_fact_ids],
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
    elif len(candidates) == 1:
        # Reached only when a lone `basis: "ranking"` candidate exists but
        # `_repository_signals` could not corroborate it (RFC-0011) — genuinely
        # different from "nothing matched at all", and worded that way rather
        # than reusing the empty-candidates message below.
        why = (
            f"'{candidates[0]}' ranks closely against this request, but I couldn't confirm "
            "it with a cross-repository relationship or matching source content, so I'm not "
            "confident enough to select it automatically."
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
    # Corroboration means the graph independently confirmed this repository
    # exists — checked against the `repository` FACT itself, never against a
    # derived `repository_candidate` inference. Checking the inference would
    # be circular: the inference that would corroborate a verified claim is
    # only (re)computed by `resync_verified_claim_candidates`, which itself
    # runs *after* `engine._settle_claims` decides whether this claim held
    # (see ADR 0010 §7, item 1) — so at the moment this function is called,
    # no such inference can exist yet even for a claim that's about to be
    # corroborated.
    return any(f.subject.lower() == claim.lower() for f in ledger.facts_of("repository"))


# Runner-up repository within this fraction of the leader's relevance score
# is treated as equally plausible, so both survive as candidates and the
# `repository` capability's "Owning repository identified" signal stays
# unsatisfied unless one of them is separately explicit. Public (not
# underscore-prefixed): `investigators.GraphInvestigator` imports this same
# constant to phrase its observation text ("'X' is the clear match" vs "N
# repositories score almost identically"), so the two modules can never
# describe a ranking differently from how `resync_ranked_candidates`
# actually interprets it — interpreting a ranking is still exclusively the
# resync hooks' job (ADR 0010, invariant I3); this constant is shared
# because both a narration and a decision need to agree on what "tied"
# means, not because the investigator makes the decision too.
TIE_RATIO = 0.9

# RFC-0011 (repository candidate verification) — the staged candidate
# funnel's two width limits. Both are bounds on *how many candidates get
# deeper attention*, not new thresholds invented for one ticket:
#
# - `CANDIDATE_FUNNEL_WIDTH` reuses `DEFAULT_CANDIDATE_LIMIT`, the existing
#   "how many top-ranked repositories deserve a closer look" default
#   `format_graph_context` already used to decide what reaches an LLM
#   prompt — the same judgment, reused for "what deserves a graph query"
#   instead of "what deserves a token budget".
# - `SOURCE_RETRIEVAL_WIDTH` narrows further, matching the funnel shape
#   itself (top-N ranked -> cheap graph corroboration -> a smaller top-K
#   -> the strictly more expensive source-content fetch) — real GitHub
#   calls are the single most expensive step available to Context
#   Discovery, so only the survivors of the cheap stage reach it.
CANDIDATE_FUNNEL_WIDTH = DEFAULT_CANDIDATE_LIMIT
SOURCE_RETRIEVAL_WIDTH = 3

# RFC-0014 — the funnel's source-retrieval stage now fetches actual file
# *content*, not repository metadata (see `GitHubInvestigator`'s escalation
# branch) — bounded per candidate the same way `SOURCE_RETRIEVAL_WIDTH`
# bounds candidates: at most this many files, per candidate, ranked by how
# many of the ticket's own vocabulary words their already-indexed name
# matches (`component` facts the graph-corroboration stage already
# recorded — no new GitHub call needed to pick *which* files). Each file
# itself is separately capped by `GitHubTool._build_file_result`'s
# existing `_MAX_CHARS` (8000) — reused, not re-invented. Worst-case
# GitHub API calls added by this stage: `SOURCE_RETRIEVAL_WIDTH *
# MAX_SOURCE_FILES_PER_CANDIDATE`.
MAX_SOURCE_FILES_PER_CANDIDATE = 2

# RFC-0015 — generic term specificity, derived from the corpus this
# investigation has actually observed (every scoped candidate's own
# indexed `component` names — see `_term_document_frequencies`), never a
# fixed word list. The live PROT-5764 benchmark exposed exactly why this
# is needed: raw token overlap treated "validation"/"failed"/"client"
# (generic ETL/programming vocabulary present in most scoped candidates)
# as equally strong evidence as a term specific to this one ticket's
# actual concept — this module is what tells those apart, purely from
# how common each term is *within what this run has already scoped*, no
# hardcoded word anywhere.


def _term_document_frequencies(
    ledger: Ledger, *, exclude_repository: str | None = None
) -> tuple[dict[str, int], int]:
    """term -> number of distinct scoped repositories *other than
    `exclude_repository`* with at least one `component` whose own name
    contains it, plus the total number of such other repositories. The
    corpus is exactly "every candidate this run has already scoped" — no
    separate, expensive corpus-building pass: this data is already in the
    ledger by the time source selection or corroboration runs, the same
    reason RFC-0014's file selection needed no new GitHub call to pick
    *which* files.

    `exclude_repository` is always the specific candidate a term match is
    being scored *for* — the question this answers is "how common is this
    term among the OTHER candidates," not "including the one candidate
    whose own component is obviously why the term matched in the first
    place." Without the exclusion, a term unique to exactly the correct
    candidate would still show `df=1` (itself) rather than the `df=0`
    ("no *competing* candidate shares this") that actually reflects how
    specific it is.
    """
    repos_by_term: dict[str, set[str]] = {}
    all_repos: set[str] = set()
    for fact in ledger.facts_of("component"):
        repo = str(fact.value.get("repository") or "")
        if not repo or repo == exclude_repository:
            continue
        all_repos.add(repo)
        # RFC-0017 — `_match_text` (name + type + file_path), the same
        # match text `app.agents.planning.tools.rank_repositories` already
        # uses for repository-level ranking, not the bare symbol name. A
        # file's own path is frequently the only place domain vocabulary
        # appears (a function named `main` inside `pipeline/validation/
        # schema_validator.py` is otherwise invisible to name-only
        # matching) — reusing the existing, already-tested match-text
        # shape here instead of re-deriving a narrower one a second time.
        for term in tokenize(_match_text(fact.value)):
            repos_by_term.setdefault(term, set()).add(repo)
    return {term: len(repos) for term, repos in repos_by_term.items()}, len(all_repos)


def _term_specificity_weights(
    ledger: Ledger, terms: frozenset[str], *, exclude_repository: str | None = None
) -> dict[str, float]:
    """One weight per term in `terms`, in (0.0, 1.0] — 1.0 for a term no
    *other* scoped candidate's own symbols contain (maximally specific:
    nothing competing already examined shares it), approaching 0.0 for a
    term that appears in every other scoped candidate's symbols (every
    sibling scaffold has it, so it can't discriminate between them).
    Classic IDF, normalized to a fixed `[0, 1]` range so it can be
    compared against a fixed threshold regardless of how many candidates
    happen to be scoped this run:

        idf(term)    = ln((N + 1) / (df(term) + 1))
        weight(term) = idf(term) / ln(N + 1)        (= 1.0 exactly when df=0)

    `N` is the number of distinct *other scoped* repositories observed so
    far (see `exclude_repository` on `_term_document_frequencies`) —
    deliberately not a global, all-repositories corpus (a fresh, expensive
    full-fleet scan); "common relative to what this investigation has
    actually looked at" is the same generic, cheap proxy this module
    already uses elsewhere (RFC-0012's relationship degree, computed the
    same way — from ledger facts already gathered, not a new query).
    """
    document_frequency, n = _term_document_frequencies(ledger, exclude_repository=exclude_repository)
    # `n <= 1`, not just `n == 0`: with at most one *other* scoped
    # repository's components to compare against, there is no basis to
    # call any term "common" — every term trivially has `df == n` for the
    # one repo that has it, which would otherwise score every match as
    # maximally generic regardless of what the term actually is. Treat
    # everything as maximally specific until there's a real corpus (two
    # or more other scoped repositories) to judge commonality against.
    if n <= 1:
        return dict.fromkeys(terms, 1.0)
    denom = math.log(n + 1) or 1.0
    return {
        term: math.log((n + 1) / (document_frequency.get(term, 0) + 1)) / denom for term in terms
    }


# A single highly-specific term (weight at/above this) is sufficient
# evidence on its own; below it, only *multiple* independently-moderate
# terms together clear the bar (see `_matched_term_specificity`) — Phase
# 2's "a term occurring in only a few repositories should contribute
# more" and "a phrase/multi-token concept should be stronger than
# isolated common words," made concrete as two independent ways to clear
# the same bar, rather than one blended score a handful of common words
# could still add up to by sheer count.
_STRONG_TERM_WEIGHT = 0.6
_MODERATE_TERM_WEIGHT = 0.3
_MIN_MODERATE_TERM_MATCHES = 2

# RFC-0017 — how many of a match's own strongest terms the continuous
# co-occurrence score (below) considers. Bounded, not "all matched terms,"
# specifically so that piling on more merely-generic terms can't keep
# inflating the score without limit — three is the same "several
# independent signals" shape `_MIN_MODERATE_TERM_MATCHES` already used,
# now applied continuously instead of as a hard per-term cliff.
_MAX_COMBINATION_TERMS = 3


def _matched_term_specificity(matched_terms: set[str], weights: dict[str, float]) -> float:
    """The specificity score for one set of matched terms — the highest of
    three independent ways to earn it: "my single strongest term's own
    weight," the original discrete "how many independently-moderate terms
    do I have together" bonus (RFC-0015), and a continuous co-occurrence
    score (RFC-0017, below). `0.0` for no matches.

    RFC-0017's continuous score exists because the discrete bonus above
    has a hard cliff: it only credits terms that *individually* already
    clear `_MODERATE_TERM_WEIGHT`, so three terms that are each genuinely
    on-topic but only moderately rare (e.g. "schema"/"pipeline"/
    "validation" all appearing in a shared ETL vocabulary) contribute
    *nothing* if none of them alone reaches 0.3 — even though together
    they are a real, specific, multi-concept match a single incidental
    high-weight term (one rare word in an unrelated function) would
    otherwise beat outright. Verified against the live PROT-5764 benchmark
    before this was added: exactly this shape was why `schema_validator.py`
    scored below `logger.py` even after including file-path text.

    The combination itself is a bounded noisy-OR — `1 - product(1 - w)`
    over the `_MAX_COMBINATION_TERMS` strongest matched terms — chosen
    over a plain sum specifically for its diminishing-returns shape: it
    reduces to exactly the single term's own weight when only one term
    matched (so it never changes the single-strong-term case), and each
    additional weak term contributes less than the last, so several
    genuinely common terms still cannot inflate a match past what a
    handful of independent, real-but-moderate signals plausibly deserve.
    Bounding to the `_MAX_COMBINATION_TERMS` strongest matches (not every
    matched term) is what keeps piling on arbitrarily many generic words
    from ever winning by sheer count — see
    `test_many_generic_terms_never_outscore_one_highly_specific_term`.
    """
    term_weights = sorted((weights.get(t, 0.0) for t in matched_terms), reverse=True)
    if not term_weights:
        return 0.0
    strongest = term_weights[0]
    moderate_count = sum(1 for w in term_weights if w >= _MODERATE_TERM_WEIGHT)
    discrete_multi_term_score = (
        min(1.0, moderate_count / _MIN_MODERATE_TERM_MATCHES) if moderate_count else 0.0
    )
    remaining_after_top = 1.0
    for w in term_weights[:_MAX_COMBINATION_TERMS]:
        remaining_after_top *= 1.0 - w
    continuous_cooccurrence_score = 1.0 - remaining_after_top
    return max(strongest, discrete_multi_term_score, continuous_cooccurrence_score)


# Below this, matched vocabulary is evidence of a shared scaffold/domain
# at best, not a specific behavioral connection to this ticket — the same
# "must clear a bar to count as corroboration" shape RFC-0012 already
# established for relationship evidence (`_MIN_RELATIONSHIP_SPECIFICITY`),
# applied here to lexical/source evidence instead of relationship degree.
MIN_SOURCE_EVIDENCE_SPECIFICITY = 0.6

# RFC-0012 (Problem A) — a relationship's own weight, by confidence tier.
# Every `repository_relationship` fact carries a `confidence` (`app.
# indexer.graph.cross_repo_linker`'s own hand-assigned vocabulary):
# "structural" for a literal, unambiguous match (a `@FeignClient` target,
# a Kafka topic literal) — the strongest evidence this module ever sees;
# "heuristic" for a name-coordinate match (a manifest dependency, or a
# source-level import — RFC-0012 Problem B) — real, but a guess; and
# "ambiguous" for a source-level import that matched more than one
# indexed repository at once (`cross_repo_linker._downgrade_ambiguous_
# imports`) — evidence an import happened, not evidence of *which*
# repository it names. `0.0` here isn't "excluded from the graph" (the
# fact still exists, still visible), it's "excluded from single-handedly
# deciding a repository is identified" — see `_corroboration_evidence`.
_RELATIONSHIP_CONFIDENCE_WEIGHT = {"structural": 1.0, "heuristic": 0.6, "ambiguous": 0.0}

# RFC-0012 (Problem A) — below this weight, a relationship no longer
# counts as corroborating evidence on its own. Not a blacklist of any
# specific repository or dependency: every relationship is still weighed
# by the same formula regardless of what it points at — a shared library
# depended on by only one caller in what this run has observed still
# clears this bar exactly like any other single-caller relationship
# would. What changes the outcome is purely how many *distinct* callers
# this run has observed sharing the same target (see
# `_relationship_degree`) — the graph-theoretic notion of degree/fan-in,
# not a name.
_MIN_RELATIONSHIP_SPECIFICITY = 0.5


def _has_live_candidate(ledger: Ledger, name: str) -> bool:
    return any(
        i.kind == "repository_candidate" and not i.withdrawn and i.statement == name
        for i in ledger.inferences
    )


def _has_live_assumption(ledger: Ledger, statement: str) -> bool:
    return any(
        i.kind == "assumption" and not i.withdrawn and i.statement == statement
        for i in ledger.inferences
    )


def _is_explicit_repository(ledger: Ledger, name: str) -> bool:
    """Whether `name` qualifies as an *explicit* repository candidate,
    computed directly from facts — never from a previously-written
    inference. This is what makes `resync_ranked_candidates` and
    `resync_relationship_candidates` safe to call in any order relative to
    `resync_repository_candidates`/`resync_verified_claim_candidates`
    (ADR 0010, invariant I3's order-independence requirement): if this
    instead read `live_inferences(...)`, a suggested-candidate hook running
    before the explicit-candidate hooks in a given pass would see nothing
    explicit yet and wrongly promote the same name as merely suggested.
    """
    if name not in {f.subject for f in ledger.facts_of("repository")}:
        return False
    for ref in ledger.facts_of("reference"):
        if ref.subject == name and ref.value.get("type") in _REPOSITORY_REFERENCE_TYPES:
            return True
    for claim in ledger.facts_of("user_statement", verified_only=True):
        if claim.subject == name and claim.value.get("capability") == "repository":
            return True
    return False


def resync_repository_candidates(ledger: Ledger) -> None:
    """Ledger invariant, re-established every reasoning cycle: every indexed
    repository explicitly referenced in the request text has a live
    `repository_candidate` inference tagged `source: "explicit"`.

    Pure and I/O-free — it only reads `reference`/`repository` facts already
    in the ledger, so it runs on *every* cycle (see `engine._resync`), not
    only when `GraphInvestigator` itself happens to run. This matters
    because of a real ordering gap: a local repository name can only be
    recognized once repository facts exist (see `RequestParseInvestigator`'s
    `match_repository_names` pass), which can land on a cycle *after*
    ranking already satisfied the `repository` capability with a single
    (merely suggested) leader — at which point nothing would ever propose
    another graph action, and a second, explicitly-named repository would
    never get promoted at all. Re-deriving this invariant on every cycle,
    independent of investigator scheduling, is what closes that gap.

    Additive only — withdrawal of stale `repository_candidate` inferences is
    owned centrally by `engine._resync`, once per resync pass, before any
    hook runs (ADR 0010 §7). A hook that withdrew its own kind's inferences
    could erase what an earlier hook in the same pass already wrote.
    """
    repositories = {f.subject: f for f in ledger.facts_of("repository")}
    if not repositories:
        return
    for ref in ledger.facts_of("reference"):
        if ref.value.get("type") not in _REPOSITORY_REFERENCE_TYPES:
            continue
        repo_fact = repositories.get(ref.subject)
        if repo_fact is None or _has_live_candidate(ledger, ref.subject):
            continue
        ledger.add_inference(
            kind="repository_candidate",
            statement=ref.subject,
            supporting_fact_ids=[repo_fact.fact_id, ref.fact_id],
            value={"source": "explicit", "reason": "Named directly in the request."},
        )


def resync_verified_claim_candidates(ledger: Ledger) -> None:
    """Promotes a corroborated human answer about which repository this work
    belongs to into an explicit candidate — the second of the two `source:
    "explicit"` origins (the first is `resync_repository_candidates`, for
    text the request itself named).

    A claim counts as corroborated once `Fact.verified` is raised `True`
    (only `engine._settle_claims` ever does that) for a `user_statement`
    fact whose `value["capability"] == "repository"`, and the ledger also
    holds a `repository` fact with the claimed name — see
    `_verify_repository`'s own docstring for why that fact-level check, not
    an inference-level one, is what this depends on.
    """
    repositories = {f.subject: f for f in ledger.facts_of("repository")}
    if not repositories:
        return
    for claim in ledger.facts_of("user_statement", verified_only=True):
        if claim.value.get("capability") != "repository":
            continue
        repo_fact = repositories.get(claim.subject)
        if repo_fact is None or _has_live_candidate(ledger, claim.subject):
            continue
        ledger.add_inference(
            kind="repository_candidate",
            statement=claim.subject,
            supporting_fact_ids=[repo_fact.fact_id, claim.fact_id],
            value={"source": "explicit", "reason": "Confirmed by your answer."},
        )


def resync_ranked_candidates(ledger: Ledger) -> None:
    """Promotes `source: "suggested"` candidates from two fact sources
    `GraphInvestigator` only ever *observes*, never interprets (ADR 0010,
    invariant I1):

    - Exactly one repository indexed at all — unconditionally the sole
      candidate, cited to the `repository` fact itself, with an `assumption`
      inference stating the choice was made from absence, not a match.
    - Two or more repositories indexed and a `repository_ranking` fact
      exists (the investigator's `rank_repositories` output, recorded
      verbatim) — every repository scoring within `TIE_RATIO` of the
      leader is promoted; a single leader also gets an `assumption`
      inference.

    Both branches skip any name `_is_explicit_repository` already covers —
    an explicit match is never also listed as merely suggested.
    """
    by_name = {f.subject: f for f in ledger.facts_of("repository")}
    if not by_name:
        return

    if len(by_name) == 1:
        only = next(iter(by_name.values()))
        if not _is_explicit_repository(ledger, only.subject) and not _has_live_candidate(
            ledger, only.subject
        ):
            ledger.add_inference(
                kind="repository_candidate",
                statement=only.subject,
                supporting_fact_ids=[only.fact_id],
                # `basis: "sole_repository"`, not `"ranking"` — there was no
                # actual competition to rank against, so this candidate is
                # exempt from the corroboration requirement `_repository_
                # signals` applies to a genuine ranking leader (see
                # `RANKING_BASIS` below).
                value={
                    "source": "suggested",
                    "reason": "Only indexed repository.",
                    "basis": "sole_repository",
                },
            )
            statement = (
                f"This work belongs to '{only.subject}' — it is the only indexed repository, "
                "not something the request named."
            )
            if not _has_live_assumption(ledger, statement):
                ledger.add_inference(
                    kind="assumption",
                    statement=statement,
                    supporting_fact_ids=[only.fact_id],
                )
        return

    ranking_facts = ledger.facts_of("repository_ranking")
    if not ranking_facts:
        return
    if any(_is_explicit_repository(ledger, name) for name in by_name):
        # A ranking's suggestions are a best guess for when nothing about
        # this request is confirmed yet. Once any repository is explicit —
        # named directly, or a corroborated human answer — the ranking's
        # guess about a *different* repository is superseded, not merely one
        # candidate among several: continuing to suggest it would relitigate
        # an ambiguity an explicit answer already resolved. (Relationship-
        # based suggestions are the opposite: they only ever fire *because*
        # an explicit repository exists — see `resync_relationship_
        # candidates` — so they are unaffected by this check.)
        return
    scored: list[list[Any]] = ranking_facts[-1].value.get("scored") or []
    if not scored or scored[0][0] <= 0:
        return

    top_score = scored[0][0]
    leaders = [name for score, name in scored if score >= top_score * TIE_RATIO]
    for name in leaders:
        fact = by_name.get(name)
        if (
            fact is None
            or _is_explicit_repository(ledger, name)
            or _has_live_candidate(ledger, name)
        ):
            continue
        ledger.add_inference(
            kind="repository_candidate",
            statement=name,
            supporting_fact_ids=[fact.fact_id],
            # `basis: "ranking"` — a lexical-ranking survivor, explicitly
            # NOT treated as equivalent to an explicit or sole-repository
            # candidate. `_repository_signals` requires independent
            # corroboration (a real cross-repository relationship, or
            # fetched source content that mentions this request's own
            # vocabulary) before a `"ranking"`-basis candidate counts as
            # "identified" — see `_corroborated_ranking_candidates`.
            value={
                "source": "suggested",
                "reason": "Ranks closely against this request's terms.",
                "basis": "ranking",
            },
        )

    if len(leaders) == 1 and not _is_explicit_repository(ledger, leaders[0]):
        leader_fact = by_name.get(leaders[0])
        if leader_fact is not None:
            statement = (
                f"This work belongs to '{leaders[0]}' — inferred from how closely its "
                "components match the request, which did not name a repository."
            )
            if not _has_live_assumption(ledger, statement):
                ledger.add_inference(
                    kind="assumption",
                    statement=statement,
                    supporting_fact_ids=[leader_fact.fact_id],
                )


def ranked_repository_names(ledger: Ledger, *, limit: int = CANDIDATE_FUNNEL_WIDTH) -> list[str]:
    """The top `limit` repository names from the most recent ranking, best
    first, excluding whatever is already explicit — the candidate funnel's
    first stage. Pure read of the `repository_ranking` fact `GraphInvestigator`
    already records; used by `GraphInvestigator.propose` to decide which
    ranking survivors are worth a bounded, scoped corroboration query, so a
    ranking of 62 repositories never turns into 62 graph queries.

    Deliberately reads every scored repository, not only the `TIE_RATIO`
    "leaders" `resync_ranked_candidates` promotes to a live candidate — a
    repository that just misses the tie cutoff is exactly the kind of thing
    corroborating evidence should be able to promote ahead of a leader that
    only *looks* strong lexically (requirement: cross-repository relationships
    usable as evidence *before* selection, not only to confirm it after).
    """
    ranking_facts = ledger.facts_of("repository_ranking")
    if not ranking_facts:
        return []
    scored: list[list[Any]] = ranking_facts[-1].value.get("scored") or []
    names = [
        name
        for _score, name in scored
        if not _is_explicit_repository(ledger, name)
    ]
    return names[:limit]


def _ranking_ticket_terms(ledger: Ledger) -> frozenset[str]:
    """The request's own specific vocabulary (field/function/entity names —
    see `classifier.PlanningProfile.ticket_terms`), tokenized, as recorded
    onto the most recent `repository_ranking` fact by `GraphInvestigator`.

    Deliberately NOT the full `search_terms` used for lexical ranking
    itself — that list also contains the fixed, generic capability
    vocabulary ("batch", "spark", "airflow", ...) every sibling repository
    built from the same scaffold matches equally well. Source-content
    corroboration needs the discriminating half only, or it would just be
    re-deriving the same lexical signal a second time under a different
    name.
    """
    ranking_facts = ledger.facts_of("repository_ranking")
    if not ranking_facts:
        return frozenset()
    terms = ranking_facts[-1].value.get("ticket_terms") or []
    return tokenize(" ".join(str(t) for t in terms))


def _relationship_degree(ledger: Ledger) -> dict[str, int]:
    """Target repository name -> its fan-in — how many *distinct*
    repositories have a real cross-repository relationship edge into it —
    the graph-theoretic notion of "how common is this target as a
    dependency" that separates a high-degree shared dependency from a
    rare, specific one (RFC-0012, Problem A).

    RFC-0016 fix: prefers `target_consumer_count`, the graph-wide fan-in
    `Neo4jGraphTool` now attaches to every `repository_relationship`
    fact's value (one `get_incoming_cross_repository_edge_count` read per
    distinct target — see `app.tools.implementations.neo4j_tool`), over
    counting distinct source repositories *this run's ledger happens to
    already contain a fact for*.

    The two are not the same thing, and the gap matters: `GraphInvestigator`
    only ever scopes a bounded handful of top-ranked candidates per run
    (`CANDIDATE_FUNNEL_WIDTH`), so a shared repository with dozens of real
    consumers graph-wide would previously show a "degree" of at most
    however many of *those few scoped candidates* happened to point at it
    — sometimes 1, making a genuinely popular shared library score as if
    it had a single, rare, discriminating caller. Reading the real
    graph-wide count fixes that without changing the funnel width, the
    weighting formula, or any threshold below.

    Facts without `target_consumer_count` (synthetic ledgers built
    directly in tests, or a backend that hasn't populated it) fall back to
    the original within-ledger distinct-source count — this is a strict
    enrichment, not a breaking change to the fact shape.
    """
    sources_by_target: dict[str, set[str]] = {}
    graph_wide_count: dict[str, int] = {}
    for fact in ledger.facts_of("repository_relationship"):
        source_repo = str(fact.value.get("source_repository", ""))
        if source_repo:
            sources_by_target.setdefault(fact.subject, set()).add(source_repo)
        count = fact.value.get("target_consumer_count")
        if isinstance(count, int) and count > 0:
            # Every fact targeting the same subject carries the same
            # graph-wide truth; `max` just tolerates a fact recorded
            # before an index refresh sitting alongside a fresher one.
            graph_wide_count[fact.subject] = max(graph_wide_count.get(fact.subject, 0), count)
    return {
        target: graph_wide_count.get(target, len(sources))
        for target, sources in sources_by_target.items()
    }


# RFC-0016 — below this graph-wide fan-in, a repository is just a normal
# node with a couple of callers; at/above it, enough *independent* other
# repositories route through it that treating it as shared infrastructure
# (a provider, not a tenant-specific implementation) is the better
# explanation than coincidence. `3` mirrors `_MIN_MODERATE_TERM_MATCHES`'s
# own reasoning (RFC-0015): two is still plausibly two related teams
# sharing code by accident; three or more independent callers is the
# point a *capability* is the more likely explanation. Purely structural
# (a count), never a name — the same repository with 2 consumers today and
# 3 tomorrow changes role automatically as the graph grows.
_SHARED_PROVIDER_MIN_CONSUMERS = 3


def repository_role(consumer_count: int) -> str:
    """Classify a repository's architectural role from its graph-wide
    fan-in alone — `"shared_provider"` (many independent repositories
    depend on it — likely a capability/library other repos consume) or
    `"consumer"` (an ordinary node in the dependency graph).

    Deliberately just a label derived from a count, not a blacklist or an
    exclusion: `_corroboration_evidence` still lets a `shared_provider`
    become the identified repository whenever *other* evidence (ticket-
    specific lexical/source-content specificity, RFC-0015) points at it
    directly — a shared provider can absolutely be the repository a ticket
    is actually about (e.g. "the shared library itself has a bug"). The
    role only affects how much weight its *relationship edges* carry as
    evidence for identification, via `_relationship_degree`'s existing
    `base_weight / target_degree` formula — nothing here is a second,
    parallel gate.
    """
    return "shared_provider" if consumer_count >= _SHARED_PROVIDER_MIN_CONSUMERS else "consumer"


def _corroboration_evidence(ledger: Ledger) -> dict[str, list[str]]:
    """Repository name -> supporting fact ids, for every repository
    independently corroborated by evidence beyond a lexical ranking score —
    the funnel's "cheap graph/relationship corroboration" and "source
    evidence retrieval" stages, read back as a pure function of facts
    (never stored as a flag anywhere, matching how every other signal in
    this module is re-derived fresh each cycle rather than cached).

    Two independent kinds of corroboration, either is sufficient, and a
    repository can be cited by both:

    - A real cross-repository graph edge (`repository_relationship` fact —
      `app.indexer.graph.cross_repo_linker`) targets it, and clears the
      specificity bar below. Cheap: already gathered by `GraphInvestigator`'s
      existing scoped-traversal machinery once `propose` schedules a
      corroboration query for it (see `GraphInvestigator.propose`'s
      corroborate branch) — no new traversal logic, only a new *trigger*
      for the traversal that already existed.
    - Fetched source content (`pull_request` fact — `GitHubInvestigator`)
      for it actually mentions this request's own specific vocabulary
      (`_ranking_ticket_terms`), not merely the generic capability keywords
      every sibling scaffold shares. A repository whose source WAS fetched
      but does not mention any of that vocabulary is simply absent here —
      this is how source evidence can reject a lexical leader rather than
      merely rubber-stamp it.

    RFC-0012 (Problem A) — a relationship fact is no longer unconditional
    evidence. Its weight is `confidence_weight(fact) / degree(target)`
    (see `_RELATIONSHIP_CONFIDENCE_WEIGHT`/`_relationship_degree`): a
    "structural" edge to a target only one repository in this run points
    at scores 1.0 and always counts; a "heuristic" edge to a target three
    different repositories all point at scores 0.2 and is excluded below
    `_MIN_RELATIONSHIP_SPECIFICITY` — a high-degree shared dependency
    (a library many unrelated repositories legitimately use) is weaker
    identification evidence than a rare, specific one, exactly the same
    way regardless of which repository or library is involved. Nothing is
    blacklisted: the same target can still corroborate a caller whenever
    this run has only observed one caller for it.

    Deliberately not scoped to already-live candidates: a repository that
    just missed the lexical ranking's `TIE_RATIO` cutoff — and so never
    became a `basis: "ranking"` candidate at all — is exactly the kind of
    thing corroborating evidence should be able to promote ahead of a
    leader that only *looks* strong lexically (requirement: cross-
    repository relationships usable as evidence *before* selection, not
    only to confirm it after). See `resync_corroborated_candidates`, the
    hook that actually promotes one.
    """
    evidence: dict[str, list[str]] = {}
    degree = _relationship_degree(ledger)
    for fact in ledger.facts_of("repository_relationship"):
        confidence = str(fact.value.get("confidence", "heuristic"))
        base_weight = _RELATIONSHIP_CONFIDENCE_WEIGHT.get(confidence, 0.0)
        target_degree = max(degree.get(fact.subject, 1), 1)
        weight = base_weight / target_degree
        if weight < _MIN_RELATIONSHIP_SPECIFICITY:
            continue
        # Both endpoints, not only the target `fact.subject` records: for a
        # *ranked* candidate (as opposed to `resync_relationship_
        # candidates`'s explicit-source case), the discriminating fact is
        # usually "this candidate has a real dependency edge to something,"
        # not specifically which direction it points — the PROT-5764 shape
        # exactly: the caller repository is the *source* of a real
        # DEPENDS_ON_REPOSITORY edge to the shared library, and that edge
        # is what makes the caller verifiably real and connected, versus a
        # lexically-identical sibling with no graph edges at all.
        evidence.setdefault(fact.subject, []).append(fact.fact_id)
        source_repo = str(fact.value.get("source_repository", ""))
        if source_repo:
            evidence.setdefault(source_repo, []).append(fact.fact_id)

    ticket_terms = _ranking_ticket_terms(ledger)
    if ticket_terms:
        # RFC-0015 — lexical/source matches must clear the same kind of
        # specificity bar relationship evidence already does (RFC-0012):
        # *which* words matched, not merely *whether* any did. Weights are
        # specific to *which candidate* a match is being scored for (see
        # `_term_specificity_weights`'s `exclude_repository`), so this
        # caches one weight map per repository rather than one shared map
        # for all of them — a term unique to exactly the correct
        # candidate must score as specific *for that candidate*, not be
        # diluted by counting the candidate's own match against itself.
        weights_by_repo: dict[str, dict[str, float]] = {}

        def _weights_for(repo: str) -> dict[str, float]:
            if repo not in weights_by_repo:
                weights_by_repo[repo] = _term_specificity_weights(
                    ledger, ticket_terms, exclude_repository=repo
                )
            return weights_by_repo[repo]

        by_name = {f.subject: f for f in ledger.facts_of("repository")}
        for fact in ledger.facts_of("pull_request"):
            matched_repo = next(
                (
                    name
                    for name, repo_fact in by_name.items()
                    if fact.subject == (repo_fact.value.get("full_name") or name)
                ),
                None,
            )
            matched_terms = tokenize(fact.text) & ticket_terms
            if matched_repo is None or not matched_terms:
                continue
            score = _matched_term_specificity(matched_terms, _weights_for(matched_repo))
            if score < MIN_SOURCE_EVIDENCE_SPECIFICITY:
                continue
            evidence.setdefault(matched_repo, []).append(fact.fact_id)
        # RFC-0014 — actual file *content* (`source_file` facts —
        # `GitHubInvestigator`'s bounded escalation fetch, see
        # `_select_relevant_source_files`), kept distinct from the
        # `pull_request` branch above (repository metadata/PR text): the
        # fact's own `repository` property already says which candidate it
        # belongs to, no `full_name` reconstruction needed, because this
        # module controls exactly how that fact gets written.
        #
        # RFC-0015 — the live PROT-5764 benchmark's actual failure mode:
        # a fetched file whose only overlap with the ticket was generic
        # ETL/programming vocabulary ("validation", "failed", "client")
        # used to corroborate on *any* overlap at all. Same specificity
        # bar as the `pull_request` branch above now applies here too.
        for fact in ledger.facts_of("source_file"):
            repo_name = str(fact.value.get("repository", ""))
            matched_terms = tokenize(fact.text) & ticket_terms
            if not repo_name or not matched_terms:
                continue
            score = _matched_term_specificity(matched_terms, _weights_for(repo_name))
            if score < MIN_SOURCE_EVIDENCE_SPECIFICITY:
                continue
            evidence.setdefault(repo_name, []).append(fact.fact_id)
    return evidence


def _select_relevant_source_files(
    ledger: Ledger, repository: str, *, limit: int = MAX_SOURCE_FILES_PER_CANDIDATE
) -> list[str]:
    """RFC-0014 — this repository's own already-indexed components
    (functions/classes/modules — `component` facts, recorded by the
    graph-corroboration stage's own scoped traversal, which always runs
    *before* a candidate is eligible for source escalation), ranked by how
    many of the ticket's own specific vocabulary words (`_ranking_ticket_
    terms`) their name matches, reduced to the distinct file paths of the
    top `limit`.

    No new GitHub call needed to produce this list — the symbol/file map
    already exists from a stage this candidate already passed through to
    reach here (`GraphInvestigator`'s scoped traversal records a
    `component` fact, with `file_path`, for every function/class/module in
    scope). This is what replaces "fetch repository metadata and hope the
    repo name happens to share a word with the ticket" with "fetch the
    file(s) whose own indexed symbols actually match what the ticket is
    about" — entirely generic: no filename, symbol name, or ticket term is
    ever hardcoded here, only the request's own vocabulary and the
    repository's own already-indexed structure.

    Deliberately no fallback when nothing matches (empty ticket_terms, or
    no component whose name shares any word with them): "nothing indexed
    corresponds to what this ticket is actually about" is a real, honest
    answer, not a reason to guess at some other file.

    RFC-0015 — ranked by *specificity* (`_matched_term_specificity`), not
    raw overlap count: a component matching one rare term outranks one
    matching three generic ones. Selection itself still only requires
    *some* match (any specificity > 0) — the file is worth reading to see
    what it actually contains; the specificity bar that decides whether
    its *fetched content* is strong enough to corroborate the repository
    is enforced later, in `_corroboration_evidence`, not here. A component
    name is a cheap hint about where to look, not the evidence itself.

    RFC-0017 — matched against `_match_text` (name + type + file_path,
    `app.agents.planning.tools`'s existing repository-ranking match text,
    reused rather than re-derived) instead of the bare component name: a
    file's own path is frequently the only place domain vocabulary
    appears (a function named `main` inside `pipeline/validation/
    schema_validator.py` was previously invisible to name-only matching).
    Also applies the same test-code discount (`_is_test_component`/
    `_TEST_RELEVANCE_FACTOR`) `rank_repositories` already uses — without
    it, a repository's test suite (routinely the majority of its indexed
    components) can win a top-`limit` cutoff on vocabulary the test
    merely exercises, crowding out the production file actually worth
    reading.
    """
    ticket_terms = _ranking_ticket_terms(ledger)
    if not ticket_terms:
        return []
    weights = _term_specificity_weights(ledger, ticket_terms, exclude_repository=repository)
    scored: dict[str, float] = {}
    for fact in ledger.facts_of("component"):
        if fact.value.get("repository") != repository:
            continue
        file_path = str(fact.value.get("file_path") or "")
        if not file_path:
            continue
        matched_terms = tokenize(_match_text(fact.value)) & ticket_terms
        if not matched_terms:
            continue
        score = _matched_term_specificity(matched_terms, weights)
        if _is_test_component(fact.value):
            score *= _TEST_RELEVANCE_FACTOR
        scored[file_path] = max(scored.get(file_path, 0.0), score)
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    return [path for path, _score in ranked[:limit]]


def _corroborated_ranking_candidates(ledger: Ledger) -> list[Inference]:
    """Live `basis: "ranking"` candidates (TIE_RATIO leaders) that are also
    independently corroborated — see `_corroboration_evidence`. A ranking
    leader that already has its own `repository_candidate` inference only
    needs to be *unblocked*, not re-promoted; a non-leader repository with
    corroborating evidence is promoted separately, by
    `resync_corroborated_candidates`.
    """
    candidates = [
        c
        for c in ledger.live_inferences("repository_candidate")
        if c.value.get("source") == "suggested" and c.value.get("basis") == "ranking"
    ]
    if not candidates:
        return []
    evidence = _corroboration_evidence(ledger)
    return [c for c in candidates if c.statement in evidence]


def resync_corroborated_candidates(ledger: Ledger) -> None:
    """Promotes `source: "suggested", basis: "corroborated"` candidates for
    a repository that never won the lexical ranking outright — never
    became a `basis: "ranking"` `TIE_RATIO` leader — but is independently
    corroborated by a real cross-repository relationship or by fetched
    source content matching this request's own vocabulary (see
    `_corroboration_evidence`).

    This is the mechanism that actually satisfies "cross-repository
    relationships must be usable as evidence before final repository
    selection, not only after selection": without it, corroboration could
    only ever *unblock* a repository the lexical ranking had already
    chosen as its leader (see `_corroborated_ranking_candidates`), never
    promote a different one the ranking alone ranked lower.

    Skips anything already explicit or already a live candidate under any
    basis — corroboration only ever adds a new candidate here, never
    duplicates or reinterprets one another hook already produced (ADR
    0010, invariant I3 — order-independent, idempotent).
    """
    by_name = {f.subject: f for f in ledger.facts_of("repository")}
    if not by_name:
        return
    for name, fact_ids in _corroboration_evidence(ledger).items():
        repo_fact = by_name.get(name)
        if repo_fact is None or _is_explicit_repository(ledger, name) or _has_live_candidate(ledger, name):
            continue
        ledger.add_inference(
            kind="repository_candidate",
            statement=name,
            supporting_fact_ids=[repo_fact.fact_id, *fact_ids],
            value={
                "source": "suggested",
                "reason": (
                    "Independently corroborated by a cross-repository relationship or "
                    "matching source content."
                ),
                "basis": "corroborated",
            },
        )


def resync_relationship_candidates(ledger: Ledger) -> None:
    """Promotes `source: "suggested"` candidates from real cross-repository
    graph edges (see `app.indexer.graph.cross_repo_linker`), recorded as
    `repository_relationship` facts by `GraphInvestigator` for *every* edge
    it observes, unconditionally (ADR 0010, Theme A).

    Only ever promotes a relationship whose `source_repository` is currently
    explicit — `_is_explicit_repository`, not `live_inferences`, so this is
    correct regardless of whether this hook runs before or after the
    explicit-candidate hooks in the same pass. A suggested candidate's own
    relationships are never chained into further suggestions (no
    heuristic-on-heuristic compounding).
    """
    by_name = {f.subject: f for f in ledger.facts_of("repository")}
    if not by_name:
        return
    for fact in ledger.facts_of("repository_relationship"):
        source_repo = str(fact.value.get("source_repository", ""))
        target_repo = fact.subject
        if (
            target_repo not in by_name
            or not _is_explicit_repository(ledger, source_repo)
            or _is_explicit_repository(ledger, target_repo)
            or _has_live_candidate(ledger, target_repo)
        ):
            continue
        ledger.add_inference(
            kind="repository_candidate",
            statement=target_repo,
            supporting_fact_ids=[by_name[target_repo].fact_id, fact.fact_id],
            value={
                "source": "suggested",
                "reason": str(fact.value.get("reason", "")),
                "relationship": str(fact.value.get("via", "")),
                # ADR 0010 (Theme E) — "structural" (a literal Feign target
                # or Kafka topic name) or "heuristic" (a dependency-name
                # match); threaded through to `RepositoryCandidate` so the
                # UI can distinguish the two rather than presenting every
                # suggestion with equal certainty.
                "confidence": str(fact.value.get("confidence", "")),
            },
        )


# Pure, no-I/O ledger-consistency steps run every reasoning cycle (see
# `engine._resync`), regardless of which investigator (if any) acted this
# cycle. Order matters only for which of two *explicit* sources wins the
# displayed `reason` text on the rare case both are true for the same
# repository in the same cycle (`resync_repository_candidates` wins ties
# over `resync_verified_claim_candidates`) — it never affects which names
# end up live or whether a name is explicit vs suggested, since
# `_is_explicit_repository` recomputes that from facts, not from what an
# earlier hook in this list happened to have written already. A registry,
# not a hardcoded call, so a future candidate source is one more entry, not
# an engine change.
LEDGER_RESYNC_HOOKS: tuple[Callable[[Ledger], None], ...] = (
    resync_repository_candidates,
    resync_verified_claim_candidates,
    resync_ranked_candidates,
    resync_relationship_candidates,
    resync_corroborated_candidates,
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


def _documentation_unreachable_detail(ledger: Ledger) -> str:
    """ "Confluence is not connected" is only one of several reasons the
    "Documentation source reachable" signal below can fail — the others
    (every MCP call attempted but rejected, e.g. an Atlassian API token
    missing Teamwork Graph permission) mean Confluence *is* connected, and
    telling an operator to "Connect Confluence" for those is actively
    wrong guidance (this is what ConfluenceInvestigator.run's own summary
    already distinguishes — see its docstring — this just surfaces the
    same distinction here instead of a second, generic hardcoded string)."""
    unavailable = [
        e for e in ledger.evidence if e.provider == "confluence" and e.outcome == "unavailable"
    ]
    if unavailable and unavailable[-1].summary:
        return unavailable[-1].summary
    return "Confluence is not connected"


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
            detail=_documentation_unreachable_detail(ledger),
            evidence_ids=[e.evidence_id for e in reachable],
        ),
    ]


def _documentation_remediation(ledger: Ledger) -> list[str]:
    """Same "don't send someone to fix a healthy system" rule
    `_architecture_remediation` above already applies to the graph
    connection: "Connect Confluence" is only correct advice when there is
    no Confluence connection to search with. If one exists and was
    searched but every call was rejected (e.g. the Atlassian API token
    lacks Teamwork Graph permission — see `ConfluenceProvider.
    resolve_for_issue`), the fix is on the Atlassian side, not GraphForge's
    Settings -> Integrations screen."""
    unavailable = [
        e for e in ledger.evidence if e.provider == "confluence" and e.outcome == "unavailable"
    ]
    attempted = any(e.summary and "not connected" not in e.summary.lower() for e in unavailable)
    if attempted:
        return [
            "Check the Confluence evidence entry's error detail for the exact cause",
            "Verify the Atlassian API token has Teamwork Graph permission",
            "Link a design page to the ticket",
        ]
    return ["Connect Confluence", "Link a design page to the ticket"]


# ---------------------------------------------------------------------------
# runtime_execution (RFC-004 Capability 1 — shadow mode, Phase 1a)
# ---------------------------------------------------------------------------


def _runtime_execution_necessity(_ledger: Ledger) -> Necessity:
    """Deliberately always `"not_applicable"` for the whole of Phase 1a —
    not a placeholder, the actual shadow-mode mechanism. `overall_confidence`
    and `unmet` both exclude every `not_applicable` assessment, which is the
    same exclusion the framework already applies to a genuinely inapplicable
    capability; reused here, unchanged, to keep this capability assessed and
    visible on its own (see `_runtime_execution_signals`) while structurally
    incapable of moving the aggregate confidence number, the investigation
    work-queue (`unmet`), or `engine._select`'s action prioritization. This
    is not conditioned on evidence — it must hold even once `call_edge`
    facts exist, which is exactly the case shadow mode exists to observe
    without acting on."""
    return "not_applicable"


def _runtime_execution_signals(ledger: Ledger) -> list[ConfidenceSignal]:
    """Evidence-only: reads `call_edge` facts already recorded by
    `curate_evidence` (RFC-004 Commit 4) and nothing else — no LLM, no
    inference beyond what a fact literally states. A `call_edge` fact with
    an empty `steps` list is `curate_evidence`'s own honest "no CALLS edge
    found" outcome (see `runtime_execution.build_call_chains`'s docstring)
    — real evidence that nothing was reconstructed, not the same as no
    attempt having been made, and not treated as satisfying this signal."""
    call_edge_facts = ledger.facts_of("call_edge")
    with_steps = [f for f in call_edge_facts if f.value.get("steps")]
    return [
        signal(
            "Call chain reconstructed from CALLS edges",
            bool(with_steps),
            LOAD_BEARING_WEIGHT,
            detail="no call_edge fact recorded at least one traversed CALLS edge",
            evidence_ids=[f.evidence_id for f in with_steps],
            fact_ids=[f.fact_id for f in with_steps],
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
        key="documentation",
        label="Documentation",
        gap_summary="No design documentation was found for this work.",
        gap_why=(
            "Design docs often carry constraints a ticket omits; without them Planning may "
            "miss a stated requirement."
        ),
        necessity=_documentation_necessity,
        signals=_documentation_signals,
        remediation=_documentation_remediation,
    ),
    Capability(
        key="runtime_execution",
        label="Runtime Execution",
        gap_summary="No call chain has been reconstructed for this request.",
        gap_why=(
            "Shadow mode (RFC-004 Phase 1a): observational only. Never required or "
            "recommended, so it never blocks readiness or factors into overall confidence — "
            "see `_runtime_execution_necessity`."
        ),
        necessity=_runtime_execution_necessity,
        signals=_runtime_execution_signals,
        remediation=lambda _l: [
            "No action required — Runtime Execution Discovery is observational in this phase."
        ],
        # No question/verify: shadow mode never asks the human anything, and
        # nothing a human could answer would help reconstruct a call chain.
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
