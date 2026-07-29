"""ContextResolutionPipeline — the seam between "understanding the
request" and "planning".

Named for what it does, not for a specific source system: it resolves
whatever context a request needs, regardless of whether that context
turns out to come from Jira, Confluence, GitHub, the Knowledge Graph, or
a provider that doesn't exist yet (`ContextResolver` and
`RequestResolutionPipeline` were the other candidates considered —
`ContextResolutionPipeline` was chosen because "resolution" is the one
word that covers both meanings this module actually performs: resolving
a *reference* to a concrete entity, and resolving a *capability* to a
concrete provider).

`resolve()` is the only entry point the Planning Agent calls. Everything
below it — reference detection, provider capability lookup, retrieval,
normalization, optional LLM-assisted discovery — is invisible to the
agent; it only ever sees the `EnrichedPlanningRequest` this returns.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.llm import STAGE_PLANNING, stage_for
from app.agents.planning.classifier import PlanningProfile, analyse
from app.agents.planning.tools import to_evidence
from app.context_pipeline.discovery import recommend_additional_context
from app.context_pipeline.models import (
    AdditionalContextRecommendation,
    EnrichedPlanningRequest,
    Reference,
    ReferenceType,
)
from app.context_pipeline.providers import (
    ConfluenceProvider,
    GitHubProvider,
    GraphProvider,
    JiraProvider,
    wrap_artifact_text,
)
from app.context_pipeline.reference_detection import detect_references
from app.core.config import get_settings
from app.tools import ContextBuilder, ToolExecutor, ToolInput, get_tool_registry
from app.tools.implementations.github_tool import GitHubTool, extract_pr_or_issue_ref

logger = logging.getLogger(__name__)


class ContextResolutionPipeline:
    """Stateless orchestrator — constructed fresh per run (mirrors
    `PlanningAgent` itself), since every dependency it needs (db session,
    graph repository, user id) is run-scoped."""

    async def resolve(
        self,
        *,
        raw_request: str,
        db: AsyncSession,
        graph_repo_override: Any,
        user_id: object,
        model: str | None,
        extras: dict[str, Any],
    ) -> EnrichedPlanningRequest:
        evidence: list[Evidence] = []
        artifacts = []
        metadata: dict[str, Any] = {}
        stage = stage_for(extras, STAGE_PLANNING)

        # ------------------------------------------------------------
        # Phase 3 — deterministic reference detection
        # ------------------------------------------------------------
        references = detect_references(raw_request)
        logger.info(
            "context_pipeline_references_detected count=%d types=%s",
            len(references),
            ",".join(r.type.value for r in references) or "none",
        )

        # ------------------------------------------------------------
        # Phase 4/5 — provider capability resolution + retrieval
        # ------------------------------------------------------------
        registry = get_tool_registry()
        executor = ToolExecutor(registry=registry)

        enriched_text = raw_request
        jira_reference = next((r for r in references if r.type == ReferenceType.JIRA_ISSUE), None)

        if jira_reference is not None:
            jira_provider = JiraProvider(executor)
            jira_artifact = await jira_provider.resolve(jira_reference)
            if jira_artifact is not None:
                evidence.append(jira_artifact.evidence)
                artifacts.append(jira_artifact)
                if jira_artifact.text:
                    enriched_text += wrap_artifact_text("jira", jira_artifact.text)

                    # Confluence is anchor-driven off a *resolved* Jira issue,
                    # not off its own detected reference — see
                    # ConfluenceProvider.resolve_for_issue's docstring.
                    confluence_provider = ConfluenceProvider(db)
                    confluence_artifact = await confluence_provider.resolve_for_issue(
                        jira_issue_key=jira_reference.normalized_value,
                        task_description=raw_request,
                        model=model,
                        stage=stage,
                    )
                    if confluence_artifact is not None:
                        evidence.append(confluence_artifact.evidence)
                        evidence.extend(confluence_artifact.raw.get("extra_evidence", []))
                        artifacts.append(confluence_artifact)
                        if confluence_artifact.text:
                            enriched_text += wrap_artifact_text(
                                "confluence", confluence_artifact.text
                            )

        # Re-detected against `enriched_text`, not the initial `references`
        # list computed from the raw prompt: a bare Jira URL's real ticket
        # description can itself mention a GitHub PR/issue that the raw
        # prompt never did, and the original inline implementation this
        # pipeline replaces always scanned the Jira-enriched text for a
        # GitHub reference, not the literal user input. Re-running full
        # detection would also re-match the Jira reference inside its own
        # wrapped content and any incidental bare-repo-shaped token in the
        # fetched ticket text — narrowly re-checking just the GitHub shapes
        # here avoids that noise.
        gh_ref = extract_pr_or_issue_ref(enriched_text)
        github_reference = (
            Reference(
                type=ReferenceType.GITHUB_PULL_REQUEST,
                provider="github",
                confidence=1.0,
                raw_value=f"{gh_ref[0]}/{gh_ref[1]}#{gh_ref[2]}",
                normalized_value=f"{gh_ref[0]}/{gh_ref[1]}#{gh_ref[2]}",
            )
            if gh_ref is not None
            else None
        )
        if github_reference is not None and github_reference not in references:
            references.append(github_reference)
        if github_reference is not None:
            github_token = None
            if user_id is not None:
                from app.services.github_service import get_decrypted_access_token

                github_token = await get_decrypted_access_token(db, user_id)

            if github_token is None:
                logger.info(
                    "context_pipeline_github_skipped_not_connected ref=%s",
                    github_reference.normalized_value,
                )
            else:
                github_tool = GitHubTool(
                    {
                        "github_token": github_token,
                        "github_mcp_server_url": get_settings().github_mcp_default_server_url,
                        "github_mcp_api_key": github_token,
                    }
                )
                github_provider = GitHubProvider(executor, github_tool)
                github_artifact = await github_provider.resolve(github_reference)
                if github_artifact is not None:
                    evidence.append(github_artifact.evidence)
                    artifacts.append(github_artifact)
                    if github_artifact.text:
                        enriched_text += wrap_artifact_text("github", github_artifact.text)

        # ------------------------------------------------------------
        # Capability classification — deterministic, on the fully
        # enriched text (Jira/Confluence/GitHub content already folded
        # in), so a bare ticket URL's real content drives it, not just
        # the literal string pasted into the prompt.
        # ------------------------------------------------------------
        profile: PlanningProfile = analyse(enriched_text)
        logger.info(
            "context_pipeline_analysed pattern=%s capabilities=%s",
            profile.key,
            ",".join(c.key for c in profile.capabilities) or "none",
        )

        # ------------------------------------------------------------
        # Phase 6 — LLM-assisted discovery (opt-in; see settings docstring)
        # ------------------------------------------------------------
        recommendation: AdditionalContextRecommendation | None = None
        if not references and get_settings().enable_context_discovery:
            recommendation = await recommend_additional_context(
                enriched_text, model=model, stage=stage
            )
            if recommendation is not None:
                capability_label = (
                    recommendation.capability.value if recommendation.capability else "none"
                )
                evidence.append(
                    Evidence(
                        kind="llm_reasoning",
                        reference="context_discovery",
                        summary=(
                            f"should_search={recommendation.should_search} "
                            f"capability={capability_label}: {recommendation.reasoning}"
                        ),
                    )
                )

        # ------------------------------------------------------------
        # Graph retrieval — always runs (repository/architecture context
        # has never been reference-gated), ranked by the profile's search
        # terms exactly as before. One call: `Neo4jGraphTool` already
        # bundles repository lookup + component/topic traversal + ranking
        # (see GraphProvider.retrieve's docstring for why this must stay
        # a single call rather than two separate tool instantiations).
        # ------------------------------------------------------------
        tool_input = ToolInput(
            query=enriched_text,
            parameters={
                "db": db,
                "user_id": user_id,
                "relevance_terms": profile.search_terms,
                "graph_repo": graph_repo_override,
            },
        )
        graph_provider = GraphProvider(executor)
        graph_result = await graph_provider.retrieve(tool_input)
        repos_obs, traverse_obs = GraphProvider.observations_from_result(graph_result)
        evidence.append(to_evidence(repos_obs, "tool_call"))
        evidence.append(to_evidence(traverse_obs, "graph_traversal"))

        indexed_repos: list[dict[str, Any]] = graph_result.data.get("indexed_repositories", [])
        graph_components: list[dict[str, Any]] = graph_result.data.get("components", [])
        graph_topics: list[dict[str, Any]] = graph_result.data.get("kafka_topics", [])
        ranked_repository_names: list[str] = graph_result.data.get("ranked_repositories", [])
        component_count = len(graph_components)
        topic_count = len(graph_topics)
        graph_context_text = ContextBuilder().build([graph_result]).context_text

        repos_succeeded = repos_obs.succeeded
        traverse_succeeded = traverse_obs.succeeded
        graph_unavailable = not repos_succeeded or (bool(indexed_repos) and not traverse_succeeded)
        graph_has_data = (
            not graph_unavailable
            and bool(indexed_repos)
            and (component_count > 0 or topic_count > 0)
        )

        logger.info(
            "context_pipeline_graph_retrieved indexed_repo_count=%d component_count=%d "
            "topic_count=%d",
            len(indexed_repos),
            component_count,
            topic_count,
        )

        metadata["detected_reference_types"] = [r.type.value for r in references]
        metadata["graph_unavailable"] = graph_unavailable
        metadata["additional_context_recommended"] = bool(
            recommendation and recommendation.should_search
        )

        return EnrichedPlanningRequest(
            original_request=raw_request,
            enriched_text=enriched_text,
            resolved_references=references,
            artifacts=artifacts,
            profile=profile,
            indexed_repositories=indexed_repos,
            graph_components=graph_components,
            graph_topics=graph_topics,
            ranked_repository_names=ranked_repository_names,
            graph_context_text=graph_context_text,
            graph_available=not graph_unavailable,
            graph_has_data=graph_has_data,
            additional_context_recommendation=recommendation,
            evidence=evidence,
            planning_metadata=metadata,
        )
