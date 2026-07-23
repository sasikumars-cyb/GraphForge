"""The Change Investigation Agent's reasoning loop.

`InvestigationAgent.investigate()` is the entry point: given a pull
request, it runs Goal -> Plan -> Select Tool -> Execute -> Observe ->
Decide for each piece of optional evidence (traversal, cross-repository
metadata, indexing summary, diff, git history), recording every decision -
including skips - to `reasoning_log`. Tool selection is rule-based
(`AgentPlanner`), not an LLM call; the single LLM call this agent makes is
reserved for the final synthesis step, reusing the exact same
`ContextBuilder` -> `ILLMProvider` -> `grounded_in()` pipeline
`AIAnalysisService` uses for its single-shot path.

Never discovers a dependency, repository, or relationship itself - every
fact in `AgentState` comes from a real reader/provider call. The
canonical deterministic analysis (`PullRequestAnalysis`, produced by
`ImpactAnalysisEngine.analyze_pull_request`) is untouched by this agent:
an investigation's traversal is adaptive (it may stop early), so its
result is scoped only to the AI-enriched analysis, never persisted as the
canonical impact record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.models import AgentState, ReasoningStep
from app.ai.agent.planner import AgentPlanner
from app.ai.agent.tools import (
    ReadDependencyGraphTool,
    ReadGitDiffTool,
    ReadGitHistoryTool,
    ReadIndexingInformationTool,
    RetrieveRepositoryMetadataTool,
    ToolExecutor,
    ToolRegistry,
    TraverseDependencyGraphTool,
)
from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext, ContextBuilder
from app.ai.services.persistence import persist_ai_analysis_result
from app.analysis.engine.impact_analysis_engine import RepositoryNotIndexedError
from app.analysis.graph.interfaces import IImpactGraphReader
from app.analysis.models.impact import ImpactAnalysisResult, impacted_node_from_graph_node
from app.analysis.services.dependency_path_builder import build_dependency_paths
from app.analysis.services.risk_classifier import classify_risk
from app.core.exceptions import NotFoundError
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphNode
from app.integrations.interfaces import IVersionControlProvider
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.services.github_service import get_decrypted_access_token


def _is_pom_file(path: str) -> bool:
    return path == "pom.xml" or path.endswith("/pom.xml")


def _dedupe_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return list({node.id: node for node in nodes}.values())


@dataclass(frozen=True)
class InvestigationResult:
    """What an investigation produces: the AI-enriched analysis plus the
    full, explainable trail of decisions that led to it."""

    analysis: AIAnalysisResult
    reasoning_log: list[ReasoningStep]


class InvestigationAgent:
    def __init__(
        self,
        db: AsyncSession,
        graph_repository: IGraphRepository,
        impact_graph_reader: IImpactGraphReader,
        version_control_provider: IVersionControlProvider,
        llm_provider: ILLMProvider,
        planner: AgentPlanner | None = None,
    ) -> None:
        self._db = db
        self._graph_repository = graph_repository
        self._impact_graph_reader = impact_graph_reader
        self._version_control_provider = version_control_provider
        self._llm_provider = llm_provider
        self._planner = planner or AgentPlanner()

    async def investigate(self, pull_request_id: uuid.UUID) -> InvestigationResult:
        pull_request = await self._db.get(PullRequest, pull_request_id)
        if pull_request is None:
            raise NotFoundError("Pull request not found.")

        repository = await self._db.get(Repository, pull_request.repository_id)
        if repository is None:
            raise NotFoundError("Repository not found.")

        repository_id = str(repository.id)
        if not await self._graph_repository.has_graph(repository_id):
            raise RepositoryNotIndexedError(
                "This repository has not been indexed yet - run POST "
                "/repositories/{id}/index before investigating a pull request."
            )

        access_token = await get_decrypted_access_token(self._db, repository.user_id)
        changed_files = await self._version_control_provider.list_changed_files(
            owner=repository.owner,
            repo=repository.name,
            pull_number=pull_request.number,
            access_token=access_token,
        )
        changed_paths = {changed.path for changed in changed_files}
        changed_paths.update(
            changed.previous_path for changed in changed_files if changed.previous_path
        )

        state = AgentState(
            changed_files=sorted(changed_paths),
            pom_changed=any(_is_pom_file(path) for path in changed_paths),
        )
        executor = ToolExecutor(
            self._build_tool_registry(
                repository=repository,
                repository_id=repository_id,
                pull_number=pull_request.number,
                access_token=access_token,
            )
        )

        await self._gather_evidence(state, executor)

        result = self._finalize_impact_result(state)
        analysis, context = await self._synthesize(
            state=state,
            result=result,
            repository=repository,
            pull_request=pull_request,
            synthesis_label="Sufficient evidence gathered - synthesizing with the LLM provider.",
        )

        retry_decision = self._planner.should_retry_after_low_confidence(
            confidence_score=analysis.confidence.score,
            has_diff=bool(state.diff_content),
            has_authors=bool(state.recent_file_authors),
            has_impacted_services=state.has_impacted_services,
            has_impacted_repositories=bool(state.impacted_repositories),
            has_cross_repository_peers=bool(state.cross_repository_peer_hops),
        )
        # Hard cap: at most one retry, ever - this is a plain `if`, never a
        # loop, and confidence is not re-checked after the second
        # synthesis. Do not "helpfully" wrap this in a while loop.
        if retry_decision.should_call and retry_decision.tool_name:
            observation = await executor.execute(retry_decision.tool_name, state)
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal=(
                        "Decide whether more evidence exists worth gathering after a "
                        "low-confidence result."
                    ),
                    plan=retry_decision.reasoning,
                    tool_selected=retry_decision.tool_name,
                    observation=observation,
                    decision="Re-synthesizing with the additional evidence.",
                )
            )
            analysis, context = await self._synthesize(
                state=state,
                result=result,
                repository=repository,
                pull_request=pull_request,
                synthesis_label=(
                    f"Retried after confidence {analysis.confidence.score:.2f} (below 0.5)."
                ),
            )

        # Grounding uses whichever context was produced *last* - a
        # metadata retry can change `impacted_repositories`, so only the
        # final context's known-repository set is valid to ground against.
        known_repository_names = {r["name"] for r in context.impacted_repositories}
        analysis.release_coordination_plan = analysis.release_coordination_plan.grounded_in(
            known_repository_names, repository.name
        )

        await persist_ai_analysis_result(self._db, pull_request_id, analysis)

        return InvestigationResult(analysis=analysis, reasoning_log=state.reasoning_log)

    async def _synthesize(
        self,
        *,
        state: AgentState,
        result: ImpactAnalysisResult,
        repository: Repository,
        pull_request: PullRequest,
        synthesis_label: str,
    ) -> tuple[AIAnalysisResult, AIContext]:
        """Build the current context and call the LLM once - shared by the
        first synthesis and the conditional low-confidence retry so the
        context-building sequence is never duplicated."""
        context = (
            ContextBuilder()
            .with_repository(
                name=repository.name,
                owner=repository.owner,
                default_branch=repository.default_branch,
            )
            .with_pull_request(
                title=pull_request.title,
                number=pull_request.number,
                head_ref=pull_request.head_ref,
                base_ref=pull_request.base_ref,
            )
            .with_analysis(result)
            .with_changed_files(state.changed_files)
            .with_repositories(
                state.impacted_repositories or self._current_repository_only(repository)
            )
            .with_diff(state.diff_content)
            .with_recent_file_authors(state.recent_file_authors)
            .build()
        )
        state.reasoning_log.append(
            ReasoningStep(
                step_number=len(state.reasoning_log) + 1,
                goal="Generate the final grounded impact analysis.",
                plan=synthesis_label,
                tool_selected=None,
                observation=None,
                decision=f"Calling the LLM with {result.risk.value} risk and "
                f"{len(state.changed_files)} changed file(s) in context.",
            )
        )
        analysis = await self._llm_provider.analyze(context)
        return analysis, context

    def _build_tool_registry(
        self,
        *,
        repository: Repository,
        repository_id: str,
        pull_number: int,
        access_token: str | None,
    ) -> ToolRegistry:
        tools = [
            ReadDependencyGraphTool(self._impact_graph_reader, repository_id),
            TraverseDependencyGraphTool(self._impact_graph_reader, repository_id),
            ReadIndexingInformationTool(self._graph_repository, repository_id),
            RetrieveRepositoryMetadataTool(self._db, repository),
            ReadGitDiffTool(
                self._version_control_provider,
                owner=repository.owner,
                repo=repository.name,
                pull_number=pull_number,
                access_token=access_token,
            ),
            ReadGitHistoryTool(
                self._version_control_provider,
                owner=repository.owner,
                repo=repository.name,
                access_token=access_token,
            ),
        ]
        return ToolRegistry(tools={tool.name: tool for tool in tools})

    async def _gather_evidence(self, state: AgentState, executor: ToolExecutor) -> None:
        # Step 1 (mandatory): without this, there is no way to know
        # whether the change touches the architecture graph at all.
        observation = await executor.execute("read_dependency_graph", state)
        state.reasoning_log.append(
            ReasoningStep(
                step_number=1,
                goal="Determine whether this change touches the indexed architecture graph.",
                plan=(
                    "Always map changed files to graph nodes first - every later "
                    "decision depends on this."
                ),
                tool_selected="read_dependency_graph",
                observation=observation,
                decision="Proceeding to decide whether downstream traversal is warranted.",
            )
        )

        has_direct_nodes = bool(state.direct_nodes)
        traverse_decision = self._planner.should_traverse_graph(has_direct_nodes=has_direct_nodes)
        if traverse_decision.should_call:
            observation = await executor.execute("traverse_dependency_graph", state)
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal=(
                        "Find what downstream services, APIs, and repositories this "
                        "change impacts."
                    ),
                    plan=traverse_decision.reasoning,
                    tool_selected="traverse_dependency_graph",
                    observation=observation,
                    decision="Downstream impact resolved; classifying risk next.",
                )
            )
        else:
            observation = await executor.execute("read_indexing_information", state)
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal=(
                        "Confirm the empty match is a real non-architectural change, "
                        "not a stale index."
                    ),
                    plan=traverse_decision.reasoning,
                    tool_selected="read_indexing_information",
                    observation=observation,
                    decision="Skipping traversal; classifying as LOW risk.",
                )
            )

        state.risk = classify_risk(
            state.direct_service_nodes,
            pom_changed=state.pom_changed,
            topics_touched=bool(state.topic_hops),
        )

        metadata_decision = self._planner.should_retrieve_repository_metadata(
            has_cross_repository_impact=bool(state.cross_repository_peer_hops)
        )
        if metadata_decision.should_call:
            observation = await executor.execute("retrieve_repository_metadata", state)
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal="Resolve downstream repository names for the coordination plan.",
                    plan=metadata_decision.reasoning,
                    tool_selected="retrieve_repository_metadata",
                    observation=observation,
                    decision="Repository metadata available for the coordination plan.",
                )
            )
        else:
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal="Resolve downstream repository names for the coordination plan.",
                    plan=metadata_decision.reasoning,
                    tool_selected=None,
                    observation=None,
                    decision=(
                        "Skipped - no coordination plan is needed beyond the current repository."
                    ),
                )
            )

        diff_decision = self._planner.should_read_diff(risk=state.risk)
        if diff_decision.should_call:
            observation = await executor.execute("read_git_diff", state)
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal="Ground breaking-change analysis in the actual code change.",
                    plan=diff_decision.reasoning,
                    tool_selected="read_git_diff",
                    observation=observation,
                    decision="Diff available for the LLM synthesis step.",
                )
            )
        else:
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal="Ground breaking-change analysis in the actual code change.",
                    plan=diff_decision.reasoning,
                    tool_selected=None,
                    observation=None,
                    decision="Skipped - low risk, the node summary is sufficient context.",
                )
            )

        history_decision = self._planner.should_read_git_history(
            has_impacted_services=state.has_impacted_services
        )
        if history_decision.should_call:
            observation = await executor.execute("read_git_history", state)
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal="Ground reviewer suggestions in real commit history.",
                    plan=history_decision.reasoning,
                    tool_selected="read_git_history",
                    observation=observation,
                    decision="Authorship available to ground reviewer suggestions.",
                )
            )
        else:
            state.reasoning_log.append(
                ReasoningStep(
                    step_number=len(state.reasoning_log) + 1,
                    goal="Ground reviewer suggestions in real commit history.",
                    plan=history_decision.reasoning,
                    tool_selected=None,
                    observation=None,
                    decision=(
                        "Skipped - no service impact, so no reviewer suggestion needs grounding."
                    ),
                )
            )

    def _finalize_impact_result(self, state: AgentState) -> ImpactAnalysisResult:
        indirect_nodes = _dedupe_nodes(
            [hop.from_node for hop in state.same_repository_peer_hops]
            + [hop.from_node for hop in state.cross_repository_peer_hops]
        )
        return ImpactAnalysisResult(
            risk=state.risk,
            directly_impacted_services=[
                impacted_node_from_graph_node(node) for node in state.direct_service_nodes
            ],
            indirectly_impacted_services=[
                impacted_node_from_graph_node(node) for node in indirect_nodes
            ],
            impacted_apis=[
                impacted_node_from_graph_node(node)
                for node in _dedupe_nodes([hop.to_node for hop in state.api_hops])
            ],
            impacted_topics=[
                impacted_node_from_graph_node(node)
                for node in _dedupe_nodes([hop.to_node for hop in state.topic_hops])
            ],
            impacted_libraries=[impacted_node_from_graph_node(node) for node in state.dependencies],
            dependency_paths=build_dependency_paths(
                state.api_hops,
                state.topic_hops,
                state.same_repository_peer_hops,
                state.cross_repository_peer_hops,
            ),
        )

    def _current_repository_only(self, repository: Repository) -> list[dict[str, str]]:
        return [
            {
                "id": str(repository.id),
                "owner": repository.owner,
                "name": repository.name,
                "full_name": repository.full_name,
                "relation": "current",
            }
        ]
