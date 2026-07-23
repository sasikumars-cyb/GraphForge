"""RunCoordinator — executes a selected agent and persists the run.

This is the inter-agent execution layer on top of each agent's own
intra-agent Plan/Select/Execute/Observe/Decide loop. Phase 1 is
sequential (one agent per run); Phase 2 adds parallel execution.

Cross-stage context (e.g. the Workflow API chaining Planning → Development
→ Testing → Review) is built by reading each stage's persisted Run/AgentStep
rows back out of Postgres (see app/services/workflow_service.py), not via
any in-memory state on this class — RunCoordinator itself is stateless
across calls beyond the single (subject, goal) it's given.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.agents._contract import AgentContext, AgentOutput, Subject
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.selector import AgentSelector


class RunCoordinator:
    """Executes the agent selected for (subject, goal) and persists the
    outcome as a Run + AgentStep pair in Postgres.
    """

    def __init__(
        self,
        db: AsyncSession,
        registry: AgentRegistry,
        selector: AgentSelector,
    ) -> None:
        self._db = db
        self._registry = registry
        self._selector = selector

    async def execute(
        self,
        subject: Subject,
        goal: str,
        model: str | None = None,
    ) -> Run:
        """Select the agent for `goal`, run it against `subject`, and
        persist a Run + AgentStep. Returns the completed Run row.

        Raises NotFoundError if `goal` has no registered agent.
        Never swallows exceptions — errors are persisted as
        status="failed" then re-raised so callers can surface them.
        """
        run = Run(
            id=uuid.uuid4(),
            subject_id=subject.subject_id,
            subject_type=subject.subject_type,
            display_name=subject.display_name,
            goal=goal,
            model=model,
            status="queued",
        )
        self._db.add(run)
        await self._db.flush()  # assign PK without committing

        try:
            agent_id = self._selector.select(goal)
        except Exception as exc:
            await self._fail_run(run, str(exc))
            await self._db.commit()
            raise

        entry = self._registry.get(agent_id)
        if entry is None:
            await self._fail_run(run, f"Agent '{agent_id}' is registered in the Selector but not in the Registry.")
            await self._db.commit()
            from app.core.exceptions import NotFoundError
            raise NotFoundError(f"Agent '{agent_id}' selected but not found in registry.")

        manifest, agent = entry

        step = AgentStep(
            id=uuid.uuid4(),
            run_id=run.id,
            agent_id=agent_id,
            status="running",
        )
        self._db.add(step)

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        await self._db.flush()

        logger.info(
            "agent_run_started run_id=%s agent_id=%s subject_id=%s goal=%s",
            str(run.id), agent_id, subject.subject_id, goal,
        )

        # Inject db session via extras so agents can do Postgres queries
        # per-run without holding a session reference at construction time.
        context = AgentContext(subject=subject, goal=goal, model=model, extras={"db": self._db})
        start_ms = time.monotonic()

        try:
            output: AgentOutput = await agent.run(context)
        except Exception as exc:
            latency_ms = int((time.monotonic() - start_ms) * 1000)
            await self._fail_step(step, str(exc), latency_ms)
            await self._fail_run(run, str(exc))
            await self._db.commit()
            logger.error(
                "agent_run_failed run_id=%s agent_id=%s error=%s",
                str(run.id), agent_id, str(exc),
            )
            raise

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        now = datetime.now(timezone.utc)

        # Persist AgentOutput into the step row
        step.status = "completed"
        step.confidence_score = output.confidence.score
        step.confidence_reasoning = output.confidence.reasoning
        step.evidence = [e.model_dump() for e in output.evidence]
        step.result = output.result
        step.graph_facts_written = output.graph_facts_written
        step.prompt_version = output.prompt_version
        step.output_ref = output.output_ref
        step.latency_ms = latency_ms
        step.completed_at = now

        run.status = "completed"
        run.completed_at = now

        await self._db.commit()

        logger.info(
            "agent_run_completed run_id=%s agent_id=%s subject_id=%s confidence=%.2f evidence_count=%d latency_ms=%d",
            str(run.id), agent_id, subject.subject_id, output.confidence.score,
            len(output.evidence), latency_ms,
        )

        return run

    async def _fail_step(self, step: AgentStep, error: str, latency_ms: int) -> None:
        step.status = "failed"
        step.error_message = error
        step.latency_ms = latency_ms
        step.completed_at = datetime.now(timezone.utc)

    async def _fail_run(self, run: Run, error: str) -> None:
        run.status = "failed"
        run.error_message = error
        run.completed_at = datetime.now(timezone.utc)
