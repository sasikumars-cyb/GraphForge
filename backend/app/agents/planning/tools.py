"""Planning Agent tools — own minimal tool-calling code.

NOT shared with the Review Agent's ToolRegistry. These tools are specific to
the planning domain: gathering a high-level architecture overview from the
Knowledge Graph to ground an implementation plan.

Each tool wraps one or more existing deterministic graph-read methods and
returns an Observation describing what it found. No write operations here —
agents never write to the graph directly (GraphWriter rule from AGENT_FRAMEWORK.md).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.graph.interfaces import IGraphRepository
from app.models.repository import Repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observation — mirrors the Review Agent's Observation shape but is
# intentionally a separate type (no shared framework between agents).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanningObservation:
    """What a planning tool call returned."""

    tool_name: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    succeeded: bool = True
    error: str = ""


# ---------------------------------------------------------------------------
# Planning tools
# ---------------------------------------------------------------------------


class GetIndexedRepositoriesTool:
    """Fetch the repositories owned by `user_id` that have been successfully
    indexed into the Knowledge Graph.

    Scoping is mandatory, not optional. `repositories` holds one row per
    (user, repo) tracking relationship — see app.models.repository — so two
    users tracking the same GitHub repo each get their own row with their
    own graph. An unscoped read therefore returns *other accounts'*
    repositories, which then reach the LLM prompt, the evidence pool that
    verifies its claims, and the run's visible "Found N indexed
    repositories" summary. That is a cross-tenant read, so this deliberately
    takes `user_id` as a required argument and fails closed rather than
    defaulting to "all" — a missing caller should break loudly in tests, not
    silently widen access in production.

    Evidence kind: tool_call
    Data: list of repository names and their IDs.
    """

    name = "get_indexed_repositories"

    def __init__(
        self,
        db: AsyncSession,
        graph_repository: IGraphRepository,
        user_id: object,
    ) -> None:
        self._db = db
        self._graph_repository = graph_repository
        self._user_id = user_id

    async def execute(self) -> PlanningObservation:
        try:
            result = await self._db.execute(
                select(Repository).where(Repository.user_id == self._user_id)
            )
            all_repos: list[Repository] = list(result.scalars().all())

            indexed: list[dict[str, str]] = []
            for repo in all_repos:
                if await self._graph_repository.has_graph(str(repo.id)):
                    indexed.append({"id": str(repo.id), "name": repo.name, "owner": repo.owner})

            summary = (
                f"Found {len(indexed)} indexed repositor{'y' if len(indexed) == 1 else 'ies'} "
                f"out of {len(all_repos)} tracked."
            )
            if indexed:
                names = ", ".join(r["name"] for r in indexed[:10])
                summary += f" Indexed: {names}."

            logger.debug(
                "planning_tool_get_indexed_repos indexed=%d total=%d",
                len(indexed),
                len(all_repos),
            )

            return PlanningObservation(
                tool_name=self.name,
                summary=summary,
                data={"indexed_repositories": indexed, "total_tracked": len(all_repos)},
            )
        except Exception as exc:
            logger.warning("planning_tool_get_indexed_repos_failed error=%s", str(exc))
            return PlanningObservation(
                tool_name=self.name,
                summary=f"Failed to retrieve repositories: {exc}",
                data={},
                succeeded=False,
                error=str(exc),
            )


class TraverseArchitectureGraphTool:
    """Traverse the Knowledge Graph for a set of repositories: get all
    Components (Controllers, Services, FeignClients) and KafkaTopics.

    This is the graph-traversal step that grounds the planning agent's
    output in real architecture facts rather than LLM hallucinations.

    Evidence kind: graph_traversal
    Data: component list, kafka topic list, per-repository counts.
    """

    name = "traverse_architecture_graph"

    _ARCHITECTURE_LABELS = ("Component", "KafkaTopic")

    def __init__(self, graph_repository: IGraphRepository) -> None:
        self._graph_repository = graph_repository

    async def execute(self, repositories: list[dict[str, str]]) -> PlanningObservation:
        if not repositories:
            return PlanningObservation(
                tool_name=self.name,
                summary="No indexed repositories to traverse.",
                data={"components": [], "kafka_topics": [], "repository_count": 0},
            )

        all_components: list[dict[str, Any]] = []
        all_topics: list[dict[str, Any]] = []
        errors: list[str] = []

        for repo in repositories:
            repo_id = repo["id"]
            repo_name = repo["name"]
            try:
                # Components: Controllers, Services, FeignClients
                components = await self._graph_repository.get_nodes_by_label(repo_id, "Component")
                for node in components:
                    all_components.append(
                        {
                            "id": node.id,
                            "name": node.properties.get("name", node.id),
                            "type": next(
                                (label for label in node.labels if label != "Component"),
                                "Component",
                            ),
                            "repository": repo_name,
                            "file_path": node.properties.get("file_path", ""),
                        }
                    )

                # Kafka topics
                topics = await self._graph_repository.get_nodes_by_label(repo_id, "KafkaTopic")
                for node in topics:
                    all_topics.append(
                        {
                            "id": node.id,
                            "name": node.properties.get("name", node.id),
                            "repository": repo_name,
                        }
                    )

            except Exception as exc:
                errors.append(f"{repo_name}: {exc}")
                logger.warning(
                    "planning_tool_traverse_failed_for_repo repo=%s error=%s",
                    repo_name,
                    str(exc),
                )

        # Kafka topic extraction only exists for Java/Spring Boot
        # (@KafkaListener/KafkaTemplate) — see indexer/extractors/kafka.py.
        # A Python repository always yields zero, not because it has no
        # messaging, but because nothing looks for it there yet. Stating
        # "0 Kafka topics" as a finding would misrepresent an unimplemented
        # detector as a real absence, so the clause only appears when
        # there's something to report.
        found_clause = (
            f"found {len(all_components)} component{'s' if len(all_components) != 1 else ''}"
        )
        if all_topics:
            found_clause += (
                f" and {len(all_topics)} Kafka topic{'s' if len(all_topics) != 1 else ''}"
            )
        repo_word = "y" if len(repositories) == 1 else "ies"
        summary_parts = [
            f"Graph traversal across {len(repositories)} repositor{repo_word}",
            found_clause + ".",
        ]
        if errors:
            summary_parts.append(
                f"{len(errors)} repositor{'y' if len(errors) == 1 else 'ies'} failed."
            )
        summary = " ".join(summary_parts)

        logger.info(
            "planning_tool_traverse_architecture_graph components=%d topics=%d repos=%d",
            len(all_components),
            len(all_topics),
            len(repositories),
        )

        # Succeeded only if at least one repository was traversed without error.
        # All-failures means the graph was unreachable, not that it was empty.
        all_failed = len(errors) == len(repositories)
        return PlanningObservation(
            tool_name=self.name,
            summary=summary,
            data={
                "components": all_components,
                "kafka_topics": all_topics,
                "repository_count": len(repositories),
            },
            succeeded=not all_failed,
            error="; ".join(errors) if all_failed else "",
        )


# ---------------------------------------------------------------------------
# Graph context formatter — turns tool observations into LLM-readable text
# ---------------------------------------------------------------------------


_TOKEN_BOUNDARY_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")


def _tokenize(text: str) -> frozenset[str]:
    """Split an identifier, file path, or free-text string into its
    individual sub-words: `snake_case`, `dotted.paths`, `slash/separated`,
    and `camelCase` are all token breaks — the same tokenization a code
    search engine applies to identifiers.

    This is what a component name and a search term are compared through,
    rather than raw substring containment. Substring containment treats
    "process" as a hit inside "test_already_**process**ed_file" and "page"
    as a hit inside "**page**ination" — real words that happen to contain
    the query as a fragment, not an actual match. Token equality doesn't
    make that mistake, and it composes correctly for multi-word terms too:
    "rate_attribute" tokenizes to {"rate", "attribute"}, which overlaps
    *both* words of `transform_rate_attribute`'s {"transform", "rate",
    "attribute"} — two-token overlap naturally outscores any single
    incidental one-token match without needing separate phrase handling.
    """
    spaced = _CAMEL_BOUNDARY_RE.sub(r"\1_\2", text)
    return frozenset(t for t in _TOKEN_BOUNDARY_RE.split(spaced.lower()) if len(t) >= 3)


def _relevance(text: str, terms: list[str], weights: dict[str, float] | None = None) -> float:
    """How much this text matches the search terms, by token overlap —
    flat count of matching tokens, or (when `weights` is given) the sum of
    each matched token's weight. See `_term_weights` for where the weights
    come from."""
    text_tokens = _tokenize(text)
    term_tokens = _tokenize(" ".join(terms))
    matched = text_tokens & term_tokens
    if weights is None:
        return float(len(matched))
    return sum(weights.get(t, 1.0) for t in matched)


def _match_text(component: dict[str, Any]) -> str:
    """The text a component is ranked on: its name, its kind, and the file
    it lives in.

    The path is not decoration — it is often the only place the domain
    vocabulary appears. A function called `main` or `run` inside
    `notebooks/parse_manifest.py` is *entirely* invisible to name-only
    matching, and a brief about manifests will never surface it. Including
    the path also lets directory conventions carry their real meaning
    (`notebooks/`, `loaders/`, `parsers/`), which is exactly the structure
    an engineer uses to navigate an unfamiliar repository.

    Used for both the ranking score and the document frequencies it is
    normalized against, so the two always agree on what "matched" means.
    """
    return f"{component['name']} {component['type']} {component.get('file_path', '')}"


_TEST_PATH_RE = re.compile(r"(^|/)tests?(/|_)|(^|/)test_|_test\.py$|(^|/)conftest\.py$")

# How much a test component's score is discounted relative to production
# code. Not zero: a test is often the clearest executable description of
# the behaviour a brief is about, and `test_manifest_taskvalues_failure`
# genuinely tells you something. But tests outnumber production code
# heavily in these repositories — 949 of one repo's 1232 components — and
# they inherit the vocabulary of whatever they exercise, so on any
# name-and-path match they arrive as a *block*: every helper, fixture and
# case in one test module scores identically and, being alphabetically
# dense, sweeps a top-N cutoff entirely.
#
# Observed directly: after locator stripping and path matching were
# added, the top 12 components for a manifest-sizing brief were eleven
# members of a single `test_manifest_pipeline.py` plus one config module,
# with the parser, the notebook and the transform class it was actually
# about sitting at #13, #30, #35 and #36. The discount is what lets
# production code win a tie it should never have been losing.
_TEST_RELEVANCE_FACTOR = 0.3


def _is_test_component(component: dict[str, Any]) -> bool:
    """Whether this component is test code, by path or by name."""
    path = str(component.get("file_path", ""))
    name = str(component.get("name", ""))
    return bool(_TEST_PATH_RE.search(path)) or name.split(".")[-1].startswith("test_")


def rank_score(
    component: dict[str, Any], terms: list[str], weights: dict[str, float] | None = None
) -> float:
    """A component's ranking score: token-overlap relevance, discounted if
    it is test code. The single scoring function for both repository
    ranking and per-repository component selection, so the prompt and the
    repository order can never disagree about what scored well."""
    score = _relevance(_match_text(component), terms, weights)
    return score * _TEST_RELEVANCE_FACTOR if _is_test_component(component) else score


def _term_weights(terms: list[str], components: list[dict[str, Any]]) -> dict[str, float]:
    """Inverse-document-frequency weight for each search-term token over the
    *current* component pool: a token that matches almost every component is
    nearly worthless as a discriminator; a token that matches one or two is
    exactly what should decide the ranking.

    This is what lets `relevance_terms` mix two very different kinds of
    vocabulary safely: a fixed capability-keyword list ("loader", "schema",
    "validator" — deliberately generic, so it matches broadly) and free-text
    terms pulled straight out of the brief (see
    `app.agents.planning.classifier.extract_key_terms` — deliberately
    specific, e.g. a field or function name mentioned once). A flat
    "one match = one point" count lets the generic terms drown out the
    specific ones just by matching more often; weighting by rarity fixes
    that without needing to hand-classify which list a term came from, and
    self-calibrates to whatever this repository set's own vocabulary is —
    no fixed thresholds, no stopword tuning per domain.

    The falloff is logarithmic, not linear. A raw `1/(1+df)` is far too
    steep to use as a ranking weight: over a 2039-component pool it scored
    a token matching one component at 0.5 and "manifest" — matching 108,
    and the actual subject of the brief — at 0.0092, a **54x** penalty for
    being the right word. Rarity should break ties between plausible
    terms, not overrule topicality outright. `1/(1+log1p(df))` keeps the
    same ordering while compressing that gap to ~3x, so a genuinely
    on-topic term stays competitive against an incidental one and a
    component matching two domain tokens outranks one matching a single
    rare token.
    """
    term_tokens = _tokenize(" ".join(terms))
    if not term_tokens:
        return {}
    token_sets = [_tokenize(_match_text(c)) for c in components]
    weights: dict[str, float] = {}
    for t in term_tokens:
        df = sum(1 for ts in token_sets if t in ts)
        # A term matching zero components in this pool wasn't measured as
        # rare — it wasn't measured at all, and its IDF math (log1p(0)==0)
        # would otherwise land it at the *maximum* possible weight, 1.0,
        # the same value a component-scoring call could never legitimately
        # produce. That's silently unreachable here (nothing in
        # `components` can match a token with df=0 against `components`
        # itself), but this same weights dict is also handed to
        # `_relevance` for scoring bare *repository names* — text that
        # never went into the df count — where a df=0 term COULD still
        # match and would cash in that undeserved maximum. Pin it to 0.0
        # explicitly rather than counting on `_relevance`'s `.get(t, 1.0)`
        # fallback to ever agree; an *absent* key still resolves to 1.0
        # there, so leaving df=0 terms out of this dict is not the same
        # fix as this.
        weights[t] = 0.0 if df == 0 else 1.0 / (1 + math.log1p(df))
    return weights


def rank_repositories(
    indexed_repos: list[dict[str, str]],
    components: list[dict[str, Any]],
    relevance_terms: list[str] | None = None,
) -> list[tuple[float, str]]:
    """Score and sort indexed repositories by keyword/component overlap with
    the required capabilities. Returns (score, name) pairs, best first —
    the single source of truth for repository ranking, used both to decide
    which repositories reach the LLM prompt (`format_graph_context` below)
    and to pick the top candidate for the entity/tenant mismatch check
    (see app.agents.verification.check_entity_mismatch), so both paths
    agree on which repository was actually selected.
    """
    terms = relevance_terms or []
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for comp in components:
        by_repo.setdefault(comp["repository"], []).append(comp)
    weights = _term_weights(terms, components)

    def score(name: str) -> float:
        if not terms:
            return 0.0
        total = _relevance(name, terms, weights) * 2
        for comp in by_repo.get(name, []):
            total += rank_score(comp, terms, weights)
        return total

    return sorted(
        ((score(r["name"]), r["name"]) for r in indexed_repos),
        key=lambda pair: (-pair[0], pair[1]),
    )


def _component_budget(repo_component_count: int) -> int:
    """How many components from one repository reach the prompt.

    A flat cutoff cannot serve both ends of the range: five components is
    a reasonable summary of a 40-component service and a rounding error
    on a 1232-component monorepo, where the five best-scoring entries are
    routinely all members of a single file. Scaling with repository size
    keeps small repositories concise while giving a large one enough room
    that a relevant *module* is represented by more than its constructor.

    Bounded at both ends: never fewer than 8 (below that a tie inside one
    file consumes the entire budget), never more than 20 (the prompt has
    a token budget too, and past ~20 the tail stops being relevant).
    """
    return max(8, min(20, -(-repo_component_count // 100)))


def stars_for_rank(rank_index: int) -> int:
    """Deterministic 1-5 star rating from a repository's position in
    `rank_repositories`' output (0 = best match). Replaces the LLM's
    free-generated `stars` value in RepositoryUsage with the same ground
    truth already used to decide which repositories reach the prompt."""
    return max(1, 5 - rank_index)


def format_graph_context(
    repos_observation: PlanningObservation,
    traverse_observation: PlanningObservation,
    relevance_terms: list[str] | None = None,
    max_repos: int = 4,
    max_components_per_repo: int | None = None,
) -> str:
    """Format graph tool observations into a compact, LLM-readable context.

    When `relevance_terms` is supplied (the search terms derived from the
    detected capabilities), repositories and components are ranked by how well
    they match what the architecture actually needs, and only the top matches
    are included. This does three things at once:

    - better reuse recommendations, because the model sees the repositories
      that implement the required capabilities rather than an arbitrary slice;
    - less repository bias, because unrelated services never reach the prompt
      and so cannot be pattern-matched into the architecture;
    - fewer tokens, because the inventory shrinks to what is relevant.

    With no terms it degrades to the previous behaviour (first N, unranked),
    so callers that have not done capability analysis still work.
    """
    parts: list[str] = []
    terms = relevance_terms or []

    indexed_repos: list[dict[str, str]] = repos_observation.data.get("indexed_repositories", [])
    if not indexed_repos:
        return "No repositories have been indexed into the Knowledge Graph yet."

    components: list[dict[str, Any]] = traverse_observation.data.get("components", [])

    # Score each repository by how many of its components match the required
    # capabilities. Repository name counts too — a repo called
    # "etl-customer-orders" is evidence in its own right.
    by_repo_all: dict[str, list[dict[str, Any]]] = {}
    for comp in components:
        by_repo_all.setdefault(comp["repository"], []).append(comp)

    weights = _term_weights(terms, components)
    scored = rank_repositories(indexed_repos, components, terms)
    if terms:
        # Drop repositories that match no required capability at all — they
        # are the ones that get pattern-matched into the architecture for no
        # reason. Only when something genuinely scored, so a brief we could
        # not score still sees an inventory rather than an empty list.
        positive = [name for score, name in scored if score > 0]
        shown = (positive or [name for _, name in scored])[:max_repos]
    else:
        shown = [name for _, name in scored]
    omitted = len(scored) - len(shown)

    header = f"**Relevant repositories**: {', '.join(shown)}"
    if omitted > 0:
        # Stated explicitly so the model knows the list is a filtered view and
        # does not assume these are the only repositories that exist.
        plural = "y" if omitted == 1 else "ies"
        header += (
            f" ({omitted} further indexed repositor{plural} less relevant to these capabilities)"
        )
    parts.append(header)

    if components:
        comp_lines = []
        for repo in shown:
            comps = by_repo_all.get(repo, [])
            if terms:
                comps = sorted(
                    comps,
                    key=lambda c: (
                        -rank_score(c, terms, weights),
                        # Every component sharing a file scores identically
                        # once the path is part of the match text, so the
                        # tiebreak decides which of them the model actually
                        # sees. Alphabetical order alone hands those slots
                        # to `__init__` and other private members — real
                        # code, but the least self-describing name in the
                        # file. Prefer names that mean something.
                        c["name"].split(".")[-1].startswith("_"),
                        c["name"],
                    ),
                )
            budget = max_components_per_repo or _component_budget(len(comps))
            listed = [f"{c['name']} ({c['type']})" for c in comps[:budget]]
            if listed:
                comp_lines.append(f"  {repo}: {', '.join(listed)}")
        parts.append(
            "**Components**:\n" + "\n".join(comp_lines)
            if comp_lines
            else "**Components**: none in the relevant repositories"
        )
    else:
        parts.append("**Components**: none indexed yet")

    # Omitted entirely (not "none indexed yet") when empty: Kafka detection
    # only exists for Java/Spring Boot, so an empty list from a Python
    # repository isn't a grounded "no messaging" finding — asserting it to
    # the LLM would present an unimplemented detector as a real absence.
    topics: list[dict[str, Any]] = traverse_observation.data.get("kafka_topics", [])
    if topics:
        topic_names = list({t["name"] for t in topics})[:12]
        parts.append(f"**Kafka topics**: {', '.join(topic_names)}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Evidence builders
# ---------------------------------------------------------------------------


def to_evidence(observation: PlanningObservation, kind: str) -> Evidence:
    """Convert a PlanningObservation to a contract Evidence entry.

    If the observation failed, the evidence kind is forced to
    ``"tool_call"`` with a failure-prefixed summary — never
    ``"graph_traversal"`` or the requested ``kind``, because that would
    imply a successful traversal/call that did not happen (P0-1).

    `status` is set directly from `observation.succeeded` — a UI can key
    off this instead of parsing the `summary` text (see `Evidence.status`'s
    own docstring for why that distinction exists).
    """
    if not observation.succeeded:
        return Evidence(
            kind="tool_call",
            reference=observation.tool_name,
            summary=f"FAILED: {observation.summary}",
            status="failed",
        )
    return Evidence(
        kind=kind,
        reference=observation.tool_name,
        summary=observation.summary,
        status="success",
    )
