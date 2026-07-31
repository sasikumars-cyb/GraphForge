"""Tests for the context-acquisition boundary (app.context_pipeline).

Covers the passive parts the reasoning engine drives: deterministic reference
detection, and each provider adapter's normalization of a retrieval into a
`ResolvedArtifact` (including the unavailable/not-found distinction the
confidence signals depend on).

Also covers — in `test_planning_agent_operates_solely_on_discovered_context` —
that the Planning Agent does none of this itself and acquires context through
exactly one call into the reasoning engine.

The reasoning engine's own behavior lives in test_context_reasoning_engine.py.
There is no pipeline object left to test: `ContextResolutionPipeline`'s fixed
provider sequence was removed when reasoning took over orchestration.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.context_pipeline.models import ProviderCapability, Reference, ReferenceType
from app.context_pipeline.providers import (
    ConfluenceProvider,
    GitHubProvider,
    GraphProvider,
    JiraProvider,
)
from app.context_pipeline.reasoning.capabilities import GRAPH_TRAVERSAL_ACTION
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
async def test_github_provider_resolves_a_bare_repository_reference_via_get_repository() -> None:
    """A GITHUB_REPOSITORY reference (no #issue/#pr) has no shape execute()'s
    regex matches — this is the fix for that gap: resolve() must call
    get_repository() directly instead of routing through execute()."""
    github_tool = MagicMock()
    github_tool.get_repository = AsyncMock(
        return_value=ToolResult(
            tool_id="github",
            tool_name="GitHub",
            success=True,
            data={"context_text": "GitHub repository acme/widgets — Widget factory"},
            summary="Fetched GitHub repository acme/widgets",
        )
    )
    reference = Reference(
        type=ReferenceType.GITHUB_REPOSITORY,
        provider="github",
        confidence=0.7,
        raw_value="acme/widgets",
        normalized_value="acme/widgets",
    )

    artifact = await GitHubProvider(MagicMock(), github_tool=github_tool).resolve(reference)

    github_tool.get_repository.assert_awaited_once_with("acme", "widgets")
    assert artifact is not None
    assert artifact.text == "GitHub repository acme/widgets — Widget factory"
    assert artifact.evidence.status == "success"


@pytest.mark.asyncio
async def test_github_provider_reports_failure_for_a_malformed_repository_reference() -> None:
    github_tool = MagicMock()
    github_tool.get_repository = AsyncMock()
    reference = Reference(
        type=ReferenceType.GITHUB_REPOSITORY,
        provider="github",
        confidence=0.7,
        raw_value="not-a-valid-repo-ref",
        normalized_value="not-a-valid-repo-ref",
    )

    artifact = await GitHubProvider(MagicMock(), github_tool=github_tool).resolve(reference)

    github_tool.get_repository.assert_not_awaited()
    assert artifact is not None
    assert artifact.text == ""
    assert artifact.evidence.status == "failed"


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
async def test_planning_agent_operates_solely_on_discovered_context() -> None:
    """The agent must consume the reasoning engine's output as-is — no direct
    Jira/GitHub/graph tool dispatch of its own.

    Patches `reasoning.engine.discover` (not any tool/executor seam) to prove
    the agent's entire context-gathering surface is that one call. This is also
    the regression guard for the architecture unification: a standalone
    planning run used to acquire context through a separate fixed provider
    pipeline, so it silently got none of the reasoning-first behavior.
    """
    from app.agents.planning.agent import PlanningAgent
    from app.context_pipeline.reasoning.memory import WorkingContext

    state = WorkingContext()
    state.metadata.goal = "Fix the login issue"
    state.derived["original_request"] = "Fix the login issue"
    state.derived["enriched_text"] = "Fix the login issue [[jira: NPT-6 real ticket body]]"
    state.derived["graph_context_text"] = "**Relevant repositories**: svc-a"

    jira_ev = state.ledger.add_evidence(
        provider="jira", action="fetch_work_item:NPT-6", outcome="success", summary="Fetched NPT-6"
    )
    state.ledger.add_fact(
        kind="work_item", subject="NPT-6", provider="jira", evidence_id=jira_ev.evidence_id
    )
    state.ledger.add_evidence(
        provider="graph",
        action="survey_architecture",
        outcome="success",
        summary="Looked up indexed repositories: 1 found.",
    )
    # The traversal record is what earns `kind="graph_traversal"` in the
    # contract projection, and what the `architecture` capability reads for
    # reachability. The repository lookup above reads Postgres and must not
    # count as either.
    graph_ev = state.ledger.add_evidence(
        provider="graph",
        action=GRAPH_TRAVERSAL_ACTION,
        outcome="success",
        summary="Traversed the architecture graph: 1 component(s).",
    )
    repo = state.ledger.add_fact(
        kind="repository",
        subject="svc-a",
        provider="graph",
        evidence_id=graph_ev.evidence_id,
        value={"id": "1", "name": "svc-a", "owner": "acme"},
    )
    state.ledger.add_fact(
        kind="component",
        subject="LoginController",
        provider="graph",
        evidence_id=graph_ev.evidence_id,
        value={
            "name": "LoginController",
            "type": "Component",
            "repository": "svc-a",
            "file_path": "",
        },
    )
    state.ledger.add_inference(
        kind="repository_candidate", statement="svc-a", supporting_fact_ids=[repo.fact_id]
    )
    state.refresh_assessments()

    subject = Subject(subject_id="s1", subject_type="freetext", display_name="Fix the login issue")
    context = AgentContext(
        subject=subject,
        goal="plan_freeform",
        extras={"db": AsyncMock(), "user_id": "user-1"},
        model=None,
    )

    with (
        patch(
            "app.agents.planning.agent.discover", new=AsyncMock(return_value=state)
        ) as mock_discover,
        patch(
            "app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_make_llm_response())
        ),
    ):
        output = await PlanningAgent().run(context)

    mock_discover.assert_awaited_once()
    # original_request passed through untouched — the UI's Task Description
    # field must show the literal user input, not the enriched text.
    assert output.result["task_description"] == "Fix the login issue"
    assert output.result["repositories_consulted"] == ["svc-a"]
    assert output.result["graph_context_used"] is True
    assert any(e.kind == "tool_call" for e in output.evidence)
    assert any(e.kind == "graph_traversal" for e in output.evidence)
