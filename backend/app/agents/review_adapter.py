"""Review Agent Adapter — PW-3.

Wraps the existing, untouched InvestigationAgent.investigate() behind the
IAgent protocol so the Orchestrator can select and execute it generically.
This is an adapter, not a migration: app/ai/agent/* is never modified.

Also contains the PR-reference resolver: given a Subject with
subject_type="pull_request" and subject_id="pr:<uuid>", extracts the UUID
and passes it to InvestigationAgent.investigate().

Evidence mapping from the reasoning_log:
- Steps whose tool_selected is a graph-read tool (read_dependency_graph,
  traverse_dependency_graph) become kind="graph_traversal".
- All other tool steps become kind="tool_call".
- The LLM synthesis step becomes kind="llm_reasoning".
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import (
    AgentContext,
    AgentManifest,
    AgentOutput,
    Confidence,
    Evidence,
    Subject,
)
from app.agents.git_ops._artifact_reader import HasRuns, get_stage_result
from app.agents.llm import STAGE_REVIEW, StageAwareLLMProvider, stage_for
from app.ai.agent.investigation_agent import InvestigationAgent, InvestigationResult
from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.graph.interfaces import IGraphRepository
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.factory import create_version_control_provider
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest (registered in app/orchestrator/registry.py at startup)
# ---------------------------------------------------------------------------
REVIEW_MANIFEST = AgentManifest(
    agent_id="review",
    purpose="Investigate a pull request and produce an AI-enriched impact analysis.",
    goals=frozenset({"review_pr"}),
    accepted_subject_types=frozenset({"pull_request"}),
    cost_class="standard",
    max_graph_hops=3,
    output_schema_name="AIAnalysisResult",
    # ADR 0011, OD-3 — LLM (this agent's own reasoning), Neo4j (max_graph_hops
    # > 0, above).
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)

# Graph-read tools map to graph_traversal evidence kind.
_GRAPH_TRAVERSAL_TOOLS = frozenset(
    {
        "read_dependency_graph",
        "traverse_dependency_graph",
    }
)


def resolve_pr_subject(pull_request_id: uuid.UUID, display_name: str = "") -> Subject:
    """Build a Subject for a pull request — the minimal PR-reference resolver.

    subject_id format: "pr:<uuid>" — consistent with API_CONTRACTS.md.
    graph_node_ids is empty; the Review Agent discovers graph nodes itself.
    """
    return Subject(
        subject_id=f"pr:{pull_request_id}",
        subject_type="pull_request",
        graph_node_ids=[],
        display_name=display_name or f"PR {pull_request_id}",
    )


def _extract_pr_uuid(subject: Subject) -> uuid.UUID:
    """Extract a UUID from a subject_id of the form 'pr:<uuid>'.

    Raises NotFoundError on malformed input — never silently returns a
    wrong value.
    """
    sid = subject.subject_id
    if not sid.startswith("pr:"):
        raise NotFoundError(f"Review Agent expects subject_id 'pr:<uuid>', got '{sid}'.")
    raw = sid[3:]
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(
            f"subject_id '{sid}' does not contain a valid UUID after 'pr:': {exc}"
        ) from exc


def _map_evidence(result: InvestigationResult) -> list[Evidence]:
    """Convert reasoning_log steps into Evidence entries.

    Every step with a tool_selected is a tool evidence entry. Graph-read
    tools are labelled graph_traversal; others are tool_call. The final
    LLM synthesis step is llm_reasoning. Steps with no tool_selected
    (skip decisions) are not included — a skip is a recorded decision in
    the reasoning log but not an external evidence source.
    """
    evidence: list[Evidence] = []
    for step in result.reasoning_log:
        if step.tool_selected is None:
            continue
        kind = "graph_traversal" if step.tool_selected in _GRAPH_TRAVERSAL_TOOLS else "tool_call"
        summary = (
            step.observation.summary
            if step.observation
            else f"{step.tool_selected} executed (no observation recorded)"
        )
        evidence.append(
            Evidence(
                kind=kind,
                reference=step.tool_selected,
                summary=summary,
            )
        )

    # Synthesis step — the LLM call that produced the final analysis
    evidence.append(
        Evidence(
            kind="llm_reasoning",
            reference="llm_synthesis",
            summary=(
                f"LLM synthesis with confidence {result.analysis.confidence.score:.2f}: "
                f"{result.analysis.confidence.reasoning[:120]}"
            ),
        )
    )
    return evidence


def _upstream_context_summary(source_workflow: HasRuns) -> str:
    """One line per upstream stage's executive summary, read from the
    approved blueprint this PR was generated from (`context.extras[
    "source_workflow"]` — populated by the same auto_execution linkage
    `code_generation`'s own blueprint context already reads, see
    `app.api.v1.routers.workflows`'s `extras={"source_workflow": ...}`).

    Deliberately additive-only, not a change to `InvestigationAgent`
    itself (see this module's docstring — "app/ai/agent/* is never
    modified"): this doesn't feed the summaries into the Review Agent's
    own reasoning, it attaches them as evidence so a human reading the
    review can see what was planned/tested alongside what
    `InvestigationAgent` independently found in the actual diff, without
    touching that agent's investigation logic at all.
    """
    lines: list[str] = []
    for stage, label in (
        ("planning", "Planning"),
        ("development", "Development"),
        ("testing", "Testing"),
    ):
        result = get_stage_result(source_workflow, stage)
        summary = result.get("executive_summary") if result else None
        if summary:
            lines.append(f"{label}: {summary}")
    if not lines:
        return ""
    return "What was planned for this change:\n" + "\n".join(lines)


class ReviewAgentAdapter:
    """Adapts InvestigationAgent.investigate() to the IAgent protocol.

    Stateless singleton — db session is injected per-run via
    context.extras["db"] by the RunCoordinator. The same factory approach
    as _build_investigation_agent() in the existing ai_analysis router.
    """

    def _build_agent(
        self,
        db: AsyncSession,
        model: str | None,
        stage: str,
        graph_repository: IGraphRepository | None = None,
    ) -> InvestigationAgent:
        driver = get_driver()
        # Prefer the hop-budgeted repository RunCoordinator's Context
        # Preparation step builds from REVIEW_MANIFEST.max_graph_hops;
        # construct a plain, unbudgeted one only when running outside
        # that dispatcher (e.g. a unit test calling `.run()` directly).
        return InvestigationAgent(
            db=db,
            graph_repository=graph_repository or Neo4jGraphRepository(driver),
            impact_graph_reader=Neo4jImpactGraphReader(driver),
            version_control_provider=create_version_control_provider(get_settings()),
            llm_provider=StageAwareLLMProvider(stage=stage, model=model),
        )

    async def run(self, context: AgentContext) -> AgentOutput:
        db: AsyncSession = context.extras["db"]
        pr_uuid = _extract_pr_uuid(context.subject)

        logger.info(
            "review_agent_started subject_id=%s pr_uuid=%s model=%s",
            context.subject.subject_id,
            str(pr_uuid),
            context.model,
        )

        agent = self._build_agent(
            db,
            context.model,
            stage_for(context.extras, STAGE_REVIEW),
            graph_repository=context.extras.get("graph_repository"),
        )
        result = await agent.investigate(pr_uuid)

        evidence = _map_evidence(result)

        # Lighter-touch upstream context handoff — see
        # _upstream_context_summary's own docstring for why this is
        # evidence-only rather than a change to InvestigationAgent itself.
        # Only present when this review is part of an Auto Execution
        # workflow chained from an approved planning blueprint; a
        # standalone/webhook-triggered review has no source_workflow and
        # this is simply skipped.
        source_workflow: Any = context.extras.get("source_workflow")
        if source_workflow is not None:
            upstream_summary = _upstream_context_summary(source_workflow)
            if upstream_summary:
                evidence.append(
                    Evidence(
                        kind="tool_call",
                        reference="source_workflow_context",
                        summary=upstream_summary,
                    )
                )

        return AgentOutput(
            agent_id="review",
            subject_id=context.subject.subject_id,
            confidence=Confidence(
                score=result.analysis.confidence.score,
                reasoning=result.analysis.confidence.reasoning,
            ),
            evidence=evidence,
            result=result.analysis.model_dump(),
            prompt_version=result.analysis.prompt_version,
            output_ref=f"ai-analysis:{pr_uuid}",
        )
