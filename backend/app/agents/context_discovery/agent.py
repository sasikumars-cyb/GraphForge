"""Context Discovery Agent — goal=discover_context.

Implements the IAgent protocol as a thin wrapper around the existing
`ContextResolutionPipeline` (app.context_pipeline) — the module the
Planning Agent used to call directly. Wrapping it as its own agent is
what turns context resolution into a first-class workflow stage: a real
Run, a real AgentStep, a real confidence score, gated by the same
approve/reject/continue machinery every other stage already uses (see
the Context Discovery / Context Explorer architecture review).

This agent performs no reasoning about *what to build* — that is
Planning's job, and Planning's alone. It answers exactly one question:
what exists (repositories, components, topics, Jira/Confluence/GitHub
content) that a plan might need? Its result is a `ContextDiscoveryResult`
(schemas.py), persisted into AgentStep.result exactly like every other
agent's output, and read back downstream via `get_stage_result()` — the
same function Development, Testing, Documentation Planning, and
Engineering Review already use to consume prior stages.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, AgentOutput, Confidence, Evidence
from app.agents.context_discovery.schemas import ContextDiscoveryResult
from app.context_pipeline import ContextResolutionPipeline
from app.context_pipeline.models import EnrichedPlanningRequest, Reference

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"


def _serialize_reference(ref: Reference) -> dict[str, Any]:
    data = asdict(ref)
    data["type"] = ref.type.value  # StrEnum -> plain str for JSON storage
    return data


def build_context_discovery_result(
    enriched: EnrichedPlanningRequest,
) -> ContextDiscoveryResult:
    """Project the pipeline's in-memory `EnrichedPlanningRequest` into the
    JSON-serializable shape that actually gets persisted. Kept as its own
    function (rather than inlined in `run()`) so a test can exercise the
    projection without constructing a full AgentContext."""
    recommendation = enriched.additional_context_recommendation
    return ContextDiscoveryResult(
        original_request=enriched.original_request,
        enriched_text=enriched.enriched_text,
        resolved_references=[_serialize_reference(r) for r in enriched.resolved_references],
        indexed_repositories=enriched.indexed_repositories,
        graph_components=enriched.graph_components,
        graph_topics=enriched.graph_topics,
        ranked_repository_names=enriched.ranked_repository_names,
        graph_context_text=enriched.graph_context_text,
        graph_available=enriched.graph_available,
        graph_has_data=enriched.graph_has_data,
        additional_context_recommendation=(
            {
                "should_search": recommendation.should_search,
                "capability": (
                    recommendation.capability.value if recommendation.capability else None
                ),
                "reasoning": recommendation.reasoning,
            }
            if recommendation is not None
            else None
        ),
        planning_metadata=enriched.planning_metadata,
        prompt_version=_PROMPT_VERSION,
    )


def _confidence_for(enriched: EnrichedPlanningRequest) -> Confidence:
    """Same three-tier formula Planning has always used to judge its own
    graph grounding (base_confidence in agents/planning/agent.py) — Context
    Discovery is the stage actually doing the discovering now, so it, not
    Planning, is what that confidence score should describe."""
    if not enriched.graph_available:
        return Confidence(
            score=0.25,
            reasoning=(
                "Knowledge Graph was unavailable (infrastructure error); only "
                "Jira/Confluence/GitHub content (if any) could be resolved."
            ),
        )
    if enriched.graph_has_data:
        return Confidence(
            score=0.85,
            reasoning=(
                f"Graph traversal found {len(enriched.graph_components)} component(s) "
                f"and {len(enriched.graph_topics)} topic(s) across "
                f"{len(enriched.indexed_repositories)} indexed repositories."
            ),
        )
    return Confidence(
        score=0.40,
        reasoning=(
            f"Graph is healthy but contains no architecture data "
            f"({len(enriched.indexed_repositories)} indexed repositories, "
            "0 components, 0 topics)."
        ),
    )


class ContextDiscoveryAgent:
    """Implements IAgent for goal=discover_context.

    Stateless singleton — db session, graph repository, and user id are
    all resolved per-run from context.extras, exactly like every other
    agent registered in app.agents.setup.
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        raw_request: str = context.subject.display_name
        subject_id: str = context.subject.subject_id

        logger.info(
            "context_discovery_agent_started subject_id=%s request=%.80s",
            subject_id,
            raw_request,
        )

        db: AsyncSession = context.extras["db"]
        user_id: uuid.UUID | None = context.extras.get("user_id")

        enriched = await ContextResolutionPipeline().resolve(
            raw_request=raw_request,
            db=db,
            graph_repo_override=context.extras.get("graph_repository"),
            user_id=user_id,
            model=context.model,
            extras=context.extras,
        )

        result = build_context_discovery_result(enriched)
        confidence = _confidence_for(enriched)

        evidence: list[Evidence] = list(enriched.evidence)
        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="context_discovery_summary",
                summary=(
                    f"Resolved {len(enriched.resolved_references)} reference(s), "
                    f"{len(enriched.indexed_repositories)} indexed repositor"
                    f"{'y' if len(enriched.indexed_repositories) == 1 else 'ies'}, "
                    f"{len(enriched.graph_components)} component(s), and "
                    f"{len(enriched.graph_topics)} topic(s) for: {raw_request[:80]}"
                ),
            )
        )

        logger.info(
            "context_discovery_agent_completed subject_id=%s confidence=%.2f "
            "reference_count=%d indexed_repo_count=%d component_count=%d",
            subject_id,
            confidence.score,
            len(enriched.resolved_references),
            len(enriched.indexed_repositories),
            len(enriched.graph_components),
        )

        return AgentOutput(
            agent_id="context_discovery",
            subject_id=subject_id,
            confidence=confidence,
            evidence=evidence,
            result=result.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
