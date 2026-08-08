"""Unit tests for the Planning Agent (PW-4).

Covers:
- Happy path: produces AgentOutput with graph_traversal + tool_call Evidence
- No-indexed-repos path: still produces Evidence (empty results are still evidence)
- Tool failure path: agent continues, evidence records the failure
- LLM failure path: raises (per AGENT_FRAMEWORK.md error policy)
- Output schema: PlanningResult fields populate correctly

All graph and LLM calls are mocked — no real Neo4j or OpenAI needed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.planning.agent import (
    PlanningAgent,
    PlanningLLMError,
    _render_prompt,
    _reuse_percent_mismatch,
    _selected_repo_names,
)
from app.agents.planning.classifier import analyse, detect_task_mode, extract_key_terms
from app.agents.planning.schemas import RepositoryUsage
from app.agents.planning.tools import (
    GetIndexedRepositoriesTool,
    PlanningObservation,
    TraverseArchitectureGraphTool,
    format_graph_context,
    rank_repositories,
    to_evidence,
)
from app.agents.text_relevance import relevance, term_weights
from app.graph.models import GraphNode

# ---------------------------------------------------------------------------
# Tool unit tests (no I/O)
# ---------------------------------------------------------------------------


def test_planning_observation_fields() -> None:
    obs = PlanningObservation(
        tool_name="test_tool",
        summary="Found 5 components.",
        data={"count": 5},
    )
    assert obs.succeeded is True
    assert obs.error == ""


def test_to_evidence_graph_traversal() -> None:
    obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Traversed 2 repos, 10 components.",
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert ev.reference == "traverse_architecture_graph"
    assert "10 components" in ev.summary


def test_to_evidence_tool_call() -> None:
    obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="Found 3 indexed repositories.",
    )
    ev = to_evidence(obs, "tool_call")
    assert ev.kind == "tool_call"


def test_to_evidence_failed_observation_never_reports_graph_traversal() -> None:
    """P0-1: A failed tool must never produce graph_traversal evidence."""
    obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Connection refused",
        succeeded=False,
        error="Connection refused",
    )
    ev = to_evidence(obs, "graph_traversal")
    # Must NOT be graph_traversal — the traversal didn't actually happen
    assert ev.kind == "tool_call"
    assert "FAILED" in ev.summary


def test_to_evidence_failed_observation_summary_includes_failure() -> None:
    """P0-1: Failed evidence must clearly indicate failure in summary."""
    obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="DB unavailable",
        succeeded=False,
        error="DB unavailable",
    )
    ev = to_evidence(obs, "tool_call")
    assert "FAILED" in ev.summary


def test_to_evidence_succeeded_observation_preserves_kind() -> None:
    """Successful observations get their requested kind unchanged."""
    obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Found 5 components.",
        succeeded=True,
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert "FAILED" not in ev.summary


def test_format_graph_context_no_repos() -> None:
    repos_obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="No repos.",
        data={"indexed_repositories": [], "total_tracked": 0},
    )
    traverse_obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="No repos to traverse.",
        data={"components": [], "kafka_topics": [], "repository_count": 0},
    )
    ctx = format_graph_context(repos_obs, traverse_obs)
    assert "No repositories" in ctx


def test_format_graph_context_with_data() -> None:
    repos_obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="2 repos.",
        data={
            "indexed_repositories": [
                {"id": "r1", "name": "order-service", "owner": "acme"},
                {"id": "r2", "name": "payment-service", "owner": "acme"},
            ],
            "total_tracked": 2,
        },
    )
    traverse_obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Found 3 components.",
        data={
            "components": [
                {
                    "id": "c1",
                    "name": "OrderController",
                    "type": "Controller",
                    "repository": "order-service",
                    "file_path": "",
                },
                {
                    "id": "c2",
                    "name": "PaymentService",
                    "type": "Service",
                    "repository": "payment-service",
                    "file_path": "",
                },
            ],
            "kafka_topics": [
                {"id": "t1", "name": "order.created", "repository": "order-service"},
            ],
            "repository_count": 2,
        },
    )
    ctx = format_graph_context(repos_obs, traverse_obs)
    assert "order-service" in ctx
    assert "order.created" in ctx


def test_format_graph_context_ranks_specific_component_over_generic_noise() -> None:
    """A brief that names a specific field/function should surface the
    component that actually contains it, not whichever component happens
    to share a few common English words with the brief.

    Regression test for a real failure: the Planning Agent's context for a
    ticket about `realservicepointno` handling in `rate_attribute` exports
    listed unrelated config/loader components, because relevance terms were
    scored by "one point per substring match" with no regard for how common
    that substring was — so a component sharing several generic, ticket-
    boilerplate words ("create", "input", "value", "file", "record")
    outscored the one component whose name is actually a near-match for
    what the brief names. Exercises the real production path — capability
    detection + free-text term extraction (`classifier.analyse`) feeding
    `format_graph_context`, exactly as `PlanningAgent` wires them — not the
    scoring internals in isolation, since the fix for this spans both.
    """
    task_description = (
        "Jira: PROT-5746 — Rate_attribute record is not getting generated "
        "when realservicepointno is 'n/a'. Steps: create input data with "
        "realservicepointno value 'n/a'. Verify the rate_attribute file — "
        "records having value 'n/a' are not exported."
    )

    repos_obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="1 repo.",
        data={
            "indexed_repositories": [{"id": "r1", "name": "soco-ingest", "owner": "acme"}],
            "total_tracked": 1,
        },
    )
    components = [
        # The actual match: shares the brief's specific vocabulary.
        {
            "id": "c1",
            "name": "transform_rate_attribute",
            "type": "Function",
            "repository": "soco-ingest",
            "file_path": "src/transforms/export/rate_attribute.py",
        },
        # Noise: shares only generic, ticket-boilerplate words with the
        # brief ("create", "input"), and under naive substring matching
        # would also pick up an incidental hit ("value" inside "values").
        {
            "id": "c2",
            "name": "_create_input_schema",
            "type": "Function",
            "repository": "soco-ingest",
            "file_path": "src/config/pipeline_config.py",
        },
        # More noise: incidental substring hits under naive matching
        # ("process" inside "processed", "file" as a real but generic word).
        {
            "id": "c3",
            "name": "test_already_processed_file_exits_early",
            "type": "Function",
            "repository": "soco-ingest",
            "file_path": "tests/test_loader.py",
        },
    ]
    traverse_obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Found 3 components.",
        data={"components": components, "kafka_topics": [], "repository_count": 1},
    )

    profile = analyse(task_description)
    ctx = format_graph_context(repos_obs, traverse_obs, relevance_terms=profile.search_terms)

    rate_attribute_pos = ctx.find("transform_rate_attribute")
    noise_pos = ctx.find("_create_input_schema")
    assert rate_attribute_pos != -1
    assert rate_attribute_pos < noise_pos, (
        "the component matching the brief's own specific vocabulary should "
        "rank ahead of components that only share generic words"
    )


def test_key_terms_ignore_urls_and_absolute_paths_but_keep_repo_paths() -> None:
    """Where a brief *points* must never outrank what it is *about*.

    Regression test for a real failure (PROT-5723). The brief was one
    sentence of subject matter wrapped around a Jira URL and an absolute
    checkout path. Those two locators supplied 16 of the 25 extracted
    terms, pushing the ticket's actual subject ("manifest") to position
    23, and — because each locator token matched only one or two
    components — `term_weights` then scored them as the *most*
    discriminating terms in the run. The resulting top-ranked components
    were a Jira-comment helper and three path utilities.

    A repository-relative path is the opposite case and must survive: a
    brief that names the file is handing over the best term in it.
    """
    terms = extract_key_terms(
        "Prepare implementation plan for https://acme.atlassian.net/browse/PROT-5723\n"
        'The repo is already in my local "/home/adevuser/git_repositories/soco-ingest"\n'
        "SOCO C2M RCS - Pipeline change to address bigger manifest"
    )

    for locator_token in ("https", "atlassian", "browse", "jira", "adevuser", "git_repositories"):
        assert locator_token not in terms, f"{locator_token!r} is a locator, not subject matter"
    assert "manifest" in terms
    assert "pipeline" in terms

    relative = extract_key_terms("The failure is in soco_ingest/src/parsers/manifest_parser.py")
    assert "manifest_parser" in relative, "a repo-relative path is signal, not noise"


def test_ranking_prefers_production_code_over_a_matching_test_module() -> None:
    """One test module should not consume the whole prompt budget.

    Tests inherit the vocabulary of whatever they exercise, and every
    member of a test file scores identically once the file path is part
    of the match text — so on any topical match they arrive as a block.
    Observed directly on PROT-5723: eleven members of a single
    `test_manifest_pipeline.py` took the top slots while the parser and
    notebook the brief was actually about sat outside the cutoff.
    """
    repos_obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="1 repo.",
        data={
            "indexed_repositories": [{"id": "r1", "name": "soco-ingest", "owner": "acme"}],
            "total_tracked": 1,
        },
    )
    components = [
        {
            "id": "c1",
            "name": "TransformManifestParser",
            "type": "Class",
            "repository": "soco-ingest",
            "file_path": "soco_ingest/src/parsers/manifest_parser.py",
        },
    ] + [
        {
            "id": f"t{i}",
            "name": name,
            "type": "Function",
            "repository": "soco-ingest",
            "file_path": "tests/integration/test_manifest_pipeline.py",
        }
        for i, name in enumerate(
            [
                "FakeDbutils",
                "FakeTaskValues",
                "test_manifest_invalid_date_token",
                "test_manifest_missing_metadata",
                "test_manifest_pipeline_end_to_end",
                "test_manifest_taskvalues_failure",
                "test_manifest_unknown_filetype",
            ]
        )
    ]
    traverse_obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary=f"Found {len(components)} components.",
        data={"components": components, "kafka_topics": [], "repository_count": 1},
    )

    profile = analyse("SOCO C2M RCS - Pipeline change to address bigger manifest")
    ctx = format_graph_context(repos_obs, traverse_obs, relevance_terms=profile.search_terms)

    parser_pos = ctx.find("TransformManifestParser")
    assert parser_pos != -1, "the production class must reach the prompt at all"
    first_test_pos = min(
        pos for pos in (ctx.find("test_manifest_"), ctx.find("FakeDbutils")) if pos != -1
    )
    assert (
        parser_pos < first_test_pos
    ), "production code should outrank a test module that merely shares its vocabulary"


def test_ranking_uses_file_path_so_generic_names_are_reachable() -> None:
    """A function called `main` in `notebooks/parse_manifest.py` is
    invisible to name-only matching, yet it is exactly what a brief about
    manifests needs. The path is often the only place the domain
    vocabulary appears."""
    repos_obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="1 repo.",
        data={
            "indexed_repositories": [{"id": "r1", "name": "soco-ingest", "owner": "acme"}],
            "total_tracked": 1,
        },
    )
    components = [
        {
            "id": "c1",
            "name": "declare_widgets",
            "type": "Function",
            "repository": "soco-ingest",
            "file_path": "soco_ingest/notebooks/parse_manifest.py",
        },
        {
            "id": "c2",
            "name": "helper",
            "type": "Function",
            "repository": "soco-ingest",
            "file_path": "soco_ingest/src/util/strings.py",
        },
    ]
    traverse_obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Found 2 components.",
        data={"components": components, "kafka_topics": [], "repository_count": 1},
    )

    profile = analyse("Pipeline change to address bigger manifest")
    ctx = format_graph_context(repos_obs, traverse_obs, relevance_terms=profile.search_terms)

    assert ctx.find("declare_widgets") != -1
    assert ctx.find("declare_widgets") < ctx.find(
        "helper"
    ), "a generically-named component in a topically-named file should still rank"


class TestDetectTaskMode:
    """E1/E8 regression: a real ticket titled "pipeline change to address
    bigger manifest" — a one-line bug fix — was planned with a prompt
    whose first instruction was "design the architecture", and it
    produced exactly that: an invented 8-layer architecture ending in
    layers ("Curated & Warehouse", "Analytics & BI") the actual pipeline
    doesn't have. `detect_task_mode` is what routes a brief like this to
    the brownfield template instead.
    """

    def test_bugfix_keyword_is_detected(self):
        assert detect_task_mode("Pipeline change to address bigger manifest") == "bugfix"
        assert detect_task_mode("Fix null handling in the export job") == "bugfix"
        assert detect_task_mode("Job fails when the manifest exceeds 200 files") == "bugfix"

    def test_greenfield_keyword_is_detected(self):
        assert detect_task_mode("Build a new event-driven order pipeline") == "greenfield"
        assert detect_task_mode("Design a greenfield ingestion system") == "greenfield"

    def test_enhancement_keyword_is_detected(self):
        assert detect_task_mode("Add support for CSV exports in the reporting service") == (
            "enhancement"
        )

    def test_no_signal_defaults_to_greenfield(self):
        # Preserves existing behavior for an ordinary new-work brief with
        # no bugfix/enhancement/greenfield verb at all.
        assert detect_task_mode("Plan a new Kafka consumer for order events") == "greenfield"

    def test_empty_text_defaults_to_greenfield(self):
        assert detect_task_mode("") == "greenfield"

    def test_bugfix_wins_over_enhancement_on_a_tie(self):
        # The narrower reading wins when a brief carries both signals —
        # brownfield framing still permits new architecture where the
        # prompt genuinely calls for it, so it's the safer default.
        assert detect_task_mode("Fix the bug and add support for retries") == "bugfix"


class TestRenderPromptPicksTemplateByTaskMode:
    def test_bugfix_brief_uses_the_brownfield_template(self):
        profile = analyse("Pipeline change to address bigger manifest")
        assert profile.task_mode == "bugfix"
        prompt = _render_prompt("Pipeline change to address bigger manifest", "", profile)
        assert "Senior Engineer" in prompt
        assert "greenfield work" in prompt
        assert "Principal Solution Architect" not in prompt

    def test_greenfield_brief_uses_the_original_template(self):
        profile = analyse("Build a new event-driven order pipeline")
        assert profile.task_mode == "greenfield"
        prompt = _render_prompt("Build a new event-driven order pipeline", "", profile)
        assert "Principal Solution Architect" in prompt
        assert "Senior Engineer" not in prompt


class TestReusePercentMismatch:
    """E2 regression: a real run's executive summary said "~40% of
    required capabilities exist in ds-databricks-soco-gpc..." while
    `repository_usage` for that same repository said
    `estimated_reuse_pct: 75` — two different numbers for the same claim.
    """

    def test_flags_a_genuine_mismatch(self):
        usage = [RepositoryUsage(name="repo-a", estimated_reuse_pct=75)]
        warning = _reuse_percent_mismatch(
            "Roughly 40% of required capabilities already exist in repo-a.", usage
        )
        assert warning is not None
        assert "40%" in warning
        assert "75%" in warning

    def test_matching_percentage_is_not_flagged(self):
        usage = [RepositoryUsage(name="repo-a", estimated_reuse_pct=75)]
        assert _reuse_percent_mismatch("About 75% of this already exists in repo-a.", usage) is None

    def test_close_percentage_within_tolerance_is_not_flagged(self):
        usage = [RepositoryUsage(name="repo-a", estimated_reuse_pct=75)]
        assert _reuse_percent_mismatch("Roughly 73% already exists.", usage) is None

    def test_unrelated_percentage_is_not_flagged(self):
        # A sentence with a `%` that has nothing to do with reuse must not
        # trigger this — only sentences that actually discuss reuse/existing
        # coverage are in scope.
        usage = [RepositoryUsage(name="repo-a", estimated_reuse_pct=75)]
        assert _reuse_percent_mismatch("This change reduces latency by 40%.", usage) is None

    def test_no_repository_usage_is_not_flagged(self):
        assert _reuse_percent_mismatch("Roughly 40% of this already exists.", []) is None


def test_relevance_requires_whole_token_match_not_substring() -> None:
    """ "process" must not match "processed", and "page" must not match
    "pagination" — a search term is a real word, not an arbitrary substring
    of an unrelated one. See app.agents.text_relevance.tokenize."""
    assert relevance("test_already_processed_file", ["process"]) == 0
    assert relevance("test_s3_pagination_fetches_pages", ["page"]) == 0
    # But a real whole-word match still counts.
    assert relevance("process_batch", ["process"]) == 1


def test_term_weights_pins_zero_document_frequency_terms_to_zero() -> None:
    """A7 regression: a term matching no item in the pool must not
    receive the *maximum* possible weight. `1/(1+log1p(0))` evaluates to
    `1/(1+0) == 1.0` — the same value a term matching exactly one item
    out of thousands would get, for a term that was never actually
    measured as rare, just never measured at all."""
    match_texts = ["WidgetLoader Class svc/widget_loader.py"]
    weights = term_weights(["internal", "widget"], match_texts)
    assert weights["internal"] == 0.0
    assert weights["widget"] > 0.0


def test_rank_repositories_does_not_reward_a_zero_df_term_matching_only_a_repo_name() -> None:
    """A7 regression, black-box: `term_weights`' df is measured over the
    *component* pool, but the same weights dict also scores bare
    repository-name text — text that never contributed to that df count.
    Before the fix, a repository with zero real component evidence could
    still outrank one with genuine matches, purely because a search term
    happened to appear in its own name and had never been measured
    against anything, so it scored as if it were maximally rare.
    """
    indexed_repos = [
        # Zero components below belong to this repo — "internal" matches
        # nothing in the component pool (df=0) but matches this repo's
        # own name directly.
        {"name": "internal-tools-service"},
        {"name": "widget-service"},
    ]
    components = [
        {
            "id": "c1",
            "name": "WidgetLoader",
            "type": "Class",
            "repository": "widget-service",
            "file_path": "widget-service/widget_loader.py",
        },
    ]
    ranked = rank_repositories(indexed_repos, components, relevance_terms=["internal", "widget"])
    assert ranked[0][1] == "widget-service", (
        "the repository with a real, indexed component match must outrank one "
        "whose only 'match' is an unmeasured term appearing in its bare name"
    )


# ---------------------------------------------------------------------------
# GetIndexedRepositoriesTool unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_indexed_repos_with_indexed_repos() -> None:
    mock_db = AsyncMock()
    mock_repo1 = MagicMock()
    mock_repo1.id = "uuid-1"
    mock_repo1.name = "order-service"
    mock_repo1.owner = "acme"
    mock_repo1.full_name = "acme/order-service"

    # Mock db.execute().scalars().all() returning [repo1]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo1]
    mock_db.execute.return_value = mock_result

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    tool = GetIndexedRepositoriesTool(
        db=mock_db, graph_repository=mock_graph_repo, user_id="user-1"
    )
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.tool_name == "get_indexed_repositories"
    assert len(obs.data["indexed_repositories"]) == 1
    assert obs.data["indexed_repositories"][0]["name"] == "order-service"


@pytest.mark.asyncio
async def test_get_indexed_repos_full_name_is_canonical_owner_slash_name() -> None:
    """Root-cause regression: `GetIndexedRepositoriesTool` used to return
    only bare `name`/`owner` as two separate fields, never combined —
    every downstream consumer that needed one identity string (Planning's
    `repositories_consulted`, its `repository_usage` verification) read
    bare `name` alone, silently dropping the owner. `full_name` must be
    exactly the canonical "owner/name" GitHub identity, read straight from
    the already-correct `Repository.full_name` column, not reconstructed."""
    mock_db = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.id = "uuid-1"
    mock_repo.name = "prompt-library"
    mock_repo.owner = "sasikumars-cyb"
    mock_repo.full_name = "sasikumars-cyb/prompt-library"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    tool = GetIndexedRepositoriesTool(
        db=mock_db, graph_repository=mock_graph_repo, user_id="user-1"
    )
    obs = await tool.execute()

    repo = obs.data["indexed_repositories"][0]
    assert repo["name"] == "prompt-library"
    assert repo["owner"] == "sasikumars-cyb"
    assert repo["full_name"] == "sasikumars-cyb/prompt-library"


@pytest.mark.asyncio
async def test_get_indexed_repos_none_indexed() -> None:
    mock_db = AsyncMock()
    mock_repo1 = MagicMock()
    mock_repo1.id = "uuid-1"
    mock_repo1.name = "order-service"
    mock_repo1.owner = "acme"

    mock_repo_result = MagicMock()
    mock_repo_result.scalars.return_value.all.return_value = [mock_repo1]
    # GraphHealthService issues a second query — for the latest
    # IndexingJob.status of repositories with no graph — only when
    # `has_graph` comes back False, which it does below. No jobs at all
    # (empty `.all()`) is a legitimate history: never successfully indexed.
    mock_jobs_result = MagicMock()
    mock_jobs_result.all.return_value = []
    mock_db.execute.side_effect = [mock_repo_result, mock_jobs_result]

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    tool = GetIndexedRepositoriesTool(
        db=mock_db, graph_repository=mock_graph_repo, user_id="user-1"
    )
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.data["indexed_repositories"] == []
    assert obs.data["total_tracked"] == 1


@pytest.mark.asyncio
async def test_get_indexed_repos_db_failure() -> None:
    mock_db = AsyncMock()
    mock_db.execute.side_effect = Exception("DB unavailable")

    mock_graph_repo = AsyncMock()

    tool = GetIndexedRepositoriesTool(
        db=mock_db, graph_repository=mock_graph_repo, user_id="user-1"
    )
    obs = await tool.execute()

    assert obs.succeeded is False
    assert "DB unavailable" in obs.error


@pytest.mark.asyncio
async def test_get_indexed_repos_filters_by_owning_user() -> None:
    """The repository read must be scoped to the run's own user.

    `repositories` holds one row per (user, repo) tracking relationship, so
    an unscoped read returns other accounts' repositories — which then reach
    the LLM prompt, the evidence pool used to verify its claims, and the
    user-visible "Found N indexed repositories" summary. Observed in
    production as a plan reporting 5 repositories to a user who tracked 4,
    with one repository listed twice (their own copy plus another account's).
    """
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    tool = GetIndexedRepositoriesTool(db=mock_db, graph_repository=AsyncMock(), user_id="user-1")
    await tool.execute()

    executed = mock_db.execute.await_args.args[0]
    assert "WHERE" in str(executed), "repository query must be filtered, not a bare SELECT"
    assert "user_id" in str(executed), "repository query must filter on user_id"


@pytest.mark.asyncio
async def test_neo4j_graph_tool_refuses_to_run_without_a_user_id() -> None:
    """Fails closed: no user id means no scoping, and an unscoped
    cross-tenant read is worse than returning no graph context at all."""
    from app.tools.implementations.neo4j_tool import Neo4jGraphTool
    from app.tools.interfaces import ToolInput

    result = await Neo4jGraphTool(config={}).execute(
        ToolInput(query="anything", parameters={"db": AsyncMock()})
    )

    assert result.success is False
    assert "user_id" in (result.error or "")


# ---------------------------------------------------------------------------
# TraverseArchitectureGraphTool unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traverse_empty_repos_returns_empty_observation() -> None:
    mock_graph_repo = AsyncMock()
    tool = TraverseArchitectureGraphTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([])

    assert obs.tool_name == "traverse_architecture_graph"
    assert obs.data["components"] == []
    assert obs.data["kafka_topics"] == []
    assert "No indexed" in obs.summary


@pytest.mark.asyncio
async def test_traverse_returns_components_and_topics() -> None:
    mock_graph_repo = AsyncMock()

    component_node = GraphNode(
        id="comp-1",
        labels=["Component", "Service"],
        properties={"name": "OrderService", "file_path": "src/OrderService.java"},
    )
    topic_node = GraphNode(
        id="topic-1",
        labels=["KafkaTopic"],
        properties={"name": "order.created"},
    )

    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: (
            [component_node] if label == "Component" else [topic_node]
        )
    )

    tool = TraverseArchitectureGraphTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "repo-1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is True
    assert len(obs.data["components"]) == 1
    assert obs.data["components"][0]["name"] == "OrderService"
    assert len(obs.data["kafka_topics"]) == 1
    assert obs.data["kafka_topics"][0]["name"] == "order.created"
    assert "1 component" in obs.summary
    assert "1 Kafka topic" in obs.summary


@pytest.mark.asyncio
async def test_traverse_all_repos_fail_marks_as_failed() -> None:
    """P0-1: If all repos fail during traversal, succeeded must be False."""
    mock_graph_repo = AsyncMock()
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Neo4j connection refused")
    )

    tool = TraverseArchitectureGraphTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "repo-1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is False
    assert obs.error != ""
    assert "failed" in obs.summary.lower()


# ---------------------------------------------------------------------------
# PlanningAgent integration tests (mocked LLM + graph)
# ---------------------------------------------------------------------------


def _make_planning_context(
    display_name: str = "Plan a new Kafka consumer for order events",
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name=display_name,
    )
    mock_db = AsyncMock()
    return AgentContext(
        subject=subject,
        goal="plan_freeform",
        # `user_id` is always present in production (the run coordinator puts
        # it there — see app.orchestrator.run_coordinator) and the graph tool
        # now requires it, since repository rows are per-user and an unscoped
        # read would pull other accounts' repositories into the plan.
        extras={"db": mock_db, "user_id": "user-1"},
    )


def _make_llm_response(steps: int = 3) -> str:
    return json.dumps(
        {
            "executive_summary": "A plan to implement the feature.",
            "implementation_steps": [
                {
                    "order": i + 1,
                    "description": f"Step {i + 1}",
                    "affected_component": "OrderService",
                    "risk_note": "",
                }
                for i in range(steps)
            ],
            "affected_components": ["OrderService"],
            "kafka_topics_involved": ["order.created"],
            "risk_considerations": ["Risk 1"],
            "graph_context_used": True,
        }
    )


@pytest.mark.asyncio
async def test_planning_agent_happy_path_has_graph_evidence() -> None:
    """Core requirement: output must include at least one graph_traversal or
    tool_call Evidence entry per the Definition of Done."""
    context = _make_planning_context()

    # Mock graph repo
    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    component_node = GraphNode(
        id="c1",
        labels=["Component", "Service"],
        properties={"name": "OrderService"},
    )
    topic_node = GraphNode(
        id="t1",
        labels=["KafkaTopic"],
        properties={"name": "order.created"},
    )
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: (
            [component_node] if label == "Component" else [topic_node]
        )
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch(
            "app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_make_llm_response())
        ),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Must have evidence
    assert len(output.evidence) > 0

    # Core requirement: at least one graph_traversal or tool_call entry
    evidence_kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in evidence_kinds or "tool_call" in evidence_kinds, (
        "Planning Agent output must include at least one 'graph_traversal' or 'tool_call' "
        "Evidence entry — pure 'llm_reasoning' is not sufficient per the Definition of Done."
    )

    # Result is populated
    assert output.result["executive_summary"]
    assert len(output.result["implementation_steps"]) == 3

    # Confidence is within bounds
    assert 0.0 <= output.confidence.score <= 1.0

    # agent_id correct
    assert output.agent_id == "planning"
    assert output.subject_id == "freetext:abc123"


@pytest.mark.asyncio
async def test_planning_agent_result_includes_real_llm_trace() -> None:
    """The Log tab needs the actual prompt/response, not just a one-line
    Evidence summary — result.llm_trace must carry the real prompt text
    (including the task description) and the real raw model response."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    llm_response = _make_llm_response()

    async def _fake_call_llm(
        user_prompt: str,
        model: str | None = None,
        _metadata_out: dict[str, Any] | None = None,
        stage: str = "planning",
        context: Any = None,
        purpose: str = "initial",
        sequence: int = 0,
    ) -> str:
        """Honours the *whole* `_call_llm` contract, not just its return
        value: the real function fills `_metadata_out` in place (see
        `app.agents.llm.invoke_llm_json` / `LLM_INVOCATION_METADATA_KEYS`),
        and `llm_trace` is assembled from exactly those keys. A bare
        `AsyncMock(return_value=...)` returns the string but silently skips
        the out-param, so the trace it produced was missing every metric —
        a gap in the double, not in the agent."""
        if _metadata_out is not None:
            _metadata_out.update(
                {
                    "provider": "openai",
                    "model": "gpt-4",
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "estimated_cost_usd": 0.01,
                    "latency_ms": 42,
                    "retry_count": 0,
                    "finish_reason": "stop",
                    "status": "completed",
                }
            )
        return llm_response

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=_fake_call_llm),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    trace = output.result["llm_trace"]
    assert trace is not None
    assert trace["raw_response"] == llm_response
    assert context.subject.display_name in trace["prompt"]
    assert trace["latency_ms"] is not None
    assert trace["latency_ms"] >= 0
    # The trace is now assembled entirely from the shared invocation
    # metadata — assert the metrics actually flow through, not just latency.
    assert trace["provider"] == "openai"
    assert trace["total_tokens"] == 150
    assert trace["estimated_cost_usd"] == 0.01


@pytest.mark.asyncio
async def test_planning_agent_end_to_end_uses_brownfield_prompt_for_a_bugfix_brief() -> None:
    """E1/E8 regression, full pipeline: a bugfix-shaped brief must reach
    the LLM with the brownfield template, not the one whose first
    instruction is "design the architecture". Exercised through the real
    agent, not just `_render_prompt` in isolation, so this also proves
    `profile.task_mode` from `analyse()` actually reaches `_render_prompt`
    unchanged through the run() pipeline.
    """
    context = _make_planning_context(display_name="Fix the null pointer crash in the export job")

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    llm_response = _make_llm_response()

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    prompt = output.result["llm_trace"]["prompt"]
    assert "Senior Engineer" in prompt
    assert "Principal Solution Architect" not in prompt
    assert "Design the architecture" not in prompt


@pytest.mark.asyncio
async def test_planning_agent_no_indexed_repos_still_produces_evidence() -> None:
    """Even with no indexed repos, the agent must produce Evidence entries —
    querying the graph and finding nothing is still a graph traversal."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps(
        {
            "executive_summary": "Plan based on general engineering practices.",
            "implementation_steps": [
                {"order": 1, "description": "Step 1", "affected_component": "", "risk_note": ""}
            ],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": False,
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Evidence must still exist (even empty results produce evidence)
    assert len(output.evidence) > 0
    evidence_kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in evidence_kinds or "tool_call" in evidence_kinds

    # Lower confidence when no graph data
    assert output.confidence.score < 0.7


@pytest.mark.asyncio
async def test_planning_agent_llm_failure_raises() -> None:
    """Per AGENT_FRAMEWORK.md error policy: LLM failure raises, never
    returns a plausible-looking default."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(side_effect=PlanningLLMError("API key missing.")),
        ),
    ):
        agent = PlanningAgent()
        with pytest.raises(PlanningLLMError):
            await agent.run(context)


@pytest.mark.asyncio
async def test_planning_agent_output_matches_agent_output_contract() -> None:
    """Verify AgentOutput envelope fields match _contract.py schema."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)
    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(
                return_value=json.dumps(
                    {
                        "executive_summary": "Plan.",
                        "implementation_steps": [],
                        "affected_components": [],
                        "kafka_topics_involved": [],
                        "risk_considerations": [],
                        "graph_context_used": False,
                    }
                )
            ),
        ),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Check all AgentOutput envelope fields
    assert output.agent_id == "planning"
    assert output.subject_id == "freetext:abc123"
    assert isinstance(output.confidence.score, float)
    assert isinstance(output.confidence.reasoning, str)
    assert isinstance(output.evidence, list)
    assert isinstance(output.result, dict)
    assert isinstance(output.graph_facts_written, list)
    assert isinstance(output.prompt_version, str)


# ---------------------------------------------------------------------------
# P0-1: Evidence integrity — failed tools must not produce false evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_agent_graph_unavailable_no_false_evidence() -> None:
    """P0-1: When the graph is unreachable (tool fails), the agent must
    not produce graph_traversal evidence that implies a successful query."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    # get_nodes_by_label raises — Neo4j is down
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Neo4j connection refused")
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps(
        {
            "executive_summary": "Plan without graph.",
            "implementation_steps": [{"order": 1, "description": "Step 1"}],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": False,
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # No evidence entry should claim kind="graph_traversal" when the
    # traversal actually failed
    for ev in output.evidence:
        if ev.kind == "graph_traversal":
            raise AssertionError(
                f"Found graph_traversal evidence '{ev.summary}' but the "
                "graph was unavailable — this is fabricated evidence."
            )

    # At least one evidence entry must indicate the failure
    failed_entries = [e for e in output.evidence if "FAILED" in e.summary]
    assert len(failed_entries) > 0


# ---------------------------------------------------------------------------
# Work Item 3: graph_context_used must be derived from execution, never
# trusted from the LLM's self-report.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_agent_graph_context_used_overridden_when_graph_fails() -> None:
    """The LLM claims graph_context_used=True, but the graph traversal
    actually failed — the persisted result must not repeat that claim."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Neo4j connection refused")
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    # LLM lies and claims it used graph context even though every
    # traversal call failed.
    llm_response = json.dumps(
        {
            "executive_summary": "Plan.",
            "implementation_steps": [],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    assert output.result["graph_context_used"] is False, (
        "graph_context_used must reflect actual tool execution, not the "
        "LLM's self-report — the graph traversal failed here."
    )


@pytest.mark.asyncio
async def test_planning_agent_graph_context_used_true_when_graph_has_data() -> None:
    """When graph traversal genuinely succeeds and returns data,
    graph_context_used must be True regardless of what the LLM reports."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    component_node = GraphNode(
        id="c1",
        labels=["Component", "Service"],
        properties={"name": "OrderService"},
    )
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: [component_node] if label == "Component" else []
    )
    mock_graph_repo.get_outgoing_cross_repository_edges = AsyncMock(return_value=[])

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_repo.full_name = "acme/order-service"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    # LLM under-reports — the code's own signal should win.
    llm_response = json.dumps(
        {
            "executive_summary": "Plan.",
            "implementation_steps": [],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": False,
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    assert output.result["graph_context_used"] is True


# ---------------------------------------------------------------------------
# P1-3: Graph unavailable vs graph empty — confidence must distinguish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_agent_graph_unavailable_confidence_reasoning() -> None:
    """P1-3: When graph is unavailable, confidence reasoning must mention
    infrastructure error, not just 'no data'."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(side_effect=Exception("Connection refused"))

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps(
        {
            "executive_summary": "Plan.",
            "implementation_steps": [],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": False,
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Confidence must be lower than the healthy-empty case (0.45)
    assert (
        output.confidence.score <= 0.35
    ), f"Graph unavailable should give very low confidence, got {output.confidence.score}"
    assert "unavailable" in output.confidence.reasoning.lower()


@pytest.mark.asyncio
async def test_planning_agent_graph_empty_confidence_reasoning() -> None:
    """P1-3: When graph is healthy but empty, confidence reasoning must say
    'healthy' not 'unavailable'."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps(
        {
            "executive_summary": "Plan.",
            "implementation_steps": [],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": False,
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Should be healthy-empty confidence (0.45 + possible step bump)
    assert (
        0.40 <= output.confidence.score <= 0.55
    ), f"Healthy-empty graph should give moderate confidence, got {output.confidence.score}"
    assert "healthy" in output.confidence.reasoning.lower()
    assert "unavailable" not in output.confidence.reasoning.lower()


# ---------------------------------------------------------------------------
# Cross-repository verification (B1/B3/B4/B6 regression)
# ---------------------------------------------------------------------------


def _make_two_repo_context() -> tuple[AgentContext, MagicMock]:
    """A brief naming a component unique to one of two indexed repos, so
    ranking deterministically prefers that repo as the target — needed so
    the ownership-attribution assertions below don't depend on tie-break
    order between two equally-generic repos."""
    context = _make_planning_context(
        display_name="Fix the ZulutronManifestWidget bug in the pipeline"
    )

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    target_component = GraphNode(
        id="c-target",
        labels=["Component", "Class"],
        properties={
            "name": "ZulutronManifestWidget",
            "file_path": "target_repo/zulutron_manifest_widget.py",
        },
    )
    other_component = GraphNode(
        id="c-other",
        labels=["Component", "Class"],
        properties={
            "name": "OtherRepoOnlyComponent",
            "file_path": "other_repo/other_repo_only_component.py",
        },
    )

    def _nodes_for(repo_id: str, label: str) -> list[GraphNode]:
        if label != "Component":
            return []
        return {"repo-target": [target_component], "repo-other": [other_component]}.get(repo_id, [])

    mock_graph_repo.get_nodes_by_label = AsyncMock(side_effect=_nodes_for)
    mock_graph_repo.get_outgoing_cross_repository_edges = AsyncMock(return_value=[])

    mock_db = context.extras["db"]
    # `MagicMock(name=...)` is reserved (sets the mock's own debug name, not
    # an attribute) — assign `.name` after construction, per the existing
    # single-repo fixtures above.
    mock_target_repo = MagicMock(id="repo-target", owner="acme")
    mock_target_repo.name = "ds-team-apc-svc"
    mock_target_repo.full_name = "acme/ds-team-apc-svc"
    mock_other_repo = MagicMock(id="repo-other", owner="acme")
    mock_other_repo.name = "ds-team-gpc-svc"
    mock_other_repo.full_name = "acme/ds-team-gpc-svc"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_target_repo, mock_other_repo]
    mock_db.execute.return_value = mock_result

    return context, mock_graph_repo


@pytest.mark.asyncio
async def test_planning_agent_flags_component_misattributed_to_wrong_repo() -> None:
    """Regression test for the mechanism that let 4 of 7 affected_components
    in a real run belong to three unrelated repositories and pass
    verification with zero warnings: the evidence pool was pooled across
    every indexed repository, so "this component exists somewhere" was
    indistinguishable from "this component exists in the repo it was
    claimed for". `OtherRepoOnlyComponent` genuinely exists — just not in
    the target repo — and must now be flagged by name, naming its real owner.
    """
    context, mock_graph_repo = _make_two_repo_context()

    llm_response = json.dumps(
        {
            "executive_summary": "Fix the widget.",
            "implementation_steps": [],
            "affected_components": ["ZulutronManifestWidget", "OtherRepoOnlyComponent"],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    warnings = output.result["verification_warnings"]
    misattribution = [w for w in warnings if "OtherRepoOnlyComponent" in w]
    assert misattribution, f"expected a misattribution warning, got: {warnings}"
    assert "ds-team-gpc-svc" in misattribution[0], "warning must name the real owning repository"
    assert (
        "ds-team-apc-svc" in misattribution[0]
    ), "warning must name the wrongly-claimed repository"
    # The genuinely-owned component must not be caught in the same net.
    assert not any("ZulutronManifestWidget" in w for w in warnings)


@pytest.mark.asyncio
async def test_planning_agent_repository_usage_verified_false_on_misattributed_file() -> None:
    """B1/B4 regression: `verified` must reflect the specific repository's
    own files_affected claims, not just whether the repo name is indexed —
    and must default to unverified (fails closed) rather than trusted."""
    context, mock_graph_repo = _make_two_repo_context()

    llm_response = json.dumps(
        {
            "executive_summary": "Fix the widget.",
            "implementation_steps": [],
            "affected_components": ["ZulutronManifestWidget"],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
            # Canonical "owner/name" form — the format generate_code's own
            # prompt (and, per this fix, `indexed_repo_names`/
            # `per_repo_pool`) require repository_usage claims to use.
            "repository_usage": [
                {
                    "name": "acme/ds-team-apc-svc",
                    "purpose": "Widget processing",
                    "files_affected": ["other_repo/other_repo_only_component.py"],
                }
            ],
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    usage = output.result["repository_usage"][0]
    assert usage["verified"] is False, (
        "a repository whose only files_affected claim belongs to a different "
        "repository must not read as verified"
    )
    warnings = output.result["verification_warnings"]
    assert any(
        "other_repo_only_component.py" in w and "ds-team-gpc-svc" in w for w in warnings
    ), f"expected a file-misattribution warning naming the real owner, got: {warnings}"


# ---------------------------------------------------------------------------
# Repository identity normalization regression tests (KAN — P1 finding from
# the auto-execution live-QA pass: `repositories_consulted` used to carry
# only the bare repository name, so a `repository_usage` claim cited in the
# canonical "owner/name" form generate_code's own prompt requires would be
# reported as "not found among indexed repositories" even when it was
# exactly the repository this run traversed.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_agent_repository_usage_in_canonical_form_passes_verification() -> None:
    """The exact scenario the live P0 test surfaced: a `repository_usage`
    claim in "owner/name" form, for a repository this run's own graph
    traversal genuinely returned, with file claims that genuinely belong to
    it, must verify cleanly — no "not found among indexed repositories"
    warning, no file-misattribution warning."""
    context, mock_graph_repo = _make_two_repo_context()

    llm_response = json.dumps(
        {
            "executive_summary": "Fix the widget.",
            "implementation_steps": [],
            "affected_components": ["ZulutronManifestWidget"],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
            "repository_usage": [
                {
                    "name": "acme/ds-team-apc-svc",
                    "purpose": "Widget processing",
                    "files_affected": ["target_repo/zulutron_manifest_widget.py"],
                }
            ],
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    usage = output.result["repository_usage"][0]
    assert usage["verified"] is True, "a correctly-cited, correctly-owned claim must verify"
    warnings = output.result["verification_warnings"]
    assert not any(
        "cited in repository_usage was not found" in w for w in warnings
    ), (
        "a canonically-cited, genuinely-indexed repository must not be "
        f"flagged unindexed: {warnings}"
    )
    assert not any(
        "likely misattributed" in w for w in warnings
    ), f"a genuinely-owned file must not be flagged misattributed: {warnings}"


@pytest.mark.asyncio
async def test_planning_agent_repository_usage_wrong_owner_does_not_pass() -> None:
    """Canonical identity is owner+name together, not name alone: citing the
    real repository *name* under a different owner must not verify — that
    would mean the matcher only ever checked the name half of the identity."""
    context, mock_graph_repo = _make_two_repo_context()

    llm_response = json.dumps(
        {
            "executive_summary": "Fix the widget.",
            "implementation_steps": [],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
            "repository_usage": [
                {
                    "name": "wrong-owner/ds-team-apc-svc",
                    "purpose": "Widget processing",
                    "files_affected": [],
                }
            ],
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    warnings = output.result["verification_warnings"]
    assert any(
        "wrong-owner/ds-team-apc-svc" in w and "cited in repository_usage was not found" in w
        for w in warnings
    ), f"a real repository name under the wrong owner must not verify: {warnings}"


@pytest.mark.asyncio
async def test_planning_agent_repository_usage_wrong_repository_does_not_pass() -> None:
    """A repository that was never indexed at all — right-shaped name,
    wrong everything — must not verify."""
    context, mock_graph_repo = _make_two_repo_context()

    llm_response = json.dumps(
        {
            "executive_summary": "Fix the widget.",
            "implementation_steps": [],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
            "repository_usage": [
                {
                    "name": "acme/nonexistent-repo",
                    "purpose": "Widget processing",
                    "files_affected": [],
                }
            ],
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    warnings = output.result["verification_warnings"]
    assert any(
        "acme/nonexistent-repo" in w and "cited in repository_usage was not found" in w
        for w in warnings
    ), f"a never-indexed repository must not verify: {warnings}"


@pytest.mark.asyncio
async def test_planning_agent_bare_repository_name_is_not_silently_canonical() -> None:
    """The fix must not weaken verification to accommodate a malformed
    identity: a `repository_usage` claim in bare form (no owner) is not the
    canonical identity this run's `indexed_repo_names` now carries, and
    must not be silently treated as equivalent to it — the same discipline
    `code_generation.verification._is_well_formed` already applies (rejects
    anything without exactly one "/") at the next stage.

    Note: `usage.name in ranked_repo_names` (bare, sourced from Context
    Discovery and deliberately left untouched by this fix) still assigns a
    `stars` estimate for a bare name — that pre-existing, out-of-scope
    behavior is unrelated to `verified`. The thing this fix actually
    guarantees is that `indexed_repo_names` — the set `name_indexed`, and
    therefore `usage.verified`, is computed against — is canonical-only, so
    a bare name never counts as indexed for verification purposes."""
    context, mock_graph_repo = _make_two_repo_context()

    llm_response = json.dumps(
        {
            "executive_summary": "Fix the widget.",
            "implementation_steps": [],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
            "repository_usage": [
                {
                    "name": "ds-team-apc-svc",  # bare — no owner
                    "purpose": "Widget processing",
                    "files_affected": ["target_repo/zulutron_manifest_widget.py"],
                }
            ],
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    usage = output.result["repository_usage"][0]
    assert usage["verified"] is False, (
        "a bare repository name must not be silently accepted as the "
        f"canonical identity: {usage}"
    )


# ---------------------------------------------------------------------------
# Entity-mismatch wiring regression tests: `check_entity_mismatch` must be
# called against the actually-selected/target repository, never an
# arbitrary top-ranked candidate from the full Context Discovery survey
# (`ranked_repo_names[0]`) — the exact P0 finding: a real run's objective
# contained "ONLY" (a false-positive token, covered separately in
# test_verification.py) checked against `ranked_repo_names[0]`, which
# happened to be a repository entirely unrelated to the actual objective.
# ---------------------------------------------------------------------------


def _context_discovery_result_with_divergent_ranking(
    display_name: str,
) -> dict[str, Any]:
    """A multi-repository Context Discovery result where the top-ranked
    candidate (`ranked_repository_names[0]`) is deliberately NOT the
    repository that ends up selected — exactly the shape a real multi-
    repository survey produces when Context Discovery consults many
    repositories but only confirms one as the actual target."""
    return {
        "enriched_text": display_name,
        "indexed_repositories": [
            {
                "id": "r1",
                "name": "ds-team-apc-c2m-rcs-svc",
                "owner": "acme",
                "full_name": "acme/ds-team-apc-c2m-rcs-svc",
            },
            {
                "id": "r2",
                "name": "ds-team-gpc-c2m-rcs-svc",
                "owner": "acme",
                "full_name": "acme/ds-team-gpc-c2m-rcs-svc",
            },
        ],
        "graph_components": [],
        "graph_topics": [],
        # Top-ranked ("survey order") deliberately differs from selected.
        "ranked_repository_names": ["ds-team-apc-c2m-rcs-svc", "ds-team-gpc-c2m-rcs-svc"],
        "repositories": [
            {"name": "ds-team-apc-c2m-rcs-svc", "selected": False},
            {"name": "ds-team-gpc-c2m-rcs-svc", "selected": True},
        ],
        "graph_available": True,
        "graph_has_data": False,
    }


@pytest.mark.asyncio
async def test_planning_agent_entity_mismatch_uses_selected_repo_not_top_ranked() -> None:
    """The exact P0 bug: the top-ranked repo ('other-tenant-svc') does not
    contain the ticket's GPC token, but the actually-*selected* target
    ('ds-team-gpc-svc') does. Checking against `ranked_repo_names[0]` (the
    old behavior) would have produced a false-positive mismatch warning
    here; checking against the selected repository must not."""
    context = _make_planning_context(display_name="Soco_C2M_GPC_RCS -> fix the widget")
    context.extras["workflow"] = MagicMock()
    context_discovery_result = _context_discovery_result_with_divergent_ranking(
        context.subject.display_name
    )

    with (
        patch(
            "app.agents.planning.agent.get_stage_result",
            return_value=context_discovery_result,
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response()),
        ),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    warnings = output.result["verification_warnings"]
    assert not any("entity/tenant-shaped token" in w for w in warnings), warnings


@pytest.mark.asyncio
async def test_planning_agent_entity_mismatch_still_fires_when_selected_repo_lacks_token() -> None:
    """The fix must not simply disable the check: when the ticket's token
    is genuinely absent from the *selected* repository too (here neither
    candidate contains APC), the mismatch warning must still fire."""
    context = _make_planning_context(display_name="Soco_C2M_APC_RCS -> fix the widget")
    context.extras["workflow"] = MagicMock()
    context_discovery_result = _context_discovery_result_with_divergent_ranking(
        context.subject.display_name
    )

    with (
        patch(
            "app.agents.planning.agent.get_stage_result",
            return_value=context_discovery_result,
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response()),
        ),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    warnings = output.result["verification_warnings"]
    assert any("entity/tenant-shaped token" in w and "APC" in w for w in warnings), warnings
    findings = output.result["verification_findings"]
    assert any(
        f["category"] == "repository_identity_mismatch" for f in findings
    ), findings


@pytest.mark.asyncio
async def test_planning_agent_end_to_end_prompt_library_only_objective_no_false_positive() -> None:
    """E: reproduces the exact real P0 scenario that surfaced both bugs
    together — objective containing "ONLY" ('This must be the ONLY
    change...'), targeting sasikumars-cyb/prompt-library, with 17
    repositories Context Discovery consulted but only prompt-library
    actually selected. Expected: no 'ingestion-framework' (or any other
    unrelated repo) false positive, canonical repository identity
    end-to-end, and no repository-identity-mismatch finding of any kind
    for this genuinely clean scenario."""
    display_name = (
        "In the sasikumars-cyb/prompt-library repository, add a single-line "
        "module-level docstring to the top of prompt_library/registry.py "
        "stating what the file does. This must be the ONLY change: no logic "
        "changes, no other files touched, no dependency changes, no deletions."
    )
    context = _make_planning_context(display_name=display_name)
    context.extras["workflow"] = MagicMock()

    consulted_repo_names = [
        "streaming-pipeline",
        "ingestion-framework",
        "db-migrations",
        "shipping-service-java",
        "warehouse-jobs",
        "shared-java-sdk",
        "payment-service-java",
        "prompt-library",
        "llm-gateway",
        "agent-runtime",
        "analytics-pipeline-python",
        "notification-service-python",
        "customer-service-python",
        "shared-python-sdk",
        "inventory-service-python",
        "order-service-python",
        "etl-core",
    ]
    context_discovery_result = {
        "enriched_text": display_name,
        "indexed_repositories": [
            {
                "id": f"r-{name}",
                "name": name,
                "owner": "sasikumars-cyb",
                "full_name": f"sasikumars-cyb/{name}",
            }
            for name in consulted_repo_names
        ],
        "graph_components": [],
        "graph_topics": [],
        # "ingestion-framework" ranks ahead of "prompt-library" in the
        # broader survey order — exactly the real run's shape — but only
        # prompt-library is actually selected as the target.
        "ranked_repository_names": consulted_repo_names,
        "repositories": [
            {"name": name, "selected": name == "prompt-library"}
            for name in consulted_repo_names
        ],
        "graph_available": True,
        "graph_has_data": False,
    }

    with (
        patch(
            "app.agents.planning.agent.get_stage_result",
            return_value=context_discovery_result,
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response()),
        ),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    warnings = output.result["verification_warnings"]
    assert not any("ingestion-framework" in w for w in warnings), warnings
    assert not any("entity/tenant-shaped token" in w for w in warnings), warnings
    findings = output.result["verification_findings"]
    assert not any(f["category"] == "repository_identity_mismatch" for f in findings), findings

    # Canonical repository identity end-to-end.
    assert output.result["repositories_consulted"] == [
        f"sasikumars-cyb/{name}" for name in consulted_repo_names
    ]
    assert output.result["target_repositories"] == ["prompt-library"]


@pytest.mark.asyncio
async def test_planning_agent_flags_unindexed_sibling_repo_reference() -> None:
    """B6 regression: a repository the plan's own narrative names — in a
    phase deliverable, not in repository_usage — that was never indexed
    and never scored must still be flagged, not silently treated as if it
    exists. Mirrors a real run that planned work against "MPC" purely in
    prose; MPC was never selected, never indexed, and nothing else in
    verification ever looked at it.
    """
    context, mock_graph_repo = _make_two_repo_context()

    llm_response = json.dumps(
        {
            "executive_summary": "Fix the widget.",
            "implementation_steps": [],
            "affected_components": ["ZulutronManifestWidget"],
            "kafka_topics_involved": [],
            "risk_considerations": [],
            "graph_context_used": True,
            "implementation_phases": [
                {
                    "name": "Rollout",
                    "order": 1,
                    "deliverables": ["Also replicate this fix to the MPC repository"],
                }
            ],
        }
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository",
            return_value=mock_graph_repo,
        ),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    warnings = output.result["verification_warnings"]
    assert any(
        "MPC" in w and "not itself indexed" in w for w in warnings
    ), f"expected an unindexed-sibling-repo warning, got: {warnings}"


# ---------------------------------------------------------------------------
# ADR 0010 §7 P2 (Theme D) — `_selected_repo_names` reads the canonical
# `repositories` field, never the stale `selected_repositories` projection
# ---------------------------------------------------------------------------


def test_selected_repo_names_reads_the_canonical_repositories_field() -> None:
    result = {
        "repositories": [
            {"name": "ingestion-framework", "selected": True},
            {"name": "etl-core", "selected": True},
            {"name": "streaming-pipeline", "selected": False},
        ]
    }
    assert set(_selected_repo_names(result, [])) == {"ingestion-framework", "etl-core"}


def test_selected_repo_names_ignores_a_stale_selected_repositories_key() -> None:
    """The exact bug this fix closes: after a human override replaces
    `repositories`, `get_stage_result()`'s shallow merge never recomputes
    `selected_repositories` — it's still whatever `build_result` originally
    stored. `repositories` must win whenever it's present at all (ADR 0010,
    invariant I6)."""
    result = {
        "repositories": [{"name": "billing-service", "selected": True}],
        # Stale — computed from the *pre-override* repositories, must be
        # ignored now that `repositories` itself is present.
        "selected_repositories": [{"name": "payment-service"}],
    }
    assert _selected_repo_names(result, []) == ["billing-service"]


def test_selected_repo_names_falls_back_to_ranked_repo_names_when_nothing_selected() -> None:
    result = {"repositories": [{"name": "payment-service", "selected": False}]}
    assert _selected_repo_names(result, ["payment-service", "billing-service"]) == [
        "payment-service"
    ]


def test_selected_repo_names_falls_back_through_legacy_shapes_when_repositories_is_absent() -> None:
    """A result persisted before ADR 0010 (no `repositories` key at all)
    must still resolve via the pre-existing fallback chain."""
    assert _selected_repo_names({"selected_repositories": [{"name": "legacy-service"}]}, []) == [
        "legacy-service"
    ]
    assert _selected_repo_names({"implementation_candidates": ["older-service"]}, []) == [
        "older-service"
    ]
    assert _selected_repo_names({}, ["oldest-service"]) == ["oldest-service"]
