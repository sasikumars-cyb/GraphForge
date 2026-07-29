"""Tests for the Context Resolution Pipeline (app.context_pipeline).

Covers: deterministic reference detection, provider capability
resolution, context normalization, enriched planning request creation,
and — in test_planning_agent_operates_solely_on_enriched_input — that
the Planning Agent no longer does any of this itself.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.planning.classifier import analyse
from app.context_pipeline.discovery import recommend_additional_context
from app.context_pipeline.models import (
    EnrichedPlanningRequest,
    ProviderCapability,
    Reference,
    ReferenceType,
)
from app.context_pipeline.pipeline import ContextResolutionPipeline
from app.context_pipeline.providers import (
    ConfluenceProvider,
    GitHubProvider,
    GraphProvider,
    JiraProvider,
)
from app.context_pipeline.reference_detection import detect_references
from app.tools.interfaces import ToolResult

# ---------------------------------------------------------------------------
# Phase 3 — reference detection
# ---------------------------------------------------------------------------


def test_detects_a_bare_jira_issue_key() -> None:
    refs = detect_references("Fix the bug described in NPT-6 please")
    assert any(r.type == ReferenceType.JIRA_ISSUE and r.normalized_value == "NPT-6" for r in refs)


def test_detects_a_jira_browse_url() -> None:
    refs = detect_references("See https://myorg.atlassian.net/browse/ABC-123 for details")
    jira_refs = [r for r in refs if r.type == ReferenceType.JIRA_ISSUE]
    assert jira_refs and jira_refs[0].normalized_value == "ABC-123"
    assert jira_refs[0].confidence == 1.0


def test_detects_a_confluence_page_url() -> None:
    refs = detect_references(
        "Design doc: https://myorg.atlassian.net/wiki/spaces/ENG/pages/12345/Design"
    )
    confluence_refs = [r for r in refs if r.type == ReferenceType.CONFLUENCE_PAGE]
    assert confluence_refs and confluence_refs[0].normalized_value == "12345"


def test_detects_a_github_pull_request_shorthand() -> None:
    refs = detect_references("Continue the work in acme/widgets#42")
    gh_refs = [r for r in refs if r.type == ReferenceType.GITHUB_PULL_REQUEST]
    assert gh_refs and gh_refs[0].normalized_value == "acme/widgets#42"


def test_detects_a_github_url() -> None:
    refs = detect_references("https://github.com/acme/widgets/pull/7 needs a follow-up")
    gh_refs = [r for r in refs if r.type == ReferenceType.GITHUB_PULL_REQUEST]
    assert gh_refs and gh_refs[0].normalized_value == "acme/widgets#7"


def test_bare_repository_shorthand_is_detected_at_lower_confidence() -> None:
    refs = detect_references("Look at acme/widgets for the existing pattern")
    repo_refs = [r for r in refs if r.type == ReferenceType.GITHUB_REPOSITORY]
    assert repo_refs and repo_refs[0].confidence < 1.0


def test_a_file_path_is_not_misdetected_as_a_repository() -> None:
    """A file path ("app/main.py") must not trip the bare owner/repo
    heuristic — repo names essentially never contain a dot, file paths
    almost always do."""
    refs = detect_references("The bug is in app/main.py around line 40")
    assert not any(r.type == ReferenceType.GITHUB_REPOSITORY for r in refs)


def test_detects_a_known_local_repository_by_name() -> None:
    refs = detect_references(
        "Fix the ingestion bug in ds-databricks-soco-gpc",
        known_repo_names=frozenset({"ds-databricks-soco-gpc"}),
    )
    local_refs = [r for r in refs if r.type == ReferenceType.LOCAL_REPOSITORY]
    assert local_refs and local_refs[0].normalized_value == "ds-databricks-soco-gpc"


def test_freeform_text_with_no_references_detects_nothing() -> None:
    assert detect_references("Fix the login issue from yesterday") == []


# ---------------------------------------------------------------------------
# Phase 4/5 — provider resolution + context normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jira_provider_resolves_a_successful_fetch() -> None:
    executor = MagicMock()
    executor.execute = AsyncMock(
        return_value=ToolResult(
            tool_id="jira",
            tool_name="Jira",
            success=True,
            data={"context_text": "Ticket body"},
            summary="Fetched NPT-6",
        )
    )
    reference = Reference(
        type=ReferenceType.JIRA_ISSUE,
        provider="jira",
        confidence=1.0,
        raw_value="NPT-6",
        normalized_value="NPT-6",
    )

    artifact = await JiraProvider(executor).resolve(reference)

    assert artifact is not None
    assert artifact.capability == ProviderCapability.ISSUE_TRACKER
    assert artifact.text == "Ticket body"
    assert artifact.evidence.kind == "tool_call"
    assert artifact.evidence.status == "success"


@pytest.mark.asyncio
async def test_jira_provider_resolves_a_failed_fetch_without_text() -> None:
    executor = MagicMock()
    executor.execute = AsyncMock(
        return_value=ToolResult(
            tool_id="jira", tool_name="Jira", success=False, error="404 not found"
        )
    )
    reference = Reference(
        type=ReferenceType.JIRA_ISSUE,
        provider="jira",
        confidence=1.0,
        raw_value="NPT-6",
        normalized_value="NPT-6",
    )

    artifact = await JiraProvider(executor).resolve(reference)

    assert artifact is not None
    assert artifact.text == ""
    # to_evidence forces failed calls to "tool_call" with a FAILED-prefixed
    # summary, never silently reporting success.
    assert artifact.evidence.kind == "tool_call"
    assert artifact.evidence.summary.startswith("FAILED")
    assert artifact.evidence.status == "failed"


@pytest.mark.asyncio
async def test_github_provider_resolves_a_successful_fetch() -> None:
    executor = MagicMock()
    executor.execute_instance = AsyncMock(
        return_value=ToolResult(
            tool_id="github",
            tool_name="GitHub",
            success=True,
            data={"context_text": "PR description"},
            summary="Fetched acme/widgets#42",
        )
    )
    reference = Reference(
        type=ReferenceType.GITHUB_PULL_REQUEST,
        provider="github",
        confidence=1.0,
        raw_value="acme/widgets#42",
        normalized_value="acme/widgets#42",
    )

    artifact = await GitHubProvider(executor, github_tool=MagicMock()).resolve(reference)

    assert artifact is not None
    assert artifact.capability == ProviderCapability.SOURCE_CONTROL
    assert artifact.text == "PR description"
    assert artifact.evidence.status == "success"


@pytest.mark.asyncio
async def test_confluence_provider_reports_unavailable_without_mcp_config() -> None:
    """Not configured at all — distinct from "searched, found nothing"
    (status="not_found", tested below): nothing was looked up because
    there's nowhere to look."""
    db = AsyncMock()
    with patch(
        "app.context_pipeline.providers.get_confluence_mcp_config", new=AsyncMock(return_value=None)
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-6", task_description="fix it", model=None, stage="planning"
        )

    assert artifact is not None
    assert artifact.text == ""
    assert artifact.evidence.status == "unavailable"


@pytest.mark.asyncio
async def test_confluence_provider_reports_not_found_when_nothing_relevant_but_turns_ran() -> None:
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.get_confluence_mcp_config",
            new=AsyncMock(return_value=("https://mcp.example", "token", "cloud-1")),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [Evidence(kind="tool_call", reference="getTeamworkGraphContext", summary="ok")],
                )
            ),
        ),
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-6", task_description="fix it", model=None, stage="planning"
        )

    assert artifact is not None
    assert artifact.text == ""
    assert artifact.evidence.status == "not_found"


@pytest.mark.asyncio
async def test_confluence_provider_reports_unavailable_when_no_turns_ran_at_all() -> None:
    """No MCP turns ran at all (LLM call itself failed, or the active
    provider doesn't support tool-calling — see gather_confluence_context's
    own docstring) — this is "couldn't complete the lookup," not "searched
    and found nothing," so it gets the same status as no-config."""
    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.get_confluence_mcp_config",
            new=AsyncMock(return_value=("https://mcp.example", "token", "cloud-1")),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(return_value=(None, [])),
        ),
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-6", task_description="fix it", model=None, stage="planning"
        )

    assert artifact is not None
    assert artifact.evidence.status == "unavailable"


@pytest.mark.asyncio
async def test_confluence_provider_normalizes_gathered_context_into_an_artifact() -> None:
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.get_confluence_mcp_config",
            new=AsyncMock(return_value=("https://mcp.example", "token", "cloud-1")),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    "Design doc content",
                    [Evidence(kind="tool_call", reference="getTeamworkGraphContext", summary="ok")],
                )
            ),
        ),
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-6", task_description="fix it", model=None, stage="planning"
        )

    assert artifact is not None
    assert artifact.capability == ProviderCapability.DOCUMENTATION
    assert artifact.text == "Design doc content"
    assert artifact.evidence.status == "success"


def test_graph_provider_derives_observations_preserving_evidence_kinds() -> None:
    """The Planning Agent's contract requires the repository lookup to be
    reportable as `tool_call` and the traversal as `graph_traversal` —
    this is what makes that possible from the single bundled
    `Neo4jGraphTool` result."""
    graph_result = ToolResult(
        tool_id="neo4j_graph",
        tool_name="Knowledge Graph (Neo4j)",
        success=True,
        data={
            "indexed_repositories": [{"id": "1", "name": "svc-a", "owner": "acme"}],
            "components": [{"name": "OrderController", "type": "Component", "repository": "svc-a"}],
            "kafka_topics": [],
            "ranked_repositories": ["svc-a"],
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "Found 1 indexed repository.",
            "_traverse_summary": "Graph traversal found 1 component.",
        },
        summary="Knowledge Graph: 1 repository, 1 component, 0 Kafka topics.",
    )

    repos_obs, traverse_obs = GraphProvider.observations_from_result(graph_result)

    assert repos_obs.succeeded is True
    assert repos_obs.data["indexed_repositories"][0]["name"] == "svc-a"
    assert traverse_obs.succeeded is True
    assert traverse_obs.data["components"][0]["name"] == "OrderController"


# ---------------------------------------------------------------------------
# Phase 6 — LLM-assisted discovery (decision only, no retrieval)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_recommends_a_capability_when_the_llm_says_so() -> None:
    raw = json.dumps(
        {
            "should_search": True,
            "capability": "issue_tracker",
            "reasoning": "No ticket or repository was named.",
        }
    )
    with patch("app.context_pipeline.discovery.invoke_llm_json", new=AsyncMock(return_value=raw)):
        recommendation = await recommend_additional_context(
            "Fix the login issue from yesterday", model=None, stage="planning"
        )

    assert recommendation is not None
    assert recommendation.should_search is True
    assert recommendation.capability == ProviderCapability.ISSUE_TRACKER


@pytest.mark.asyncio
async def test_discovery_never_calls_a_provider_itself() -> None:
    """Phase 6 must only recommend — it has no provider/executor reference
    at all, so it is structurally incapable of retrieving anything."""
    import inspect

    from app.context_pipeline import discovery

    source = inspect.getsource(discovery)
    assert "ToolExecutor" not in source
    assert ".execute(" not in source


# ---------------------------------------------------------------------------
# Phase 7 — enriched planning request creation (pipeline end to end)
# ---------------------------------------------------------------------------


def _graph_tool_result(indexed: bool = True) -> ToolResult:
    indexed_repos = [{"id": "1", "name": "svc-a", "owner": "acme"}] if indexed else []
    components = (
        [{"name": "OrderController", "type": "Component", "repository": "svc-a", "file_path": ""}]
        if indexed
        else []
    )
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Knowledge Graph (Neo4j)",
        success=True,
        data={
            "context_text": "**Relevant repositories**: svc-a",
            "indexed_repositories": indexed_repos,
            "ranked_repositories": [r["name"] for r in indexed_repos],
            "components": components,
            "kafka_topics": [],
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": f"Found {len(indexed_repos)} indexed repositories.",
            "_traverse_summary": f"Graph traversal found {len(components)} components.",
        },
        summary="Knowledge Graph summary",
        token_estimate=10,
    )


@pytest.mark.asyncio
async def test_pipeline_resolves_a_jira_reference_into_the_enriched_request() -> None:
    async def fake_execute_all(calls):
        return [_graph_tool_result()]

    with (
        patch("app.context_pipeline.pipeline.get_tool_registry", return_value=MagicMock()),
        patch(
            "app.context_pipeline.pipeline.ToolExecutor.execute",
            new=AsyncMock(
                return_value=ToolResult(
                    tool_id="jira",
                    tool_name="Jira",
                    success=True,
                    data={"context_text": "Real ticket body"},
                    summary="Fetched NPT-6",
                )
            ),
        ),
        patch(
            "app.context_pipeline.pipeline.ToolExecutor.execute_all",
            new=AsyncMock(side_effect=fake_execute_all),
        ),
        patch(
            "app.context_pipeline.providers.get_confluence_mcp_config",
            new=AsyncMock(return_value=None),
        ),
    ):
        request = await ContextResolutionPipeline().resolve(
            raw_request="Please plan NPT-6",
            db=AsyncMock(),
            graph_repo_override=None,
            user_id="user-1",
            model=None,
            extras={"user_id": "user-1"},
        )

    assert isinstance(request, EnrichedPlanningRequest)
    assert request.original_request == "Please plan NPT-6"
    assert "Real ticket body" in request.enriched_text
    assert any(r.type == ReferenceType.JIRA_ISSUE for r in request.resolved_references)
    assert request.indexed_repositories[0]["name"] == "svc-a"
    assert request.graph_has_data is True
    # tool_call (repo lookup) + graph_traversal (component traversal) +
    # tool_call (jira fetch) at minimum — the contract's evidence
    # requirement is satisfied by the pipeline alone.
    assert any(e.kind == "graph_traversal" for e in request.evidence)
    assert any(e.kind == "tool_call" for e in request.evidence)


@pytest.mark.asyncio
async def test_pipeline_detects_a_local_repository_mentioned_by_name() -> None:
    """LOCAL_REPOSITORY detection needs the indexed repository names,
    which are only known after graph retrieval — the pipeline re-checks
    for it once those names are available rather than skipping it
    entirely (previously `known_repo_names` was never supplied at all)."""

    async def fake_execute_all(calls):
        return [_graph_tool_result()]  # indexed_repositories includes "svc-a"

    with (
        patch("app.context_pipeline.pipeline.get_tool_registry", return_value=MagicMock()),
        patch(
            "app.context_pipeline.pipeline.ToolExecutor.execute_all",
            new=AsyncMock(side_effect=fake_execute_all),
        ),
    ):
        request = await ContextResolutionPipeline().resolve(
            raw_request="Fix the ingestion bug in svc-a",
            db=AsyncMock(),
            graph_repo_override=None,
            user_id="user-1",
            model=None,
            extras={},
        )

    local_refs = [
        r for r in request.resolved_references if r.type == ReferenceType.LOCAL_REPOSITORY
    ]
    assert local_refs and local_refs[0].normalized_value == "svc-a"
    assert "local_repository" in request.planning_metadata["detected_reference_types"]


@pytest.mark.asyncio
async def test_pipeline_skips_discovery_by_default_even_with_no_references() -> None:
    async def fake_execute_all(calls):
        return [_graph_tool_result(indexed=False)]

    with (
        patch("app.context_pipeline.pipeline.get_tool_registry", return_value=MagicMock()),
        patch(
            "app.context_pipeline.pipeline.ToolExecutor.execute_all",
            new=AsyncMock(side_effect=fake_execute_all),
        ),
        patch(
            "app.context_pipeline.pipeline.recommend_additional_context", new=AsyncMock()
        ) as mock_discover,
    ):
        request = await ContextResolutionPipeline().resolve(
            raw_request="Fix the login issue from yesterday",
            db=AsyncMock(),
            graph_repo_override=None,
            user_id="user-1",
            model=None,
            extras={},
        )

    mock_discover.assert_not_called()
    assert request.additional_context_recommendation is None


@pytest.mark.asyncio
async def test_pipeline_runs_discovery_when_enabled_and_no_references_found() -> None:
    from app.context_pipeline.models import AdditionalContextRecommendation

    async def fake_execute_all(calls):
        return [_graph_tool_result(indexed=False)]

    fake_settings = MagicMock(enable_context_discovery=True, github_mcp_default_server_url="x")

    with (
        patch("app.context_pipeline.pipeline.get_tool_registry", return_value=MagicMock()),
        patch(
            "app.context_pipeline.pipeline.ToolExecutor.execute_all",
            new=AsyncMock(side_effect=fake_execute_all),
        ),
        patch("app.context_pipeline.pipeline.get_settings", return_value=fake_settings),
        patch(
            "app.context_pipeline.pipeline.recommend_additional_context",
            new=AsyncMock(
                return_value=AdditionalContextRecommendation(
                    should_search=True,
                    capability=ProviderCapability.ISSUE_TRACKER,
                    reasoning="No ticket named.",
                )
            ),
        ) as mock_discover,
    ):
        request = await ContextResolutionPipeline().resolve(
            raw_request="Fix the login issue from yesterday",
            db=AsyncMock(),
            graph_repo_override=None,
            user_id="user-1",
            model=None,
            extras={},
        )

    mock_discover.assert_called_once()
    assert request.additional_context_recommendation is not None
    assert request.additional_context_recommendation.should_search is True
    assert any(e.reference == "context_discovery" for e in request.evidence)


# ---------------------------------------------------------------------------
# Phase 8 — Planning Agent operates solely on the enriched request
# ---------------------------------------------------------------------------


def _make_llm_response(steps: int = 2) -> str:
    return json.dumps(
        {
            "executive_summary": "Ship the fix.",
            "implementation_steps": [
                {
                    "order": i + 1,
                    "description": f"Step {i + 1}",
                    "affected_component": "",
                    "risk_note": "",
                }
                for i in range(steps)
            ],
            "affected_components": [],
            "kafka_topics_involved": [],
            "risk_considerations": ["Some risk"],
            "graph_context_used": True,
        }
    )


@pytest.mark.asyncio
async def test_planning_agent_operates_solely_on_enriched_input() -> None:
    """The agent must consume the pipeline's output as-is — no direct
    Jira/GitHub/graph tool dispatch of its own. Patches
    `ContextResolutionPipeline` itself (not any tool/executor seam) to
    prove the agent's entire context-gathering surface is that one call.
    """
    from app.agents._contract import Evidence
    from app.agents.planning.agent import PlanningAgent

    profile = analyse("Fix the login issue")
    fake_request = EnrichedPlanningRequest(
        original_request="Fix the login issue",
        enriched_text="Fix the login issue [[jira: NPT-6 real ticket body]]",
        resolved_references=[
            Reference(
                type=ReferenceType.JIRA_ISSUE,
                provider="jira",
                confidence=1.0,
                raw_value="NPT-6",
                normalized_value="NPT-6",
            )
        ],
        artifacts=[],
        profile=profile,
        indexed_repositories=[{"id": "1", "name": "svc-a", "owner": "acme"}],
        graph_components=[
            {"name": "LoginController", "type": "Component", "repository": "svc-a", "file_path": ""}
        ],
        graph_topics=[],
        ranked_repository_names=["svc-a"],
        graph_context_text="**Relevant repositories**: svc-a",
        graph_available=True,
        graph_has_data=True,
        additional_context_recommendation=None,
        evidence=[
            Evidence(kind="tool_call", reference="fetch_jira_issue", summary="Fetched NPT-6"),
            Evidence(
                kind="graph_traversal",
                reference="traverse_architecture_graph",
                summary="1 component",
            ),
        ],
        planning_metadata={},
    )

    subject = Subject(subject_id="s1", subject_type="freetext", display_name="Fix the login issue")
    context = AgentContext(
        subject=subject,
        goal="plan_freeform",
        extras={"db": AsyncMock(), "user_id": "user-1"},
        model=None,
    )

    with (
        patch(
            "app.agents.planning.agent.ContextResolutionPipeline.resolve",
            new=AsyncMock(return_value=fake_request),
        ) as mock_resolve,
        patch(
            "app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_make_llm_response())
        ),
    ):
        output = await PlanningAgent().run(context)

    mock_resolve.assert_called_once()
    # original_request passed through untouched — the UI's Task Description
    # field must show the literal user input, not the enriched text.
    assert output.result["task_description"] == "Fix the login issue"
    assert output.result["repositories_consulted"] == ["svc-a"]
    assert output.result["graph_context_used"] is True
    assert any(e.kind == "tool_call" for e in output.evidence)
    assert any(e.kind == "graph_traversal" for e in output.evidence)
