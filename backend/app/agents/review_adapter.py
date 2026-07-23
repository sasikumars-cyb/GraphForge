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

import uuid

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.agents._contract import AgentContext, AgentManifest, AgentOutput, Confidence, Evidence, Subject
from app.ai.agent.investigation_agent import InvestigationAgent, InvestigationResult
from app.ai.providers.factory import create_llm_provider
from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.factory import create_version_control_provider

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
)

# Graph-read tools map to graph_traversal evidence kind.
_GRAPH_TRAVERSAL_TOOLS = frozenset({
    "read_dependency_graph",
    "traverse_dependency_graph",
})


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
        raise NotFoundError(
            f"Review Agent expects subject_id 'pr:<uuid>', got '{sid}'."
        )
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
        kind = (
            "graph_traversal"
            if step.tool_selected in _GRAPH_TRAVERSAL_TOOLS
            else "tool_call"
        )
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


class ReviewAgentAdapter:
    """Adapts InvestigationAgent.investigate() to the IAgent protocol.

    Stateless singleton — db session is injected per-run via
    context.extras["db"] by the RunCoordinator. The same factory approach
    as _build_investigation_agent() in the existing ai_analysis router.
    """

    def _build_agent(self, db: AsyncSession, model: str | None) -> InvestigationAgent:
        driver = get_driver()
        return InvestigationAgent(
            db=db,
            graph_repository=Neo4jGraphRepository(driver),
            impact_graph_reader=Neo4jImpactGraphReader(driver),
            version_control_provider=create_version_control_provider(get_settings()),
            llm_provider=create_llm_provider(model=model),
        )

    async def run(self, context: AgentContext) -> AgentOutput:
        db: AsyncSession = context.extras["db"]
        pr_uuid = _extract_pr_uuid(context.subject)

        logger.info(
            "review_agent_started subject_id=%s pr_uuid=%s model=%s",
            context.subject.subject_id, str(pr_uuid), context.model,
        )

        agent = self._build_agent(db, context.model)
        result = await agent.investigate(pr_uuid)

        evidence = _map_evidence(result)

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
