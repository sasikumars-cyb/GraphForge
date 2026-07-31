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
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.text_relevance import relevance, term_weights
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

    async def execute(
        self,
        repositories: list[dict[str, str]],
        *,
        repository_filter: list[str] | None = None,
    ) -> PlanningObservation:
        """Traverse `repositories`, or only the subset named in
        `repository_filter` when one is given.

        `repository_filter` is what stops a "scope" investigation action
        (the owning repository is already known — see
        `GraphInvestigator.propose`'s `scope_architecture` branch) from
        still fetching every Component node of every OTHER indexed
        repository too, which it did unconditionally before this
        parameter existed: `GraphInvestigator.run()` folded the known
        repository into `relevance_terms` for scoring, but never actually
        restricted which repositories got traversed, so a user with
        hundreds of indexed repositories paid a full-component fetch for
        every one of them on every single reasoning cycle, even after the
        owning repository was already settled. A genuine "survey" (no
        repository known yet — `repository_filter=None`) still traverses
        everything, which is the legitimate, unavoidable case: nothing
        can be ranked without first seeing what exists.
        """
        if repository_filter is not None:
            wanted = {name.lower() for name in repository_filter}
            repositories = [r for r in repositories if r["name"].lower() in wanted]

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
    """Whether this component is test code.

    Prefers the persisted `is_test` property computed once at index time
    (see `app.indexer.classification`) — the same ground truth every
    other consumer (Development/Testing/Documentation Planning's own
    independent grounding, the UI) now agrees on — falling back to this
    same regex-based recomputation only for a graph indexed before that
    property existed, so this fix takes effect immediately for anything
    indexed going forward without requiring every existing repository to
    be re-indexed first.
    """
    persisted = component.get("is_test")
    if persisted is not None:
        return bool(persisted)
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
    score = relevance(_match_text(component), terms, weights)
    return score * _TEST_RELEVANCE_FACTOR if _is_test_component(component) else score


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
    weights = term_weights(terms, [_match_text(c) for c in components])

    def score(name: str) -> float:
        if not terms:
            return 0.0
        total = relevance(name, terms, weights) * 2
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

    weights = term_weights(terms, [_match_text(c) for c in components])
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
            # Production components always sort ahead of test components,
            # unconditionally — not just discounted by `rank_score` when
            # `terms` produces a nonzero match. `rank_score` multiplies a
            # test component's *relevance* score by 0.3, which is a no-op
            # when that score is already 0 (no term overlap at all): 0 *
            # 0.3 == 0, identical to a production component that also
            # scored 0, so the two compared equal and fell back to
            # traversal order — arbitrary with respect to test-vs-
            # production. Combined with `_component_budget` capping a
            # large repository to as few as 8 entries, that let a
            # repository's test classes fill the entire budget while its
            # real production classes never reached the prompt at all.
            # This is exactly what happened to a real run: etl-core's
            # `SCDType2Merger`/`ExactDeduplicator` never appeared in the
            # component list the LLM saw, only their test classes did, and
            # the LLM had no way to know the real ones existed. Sorting on
            # `_is_test_component` first, before the score, makes that
            # structurally impossible: every production component in a
            # repository is listed before any test component from the same
            # repository is considered, regardless of how term-matching
            # scores either of them.
            comps = sorted(
                comps,
                key=lambda c: (
                    _is_test_component(c),
                    -rank_score(c, terms, weights) if terms else 0.0,
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
