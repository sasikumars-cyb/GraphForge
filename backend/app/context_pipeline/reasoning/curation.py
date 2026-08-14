"""Evidence curation — turns a repository's full component list into a
bounded, ranked, tiered `EvidencePackage`, the thing this redesign exists
to produce (see the architecture review this implements, and ADR 0013's
sibling ADR for the test/production grounding work this builds on).

The problem this closes: `projection.build_result` used to do
`graph_components: [dict(f.value) for f in ledger.facts_of("component")]`
— every component fact, zero filtering. A real run surfaced 238 of them,
in whatever order Neo4j returned them, with no way to tell which five
actually mattered. Every function here is pure (plain dicts and lists in,
an `EvidencePackage` out) so it needs no Neo4j/Postgres session to test
and can never itself perform I/O — the graph read that produces its
`neighborhood` input already happened by the time `curate()` is called
(see `investigators.GraphInvestigator`, which fetches it via the new
hop-bounded `get_neighborhood` primitive, not `get_full_graph`).

Scoring is a composite of independently-explainable terms (never an
opaque single number an LLM produced) — see `ComponentScore` — so
`EvidenceItem.reason` is generated FROM the score breakdown that already
decided the tier, not maintained as separate prose that could drift from
the number that actually mattered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.normalization import tokenize

Tier = Literal["must_modify", "architecture_dependency", "reusable_component", "relevant_test"]

# Budget per tier — matches the redesign's explicit evidence-budget
# section. Never "how many happened to be found", always "how many
# reach Planning" — the true count beyond the budget is preserved
# separately (`EvidencePackage.excluded_count`) rather than silently
# implied to be zero, the same honest-total convention
# `projection._findings` already uses for the human-facing report.
TIER_BUDGETS: dict[Tier, int] = {
    "must_modify": 10,
    "architecture_dependency": 10,
    "reusable_component": 10,
    "relevant_test": 5,
}

# A component scoring at or below this composite is not evidence of
# anything — it neither matches the ticket's own words nor sits near
# something that does. Excluding it isn't "recall lost", it's exactly
# the redesign's stated goal: a component with no explainable reason to
# include is a component that shouldn't be included.
_FLOOR_SCORE = 0.05

# How strongly a test-classified component's score is discounted before
# tiering — applied as a flat subtraction, not a multiplier, so it
# cannot be a no-op the way a multiplicative discount was on a
# already-zero relevance score (see app.agents.planning.tools's
# `_TEST_RELEVANCE_FACTOR` docstring for the exact bug this avoids
# repeating: 0 * 0.3 == 0, identical to a production component that
# also scored 0). A flat penalty always moves a test component's score
# down relative to an otherwise-identical production one, regardless of
# what either started at.
_TEST_PENALTY = 0.35

# Deterministic reuse-naming heuristic — the same kind of narrow,
# explainable pattern match this codebase already uses elsewhere (see
# app.indexer.classification's is_test detection) rather than an LLM
# judgment call about what "looks reusable".
_REUSE_NAME_RE = re.compile(
    r"(^|_|/)(util|utils|helper|helpers|base|abstract|interface|common|shared)($|_|\.)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ComponentScore:
    """Every term that composes one component's ranking, kept separate
    rather than pre-summed — this IS the "why selected" explanation, not
    a number a separate explanation has to be kept consistent with."""

    relevance: float
    proximity: float
    repository_bonus: float
    test_penalty: float

    @property
    def composite(self) -> float:
        return max(0.0, self.relevance + self.proximity + self.repository_bonus - self.test_penalty)


class EvidenceItem(BaseModel):
    """One component Context Discovery decided matters, with the full
    reasoning behind that decision — every field the redesign's Evidence
    Package spec asked for."""

    name: str
    repository: str
    path: str = ""
    symbol_type: str = ""
    component_type: str = ""
    is_test: bool = False
    is_test_confidence: float = 0.0
    tier: Tier
    relevance_score: float
    proximity_score: float
    repository_bonus: float
    test_penalty: float
    composite_score: float
    # The composite score, normalized against the top score in this run —
    # "how confident is Context Discovery this item belongs in its tier",
    # not a restatement of is_test_confidence (a different, unrelated
    # confidence: whether the is_test classification itself is correct).
    confidence: float
    hop_distance: int | None = None
    reason: str
    # RFC-0033 — a small, bounded snippet of the component's own fetched
    # source (never the full file — see `_select_source_excerpt`), so
    # Planning's LLM can check a claim about behavior against the actual
    # code instead of inferring it purely from ticket prose. Empty by
    # default/whenever no source was fetched or no meaningful anchor was
    # found — never a placeholder, per `_select_source_excerpt`'s own
    # docstring on why guessing is worse than omitting.
    source_excerpt: str = ""


class EvidencePackage(BaseModel):
    """The curated, budget-bounded replacement for a raw `graph_components`
    dump. `items` is already tiered and budget-sliced — a consumer never
    needs to re-rank or re-truncate it. `excluded_count` is the honest
    total minus what made it in, so "we found more but didn't show it" is
    always stated, never silently implied to be zero."""

    items: list[EvidenceItem] = Field(default_factory=list)
    excluded_count: int = 0
    total_candidates: int = 0

    def by_tier(self, tier: Tier) -> list[EvidenceItem]:
        return [item for item in self.items if item.tier == tier]


# Self-review finding: exact-token matching alone misses common,
# legitimate word-form variants a real ticket uses all the time —
# "dedup" the ticket's own word vs. `ExactDeduplicator` the class,
# "merge" vs. `SCDType2Merger` — because tokenize()'s camelCase/snake_case
# splitting produces glued tokens ("deduplicator", "scdtype2") that don't
# equal a shorter, related ticket word as a SET member, only as a prefix
# of it. A shared prefix at least `_MIN_PREFIX_MATCH_LEN` characters long
# is real, deterministic, explainable evidence of relatedness — not a
# guess, a verifiable string fact — so it earns partial credit (half an
# exact match's weight, scored separately so an exact match always still
# outranks a partial one). This does NOT close every real-world gap:
# "SCD2" the ticket's own abbreviation and `SCDType2Merger` the class
# share no prefix relationship at all ("scd2" vs "scdtype2" diverge at
# their 4th character) — closing that specific case would need semantic/
# embedding similarity, an explicit, separate, not-yet-decided trade-off
# (see the architecture review) that this deterministic prefix heuristic
# is not attempting to substitute for.
_MIN_PREFIX_MATCH_LEN = 5
_PARTIAL_MATCH_WEIGHT = 0.5


def _prefix_related(a: str, b: str) -> bool:
    if a == b:
        return False  # exact equality is scored separately, at full weight
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= _MIN_PREFIX_MATCH_LEN and longer.startswith(shorter)


def _relevance_score(component: dict[str, Any], ticket_tokens: frozenset[str]) -> float:
    """Fraction of the component's own name/path tokens that also appear
    in the ticket text — bounded [0, 1] by construction (a fraction of
    the component's own token set, never of the ticket's, so a long
    ticket can't inflate every component's score just by containing more
    words overall). A token with no exact match but a real shared prefix
    with some ticket token (see `_prefix_related`) earns partial credit
    instead of contributing nothing."""
    text = f"{component.get('name', '')} {component.get('file_path', '')}"
    component_tokens = tokenize(text)
    if not component_tokens:
        return 0.0
    exact = component_tokens & ticket_tokens
    partial_credit = sum(
        _PARTIAL_MATCH_WEIGHT
        for token in component_tokens - exact
        if any(_prefix_related(token, tt) for tt in ticket_tokens)
    )
    return (len(exact) + partial_credit) / len(component_tokens)


def _proximity_score(hop_distance: int | None) -> float:
    """1.0 at the anchor itself (hop_distance=0), decaying with distance;
    0.0 for anything outside the fetched neighborhood at all (never
    reached — the neighborhood fetch itself is what already answered
    "not connected" by omission). Replace with a personalized-PageRank
    value for the same [0, 1] range as a drop-in upgrade — every caller
    of this function is agnostic to which one produced the number."""
    if hop_distance is None:
        return 0.0
    return 1.0 / (1.0 + hop_distance)


def _reason_for(component: dict[str, Any], score: ComponentScore, hop_distance: int | None) -> str:
    """One sentence naming whichever score term actually dominated —
    generated from the same breakdown that decided the tier, so this can
    never assert a reason the score doesn't support."""
    if hop_distance == 0:
        return "Named directly in the request."
    if score.proximity > 0 and hop_distance is not None:
        plural = "s" if hop_distance != 1 else ""
        return f"{hop_distance} hop{plural} from a component named in the request."
    if score.relevance > 0:
        return "Its name/path closely matches the request's own wording."
    if score.repository_bonus > 0:
        return "In the identified repository, though not otherwise closely matched."
    return "Scored above the inclusion floor, but no single signal dominated."


def select_anchor_ids(
    components: list[dict[str, Any]],
    enriched_text: str,
    primary_repository: str,
    *,
    limit: int = 8,
) -> list[str]:
    """The node ids to seed the bounded-neighborhood graph fetch from —
    components in `primary_repository` whose own name/path most closely
    matches the request text, best first, capped at `limit`.

    Scoped to one repository deliberately: this is what decides which
    single Neo4j query (`IGraphRepository.get_neighborhood`) actually
    runs — the "Primary Repository" the redesign's evidence budget names
    (capped at 1), not every repository the ticket happens to touch.
    Components in *other* repositories can still surface in the final
    `EvidencePackage` (as `architecture_dependency`/`reusable_component`
    via relevance or repository-bonus scoring in `curate()`), they just
    never seed the neighborhood traversal itself.

    Zero relevance-scoring components (nothing in this repository
    matches the request at all) returns an empty list — callers should
    treat that as "no neighborhood to fetch", not "fetch everything to
    compensate", matching the redesign's own precision-over-recall
    principle: an empty anchor set is not evidence of failure.
    """
    repo_name_tokens: frozenset[str] = tokenize(primary_repository)
    ticket_tokens = tokenize(enriched_text) - repo_name_tokens

    candidates = [
        (component, _relevance_score(component, ticket_tokens))
        for component in components
        if str(component.get("repository", "")).lower() == primary_repository.lower()
    ]
    candidates = [(c, score) for c, score in candidates if score > 0 and c.get("id")]
    candidates.sort(key=lambda pair: pair[1], reverse=True)
    return [str(component["id"]) for component, _ in candidates[:limit]]


def _is_reuse_shaped(component: dict[str, Any]) -> bool:
    text = f"{component.get('name', '')} {component.get('file_path', '')}"
    return bool(_REUSE_NAME_RE.search(text))


# RFC-0033 — bounded source excerpts for must_modify evidence, so Planning's
# LLM can check a claim about behavior against real code instead of only
# inferring it from ticket prose (see the RFC-0032 audit: a field that was
# explicitly assigned the wrong value got described as "not assigned",
# because no evidence layer ever showed the LLM the actual line).
_EXCERPT_CONTEXT_LINES = 2
_EXCERPT_MAX_CHARS = 300
_DEF_LINE_RE = re.compile(r"^(\s*)(?:async\s+def|def|class)\s+(\w+)\b")


def _symbol_body_bounds(lines: list[str], short_name: str) -> tuple[int, int] | None:
    """The line range of `short_name`'s own `def`/`class` block: from its
    definition line up to (not including) the next sibling definition at
    the same or shallower indentation, or end of file. `None` if no
    definition line for this name is found at all — signal #1
    ("component/symbol identity") is only usable when the fetched source
    actually contains the symbol Context Discovery named, which is not
    guaranteed (the component index and a later fetch can drift, or the
    name may not be a def/class at all, e.g. a module-level constant)."""
    def_idx = next(
        (
            i
            for i, line in enumerate(lines)
            if (m := _DEF_LINE_RE.match(line)) and m.group(2) == short_name
        ),
        None,
    )
    if def_idx is None:
        return None
    indent = len(_DEF_LINE_RE.match(lines[def_idx]).group(1))  # type: ignore[union-attr]
    end_idx = len(lines)
    for i in range(def_idx + 1, len(lines)):
        m = _DEF_LINE_RE.match(lines[i])
        if m and len(m.group(1)) <= indent:
            end_idx = i
            break
    return def_idx, end_idx


def _best_vocabulary_line(
    lines: list[str], lo: int, hi: int, ticket_tokens: frozenset[str]
) -> int | None:
    """The line index in `lines[lo:hi]` whose own tokens overlap
    `ticket_tokens` the most (ties go to the earliest line — same
    insertion-order determinism this module already uses elsewhere).
    `None` if nothing in the range shares any vocabulary at all — this is
    signal #3 ("ticket-token overlap"), the same tokenization/token set
    `curate()` already computes once for relevance scoring, not a new
    matching mechanism."""
    best_idx, best_overlap = None, 0
    for i in range(lo, hi):
        overlap = len(tokenize(lines[i]) & ticket_tokens)
        if overlap > best_overlap:
            best_idx, best_overlap = i, overlap
    return best_idx


def _select_source_excerpt(text: str, name: str, ticket_tokens: frozenset[str]) -> str:
    """A small, bounded snippet of `text` anchored to whichever of these
    signals is actually available, in order:

    1. The component's own `def`/`class` block (signal: symbol identity
       Context Discovery already assigned this evidence item) — scoped
       to the shortcut `def`/`class` line, but see below.
    2. "Matched source vocabulary already produced by the existing
       relevance scorer" does not exist anywhere in this codebase today
       (`_relevance_score`/RFC-0027's term-specificity machinery both
       match ticket tokens only against a component's name/path, never
       against fetched file content) — there is no second signal to
       reuse here, so this tier is a documented no-op, not a fabricated
       one.
    3. Ticket-token overlap, scoped to the symbol's own body when found
       (1), or the whole file when it wasn't — this is what actually
       picks the specific line, since anchoring on the `def` line alone
       would show a function's signature, not its bug, for anything
       longer than a couple of lines.
    4. No line anywhere shares ticket vocabulary, and no symbol
       definition was found either: return "" rather than guess (no
       first-N-lines fallback — an arbitrary excerpt is worse than none,
       since it would look authoritative without being relevant).

    Bounded to `_EXCERPT_CONTEXT_LINES` lines of context and
    `_EXCERPT_MAX_CHARS` total — this is a pointer at the right few
    lines, never a file dump.
    """
    if not text or not name:
        return ""
    lines = text.splitlines()
    short_name = name.rsplit(".", 1)[-1]
    bounds = _symbol_body_bounds(lines, short_name)
    search_lo, search_hi = bounds if bounds is not None else (0, len(lines))

    anchor = _best_vocabulary_line(lines, search_lo, search_hi, ticket_tokens)
    if anchor is None and bounds is not None:
        # Symbol found, but nothing inside it shares ticket vocabulary —
        # the def/class line itself is still a meaningful anchor (answers
        # "which function contains this behavior?"), so use it rather
        # than falling all the way through to "".
        anchor = bounds[0]
    if anchor is None and bounds is None:
        # No symbol match at all — last resort, search the whole file.
        anchor = _best_vocabulary_line(lines, 0, len(lines), ticket_tokens)
    if anchor is None:
        return ""

    lo = max(0, anchor - _EXCERPT_CONTEXT_LINES)
    hi = min(len(lines), anchor + _EXCERPT_CONTEXT_LINES + 1)
    return "\n".join(lines[lo:hi])[:_EXCERPT_MAX_CHARS]


def curate(
    *,
    components: list[dict[str, Any]],
    neighborhood_nodes: list[dict[str, Any]],
    enriched_text: str,
    target_repositories: list[str],
    source_file_texts: dict[tuple[str, str], str] | None = None,
) -> EvidencePackage:
    """Produce the curated, tiered, budget-bounded `EvidencePackage`.

    `components` — every component this run's graph traversal returned
    (unfiltered; `target_repositories` is what actually scopes relevance,
    not a pre-filtered input, so a component from a *related* repository
    can still surface as an architecture dependency).

    `neighborhood_nodes` — the bounded-neighborhood fetch's own node
    list (each carrying `hop_distance`, `id`; see
    `IGraphRepository.get_neighborhood`) — a component not present here
    at all was outside the neighborhood entirely (proximity_score=0), not
    merely unscored.

    `enriched_text` — the request plus every retrieved prose fact (see
    `projection.render_enriched_text`) — tokenized once, reused for
    every component's relevance score.

    `target_repositories` — the identified/selected repositories (see
    `RepositoryCandidate.selected`) — components here get the ownership
    bonus; components elsewhere do not, regardless of how well they
    score otherwise.

    `source_file_texts` — RFC-0033, optional and `None` by default so
    every existing caller/test keeps today's exact behavior unchanged.
    Keyed by `(repository, path)` to whatever `source_file` fact text
    was already fetched (see `investigators.curate_evidence`, the only
    caller that has ledger access to build this) — `curate()` itself
    stays pure, this is just one more plain dict of data in, same as
    `components`/`neighborhood_nodes`. Only consulted for `must_modify`
    tier items; see `_select_source_excerpt`.
    """
    # Repository-name tokens are stripped from the relevance signal, not
    # just left in: a ticket almost always names its own repository
    # ("Repo: etl-core"), and almost every component's file path contains
    # that same repository/package name as a path segment
    # (`src/etl_core/...`) — without this, every single component in the
    # target repository would get an artificial relevance boost from
    # matching "etl"/"core" against its own path, purely because of where
    # it lives, not because of anything the ticket actually said about
    # it. Repository membership is already scored on its own merits via
    # `repository_bonus` below; relevance should measure something more
    # specific than "you live here too".
    repo_name_tokens: frozenset[str] = frozenset()
    for name in target_repositories:
        repo_name_tokens |= tokenize(name)
    ticket_tokens = tokenize(enriched_text) - repo_name_tokens
    target_repo_set = {name.lower() for name in target_repositories}
    hop_by_id = {
        str(node["id"]): node.get("hop_distance") for node in neighborhood_nodes if node.get("id")
    }

    scored: list[tuple[dict[str, Any], ComponentScore, int | None]] = []
    for component in components:
        node_id = str(component.get("id", ""))
        hop_distance = hop_by_id.get(node_id)
        in_target_repo = str(component.get("repository", "")).lower() in target_repo_set
        repository_bonus = 0.15 if in_target_repo else 0.0
        score = ComponentScore(
            relevance=_relevance_score(component, ticket_tokens),
            proximity=_proximity_score(hop_distance),
            repository_bonus=repository_bonus,
            test_penalty=_TEST_PENALTY if component.get("is_test") else 0.0,
        )
        scored.append((component, score, hop_distance))

    # Rank once, by composite score, best first — every tier is a slice
    # of this single ordering, never a separately-derived sort (see the
    # architecture review's critique of maintaining several independent
    # per-tier threshold rules).
    scored.sort(key=lambda entry: entry[1].composite, reverse=True)
    top_score = scored[0][1].composite if scored else 0.0

    def confidence_for(score: ComponentScore) -> float:
        return 0.0 if top_score <= 0 else min(1.0, score.composite / top_score)

    must_modify: list[EvidenceItem] = []
    architecture_dependency: list[EvidenceItem] = []
    reusable_component: list[EvidenceItem] = []
    relevant_test: list[EvidenceItem] = []

    def _make_item(
        component: dict[str, Any],
        score: ComponentScore,
        hop_distance: int | None,
        tier: Tier,
    ) -> EvidenceItem:
        source_excerpt = ""
        # RFC-0033 — must_modify only: this is the tier a root-cause claim
        # actually gets made about, and keeping it scoped here (rather than
        # every tier) is what keeps the added prompt text small.
        if tier == "must_modify" and source_file_texts:
            key = (str(component.get("repository", "")), str(component.get("file_path", "")))
            file_text = source_file_texts.get(key, "")
            if file_text:
                source_excerpt = _select_source_excerpt(
                    file_text, str(component.get("name", "")), ticket_tokens
                )
        return EvidenceItem(
            name=str(component.get("name", "")),
            repository=str(component.get("repository", "")),
            path=str(component.get("file_path", "")),
            symbol_type=str(component.get("symbol_type", "")),
            component_type=str(component.get("component_type") or component.get("type", "")),
            is_test=bool(component.get("is_test", False)),
            is_test_confidence=float(component.get("confidence") or 0.0),
            tier=tier,
            relevance_score=round(score.relevance, 4),
            proximity_score=round(score.proximity, 4),
            repository_bonus=score.repository_bonus,
            test_penalty=score.test_penalty,
            composite_score=round(score.composite, 4),
            confidence=round(confidence_for(score), 4),
            hop_distance=hop_distance,
            reason=_reason_for(component, score, hop_distance),
            source_excerpt=source_excerpt,
        )

    included_ids: set[str] = set()
    for component, score, hop_distance in scored:
        if score.composite <= _FLOOR_SCORE:
            continue
        node_id = str(component.get("id", ""))
        is_test = bool(component.get("is_test", False))

        if is_test:
            continue  # tests are only ever placed via the relevant_test pass below
        if len(must_modify) < TIER_BUDGETS["must_modify"] and hop_distance == 0:
            must_modify.append(_make_item(component, score, hop_distance, "must_modify"))
            included_ids.add(node_id)
        elif len(architecture_dependency) < TIER_BUDGETS["architecture_dependency"] and (
            hop_distance is not None or score.relevance > 0
        ):
            architecture_dependency.append(
                _make_item(component, score, hop_distance, "architecture_dependency")
            )
            included_ids.add(node_id)
        elif len(reusable_component) < TIER_BUDGETS["reusable_component"] and _is_reuse_shaped(
            component
        ):
            reusable_component.append(
                _make_item(component, score, hop_distance, "reusable_component")
            )
            included_ids.add(node_id)

    # Relevant tests: test-classified components whose OWN proximity
    # places them near a component that already made must_modify/
    # architecture_dependency — "this test protects code we're touching",
    # not "this test happens to mention a ticket keyword".
    protected_repo_paths = {
        (item.repository, item.path) for item in (*must_modify, *architecture_dependency)
    }
    for component, score, hop_distance in scored:
        if len(relevant_test) >= TIER_BUDGETS["relevant_test"]:
            break
        if not component.get("is_test"):
            continue
        if hop_distance is None:
            continue
        key = (str(component.get("repository", "")), str(component.get("file_path", "")))
        if key not in protected_repo_paths and hop_distance > 2:
            continue
        item = _make_item(component, score, hop_distance, "relevant_test")
        reason = f"Protects code {hop_distance} hop(s) away that this work touches."
        item = item.model_copy(update={"reason": reason})
        relevant_test.append(item)
        included_ids.add(str(component.get("id", "")))

    items = [*must_modify, *architecture_dependency, *reusable_component, *relevant_test]
    return EvidencePackage(
        items=items,
        excluded_count=max(0, len(components) - len(items)),
        total_candidates=len(components),
    )


_TIER_HEADINGS: dict[Tier, str] = {
    "must_modify": "Must modify",
    "architecture_dependency": "Architecture dependencies",
    "reusable_component": "Existing reusable components",
    "relevant_test": "Relevant tests",
}


def render_evidence_package_text(package: EvidencePackage) -> str:
    """The prompt-facing rendering every agent's LLM call reads instead of
    a raw component dump — this is the actual fix for "Planning receives
    hundreds of components instead of actionable knowledge": the LLM
    prompt itself now contains only what `curate()` decided matters, each
    with its own reason and confidence, grouped by tier instead of an
    undifferentiated list.

    Empty tiers are omitted entirely rather than printed as "none" — an
    empty section is noise, not information. An empty package as a whole
    (nothing scored above the floor at all) renders as an explicit,
    honest statement rather than silence, so a reader can tell "nothing
    relevant was found" apart from "this render function was never
    called".
    """
    if not package.items:
        return (
            "No components scored as relevant to this request "
            f"(out of {package.total_candidates} indexed)."
        )

    parts: list[str] = []
    for tier in ("must_modify", "architecture_dependency", "reusable_component", "relevant_test"):
        tier_items = package.by_tier(tier)  # type: ignore[arg-type]
        if not tier_items:
            continue
        lines = []
        for item in tier_items:
            line = (
                f"- {item.name} ({item.repository}, {item.path or 'no path'}) — {item.reason} "
                f"[confidence {item.confidence:.0%}]"
            )
            if item.source_excerpt:
                # RFC-0033 — indented so it visually belongs to this item,
                # not a new bullet; already bounded by _select_source_excerpt.
                quoted = "\n".join(f"  | {ln}" for ln in item.source_excerpt.splitlines())
                line += f"\n{quoted}"
            lines.append(line)
        parts.append(f"**{_TIER_HEADINGS[tier]}**:\n" + "\n".join(lines))  # type: ignore[index]

    if package.excluded_count:
        parts.append(
            f"({package.excluded_count} further indexed component(s) scored below the "
            "relevance floor and were not included.)"
        )
    return "\n\n".join(parts)
