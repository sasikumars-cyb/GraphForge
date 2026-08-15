"""Deterministic question grounding — shared by `POST /ask` (a single-shot
answer) and `ConversationService` (the conversational investigation loop
that seeds each new topic with the same real graph facts before an LLM is
ever allowed to reason over them).

Kept in one place, importable by both, so "what counts as an impact
question" and "which repository does this question mean" are decided
exactly once — not classified one way for a first question and a subtly
different way for a follow-up that turns out to name a new repository.

Nothing here calls an LLM. Every field on the returned `AskResponse` is
either read verbatim from a source system (`fact`) or computed
deterministically from the Knowledge Graph (`derived`) — see
`app.schemas.ask`'s own docstring on why that distinction is load-bearing
for this product, not decoration.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.text_relevance import relevance, term_weights, tokenize
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.repository import Repository
from app.schemas.ask import (
    AskAction,
    AskEvidenceItem,
    AskImpact,
    AskRepositoryCandidate,
    AskResponse,
)
from app.services.engineering_intelligence import dependency_query_service
from app.services.engineering_intelligence.contracts import (
    BlastRadius,
    EntityReference,
    QueryResult,
)
from app.services.engineering_intelligence.impact_analysis_service import compute_blast_radius

_REPO_NODE_ID_RE = re.compile(r"^([0-9a-fA-F-]{36}):repository$")

# Closed vocabulary, same spirit as `app.context.resolvers.freetext`'s own
# patterns — "impact" and "dependency" overlap in that resolver (both trip
# its single `_IMPACT_PATTERN`), but this needs to actually distinguish
# them: a blast-radius answer and a "what depends on this" answer are
# different services with different evidence, not two phrasings of one
# question. Impact is checked first so "what breaks if X changes, given
# what depends on it" — mentioning both — still gets the higher-signal
# blast-radius answer rather than a bare dependency list.
_IMPACT_PATTERN = re.compile(
    r"\b(impact\w*|affect\w*|break\w*|broke\w*|risk\w*|blast[- ]radius|ripple\w*|downstream)\b",
    re.IGNORECASE,
)
_DEPENDENCY_PATTERN = re.compile(r"\bdepend(s|encies|ency)?\b", re.IGNORECASE)

# Matches `ImpactAnalysisPage`/`impact.py`'s own default — a blast radius
# computed at a different depth would not be comparable to the one those
# pages show for the same repository.
_MAX_HOPS = 2

# M-2 — reporting limits applied to the STRUCTURED result, before any
# prose or LLM context is built. A repository legitimately reaching dozens
# of entities must not put dozens of names into a prompt (cost, and a
# prompt that buries the signal); truncating the finished sentence instead
# would leave the counts and the narration describing different sets.
# `ImpactFacts.truncated` records that it happened so no consumer implies
# an exhaustive analysis.
_MAX_AFFECTED_PER_KIND = 12
_MAX_KEY_PATHS = 3

# How many near-miss repositories to offer back when a question is
# ambiguous — enough to choose from, few enough to read.
_MAX_CANDIDATES = 5

# H-2 — the ONE rate-limit budget for anything that reaches `ground()`,
# shared by both callers (`POST /ask` and `ConversationService`'s general
# mode) rather than each router counting its own. `/ask` and `POST
# /conversations`/`POST /conversations/{id}/messages` all invoke the same
# deterministic grounding/graph-traversal cost center — a caller
# alternating between the two surfaces must not get a second, independent
# quota just because the request landed on a different endpoint. Defined
# here, not in `app.core.rate_limit` (that module is the generic
# mechanism, shared by unrelated limiters — e.g. workflow stage starts —
# that must stay on their own separate budgets) and not duplicated as a
# literal string in each router, which would silently drift the moment one
# side's key or number changed without the other.
ASK_GROUNDING_RATE_LIMIT = 30
ASK_GROUNDING_RATE_WINDOW_SECONDS = 60.0


def ask_grounding_rate_limit_key(user_id: uuid.UUID) -> str:
    return f"ask_grounding:{user_id}"


def classify(question: str) -> str:
    """ "impact" | "dependency" | "general" — general means "this endpoint
    has no deterministic path for it," not "no repository was found."""
    if _IMPACT_PATTERN.search(question):
        return "impact"
    if _DEPENDENCY_PATTERN.search(question):
        return "dependency"
    return "general"


def _match_text(repo: Repository) -> str:
    return f"{repo.name} {repo.full_name}"


def _exact_named(question: str, repositories: list[Repository]) -> list[Repository]:
    """Repositories the question names outright — `bcs-data-service` or
    `Uplight-Inc/bcs-data-service` appearing literally in the text.

    Matched on the normalized token *sequence*, not raw substring, so
    punctuation and case differences ("BCS Data Service", "bcs_data_
    service", "bcs-data-service?") all resolve, while a repository whose
    name merely happens to be a fragment of a longer word does not. This
    is the strongest signal available and deliberately outranks scoring:
    if the user typed the identifier, no threshold should be able to
    reject it.

    Only the MOST SPECIFIC matches are returned. Prefix-named siblings are
    common ("bcs-data-service", "bcs-data-service-python"), and asking
    about the longer one contains every token of the shorter one — so a
    naive subset test reports both and turns a perfectly unambiguous
    question into a clarification prompt. The longest matched name wins;
    two names of equal length both matching is a real ambiguity and both
    are returned.
    """
    question_tokens = tokenize(question)
    matches: list[tuple[int, Repository]] = []
    for repo in repositories:
        name_tokens = tokenize(repo.name)
        if name_tokens and name_tokens <= question_tokens:
            matches.append((len(name_tokens), repo))
    if not matches:
        return []
    most_specific = max(count for count, _ in matches)
    return [repo for count, repo in matches if count == most_specific]


@dataclass(frozen=True)
class RepositoryCandidate:
    name: str
    full_name: str
    repository_id: str
    score: float


@dataclass(frozen=True)
class RepositoryResolution:
    """The outcome of deciding which repository a question is about.

    Three outcomes, never collapsed into "here's a repository, good
    luck": `resolved` (act on it), `ambiguous` (several plausible — ask
    the user, and show them what we found), `no_match` (nothing plausible
    at all). The previous implementation only had the first, taking the
    argmax of a token-overlap score with no floor and no margin, which
    meant a question containing nothing but the generic word "service"
    still selected one specific repository out of a six-way tie by
    database row order — and the product then presented a full,
    evidence-badged impact assessment for it.
    """

    status: str  # "resolved" | "ambiguous" | "no_match"
    repository: Repository | None = None
    candidates: list[RepositoryCandidate] = dataclasses.field(default_factory=list)
    reason: str = ""


# Resolution safety rails (C-1). Tuned against a real 67-repository
# account; all three are necessary — each one alone still admits a wrong
# answer the others catch.
#
# _MIN_SCORE       a floor, so a single weak incidental token overlap can
#                  never resolve.
# _MIN_MARGIN      top-1 must beat top-2 by this much. This is what
#                  catches the audit's six-way tie at an identical score:
#                  a tie has margin 0 and is ambiguous by definition, no
#                  matter how high the score itself is.
# _MAX_GENERIC_DF_RATIO  a token matching more than this share of the
#                  account's repositories is describing the domain, not
#                  identifying a system, so it cannot be the only reason
#                  a repository was chosen. Computed from the pool, so it
#                  self-calibrates to any account's vocabulary.
_MIN_SCORE = 0.30
_MIN_MARGIN = 0.15
_MAX_GENERIC_DF_RATIO = 0.05

# Words that identify no system on their own, whatever the corpus makes
# of their frequency. The df ratio above already suppresses most of
# these, but it cannot catch the case where an account happens to hold
# exactly one repository containing "service" — the word would then look
# maximally rare and uniquely select it. This list is domain vocabulary,
# never repository- or customer-specific.
_GENERIC_TOKENS = frozenset(
    {
        "api",
        "app",
        "application",
        "backend",
        "code",
        "component",
        "data",
        "frontend",
        "job",
        "library",
        "module",
        "pipeline",
        "platform",
        "project",
        "repo",
        "repository",
        "server",
        "service",
        "system",
        "tool",
    }
)


def _resolve(question: str, repositories: list[Repository]) -> RepositoryResolution:
    """Pure resolution over an already-fetched, already-scoped repository
    list. Split out from the DB read so every rule below is unit-testable
    without a database (see tests/unit/services/test_ask_grounding.py)."""
    if not repositories:
        return RepositoryResolution(status="no_match", reason="no_repositories_indexed")

    terms = list(tokenize(question))
    if not terms:
        return RepositoryResolution(status="no_match", reason="no_usable_terms")

    texts = [_match_text(r) for r in repositories]

    # 1. Exact identifier — the user typed the name. Outranks scoring.
    exact = _exact_named(question, repositories)
    if len(exact) == 1:
        return RepositoryResolution(
            status="resolved", repository=exact[0], reason="exact_name_match"
        )
    if len(exact) > 1:
        return RepositoryResolution(
            status="ambiguous",
            candidates=[_candidate(r, 1.0) for r in exact],
            reason="multiple_repositories_share_that_name",
        )

    # 2. Scored token overlap, with a floor, a margin, and a requirement
    #    that something discriminating actually matched.
    weights = term_weights(terms, texts)
    token_sets = [tokenize(t) for t in texts]
    pool = len(repositories)
    generic_cutoff = max(1, int(pool * _MAX_GENERIC_DF_RATIO))

    def _is_discriminating(token: str) -> bool:
        if token in _GENERIC_TOKENS:
            return False
        df = sum(1 for ts in token_sets if token in ts)
        return 0 < df <= generic_cutoff

    scored = sorted(
        (
            (relevance(text, terms, weights), repo, tokenize(text) & tokenize(" ".join(terms)))
            for text, repo in zip(texts, repositories, strict=True)
        ),
        key=lambda row: (-row[0], row[1].full_name),  # stable: never DB row order
    )
    best_score, best_repo, best_matched = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else 0.0

    plausible = [_candidate(r, s) for s, r, _ in scored if s >= _MIN_SCORE][:_MAX_CANDIDATES]

    if best_score < _MIN_SCORE:
        return RepositoryResolution(
            status="no_match", candidates=plausible, reason="below_minimum_confidence"
        )
    if not any(_is_discriminating(t) for t in best_matched):
        # Only generic/common vocabulary matched — the question never
        # actually named a system.
        return RepositoryResolution(
            status="ambiguous" if len(plausible) > 1 else "no_match",
            candidates=plausible,
            reason="only_generic_terms_matched",
        )
    if best_score - runner_up_score < _MIN_MARGIN:
        return RepositoryResolution(
            status="ambiguous", candidates=plausible, reason="candidates_too_close"
        )
    return RepositoryResolution(status="resolved", repository=best_repo, reason="strong_match")


def _candidate(repo: Repository, score: float) -> RepositoryCandidate:
    return RepositoryCandidate(
        name=repo.name,
        full_name=repo.full_name,
        repository_id=str(repo.id),
        score=round(score, 4),
    )


async def resolve_repository_detailed(
    db: AsyncSession, user_id: uuid.UUID, question: str
) -> RepositoryResolution:
    """Full resolution outcome, including the ambiguity case. Scoped to
    `user_id`, same as every other repository read (see
    `GetIndexedRepositoriesTool`'s own docstring on why an unscoped read
    here would leak another account's repositories into this account's
    answer)."""
    result = await db.execute(select(Repository).where(Repository.user_id == user_id))
    return _resolve(question, list(result.scalars().all()))


async def resolve_repository(
    db: AsyncSession, user_id: uuid.UUID, question: str
) -> Repository | None:
    """The repository this question confidently names, or `None`.

    Kept as the narrow accessor for callers that have no way to present an
    ambiguity to the user (`refinement_grounding`); they now receive
    `None` where they used to receive an arbitrary near-miss, which is the
    safe direction. Callers that CAN ask the user should use
    `resolve_repository_detailed` and surface `candidates`."""
    return (await resolve_repository_detailed(db, user_id, question)).repository


def _severity(downstream_total: int) -> str:
    """Severity from the count of *real downstream* impacted entities —
    repositories, APIs, databases and queues, with the seed repository
    already excluded by `compute_blast_radius`.

    Domain rules, stated explicitly because this number is rendered as a
    judgement to an engineer:

    - 0  -> "low"     nothing downstream depends on this; the change is
                      contained. Reachable only because the seed is no
                      longer counted as its own dependent.
    - 1-2 -> "medium" a small, enumerable set of dependents — every one of
                      them is named in `affected_*`, so "medium" always
                      has a list behind it.
    - 3+  -> "high"   broad enough that a reviewer should look at the
                      graph rather than the summary.

    Deterministic and total: a pure function of one integer, so the same
    counts always produce the same severity, and the severity can never
    disagree with the structured counts the UI renders beside it.
    """
    if downstream_total == 0:
        return "low"
    if downstream_total <= 2:
        return "medium"
    return "high"


@dataclass(frozen=True)
class ImpactFacts:
    """The single canonical impact result for one question.

    Both renderings — the structured `AskImpact` the UI draws, and the
    prose `why` handed to the LLM as narration context — are derived from
    this one object, so they cannot disagree. Before this existed the
    structured lists came from the traversal's node labels while `why`'s
    "key paths" came from `relationship_lookup.fetch_with_confidence`,
    which returns *every* relationship of *every* involved repository, not
    the blast-radius path — that is how a report could name two databases
    in prose while `affected_databases` was empty, and name a downstream
    repository that was absent from `affected_repositories`.

    `key_paths` is therefore built from the traversal's own edges
    (`BlastRadius.subgraph`), the same walk the counts come from.
    """

    seed_repository_name: str
    affected_repositories: list[str]
    affected_apis: list[str]
    affected_databases: list[str]
    affected_queues: list[str]
    key_paths: list[str]
    relationship_count: int
    # Whole-result flag — true if ANY category (repositories, apis,
    # databases, queues, key_paths) was capped. This is what
    # `AskImpact.truncated` surfaces to the UI as one badge; it
    # deliberately stays a single boolean, matching that existing
    # single-boolean API shape.
    truncated: bool
    # M-2 — precise flag for the repository count specifically:
    # `len(affected_repositories) == _MAX_AFFECTED_PER_KIND` is NOT by
    # itself proof of truncation (the true count can legitimately equal
    # the cap exactly), so the repo-count text in `ground_impact` needs
    # this exact per-category signal rather than inferring it from the
    # bounded list's own length or from the whole-result `truncated` flag
    # above (which can be true because a DIFFERENT category — databases,
    # APIs, queues, key_paths — was the one that overflowed, in which case
    # the repository count itself is still exact and must not be qualified
    # with "more than").
    affected_repositories_truncated: bool
    severity: str

    @property
    def downstream_total(self) -> int:
        return (
            len(self.affected_repositories)
            + len(self.affected_apis)
            + len(self.affected_databases)
            + len(self.affected_queues)
        )


def build_impact_facts(
    blast_radius: BlastRadius, repository: Repository, names: dict[str, str]
) -> ImpactFacts:
    """Narrow one `BlastRadius` into the canonical, bounded `ImpactFacts`.

    Bounding happens HERE, on the structured result, before any prose or
    LLM context is built (M-2): a repository legitimately reaching dozens
    of entities must not put dozens of names into a prompt, and truncating
    the finished sentence instead would leave the counts and the narration
    describing different sets. `truncated` is carried on the result so
    every consumer can say the analysis is partial rather than implying it
    is exhaustive.
    """
    seed_node_ids = {blast_radius.seed.node_id}

    def _named(entity_ids: tuple[str, ...]) -> tuple[list[str], bool]:
        visible = [pretty_entity_name(x, names) for x in entity_ids[:_MAX_AFFECTED_PER_KIND]]
        return visible, len(entity_ids) > _MAX_AFFECTED_PER_KIND

    repositories, repos_truncated = _named(blast_radius.impacted_repositories)
    apis, apis_truncated = _named(blast_radius.impacted_apis)
    databases, dbs_truncated = _named(blast_radius.impacted_databases)
    queues, queues_truncated = _named(blast_radius.impacted_queues)

    # Key paths come from the traversal's own edges — the same walk the
    # counts above are derived from — and only edges that actually leave
    # the seed node, so a "key path" is always a real step of the blast
    # radius rather than an unrelated relationship of a touched repository.
    edges = [
        edge
        for edge in blast_radius.subgraph.edges
        if edge.source_id in seed_node_ids or edge.target_id in seed_node_ids
    ] or list(blast_radius.subgraph.edges)
    key_paths = [
        f"{pretty_entity_name(edge.source_id, names)} → "
        f"{pretty_entity_name(edge.target_id, names)} ({edge.type})"
        for edge in edges[:_MAX_KEY_PATHS]
    ]

    facts = ImpactFacts(
        seed_repository_name=repository.name,
        affected_repositories=repositories,
        affected_apis=apis,
        affected_databases=databases,
        affected_queues=queues,
        key_paths=key_paths,
        relationship_count=len(blast_radius.subgraph.edges),
        truncated=(
            repos_truncated
            or apis_truncated
            or dbs_truncated
            or queues_truncated
            or len(edges) > _MAX_KEY_PATHS
        ),
        affected_repositories_truncated=repos_truncated,
        severity="",  # replaced below — severity is a function of the final counts
    )
    return dataclasses.replace(facts, severity=_severity(facts.downstream_total))


async def display_names(
    db: AsyncSession, blast_radius: BlastRadius, user_id: uuid.UUID
) -> dict[str, str]:
    """Node id -> human-readable name, for every node the blast radius
    touched. Prefers the subgraph's own `properties.name` (set at index
    time — see `app.indexer.graph.builder.build_graph`'s Repository node);
    falls back to a `Repository` row lookup for a bare `<uuid>:repository`
    id whose graph node predates that property, so a raw id never reaches
    the answer's affected-systems list when a real name is one query
    away.

    M-3: the fallback lookup is scoped to `user_id`, the caller's own id
    — not because the graph traversal is expected to ever hand this
    function a foreign repository id (cross-repo edges are created scoped
    per-user, so it shouldn't), but because this function has no way of
    knowing that invariant holds elsewhere. Without its own filter, a
    future bug upstream that let a foreign id reach here would have this
    function silently resolve and disclose that repository's name with no
    error. Scoping here means that invariant is enforced twice, not
    once."""
    names = {
        n.id: str(n.properties["name"])
        for n in blast_radius.subgraph.nodes
        if n.properties.get("name")
    }

    unresolved_repo_ids: list[uuid.UUID] = []
    for entity_id in (*blast_radius.impacted_repositories, blast_radius.seed.node_id):
        if entity_id in names:
            continue
        match = _REPO_NODE_ID_RE.match(entity_id)
        if match:
            unresolved_repo_ids.append(uuid.UUID(match.group(1)))
    if unresolved_repo_ids:
        result = await db.execute(
            select(Repository).where(
                Repository.id.in_(unresolved_repo_ids),
                Repository.user_id == user_id,
            )
        )
        for repo in result.scalars():
            names[f"{repo.id}:repository"] = repo.full_name

    return names


def pretty_entity_name(entity_id: str, names: dict[str, str]) -> str:
    if entity_id in names:
        return names[entity_id]
    # No resolved name (a function/API/queue node, or a repository this
    # user doesn't own) — the trailing dotted segment of the node id is
    # still more readable than the full namespaced id.
    return entity_id.rsplit(":", 1)[-1]


async def ground_impact(db: AsyncSession, question: str, repository: Repository) -> AskResponse:
    blast_radius = await compute_blast_radius(
        db,
        Neo4jGraphRepository(get_driver()),
        EntityReference(repository_id=str(repository.id), node_id=f"{repository.id}:repository"),
        max_hops=_MAX_HOPS,
    )
    names = await display_names(db, blast_radius, repository.user_id)

    # One canonical result; every field below is a read of it. Nothing in
    # this function recomputes an impact number from the raw blast radius.
    facts = build_impact_facts(blast_radius, repository, names)

    downstream_count = len(facts.affected_repositories)
    # M-2: when the REPOSITORY count specifically was capped,
    # `downstream_count` is a bounded sample, not the true count — stating
    # it bare ("12 downstream repositories may be affected") reads as
    # exact when the real number could be far higher. `count_phrase` puts
    # that caveat in the ONE sentence that's always visible (`answer`/
    # `summary`), not only in `why`, which the UI renders behind a
    # collapsed "Why" disclosure a reader may never open.
    #
    # Deliberately keyed on `affected_repositories_truncated`, not the
    # whole-result `facts.truncated` — that flag can be true because a
    # DIFFERENT category (databases/APIs/queues/key_paths) overflowed
    # while the repository count itself is exact, and qualifying an exact
    # number with "more than" would be its own inaccuracy in the other
    # direction.
    count_phrase = (
        f"more than {downstream_count}"
        if facts.affected_repositories_truncated
        else str(downstream_count)
    )
    summary = (
        f"{count_phrase} downstream repositor{'y' if downstream_count == 1 else 'ies'} "
        "may be affected."
        if downstream_count
        else "No downstream repositories are affected."
    )

    why = (
        f"{facts.seed_repository_name} reaches {count_phrase} other tracked "
        f"repositor{'y' if downstream_count == 1 else 'ies'} through "
        f"{facts.relationship_count} relationship(s) within {_MAX_HOPS} hops."
    )
    if facts.key_paths:
        why += " Key paths: " + "; ".join(facts.key_paths) + "."
    if facts.truncated:
        why += (
            " This is a partial view — the blast radius was larger than the reporting "
            "limit, so it is not an exhaustive impact analysis."
        )

    evidence = [
        AskEvidenceItem(
            source="Dependency Graph",
            label=(
                f"{facts.relationship_count} relationship(s) traced across a "
                f"{_MAX_HOPS}-hop blast radius"
                + (" (partial — truncated)" if facts.truncated else "")
            ),
            provenance="derived",
        ),
        AskEvidenceItem(source="GitHub", label=repository.full_name, provenance="fact"),
    ]

    impact = AskImpact(
        severity=facts.severity,
        summary=summary,
        affected_repositories=facts.affected_repositories,
        affected_apis=facts.affected_apis,
        affected_databases=facts.affected_databases,
        affected_queues=facts.affected_queues,
        truncated=facts.truncated,
    )

    return AskResponse(
        status="answered",
        question=question,
        intent="impact",
        resolved_repository_id=str(repository.id),
        resolved_repository_name=repository.name,
        answer=f"Impact assessment — {facts.severity.title()}. {summary}",
        why=why,
        evidence=evidence,
        impact=impact,
        actions=[
            AskAction(
                label="Explore impact",
                kind="explore_impact",
                href=f"/workspace/impact-analysis?repository={repository.id}",
            ),
            AskAction(
                label="View repository",
                kind="view_repository",
                href=f"/repositories/{repository.id}",
            ),
            AskAction(
                label="View dependency graph", kind="view_dependency_graph", href="/architecture"
            ),
        ],
    )


def _dependency_evidence(result: QueryResult) -> list[AskEvidenceItem]:
    items = [
        AskEvidenceItem(
            source="Dependency Graph",
            label=f"{result.total_matched} relationship(s) in Engineering Memory",
            provenance="derived",
        )
    ]
    for insight in result.relationships[:3]:
        items.append(
            AskEvidenceItem(
                source="Dependency Graph",
                label=(
                    f"{insight.source_entity} → {insight.target_entity} "
                    f"({insight.relationship_type}, {insight.confidence_state})"
                ),
                provenance="fact" if insight.confidence_state == "verified" else "derived",
            )
        )
    return items


async def ground_dependency(db: AsyncSession, question: str, repository: Repository) -> AskResponse:
    result = await dependency_query_service.search(db, [repository.id])
    total = result.total_matched
    answer = (
        f"{total} relationship(s) found for {repository.name}."
        if total
        else f"No tracked relationships found yet for {repository.name}."
    )
    why = (
        f"Engineering Memory holds {total} relationship(s) for {repository.name}, each with an "
        "independently tracked confidence state (verified, highly likely, likely, or candidate)."
    )

    return AskResponse(
        status="answered",
        question=question,
        intent="dependency",
        resolved_repository_id=str(repository.id),
        resolved_repository_name=repository.name,
        answer=answer,
        why=why,
        evidence=_dependency_evidence(result),
        impact=None,
        actions=[
            AskAction(
                label="View dependency graph",
                kind="view_dependency_graph",
                href=f"/workspace/dependency-query?repository={repository.id}",
            ),
            AskAction(
                label="View repository",
                kind="view_repository",
                href=f"/repositories/{repository.id}",
            ),
        ],
    )


async def ground(db: AsyncSession, user_id: uuid.UUID, question: str) -> AskResponse:
    """The full deterministic attempt: classify, resolve a repository, and
    ground with the matching service — or `route_to_investigation` if
    nothing applies. This is exactly what `POST /ask` does; factored out
    so `ConversationService` can seed a new investigation topic with it
    too."""
    intent = classify(question)
    if intent == "general":
        # No deterministic path covers this question shape at all — there
        # is nothing to resolve a repository for.
        return AskResponse(
            status="route_to_investigation",
            question=question,
            intent="general",
            resolution_reason="no_deterministic_path_for_question",
        )

    resolution = await resolve_repository_detailed(db, user_id, question)

    if resolution.status == "resolved" and resolution.repository is not None:
        if intent == "impact":
            return await ground_impact(db, question, resolution.repository)
        return await ground_dependency(db, question, resolution.repository)

    if resolution.status == "ambiguous":
        # Understood the question, could not identify the system. `intent`
        # keeps its real value (M-1), and no answer/evidence/impact is
        # attached — an ungrounded turn must never carry evidence badges.
        return AskResponse(
            status="needs_clarification",
            question=question,
            intent=intent,
            resolution_reason=resolution.reason,
            candidates=[
                AskRepositoryCandidate(**dataclasses.asdict(c)) for c in resolution.candidates
            ],
        )

    return AskResponse(
        status="route_to_investigation",
        question=question,
        intent=intent,
        resolution_reason=resolution.reason,
        candidates=[AskRepositoryCandidate(**dataclasses.asdict(c)) for c in resolution.candidates],
    )
