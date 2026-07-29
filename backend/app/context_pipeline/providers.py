"""Provider capability adapters.

Each class here declares a `capability` and resolves a `Reference` (or,
for the graph provider, an ambient retrieval need) into zero or one
`ResolvedArtifact`. None of them talk to a network/MCP server directly —
every one delegates to the exact transport code that already existed
(`ToolExecutor`, `GitHubTool`, `gather_confluence_context`, the graph
tools in `app.agents.planning.tools`) so MCP/REST fallback behavior is
unchanged (see each provider's docstring for which existing call it
wraps).

Adding a new provider (Linear, ServiceNow, Notion, ...) means adding one
more class here with a `capability` and a `resolve` method, and
registering it in `pipeline.py` — nothing else in this package, and
nothing in the Planning Agent, needs to change.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.planning.confluence_context import (
    gather_confluence_context,
    get_confluence_mcp_config,
)
from app.agents.planning.tools import PlanningObservation, to_evidence
from app.agents.prompt_utils import wrap_untrusted_content
from app.context_pipeline.models import (
    ProviderCapability,
    Reference,
    ReferenceType,
    ResolvedArtifact,
)
from app.core.redact import redact_secrets
from app.tools import ToolExecutor, ToolInput
from app.tools.implementations.github_tool import GitHubTool
from app.tools.interfaces import ToolResult

logger = logging.getLogger(__name__)


class ReferenceProvider(Protocol):
    """A provider that resolves one specific reference type."""

    capability: ProviderCapability

    def can_resolve(self, reference: Reference) -> bool: ...

    async def resolve(self, reference: Reference, **kwargs: Any) -> ResolvedArtifact | None: ...


class JiraProvider:
    """Issue Tracker capability, backed by the existing `jira` Tool Registry
    entry (REST or MCP, whichever transport that entry is configured
    for — see `JiraTool`'s own docstring). Unchanged transport, just
    called from here instead of directly from the Planning Agent.
    """

    capability = ProviderCapability.ISSUE_TRACKER

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    def can_resolve(self, reference: Reference) -> bool:
        return reference.type == ReferenceType.JIRA_ISSUE

    async def resolve(self, reference: Reference, **kwargs: Any) -> ResolvedArtifact | None:
        result = await self._executor.execute("jira", ToolInput(query=reference.raw_value))
        observation = PlanningObservation(
            tool_name="fetch_jira_issue",
            summary=result.summary or (result.error or ""),
            data=result.data,
            succeeded=result.success,
            error=result.error or "",
        )
        evidence = to_evidence(observation, "tool_call")
        if not result.success:
            logger.info(
                "context_pipeline_jira_fetch_failed issue_key=%s error=%s",
                reference.normalized_value,
                result.error,
            )
            return ResolvedArtifact(
                provider="jira",
                capability=self.capability,
                reference=reference,
                title=f"Jira {reference.normalized_value}",
                text="",
                evidence=evidence,
                raw=result.data,
            )
        text = redact_secrets(result.data.get("context_text", ""))
        logger.info("context_pipeline_jira_enriched issue_key=%s", reference.normalized_value)
        return ResolvedArtifact(
            provider="jira",
            capability=self.capability,
            reference=reference,
            title=f"Jira {reference.normalized_value}",
            text=text,
            evidence=evidence,
            raw=result.data,
        )


class ConfluenceProvider:
    """Documentation capability. Anchored on a Jira issue key — Atlassian's
    MCP server has no free-text search, only graph traversal from a known
    entity (see `gather_confluence_context`'s module docstring) — so this
    only resolves when a Jira reference was also detected in the same
    request, exactly as the Planning Agent's inline version did.
    """

    capability = ProviderCapability.DOCUMENTATION

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def can_resolve(self, reference: Reference) -> bool:
        return reference.type == ReferenceType.CONFLUENCE_PAGE

    async def resolve_for_issue(
        self,
        *,
        jira_issue_key: str,
        task_description: str,
        model: str | None,
        stage: str,
    ) -> ResolvedArtifact | None:
        """Confluence retrieval is anchor-driven, not reference-driven: it
        runs whenever a Jira issue was resolved, regardless of whether a
        Confluence URL/reference was itself detected (this mirrors the
        Planning Agent's original behavior — Confluence enrichment always
        followed a successful Jira fetch)."""
        mcp_config = await get_confluence_mcp_config(self._db)
        if mcp_config is None:
            return None
        server_url, auth_token, cloud_id = mcp_config
        text, evidence_entries = await gather_confluence_context(
            mcp_server_url=server_url,
            mcp_auth_token=auth_token,
            cloud_id=cloud_id,
            jira_issue_key=jira_issue_key,
            task_description=task_description,
            model=model,
            stage=stage,
        )
        if not text:
            return ResolvedArtifact(
                provider="confluence",
                capability=self.capability,
                reference=None,
                title="Confluence",
                text="",
                evidence=Evidence(
                    kind="tool_call",
                    reference="confluence_context",
                    summary="No relevant Confluence content found.",
                ),
                raw={},
            )
        logger.info("context_pipeline_confluence_enriched issue_key=%s", jira_issue_key)
        # gather_confluence_context already returns its own Evidence list
        # (one entry per MCP turn) — folded into a single artifact whose
        # `evidence` is the last (summary) entry; the rest are appended by
        # the pipeline directly (see pipeline.py) so none of that detail is
        # lost.
        primary_evidence = evidence_entries[-1] if evidence_entries else Evidence(
            kind="tool_call", reference="confluence_context", summary="Confluence context gathered."
        )
        return ResolvedArtifact(
            provider="confluence",
            capability=self.capability,
            reference=None,
            title="Confluence",
            text=redact_secrets(text),
            evidence=primary_evidence,
            raw={"extra_evidence": evidence_entries[:-1] if evidence_entries else []},
        )


class GitHubProvider:
    """Source Control capability. GitHub access is per-user (an OAuth
    connection), so — same as before this move — a fresh `GitHubTool`
    instance is constructed per run using that run's own decrypted
    token, never the shared Tool Registry singleton.
    """

    capability = ProviderCapability.SOURCE_CONTROL

    def __init__(self, executor: ToolExecutor, github_tool: GitHubTool) -> None:
        self._executor = executor
        self._github_tool = github_tool

    def can_resolve(self, reference: Reference) -> bool:
        return reference.type in (
            ReferenceType.GITHUB_PULL_REQUEST,
            ReferenceType.GITHUB_ISSUE,
            ReferenceType.GITHUB_REPOSITORY,
        )

    async def resolve(self, reference: Reference, **kwargs: Any) -> ResolvedArtifact | None:
        result = await self._executor.execute_instance(
            self._github_tool, "github", "GitHub", ToolInput(query=reference.raw_value)
        )
        observation = PlanningObservation(
            tool_name="fetch_github_reference",
            summary=result.summary or (result.error or ""),
            data=result.data,
            succeeded=result.success,
            error=result.error or "",
        )
        evidence = to_evidence(observation, "tool_call")
        if not result.success:
            logger.info(
                "context_pipeline_github_fetch_failed ref=%s error=%s",
                reference.normalized_value,
                result.error,
            )
            return ResolvedArtifact(
                provider="github",
                capability=self.capability,
                reference=reference,
                title=f"GitHub {reference.normalized_value}",
                text="",
                evidence=evidence,
                raw=result.data,
            )
        text = redact_secrets(result.data.get("context_text", ""))
        logger.info("context_pipeline_github_enriched ref=%s", reference.normalized_value)
        return ResolvedArtifact(
            provider="github",
            capability=self.capability,
            reference=reference,
            title=f"GitHub {reference.normalized_value}",
            text=text,
            evidence=evidence,
            raw=result.data,
        )


class GraphProvider:
    """Repository Metadata + Knowledge Graph capability. Always retrieves
    (it's not reference-triggered — every planning run has always
    consulted the graph, regardless of whether the prompt names a
    specific repository).

    Delegates to the single registered `"neo4j_graph"` Tool Registry
    entry (`Neo4jGraphTool`), which already wraps repository lookup +
    component/topic traversal + ranking in one call — this deliberately
    does NOT re-instantiate `GetIndexedRepositoriesTool`/
    `TraverseArchitectureGraphTool` itself, which would run the same
    Neo4j traversal twice per planning run for no benefit (see
    `Neo4jGraphTool.execute`, which already contains that exact
    combination).
    """

    capability = ProviderCapability.GRAPH

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    async def retrieve(self, tool_input: ToolInput) -> ToolResult:
        results = await self._executor.execute_all([("neo4j_graph", tool_input)])
        return results[0]

    @staticmethod
    def observations_from_result(
        graph_result: ToolResult,
    ) -> tuple[PlanningObservation, PlanningObservation]:
        """Re-derive the individual repos/traverse observations the tool
        bundled together, preserving the "tool_call" / "graph_traversal"
        Evidence kinds the contract requires — identical to what
        `PlanningAgent.run` used to do with this same tool's output."""
        repos_obs = PlanningObservation(
            tool_name="get_indexed_repositories",
            summary=graph_result.data.get("_repos_summary", graph_result.summary),
            data={"indexed_repositories": graph_result.data.get("indexed_repositories", [])},
            succeeded=graph_result.data.get("_repos_succeeded", graph_result.success),
            error=graph_result.error or "",
        )
        traverse_obs = PlanningObservation(
            tool_name="traverse_architecture_graph",
            summary=graph_result.data.get("_traverse_summary", graph_result.summary),
            data={
                "components": graph_result.data.get("components", []),
                "kafka_topics": graph_result.data.get("kafka_topics", []),
            },
            succeeded=graph_result.data.get("_traverse_succeeded", graph_result.success),
            error=graph_result.error or "",
        )
        return repos_obs, traverse_obs


def wrap_artifact_text(provider: str, text: str) -> str:
    """Same untrusted-content wrapping every provider-sourced text got
    before this move — kept as one function so every provider (present
    and future) applies it identically rather than reimplementing the
    wrapper string."""
    return wrap_untrusted_content(provider, text)
