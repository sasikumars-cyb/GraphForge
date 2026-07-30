"""Context Discovery Agent — goal=discover_context.

Runs the reasoning loop (`app.context_pipeline.reasoning_loop`) instead of a
fixed-sequence retrieval pass: gather evidence through the existing Jira/
Confluence/GitHub/Graph providers, then reason about what's actually known,
computing a `WorkingContext` with a `readiness` verdict (READY/PARTIAL/
BLOCKED) derived from policy checks over its capabilities — never a bare
confidence threshold. When a genuine blocking ambiguity remains (repository
can't be determined, two repositories tie, a Jira reference didn't
resolve), the agent returns `awaiting_input=True` with a `pending_question`
instead of completing — the RunCoordinator persists that as a paused step
rather than a finished one (see `run_coordinator._apply_agent_output`), and
the workflow moves to `awaiting_clarification` until a human answers via
`POST /workflows/{id}/clarify`.

This agent still performs no reasoning about *what to build* — that stays
Planning's job. It answers "what exists, and what's still unclear about it?"
Its result is a `ContextDiscoveryResult` (schemas.py) — a deliberately flat
projection of the nested `WorkingContext` (see `build_context_discovery_
result`), persisted into AgentStep.result exactly like every other agent's
output, and read back downstream via `get_stage_result()`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, AgentOutput, Confidence, Evidence
from app.agents.context_discovery.schemas import ContextDiscoveryResult
from app.agents.llm import STAGE_CONTEXT_DISCOVERY, stage_for
from app.context_pipeline.reasoning_loop import (
    DiscoveryLoopResult,
    build_discovery_summary,
    evaluate_readiness_checks,
    resume_discovery,
    run_discovery_loop,
)
from app.context_pipeline.working_context import (
    BlockingIssue,
    CapabilityConfidence,
    Compatibility,
    ContextMetadata,
    GraphKnowledge,
    Knowledge,
    Reasoning,
    WorkingContext,
)

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "3.0"


def _working_context_from_result(result: dict[str, Any]) -> WorkingContext:
    """Reconstruct a nested `WorkingContext` from a previously-persisted,
    paused `AgentStep.result` (a flat `ContextDiscoveryResult` dump) — the
    inverse of `build_context_discovery_result`. Used only on resume;
    `blocking_issues` round-trips from the additive structured field
    (schemas.py), not from the flat `unresolved_questions`/`blocking_
    reasons` — those are one-way projections for older/external
    consumers, not a second source of truth to reconstruct from."""
    blocking_issues = [BlockingIssue.model_validate(i) for i in result.get("blocking_issues", [])]
    capability_confidence = CapabilityConfidence(**result.get("capability_confidence", {}))

    return WorkingContext(
        metadata=ContextMetadata(
            goal=result.get("goal", ""),
            clarification_rounds=result.get("clarification_rounds", 0),
            iteration=1,
        ),
        knowledge=Knowledge(
            entities=result.get("resolved_references", []),
            repositories=result.get("indexed_repositories", []),
            architecture={
                "components": result.get("graph_components", []),
                "topics": result.get("graph_topics", []),
            },
            implementation_candidates=result.get("ranked_repository_names", []),
            graph=GraphKnowledge(
                available=result.get("graph_available", False),
                has_data=result.get("graph_has_data", False),
                context_text=result.get("graph_context_text", ""),
            ),
        ),
        reasoning=Reasoning(
            assumptions=result.get("assumptions", []),
            blocking_issues=blocking_issues,
            user_answers=result.get("user_answers", {}),
            confidence=capability_confidence,
            readiness=result.get("readiness", "PARTIAL"),
        ),
        compatibility=Compatibility(
            original_request=result.get("original_request", ""),
            enriched_text=result.get("enriched_text", ""),
            planning_metadata=result.get("planning_metadata", {}),
        ),
    )


def build_context_discovery_result(wc: WorkingContext) -> ContextDiscoveryResult:
    """Project the reasoning loop's nested `WorkingContext` into the flat,
    JSON-serializable shape actually persisted — deliberately unchanged
    from before this refactor's nesting/BlockingIssue/capability-confidence
    additions, so Planning's `get_stage_result()` read path needs zero
    changes. Kept as its own function so a test can exercise the
    projection without constructing a full AgentContext."""
    k = wc.knowledge
    r = wc.reasoning
    unresolved = [i for i in r.blocking_issues if i.clarification_question is not None and not i.resolved]
    blocking_only = [i for i in r.blocking_issues if i.severity == "blocking" and not i.resolved]

    return ContextDiscoveryResult(
        original_request=wc.compatibility.original_request,
        enriched_text=wc.compatibility.enriched_text,
        resolved_references=k.entities,
        indexed_repositories=k.repositories,
        graph_components=k.architecture.get("components", []),
        graph_topics=k.architecture.get("topics", []),
        ranked_repository_names=k.implementation_candidates,
        graph_context_text=k.graph.context_text,
        graph_available=k.graph.available,
        graph_has_data=k.graph.has_data,
        additional_context_recommendation=None,
        planning_metadata=wc.compatibility.planning_metadata,
        prompt_version=_PROMPT_VERSION,
        goal=wc.metadata.goal,
        assumptions=r.assumptions,
        unresolved_questions=[
            {**i.clarification_question.model_dump(), "blocking": True} for i in unresolved
        ],
        user_answers=r.user_answers,
        confidence=r.confidence.overall(),
        readiness=r.readiness,
        blocking_reasons=[i.message for i in blocking_only],
        remediation_steps=[step for i in blocking_only for step in i.recommended_action],
        clarification_rounds=wc.metadata.clarification_rounds,
        capability_confidence=r.confidence.model_dump(),
        blocking_issues=[i.model_dump() for i in r.blocking_issues],
        discovery_summary=build_discovery_summary(wc).model_dump(),
    )


def _confidence_for(wc: WorkingContext) -> Confidence:
    """Confidence now comes straight from the reasoning pass's own
    capability-specific assessment (`WorkingContext.reasoning.confidence.
    overall()`) rather than a fixed three-tier formula — Context Discovery
    is the stage doing the reasoning, so its own assessed confidence is
    what this should report."""
    r = wc.reasoning
    return Confidence(
        score=r.confidence.overall(),
        reasoning=(
            f"Readiness={r.readiness} after {wc.metadata.iteration} gathering "
            f"iteration(s) and {wc.metadata.clarification_rounds} clarification "
            f"round(s). Capability confidence: work_item={r.confidence.work_item:.2f} "
            f"repository={r.confidence.repository:.2f} architecture={r.confidence.architecture:.2f} "
            f"implementation_candidates={r.confidence.implementation_candidates:.2f} "
            f"documentation={r.confidence.documentation:.2f}. "
            f"{len([i for i in r.blocking_issues if not i.resolved])} open issue(s), "
            f"{len(r.assumptions)} assumption(s) made."
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

        db: AsyncSession = context.extras["db"]
        user_id: uuid.UUID | None = context.extras.get("user_id")
        model = context.model
        stage = stage_for(context.extras, STAGE_CONTEXT_DISCOVERY)
        graph_repo_override = context.extras.get("graph_repository")

        resume = context.extras.get("resume")
        loop_result: DiscoveryLoopResult
        evidence: list[Evidence]

        if resume is not None:
            logger.info(
                "context_discovery_agent_resumed subject_id=%s question_id=%s",
                subject_id,
                resume["answer"]["question_id"],
            )
            wc = _working_context_from_result(resume["working_context"])
            loop_result = await resume_discovery(
                working_context=wc,
                question_id=resume["answer"]["question_id"],
                answer=resume["answer"]["answer"],
                model=model,
                stage=stage,
                db=db,
                user_id=user_id,
                graph_repo_override=graph_repo_override,
            )
            evidence = loop_result.evidence
        else:
            logger.info(
                "context_discovery_agent_started subject_id=%s request=%.80s",
                subject_id,
                raw_request,
            )
            loop_result = await run_discovery_loop(
                raw_request=raw_request,
                db=db,
                graph_repo_override=graph_repo_override,
                user_id=user_id,
                model=model,
                extras=context.extras,
                stage=stage,
            )
            evidence = loop_result.evidence

        wc = loop_result.working_context
        confidence = _confidence_for(wc)

        open_issues = [i for i in wc.reasoning.blocking_issues if not i.resolved]
        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="context_discovery_summary",
                summary=(
                    f"readiness={wc.reasoning.readiness} confidence={confidence.score:.2f} "
                    f"entities={len(wc.knowledge.entities)} "
                    f"repositories={len(wc.knowledge.repositories)} "
                    f"open_issues={len(open_issues)} for: {raw_request[:80]}"
                ),
            )
        )

        logger.info(
            "context_discovery_agent_completed subject_id=%s readiness=%s "
            "confidence=%.2f paused=%s",
            subject_id,
            wc.reasoning.readiness,
            confidence.score,
            loop_result.paused,
        )

        # Populate the checks the Discovery Summary reads, in case this
        # WorkingContext is about to be persisted mid-loop (paused) and
        # read back later without going through evaluate_readiness again.
        wc.reasoning.checks = evaluate_readiness_checks(wc)
        result = build_context_discovery_result(wc)
        pending = loop_result.pending_question

        return AgentOutput(
            agent_id="context_discovery",
            subject_id=subject_id,
            confidence=confidence,
            evidence=evidence,
            result=result.model_dump(),
            prompt_version=_PROMPT_VERSION,
            awaiting_input=loop_result.paused,
            pending_question=pending.model_dump() if pending is not None else None,
        )
