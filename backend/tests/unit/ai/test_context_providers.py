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
from app.context_pipeline.reasoning import capabilities
from app.context_pipeline.reasoning.capabilities import GRAPH_TRAVERSAL_ACTION
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.investigators import ConfluenceInvestigator
from app.context_pipeline.reasoning.ledger import Ledger
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


def test_detects_a_confluence_page_url_in_a_personal_space() -> None:
    """Regression (KAN-46): a personal space is keyed "~<accountId>" — the
    leading tilde isn't a `\\w` character, so this silently matched nothing
    at all before the fix, indistinguishable from "no Confluence link in
    this text." Every Atlassian user has exactly one personal space
    (auto-created), so this is not an edge case in practice."""
    refs = detect_references(
        "https://myorg.atlassian.net/wiki/spaces/~712020a3f675039f424618b2e694673681f285"
        "/pages/1802253/ETL+Core+Engineering+Context+amp+Documentation"
    )
    confluence_refs = [r for r in refs if r.type == ReferenceType.CONFLUENCE_PAGE]
    assert confluence_refs and confluence_refs[0].normalized_value == "1802253"


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


def _access(methods=(), config=None, connection_id=None):
    from app.knowledge.access_resolver import ResolvedKnowledgeAccess

    return ResolvedKnowledgeAccess(
        source_type="confluence",
        capability=ProviderCapability.DOCUMENTATION,
        methods=tuple(methods),
        config=config or {},
        connection_id=connection_id,
    )


def _mcp_method(server_url="https://mcp.example", api_key="token"):
    from app.knowledge.access_resolver import AccessMethod
    from app.knowledge.registry import Transport

    return AccessMethod(
        transport=Transport.MCP, fields={"server_url": server_url, "api_key": api_key}
    )


def _rest_method(base_url="https://example.atlassian.net", email="a@b.com", api_token="token"):
    from app.knowledge.access_resolver import AccessMethod
    from app.knowledge.registry import Transport

    return AccessMethod(
        transport=Transport.REST,
        fields={"base_url": base_url, "email": email, "api_token": api_token},
    )


@pytest.mark.asyncio
async def test_confluence_provider_reports_unavailable_without_mcp_config() -> None:
    """Not configured at all — distinct from "searched, found nothing"
    (status="not_found", tested below): nothing was looked up because
    there's nowhere to look."""
    db = AsyncMock()
    with patch(
        "app.context_pipeline.providers.resolve_knowledge_access",
        new=AsyncMock(return_value=_access()),
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
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(methods=[_mcp_method()], config={"base_url": "cloud-1"})
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="ok",
                            status="success",
                        )
                    ],
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
async def test_confluence_provider_falls_back_to_rest_when_mcp_is_unavailable_and_rest_finds_something() -> (  # noqa: E501
    None
):
    """The actual bug this is a regression test for: Atlassian's MCP
    server rejects the auto-wired API-token bearer with a permission
    error (org-level "API token access" toggle) — REST search, a
    completely different Atlassian API not gated by that toggle, still
    finds the page. Mirrors JiraTool's own MCP-then-REST fallback."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()], config={"base_url": "cloud-1"}
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="You don't have permission to connect via API token.",
                            status="failed",
                        )
                    ],
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.search_confluence_rest",
            new=AsyncMock(
                return_value=(
                    "NPT-30 rollback plan\nHow to roll back the SCD2 merge change",
                    [
                        Evidence(
                            kind="tool_call",
                            reference="confluence_rest_search",
                            summary="Searched Confluence for pages mentioning NPT-30 (1 result).",
                            status="success",
                        )
                    ],
                )
            ),
        ) as rest_search,
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    rest_search.assert_awaited_once()
    call_kwargs = rest_search.call_args.kwargs
    assert call_kwargs["base_url"] == "https://example.atlassian.net"
    assert call_kwargs["jira_issue_key"] == "NPT-30"

    assert artifact is not None
    assert artifact.evidence.status == "success"
    assert "NPT-30 rollback plan" in artifact.text


@pytest.mark.asyncio
async def test_confluence_provider_stays_unavailable_when_both_mcp_and_rest_fail() -> None:
    """REST is attempted but also finds nothing/fails — the honest
    diagnosis (MCP's own permission error, the actionable one an operator
    can fix) still surfaces, not a generic message implying nothing was
    ever tried."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()], config={"base_url": "cloud-1"}
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="permission denied",
                            status="failed",
                        )
                    ],
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.search_confluence_rest",
            new=AsyncMock(return_value=(None, [])),
        ),
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    assert artifact is not None
    assert artifact.text == ""
    assert artifact.evidence.status == "unavailable"
    assert "permission denied" in artifact.evidence.summary


@pytest.mark.asyncio
async def test_confluence_provider_reports_not_found_when_rest_fallback_searches_but_finds_nothing() -> (  # noqa: E501
    None
):
    """REST genuinely ran (no error) but turned up no pages — distinct
    from "both attempts were blocked": this is "we looked everywhere and
    there's really nothing," which is `not_found`, not `unavailable`."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()], config={"base_url": "cloud-1"}
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="permission denied",
                            status="failed",
                        )
                    ],
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.search_confluence_rest",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="confluence_rest_search",
                            summary="Searched Confluence for pages mentioning NPT-30 (0 results).",
                            status="success",
                        )
                    ],
                )
            ),
        ),
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    assert artifact is not None
    assert artifact.evidence.status == "not_found"


# ---------------------------------------------------------------------------
# Investigation Intelligence wiring (ADR 0021's own worked example) —
# `recent_repeated_failure()` skipping a doomed MCP attempt in favor of REST
# ---------------------------------------------------------------------------


def _intelligence(recent_failure=None):
    """A minimal stand-in for `InvestigationIntelligenceService` — only
    `recent_repeated_failure` is ever read by `ConfluenceProvider`, so
    nothing else needs a real implementation. `recent_failure` is whatever
    non-`None`/`None` value the read should return; the provider only
    checks identity against `None`, never inspects the event itself."""
    service = MagicMock()
    service.recent_repeated_failure = AsyncMock(return_value=recent_failure)
    return service


@pytest.mark.asyncio
async def test_skips_mcp_entirely_when_a_recent_failure_is_known_and_rest_succeeds() -> None:
    """The ADR's own worked example: a recent failure for this exact
    connection means REST is tried first: if it succeeds, the expensive
    multi-turn MCP conversation is never even attempted."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    intelligence = _intelligence(recent_failure=object())
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()],
                    config={"base_url": "cloud-1"},
                    connection_id="conn-1",
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context", new=AsyncMock()
        ) as gather_mcp,
        patch(
            "app.context_pipeline.providers.search_confluence_rest",
            new=AsyncMock(
                return_value=(
                    "NPT-30 rollback plan",
                    [
                        Evidence(
                            kind="tool_call",
                            reference="confluence_rest_search",
                            summary="Searched Confluence for pages mentioning NPT-30 (1 result).",
                            status="success",
                        )
                    ],
                )
            ),
        ) as rest_search,
    ):
        artifact = await ConfluenceProvider(db, intelligence).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    intelligence.recent_repeated_failure.assert_awaited_once()
    call_kwargs = intelligence.recent_repeated_failure.call_args.kwargs
    assert call_kwargs["scope"].scope_type == "knowledge_connection"
    assert call_kwargs["scope"].scope_id == "conn-1"
    assert call_kwargs["provider"] == "confluence"
    assert call_kwargs["capability"] == "documentation"

    rest_search.assert_awaited_once()
    gather_mcp.assert_not_awaited()

    assert artifact is not None
    assert artifact.evidence.status == "success"
    assert "NPT-30 rollback plan" in artifact.text


@pytest.mark.asyncio
async def test_falls_through_to_mcp_when_prefetched_rest_also_finds_nothing() -> None:
    """A recent failure never becomes a permanent block: if the early REST
    attempt also comes up empty, MCP still runs — and the later
    MCP-failed-so-fall-back-to-REST branch reuses that same result instead
    of calling `search_confluence_rest` a second time for the same issue."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    intelligence = _intelligence(recent_failure=object())
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()],
                    config={"base_url": "cloud-1"},
                    connection_id="conn-1",
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="permission denied",
                            status="failed",
                        )
                    ],
                )
            ),
        ) as gather_mcp,
        patch(
            "app.context_pipeline.providers.search_confluence_rest",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="confluence_rest_search",
                            summary="Searched Confluence for pages mentioning NPT-30 (0 results).",
                            status="success",
                        )
                    ],
                )
            ),
        ) as rest_search,
    ):
        artifact = await ConfluenceProvider(db, intelligence).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    gather_mcp.assert_awaited_once()
    rest_search.assert_awaited_once()  # not twice — the prefetched result is reused

    # REST's own search call succeeded (it just found 0 pages) — the same
    # "we looked everywhere, there's really nothing" outcome
    # `test_confluence_provider_reports_not_found_when_rest_fallback_
    # searches_but_finds_nothing` already establishes for the non-prefetch
    # path, reached here via the reused prefetched result instead.
    assert artifact is not None
    assert artifact.evidence.status == "not_found"


@pytest.mark.asyncio
async def test_attempts_mcp_normally_when_no_recent_failure_is_known() -> None:
    """`recent_repeated_failure` returning `None` (the common case — no
    known recent problem) must not change anything: MCP runs first, exactly
    as it did before Investigation Intelligence existed."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    intelligence = _intelligence(recent_failure=None)
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()],
                    config={"base_url": "cloud-1"},
                    connection_id="conn-1",
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    "NPT-30 design doc",
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="ok",
                            status="success",
                        )
                    ],
                )
            ),
        ) as gather_mcp,
        patch(
            "app.context_pipeline.providers.search_confluence_rest", new=AsyncMock()
        ) as rest_search,
    ):
        artifact = await ConfluenceProvider(db, intelligence).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    gather_mcp.assert_awaited_once()
    rest_search.assert_not_awaited()
    assert artifact is not None
    assert artifact.evidence.status == "success"


@pytest.mark.asyncio
async def test_no_intelligence_configured_behaves_exactly_as_before() -> None:
    """`intelligence=None` (the default, and every call site until
    `ConfluenceInvestigator` was updated) must never call
    `recent_repeated_failure` at all — the pre-Investigation-Intelligence
    behavior stays byte-for-byte the same when the service isn't wired
    in."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()],
                    config={"base_url": "cloud-1"},
                    connection_id="conn-1",
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    "NPT-30 design doc",
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="ok",
                            status="success",
                        )
                    ],
                )
            ),
        ) as gather_mcp,
        patch(
            "app.context_pipeline.providers.search_confluence_rest", new=AsyncMock()
        ) as rest_search,
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    gather_mcp.assert_awaited_once()
    rest_search.assert_not_awaited()
    assert artifact is not None
    assert artifact.evidence.status == "success"


@pytest.mark.asyncio
async def test_confluence_provider_does_not_attempt_rest_when_mcp_genuinely_found_nothing() -> None:
    """`not_found` (MCP's graph traversal ran and turned up nothing linked
    to the issue) doesn't trigger the REST fallback — REST's unrelated
    free-text search wouldn't be answering the same question by retrying,
    so this stays a single attempt, matching the pre-fallback behavior."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(
                    methods=[_mcp_method(), _rest_method()], config={"base_url": "cloud-1"}
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="ok",
                            status="success",
                        )
                    ],
                )
            ),
        ),
        patch(
            "app.context_pipeline.providers.search_confluence_rest", new=AsyncMock()
        ) as rest_search,
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-30", task_description="fix it", model=None, stage="planning"
        )

    rest_search.assert_not_awaited()
    assert artifact is not None
    assert artifact.evidence.status == "not_found"


@pytest.mark.asyncio
async def test_confluence_provider_reports_unavailable_when_every_tool_call_failed() -> None:
    """A tool call that errors (e.g. the Confluence API token lacks
    Teamwork Graph permission) is an access/infra gap, not a real search
    that came up empty — regression for the bug where an LLM synthesized
    an answer from ticket text alone after every tool call failed, and
    that got recorded as if real documentation had been found."""
    from app.agents._contract import Evidence

    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(methods=[_mcp_method()], config={"base_url": "cloud-1"})
            ),
        ),
        patch(
            "app.context_pipeline.providers.gather_confluence_context",
            new=AsyncMock(
                return_value=(
                    None,
                    [
                        Evidence(
                            kind="tool_call",
                            reference="getTeamworkGraphContext",
                            summary="Confluence graph call failed: permission denied",
                            status="failed",
                        )
                    ],
                )
            ),
        ),
    ):
        artifact = await ConfluenceProvider(db).resolve_for_issue(
            jira_issue_key="NPT-6", task_description="fix it", model=None, stage="planning"
        )

    assert artifact is not None
    assert artifact.text == ""
    assert artifact.evidence.status == "unavailable"
    # Regression for the misleading-message bug: this is a connected
    # source where every attempted call failed, not "not connected" — the
    # summary must say so, with the real per-call failure detail, not a
    # generic "Confluence is not connected" (which sends an operator to
    # re-connect a source that was never disconnected).
    assert "not connected" not in artifact.evidence.summary.lower()
    assert "permission denied" in artifact.evidence.summary


@pytest.mark.asyncio
async def test_confluence_provider_reports_unavailable_when_no_turns_ran_at_all() -> None:
    """No MCP turns ran at all (LLM call itself failed, or the active
    provider doesn't support tool-calling — see gather_confluence_context's
    own docstring) — this is "couldn't complete the lookup," not "searched
    and found nothing," so it gets the same status as no-config."""
    db = AsyncMock()
    with (
        patch(
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(methods=[_mcp_method()], config={"base_url": "cloud-1"})
            ),
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
            "app.context_pipeline.providers.resolve_knowledge_access",
            new=AsyncMock(
                return_value=_access(methods=[_mcp_method()], config={"base_url": "cloud-1"})
            ),
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


@pytest.mark.asyncio
async def test_gather_confluence_context_discards_a_synthesized_answer_after_every_tool_call_fails() -> (  # noqa: E501
    None
):
    """Regression: an Atlassian API token without Teamwork Graph permission
    makes every `getTeamworkGraphContext` call fail. The LLM was observed
    (against its own system prompt, which only covers "nothing relevant
    found") synthesizing a plausible-sounding answer from the ticket text
    alone rather than saying exactly "No relevant Confluence content
    found". Trusting that text would silently record a fabrication as if
    it were real retrieved documentation (status="success", full
    confidence) instead of the access failure it actually was. This must
    return no text — regardless of what the model says — whenever no tool
    call in the conversation ever actually succeeded.
    """
    from app.agents.planning import confluence_context as ctx_module
    from app.ai.providers.base import ToolTurnResult, ToolUseRequest
    from app.tools.mcp_support import MCPToolError

    turn_1 = ToolTurnResult(
        content_blocks=[{"text": "Let me check Confluence."}],
        tool_uses=[
            ToolUseRequest(
                id="call_1",
                name="getTeamworkGraphContext",
                input={"objectType": "JiraWorkItem", "objectIdentifier": "NPT-30"},
            )
        ],
        text="",
    )
    turn_2 = ToolTurnResult(
        content_blocks=[{"text": "synthesized"}],
        tool_uses=[],
        text=(
            "I'm unable to access the Confluence/Jira knowledge graph due to API "
            "permissions. However, based on the ticket, here is my own analysis..."
        ),
    )

    mock_provider = MagicMock()
    mock_provider.preview = MagicMock()
    mock_provider.complete_with_tools = AsyncMock(side_effect=[turn_1, turn_2])

    with (
        patch.object(ctx_module, "StageAwareLLMProvider", return_value=mock_provider),
        patch.object(
            ctx_module,
            "call_mcp_tool",
            new=AsyncMock(
                side_effect=MCPToolError("You don't have permission to connect via API token.")
            ),
        ),
    ):
        text, evidence = await ctx_module.gather_confluence_context(
            mcp_server_url="https://mcp.example",
            mcp_auth_token="token",
            cloud_id="cloud-1",
            jira_issue_key="NPT-30",
            task_description="fix it",
            model=None,
            stage="planning",
        )

    assert text is None, "a synthesized answer with no successful tool call must not be trusted"
    assert evidence and evidence[0].status == "failed"


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


# ---------------------------------------------------------------------------
# documentation capability: "reachable" signal / remediation must not claim
# "Confluence is not connected" when it demonstrably is (see providers.py's
# ConfluenceProvider.resolve_for_issue and investigators.py's
# ConfluenceInvestigator.run for the two other halves of this fix).
# ---------------------------------------------------------------------------


def test_documentation_signal_reports_connected_but_failed_reason() -> None:
    ledger = Ledger()
    ledger.add_evidence(
        provider="confluence",
        action="fetch_documentation:NPT-6",
        outcome="unavailable",
        summary=(
            "Confluence lookup could not be completed: Confluence graph call "
            "failed: permission denied"
        ),
    )

    signals = capabilities.BY_KEY["documentation"].signals(ledger)
    reachable_signal = next(s for s in signals if s.label == "Documentation source reachable")

    assert reachable_signal.satisfied is False
    assert "not connected" not in reachable_signal.detail.lower()
    assert "permission denied" in reachable_signal.detail


def test_documentation_signal_reports_not_connected_when_genuinely_unconfigured() -> None:
    ledger = Ledger()
    ledger.add_evidence(
        provider="confluence",
        action="fetch_documentation:NPT-6",
        outcome="unavailable",
        summary="Confluence is not connected.",
    )

    signals = capabilities.BY_KEY["documentation"].signals(ledger)
    reachable_signal = next(s for s in signals if s.label == "Documentation source reachable")

    assert "Confluence is not connected" in reachable_signal.detail


def test_documentation_signal_generic_detail_when_never_attempted() -> None:
    signals = capabilities.BY_KEY["documentation"].signals(Ledger())
    reachable_signal = next(s for s in signals if s.label == "Documentation source reachable")

    assert reachable_signal.detail == "Confluence is not connected"


def test_documentation_remediation_does_not_say_connect_when_already_connected() -> None:
    ledger = Ledger()
    ledger.add_evidence(
        provider="confluence",
        action="fetch_documentation:NPT-6",
        outcome="unavailable",
        summary="Confluence lookup could not be completed: permission denied",
    )

    remediation = capabilities.BY_KEY["documentation"].remediation(ledger)

    assert not any("Connect Confluence" in step for step in remediation)
    assert any("Teamwork Graph" in step for step in remediation)


def test_documentation_remediation_says_connect_when_genuinely_unconfigured() -> None:
    remediation = capabilities.BY_KEY["documentation"].remediation(Ledger())

    assert remediation == ["Connect Confluence", "Link a design page to the ticket"]


@pytest.mark.asyncio
async def test_confluence_investigator_surfaces_the_providers_own_summary_not_a_hardcoded_one() -> (
    None
):
    """Regression: ConfluenceInvestigator.run used to discard
    ConfluenceProvider's own (already-accurate) evidence.summary and
    replace it with a hardcoded "Confluence is not connected" string for
    every non-"not_found" outcome — even when the connection was live and
    the real cause was, e.g., an Atlassian permission error."""
    from app.agents._contract import Evidence as ContractEvidence
    from app.context_pipeline.models import ResolvedArtifact

    ledger = Ledger()
    action = InvestigationAction(
        provider="confluence",
        key="fetch_documentation:NPT-6",
        intent="looking for design docs",
        targets="documentation",
        params={"work_item": "NPT-6", "task_description": "fix it"},
    )
    recorder = Recorder(ledger, action, iteration=1)
    session = SessionContext(db=AsyncMock(), user_id=None)

    artifact = ResolvedArtifact(
        provider="confluence",
        capability=ProviderCapability.DOCUMENTATION,
        reference=None,
        title="Confluence",
        text="",
        evidence=ContractEvidence(
            kind="tool_call",
            reference="confluence_context",
            summary=(
                "Confluence lookup could not be completed: Confluence graph call "
                "failed: permission denied"
            ),
            status="unavailable",
        ),
        raw={},
    )

    with patch(
        "app.context_pipeline.reasoning.investigators.ConfluenceProvider"
    ) as mock_provider_cls:
        mock_provider_cls.return_value.resolve_for_issue = AsyncMock(return_value=artifact)
        outcome = await ConfluenceInvestigator().run(action, session, recorder)

    assert "permission denied" in outcome.observation
    assert "not connected" not in outcome.observation.lower()
