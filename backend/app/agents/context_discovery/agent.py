"""Context Discovery Agent — goal=discover_context.

A thin adapter between the agent contract and the reasoning engine
(`app.context_pipeline.reasoning`). Everything interesting — deciding what to
investigate next, computing confidence from evidence, deciding whether a
human needs to be asked — lives in the engine; this class translates its
`WorkingContext` into an `AgentOutput` and back.

The agent performs no reasoning about *what to build*: that stays Planning's
job. It answers "what exists, how do I know, and what's still unclear?"

Pause/resume: when the engine has exhausted every provider and one genuine
ambiguity remains, the agent returns `awaiting_input=True` with a
`pending_question` instead of completing. The RunCoordinator persists that as
a paused step (`run_coordinator._apply_agent_output`) and the workflow moves
to `awaiting_clarification` until a human answers via
`POST /workflows/{id}/clarify`. The answer resumes the *same* working memory
— the full engine state is persisted, so the resumed run knows everything the
paused one did.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, AgentOutput, Confidence
from app.agents.context_discovery.schemas import ContextDiscoveryResult
from app.agents.llm import STAGE_CONTEXT_DISCOVERY, stage_for
from app.context_pipeline.reasoning.engine import discover, resume
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.memory import WorkingContext
from app.context_pipeline.reasoning.projection import (
    build_result,
    restore,
    to_contract_evidence,
)

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "4.0"


def _confidence_for(state: WorkingContext) -> Confidence:
    """Confidence straight from the evidence-derived capability assessments,
    with the reasoning string carrying each capability's own signal
    decomposition — so the score shown in the UI is always accompanied by the
    ✓/✗ list that produced it, never a bare number."""
    breakdown = "; ".join(
        f"{a.label} {a.score:.0%}" for a in state.assessments if a.necessity != "not_applicable"
    )
    unmet_labels = [
        f"{a.label}: " + ", ".join(s.label for s in a.missing)
        for a in state.assessments
        if a.necessity != "not_applicable" and not a.satisfied
    ]
    reasoning = (
        f"Readiness={state.readiness} after {state.metadata.iteration} reasoning cycle(s) "
        f"and {state.metadata.clarification_rounds} clarification round(s), from "
        f"{len(state.ledger.facts)} fact(s) across "
        f"{len(state.ledger.evidence)} investigation(s). {breakdown}."
    )
    if unmet_labels:
        reasoning += " Missing — " + "; ".join(unmet_labels) + "."
    return Confidence(score=state.confidence, reasoning=reasoning)


class ContextDiscoveryAgent:
    """Implements IAgent for goal=discover_context.

    Stateless singleton — db session, graph repository and user id are all
    resolved per-run from `context.extras`, exactly like every other agent
    registered in `app.agents.setup`.
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        raw_request: str = context.subject.display_name
        subject_id: str = context.subject.subject_id

        db: AsyncSession = context.extras["db"]
        user_id: uuid.UUID | None = context.extras.get("user_id")
        session = SessionContext(
            db=db,
            user_id=user_id,
            graph_repo_override=context.extras.get("graph_repository"),
            model=context.model,
            stage=stage_for(context.extras, STAGE_CONTEXT_DISCOVERY),
            agent_context=context,
        )

        resume_payload: dict[str, Any] | None = context.extras.get("resume")

        if resume_payload is not None:
            answer = resume_payload["answer"]
            logger.info(
                "context_discovery_resuming subject_id=%s question_id=%s",
                subject_id,
                answer["question_id"],
            )
            state = await resume(
                state=restore(resume_payload["working_context"]),
                question_id=answer["question_id"],
                answer=answer["answer"],
                session=session,
            )
        else:
            logger.info(
                "context_discovery_starting subject_id=%s request=%.80s", subject_id, raw_request
            )
            explicit_repositories: list[str] | None = context.extras.get("explicit_repositories")
            state = await discover(
                request=raw_request,
                session=session,
                explicit_repositories=explicit_repositories,
            )

        confidence = _confidence_for(state)
        question = state.next_question()
        result = ContextDiscoveryResult.model_validate(build_result(state))

        logger.info(
            "context_discovery_output subject_id=%s readiness=%s confidence=%.2f paused=%s",
            subject_id,
            state.readiness,
            confidence.score,
            question is not None,
        )

        return AgentOutput(
            agent_id="context_discovery",
            subject_id=subject_id,
            confidence=confidence,
            evidence=to_contract_evidence(state),
            result=result.model_dump(),
            prompt_version=_PROMPT_VERSION,
            awaiting_input=question is not None,
            pending_question=question.model_dump() if question is not None else None,
        )
