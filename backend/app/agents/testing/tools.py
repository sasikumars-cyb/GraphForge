"""Test Planning Agent tools — own minimal tool-calling code.

NOT shared with Planning or Development agents (per agent isolation pattern).
These tools gather architecture context to inform test strategy decisions:
repository discovery, component discovery, and dependency traversal to
identify integration points and regression scope.

Each tool wraps existing graph-read methods and returns an Observation.
No write operations — agents never write to the graph directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.text_relevance import relevance, term_weights
from app.graph.health import GraphHealthService, GraphHealthStatus
from app.graph.interfaces import IGraphRepository
from app.graph.test_case_repository import ITestCaseGraphRepository
from app.models.repository import Repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestingObservation:
    """What a testing tool call returned."""

    tool_name: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    succeeded: bool = True
    error: str = ""


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class TestRepositoryDiscoveryTool:
    """Discover all indexed repositories for test scope determination.

    "Indexed" here means `GraphHealthService` reports the repository as
    HEALTHY — see `app.graph.health` for why that isn't the same thing as
    a Postgres `IndexingJob` saying "completed".

    Evidence kind: tool_call
    """

    name = "discover_test_repositories"

    def __init__(self, db: AsyncSession, graph_repository: IGraphRepository) -> None:
        self._db = db
        self._graph_repository = graph_repository
        self._health_service = GraphHealthService(db, graph_repository)

    async def execute(self) -> TestingObservation:
        try:
            result = await self._db.execute(select(Repository))
            all_repos: list[Repository] = list(result.scalars().all())

            health_by_repo_id = {
                health.repository_id: health
                for health in await self._health_service.for_repositories(all_repos)
            }
            # `full_name` ("owner/name") is the canonical repository
            # identity — see `planning/tools.py`'s `GetIndexedRepositoriesTool`
            # for the full rationale. `name`/`owner` kept, unchanged, for
            # existing bare-name consumers.
            indexed: list[dict[str, str]] = [
                {
                    "id": str(repo.id),
                    "name": repo.name,
                    "owner": repo.owner,
                    "full_name": repo.full_name,
                }
                for repo in all_repos
                if health_by_repo_id[repo.id].status == GraphHealthStatus.HEALTHY
            ]

            summary = (
                f"Discovered {len(indexed)} indexed repositor{'y' if len(indexed) == 1 else 'ies'} "
                f"out of {len(all_repos)} tracked."
            )

            return TestingObservation(
                tool_name=self.name,
                summary=summary,
                data={"indexed_repositories": indexed, "total_tracked": len(all_repos)},
            )
        except Exception as exc:
            logger.warning("testing_tool_discover_repos_failed error=%s", str(exc))
            return TestingObservation(
                tool_name=self.name,
                summary=f"Failed to discover repositories: {exc}",
                data={},
                succeeded=False,
                error=str(exc),
            )


class TestComponentDiscoveryTool:
    """Discover components and Kafka topics for regression scope.

    Evidence kind: graph_traversal
    """

    name = "discover_test_components"

    def __init__(self, graph_repository: IGraphRepository) -> None:
        self._graph_repository = graph_repository

    async def execute(self, repositories: list[dict[str, str]]) -> TestingObservation:
        if not repositories:
            return TestingObservation(
                tool_name=self.name,
                summary="No indexed repositories — cannot discover components for testing.",
                data={"components": [], "kafka_topics": [], "repository_count": 0},
            )

        all_components: list[dict[str, Any]] = []
        all_topics: list[dict[str, Any]] = []
        errors: list[str] = []

        for repo in repositories:
            repo_id = repo["id"]
            repo_name = repo["name"]
            try:
                components = await self._graph_repository.get_nodes_by_label(repo_id, "Component")
                for node in components:
                    all_components.append(
                        {
                            "id": node.id,
                            "name": node.properties.get("name", node.id),
                            "type": next(
                                (la for la in node.labels if la != "Component"),
                                "Component",
                            ),
                            "repository": repo_name,
                            "file_path": node.properties.get("file_path", ""),
                        }
                    )

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
                    "testing_tool_discover_components_failed repo=%s error=%s",
                    repo_name,
                    str(exc),
                )

        # Kafka detection only exists for Java/Spring Boot (see
        # indexer/extractors/kafka.py) — a Python repo always yields zero,
        # not because it has no messaging, but because nothing looks for it
        # there. Omit the clause rather than report a misleading "0".
        kafka_clause = f" and {len(all_topics)} Kafka topic(s)" if all_topics else ""
        summary = (
            f"Discovered {len(all_components)} component(s){kafka_clause} across "
            f"{len(repositories)} repositor{'y' if len(repositories) == 1 else 'ies'}."
        )
        if errors:
            summary += f" {len(errors)} repositor{'y' if len(errors) == 1 else 'ies'} failed."

        all_failed = len(errors) == len(repositories)
        return TestingObservation(
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


class TestDependencyTraversalTool:
    """Traverse edges to identify integration points and cross-repo coupling
    that require integration testing.

    Evidence kind: graph_traversal
    """

    name = "traverse_test_dependencies"

    def __init__(self, graph_repository: IGraphRepository) -> None:
        self._graph_repository = graph_repository

    async def execute(self, repositories: list[dict[str, str]]) -> TestingObservation:
        if not repositories:
            return TestingObservation(
                tool_name=self.name,
                summary="No indexed repositories — cannot traverse dependencies for testing.",
                data={"edges": [], "cross_repo_edges": [], "integration_points": []},
            )

        all_edges: list[dict[str, str]] = []
        cross_repo_edges: list[dict[str, str]] = []
        errors: list[str] = []

        for repo in repositories:
            repo_id = repo["id"]
            repo_name = repo["name"]
            try:
                graph_payload = await self._graph_repository.get_full_graph(repo_id)
                for edge in graph_payload.edges:
                    all_edges.append(
                        {
                            "source": edge.source_id,
                            "target": edge.target_id,
                            "type": edge.type,
                            "repository": repo_name,
                        }
                    )
            except Exception as exc:
                errors.append(f"{repo_name}: {exc}")
                logger.warning(
                    "testing_tool_traverse_deps_failed repo=%s error=%s",
                    repo_name,
                    str(exc),
                )

        # Identify cross-repo coupling via shared topics
        topic_producers: dict[str, list[str]] = {}
        topic_consumers: dict[str, list[str]] = {}
        for edge_dict in all_edges:
            if edge_dict["type"] == "PRODUCES_TO":
                topic_producers.setdefault(edge_dict["target"], []).append(edge_dict["repository"])
            elif edge_dict["type"] == "CONSUMES_FROM":
                topic_consumers.setdefault(edge_dict["source"], []).append(edge_dict["repository"])

        for topic_id, producers in topic_producers.items():
            consumers = topic_consumers.get(topic_id, [])
            for prod_repo in producers:
                for cons_repo in consumers:
                    if prod_repo != cons_repo:
                        cross_repo_edges.append(
                            {
                                "topic": topic_id,
                                "producer_repo": prod_repo,
                                "consumer_repo": cons_repo,
                                "type": "CROSS_REPO_KAFKA",
                            }
                        )

        # Identify key integration points (CALLS, PRODUCES_TO, CONSUMES_FROM)
        integration_points = [
            e for e in all_edges if e["type"] in ("CALLS", "PRODUCES_TO", "CONSUMES_FROM")
        ]

        summary = (
            f"Traversed {len(all_edges)} edge(s) across "
            f"{len(repositories)} repositor{'y' if len(repositories) == 1 else 'ies'}. "
            f"Found {len(integration_points)} integration point(s) and "
            f"{len(cross_repo_edges)} cross-repository coupling(s)."
        )
        if errors:
            summary += f" {len(errors)} repositor{'y' if len(errors) == 1 else 'ies'} failed."

        all_failed = len(errors) == len(repositories) and len(repositories) > 0
        return TestingObservation(
            tool_name=self.name,
            summary=summary,
            data={
                "edges": all_edges[:100],
                "cross_repo_edges": cross_repo_edges,
                "integration_points": integration_points[:50],
                "total_edges": len(all_edges),
            },
            succeeded=not all_failed,
            error="; ".join(errors) if all_failed else "",
        )


class TestRailCoverageTool:
    """Finds existing TestRail test cases relevant to this task, by
    token-overlap relevance against the task description (and any
    already-discovered component names) — not graph traversal to code,
    since TestRail cases carry no Component/Repository edge in this pass
    (see app.indexer.graph.testrail_builder's own docstring for why).

    This is the concrete "impact on existing test coverage" signal the
    Testing agent's plan cites: which of the change's regression/
    integration tests already have TestRail coverage, and which are
    net-new gaps — see format_graph_context's "Existing TestRail
    coverage" section below and testing.md's instruction to cross-
    reference against it.

    Evidence kind: graph_traversal — it *is* a live Neo4j read, even
    without a code-graph edge to traverse.
    """

    name = "find_testrail_coverage"

    def __init__(self, test_case_graph_repository: ITestCaseGraphRepository) -> None:
        self._repo = test_case_graph_repository

    async def execute(self, terms: list[str], limit: int = 15) -> TestingObservation:
        try:
            # Bounded fetch, then rank in Python (same pattern
            # rank_repositories uses for components) - simpler and more
            # transparent than building a full-text Cypher index for what
            # is, per project, at most a few thousand cases.
            cases = await self._repo.get_all_test_cases(limit=2000)
        except Exception as exc:
            logger.warning("testing_tool_testrail_coverage_failed error=%s", str(exc))
            return TestingObservation(
                tool_name=self.name,
                summary=f"Failed to read TestRail coverage: {exc}",
                data={"cases": []},
                succeeded=False,
                error=str(exc),
            )

        if not cases:
            return TestingObservation(
                tool_name=self.name,
                summary="No TestRail test cases have been synced yet.",
                data={"cases": [], "total_synced": 0},
            )

        titles = [str(node.properties.get("title", "")) for node in cases]
        weights = term_weights(terms, titles) if terms else None
        scored = sorted(
            (
                (relevance(title, terms, weights) if terms else 0.0, node)
                for title, node in zip(titles, cases, strict=True)
            ),
            key=lambda pair: -pair[0],
        )
        # With no terms to rank against, there's nothing principled to
        # prefer - surface none rather than an arbitrary first-N slice
        # the LLM would otherwise mistake for a ranked shortlist.
        top = [node for score, node in scored[:limit] if score > 0] if terms else []

        summary = (
            f"Found {len(top)} relevant TestRail case(s) out of {len(cases)} synced."
            if top
            else f"None of the {len(cases)} synced TestRail case(s) matched this task's terms."
        )

        return TestingObservation(
            tool_name=self.name,
            summary=summary,
            data={
                "cases": [
                    {
                        "title": node.properties.get("title", ""),
                        "refs": node.properties.get("refs", ""),
                    }
                    for node in top
                ],
                "total_synced": len(cases),
            },
        )


# ---------------------------------------------------------------------------
# Graph context formatter
# ---------------------------------------------------------------------------


def format_graph_context(
    repos_obs: TestingObservation,
    components_obs: TestingObservation,
    deps_obs: TestingObservation,
    testrail_obs: TestingObservation | None = None,
) -> str:
    """Format tool observations into LLM-readable context for test planning."""
    parts: list[str] = []

    indexed_repos = repos_obs.data.get("indexed_repositories", [])
    if not indexed_repos:
        parts.append("No repositories have been indexed into the Knowledge Graph yet.")
        parts.extend(_testrail_coverage_parts(testrail_obs))
        return "\n\n".join(parts)

    repo_names = [r["name"] for r in indexed_repos]
    parts.append(f"**Indexed repositories**: {', '.join(repo_names)}")

    # Components grouped by repository
    components = components_obs.data.get("components", [])
    if components:
        by_repo: dict[str, list[str]] = {}
        for comp in components[:50]:
            repo = comp["repository"]
            comp_desc = f"{comp['name']} ({comp['type']})"
            if comp.get("file_path"):
                comp_desc += f" [{comp['file_path']}]"
            by_repo.setdefault(repo, []).append(comp_desc)
        comp_lines = []
        for repo, comps in by_repo.items():
            comp_lines.append(f"  {repo}: {', '.join(comps[:15])}")
        parts.append("**Components**:\n" + "\n".join(comp_lines))
    else:
        parts.append("**Components**: none indexed yet")

    # Kafka topics — omitted when empty, not asserted as "none indexed yet":
    # detection only exists for Java/Spring Boot, so an empty list from a
    # Python repository isn't a grounded finding, just an undetected gap.
    topics = components_obs.data.get("kafka_topics", [])
    if topics:
        topic_names = list({t["name"] for t in topics})[:20]
        parts.append(f"**Kafka topics**: {', '.join(topic_names)}")

    # Integration points
    integration_points = deps_obs.data.get("integration_points", [])
    if integration_points:
        int_lines = []
        for e in integration_points[:20]:
            int_lines.append(f"  {e['source']} —[{e['type']}]→ {e['target']} ({e['repository']})")
        parts.append(
            "**Integration points (require integration testing)**:\n" + "\n".join(int_lines)
        )
    else:
        parts.append("**Integration points**: none found")

    # Cross-repo coupling
    cross_repo = deps_obs.data.get("cross_repo_edges", [])
    if cross_repo:
        coupling_lines = []
        for cr in cross_repo[:10]:
            coupling_lines.append(
                f"  {cr['producer_repo']} → [{cr['topic']}] → {cr['consumer_repo']}"
            )
        parts.append(
            "**Cross-repository coupling (high-risk integration tests)**:\n"
            + "\n".join(coupling_lines)
        )

    parts.extend(_testrail_coverage_parts(testrail_obs))

    return "\n\n".join(parts)


def _testrail_coverage_parts(testrail_obs: TestingObservation | None) -> list[str]:
    """Zero, one, or two lines to append for TestRail coverage — a
    separate helper (not inlined) so both the early-return "no repos"
    path and the normal path above render it identically."""
    if testrail_obs is None:
        return []
    cases = testrail_obs.data.get("cases", [])
    if cases:
        case_lines = [
            f"  {c['title']}" + (f" (refs: {c['refs']})" if c.get("refs") else "") for c in cases
        ]
        return [
            "**Existing TestRail coverage relevant to this task** — cross-reference proposed "
            "tests below against these; note explicitly which are already covered vs. net-new:\n"
            + "\n".join(case_lines)
        ]
    if testrail_obs.data.get("total_synced", 0) > 0:
        return ["**Existing TestRail coverage**: none of the synced cases matched this task."]
    # Nothing synced at all - omit the section rather than asserting an
    # absence that was never actually checked.
    return []


# ---------------------------------------------------------------------------
# Evidence builders
# ---------------------------------------------------------------------------


def to_evidence(observation: TestingObservation, kind: str) -> Evidence:
    """Convert a TestingObservation to a contract Evidence entry.

    If the observation failed, evidence kind is forced to "tool_call"
    with a failure-prefixed summary.
    """
    if not observation.succeeded:
        return Evidence(
            kind="tool_call",
            reference=observation.tool_name,
            summary=f"FAILED: {observation.summary}",
        )
    return Evidence(
        kind=kind,
        reference=observation.tool_name,
        summary=observation.summary,
    )
