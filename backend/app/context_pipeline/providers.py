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
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.planning.confluence_context import gather_confluence_context
from app.agents.planning.confluence_rest_search import search_confluence_rest
from app.agents.planning.tools import PlanningObservation, to_evidence
from app.agents.prompt_utils import wrap_untrusted_content
from app.agents.text_relevance import relevance, term_weights
from app.context_pipeline.models import (
    ProviderCapability,
    Reference,
    ReferenceType,
    ResolvedArtifact,
)
from app.core.redact import redact_secrets
from app.graph.test_case_repository import ITestCaseGraphRepository
from app.knowledge.access_resolver import resolve_knowledge_access
from app.knowledge.registry import Transport
from app.tools import ToolExecutor, ToolInput
from app.tools.implementations.github_tool import GitHubTool
from app.tools.implementations.google_drive_tool import GoogleDriveTool
from app.tools.interfaces import ToolResult

logger = logging.getLogger(__name__)


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
        access = await resolve_knowledge_access(self._db, "confluence", self.capability)
        method = access.preferred()
        if method is None:
            # Confluence has no currently-available way to reach the
            # DOCUMENTATION capability (no connection, or one exists but
            # can't provide MCP access even via auto-wire — see
            # `access_resolver.derive_access_methods`) — distinct from "we
            # looked and found nothing" (status="not_found" below): nothing
            # was looked up here because there's nowhere to look.
            return ResolvedArtifact(
                provider="confluence",
                capability=self.capability,
                reference=None,
                title="Confluence",
                text="",
                evidence=Evidence(
                    kind="tool_call",
                    reference="confluence_context",
                    summary="Confluence is not connected.",
                    status="unavailable",
                ),
                raw={},
            )
        server_url = method.fields["server_url"]
        auth_token = method.fields.get("api_key", "")
        # cloudId: Atlassian's own tool schema documents it as accepting "a
        # UUID or site URL" directly — the connection's own `base_url` is
        # exactly that, when present; falling back to the MCP server_url
        # itself only matters for the rare case of a connection configured
        # for MCP directly with no REST-shaped base_url ever recorded.
        cloud_id = access.config.get("base_url") or server_url
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
            # `evidence_entries` is only populated once at least one MCP
            # turn actually ran (see gather_confluence_context) — so its
            # presence is what distinguishes "reached Confluence, found
            # nothing relevant" from "never got to search at all" (the
            # LLM call itself failing, or the provider not supporting
            # tool-calling — see that function's own docstring), without
            # parsing anything provider-specific out of free text.
            #
            # Within "reached Confluence", every tool call recorded may
            # still have failed (e.g. the API token lacks Teamwork Graph
            # permission — an access/infra gap, not a real search that
            # came up empty). Treat that the same as "unavailable" so it's
            # retried once the access issue is fixed (see
            # ConfluenceInvestigator.propose's retry-on-"unavailable"
            # logic), rather than being permanently written off as
            # "not_found".
            any_call_succeeded = any(e.status == "success" for e in evidence_entries)
            status = "not_found" if any_call_succeeded else "unavailable"

            # MCP is unreachable (not just "searched and found nothing") —
            # the same shape of failure JiraTool.execute() already
            # survives by falling back to REST, which sits behind a
            # different Atlassian API entirely (plain Confluence REST, not
            # the MCP server) and so isn't affected by whatever blocked
            # MCP (most commonly the org-level "API token access" toggle —
            # see confluence_rest_search's module docstring). Only
            # attempted here, not on "not_found": a "not_found" MCP result
            # means the graph traversal genuinely ran and turned up
            # nothing linked to the issue, which REST's unrelated
            # free-text search wouldn't be answering the same question by
            # retrying.
            if status == "unavailable":
                rest_method = next(
                    (m for m in access.methods if m.transport == Transport.REST), None
                )
                if rest_method is not None:
                    rest_text, rest_evidence = await search_confluence_rest(
                        base_url=rest_method.fields.get("base_url", ""),
                        email=rest_method.fields.get("email", ""),
                        api_token=rest_method.fields.get("api_token", ""),
                        jira_issue_key=jira_issue_key,
                        task_description=task_description,
                    )
                    evidence_entries = [*evidence_entries, *rest_evidence]
                    if rest_text:
                        logger.info(
                            "context_pipeline_confluence_enriched_via_rest_fallback "
                            "issue_key=%s",
                            jira_issue_key,
                        )
                        primary_evidence = rest_evidence[-1].model_copy(
                            update={"status": "success"}
                        )
                        return ResolvedArtifact(
                            provider="confluence",
                            capability=self.capability,
                            reference=None,
                            title="Confluence",
                            text=redact_secrets(rest_text),
                            evidence=primary_evidence,
                            raw={"extra_evidence": evidence_entries[:-1]},
                        )
                    # REST also came up empty/failed — MCP's own diagnosis
                    # ("ask your org admin") stays the actionable summary
                    # below; the message just stops implying nothing was
                    # attempted beyond MCP.
                    any_rest_call_succeeded = any(e.status == "success" for e in rest_evidence)
                    status = "not_found" if any_rest_call_succeeded else status

            if status == "not_found":
                summary = "No relevant Confluence content found."
            else:
                # Surface the real per-call failure (e.g. an Atlassian API
                # token missing Teamwork Graph permission) rather than a
                # generic phrase — this is the one piece of information an
                # operator actually needs to fix it, and it was already
                # being thrown away here even though gather_confluence_
                # context recorded it on each failed call's own Evidence.
                failure_detail = next(
                    (e.summary for e in evidence_entries if e.status == "failed"), None
                )
                summary = (
                    f"Confluence lookup could not be completed: {failure_detail}"
                    if failure_detail
                    else "Confluence lookup could not be completed."
                )
            return ResolvedArtifact(
                provider="confluence",
                capability=self.capability,
                reference=None,
                title="Confluence",
                text="",
                evidence=Evidence(
                    kind="tool_call", reference="confluence_context", summary=summary, status=status
                ),
                raw={"extra_evidence": evidence_entries},
            )
        logger.info("context_pipeline_confluence_enriched issue_key=%s", jira_issue_key)
        # gather_confluence_context already returns its own Evidence list
        # (one entry per MCP turn) — folded into a single artifact whose
        # `evidence` is the last (summary) entry; the rest are appended by
        # the pipeline directly (see pipeline.py) so none of that detail is
        # lost. `status="success"` is set here (not by gather_confluence_
        # context itself) since only this method knows the *overall*
        # outcome — an individual turn's own evidence doesn't.
        primary_evidence = (
            evidence_entries[-1].model_copy(update={"status": "success"})
            if evidence_entries
            else Evidence(
                kind="tool_call",
                reference="confluence_context",
                summary="Confluence context gathered.",
                status="success",
            )
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
        if reference.type == ReferenceType.GITHUB_REPOSITORY:
            # A bare "owner/repo" mention has no #issue/#pr number for
            # execute()'s regex to find - it never matches this reference
            # type, so without this branch a GITHUB_REPOSITORY reference was
            # detected (see reference_detection.py) but silently never
            # fetched. get_repository() is a direct call, not routed through
            # `self._executor`, since it isn't shaped like ITool.execute()
            # (owner/repo, not a ToolInput) - both its REST and MCP paths
            # already carry their own bounded timeouts (see GitHubTool) and
            # already never raise, matching execute_instance's own
            # error-isolation guarantee.
            owner_repo = reference.normalized_value.split("/", 1)
            if len(owner_repo) != 2:
                result = ToolResult(
                    tool_id="github",
                    tool_name="GitHub",
                    success=False,
                    error=f"'{reference.normalized_value}' isn't a valid owner/repo reference.",
                )
            else:
                owner, repo = owner_repo
                result = await self._github_tool.get_repository(owner, repo)
        else:
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


class GoogleDriveProvider:
    """Documentation capability. Drive access is per-user (an OAuth
    connection, not an install-wide credential — see app.models.
    google_drive_connection), so — same as GitHubProvider above — a
    fresh `GoogleDriveTool` instance is constructed per run using that
    run's own decrypted (and, unlike GitHub, auto-refreshed — see
    app.services.google_drive_service.get_decrypted_access_token) token,
    never the shared Tool Registry singleton.
    """

    capability = ProviderCapability.DOCUMENTATION

    def __init__(self, executor: ToolExecutor, google_drive_tool: GoogleDriveTool) -> None:
        self._executor = executor
        self._google_drive_tool = google_drive_tool

    def can_resolve(self, reference: Reference) -> bool:
        return reference.type == ReferenceType.GOOGLE_DRIVE_FILE

    async def resolve(self, reference: Reference, **kwargs: Any) -> ResolvedArtifact | None:
        result = await self._executor.execute_instance(
            self._google_drive_tool,
            "google_drive",
            "Google Drive",
            ToolInput(query=reference.raw_value),
        )
        observation = PlanningObservation(
            tool_name="fetch_google_drive_file",
            summary=result.summary or (result.error or ""),
            data=result.data,
            succeeded=result.success,
            error=result.error or "",
        )
        evidence = to_evidence(observation, "tool_call")
        if not result.success:
            logger.info(
                "context_pipeline_google_drive_fetch_failed ref=%s error=%s",
                reference.normalized_value,
                result.error,
            )
            return ResolvedArtifact(
                provider="google_drive",
                capability=self.capability,
                reference=reference,
                title=f"Google Drive {reference.normalized_value}",
                text="",
                evidence=evidence,
                raw=result.data,
            )
        text = redact_secrets(result.data.get("context_text", ""))
        logger.info("context_pipeline_google_drive_enriched ref=%s", reference.normalized_value)
        return ResolvedArtifact(
            provider="google_drive",
            capability=self.capability,
            reference=reference,
            title=f"Google Drive: {result.data.get('name', reference.normalized_value)}",
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


class TestCoverageProvider:
    """Existing test coverage capability (TestRail-synced or CSV/Excel-
    uploaded cases — same graph subtree, see
    app.indexer.graph.testrail_builder). Always retrieves, like
    `GraphProvider` above: not `Reference`-triggered, since there's no
    "test coverage reference" a request could name to resolve against.

    Ranks by the same token-overlap relevance already used for repository/
    component ranking (`app.agents.text_relevance`) and by the Testing
    agent's own `TestRailCoverageTool`
    (`app.agents.testing.tools`) — same algorithm reused, not
    reimplemented a third time.
    """

    capability = ProviderCapability.TEST_COVERAGE

    def __init__(self, test_case_graph_repository: ITestCaseGraphRepository) -> None:
        self._repo = test_case_graph_repository

    async def retrieve(self, terms: list[str], limit: int = 15) -> tuple[list[dict[str, Any]], int]:
        """Returns `(ranked cases, total cases synced/uploaded)` — the
        total is what lets a caller distinguish "nothing synced at all"
        from "synced, but none matched this request". Each case is
        `{"title", "refs"}`."""
        cases = await self._repo.get_all_test_cases(limit=2000)
        if not cases or not terms:
            return [], len(cases)

        titles = [str(node.properties.get("title", "")) for node in cases]
        weights = term_weights(terms, titles)
        scored = sorted(
            (
                (relevance(title, terms, weights), node)
                for title, node in zip(titles, cases, strict=True)
            ),
            key=lambda pair: -pair[0],
        )
        ranked = [
            {"title": node.properties.get("title", ""), "refs": node.properties.get("refs", "")}
            for score, node in scored[:limit]
            if score > 0
        ]
        return ranked, len(cases)


def wrap_artifact_text(provider: str, text: str) -> str:
    """Same untrusted-content wrapping every provider-sourced text got
    before this move — kept as one function so every provider (present
    and future) applies it identically rather than reimplementing the
    wrapper string."""
    return wrap_untrusted_content(provider, text)
