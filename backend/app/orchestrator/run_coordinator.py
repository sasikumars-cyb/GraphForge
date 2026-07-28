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

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, AgentOutput, Subject
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.selector import AgentSelector

logger = logging.getLogger(__name__)


class RunCoordinator:
    """Executes the agent selected for (subject, goal) and persists the
    outcome as a Run + AgentStep pair in Postgres.
    """

    def __init__(
        self,
        db: AsyncSession,
        registry: AgentRegistry,
        selector: AgentSelector | None = None,
    ) -> None:
        """`selector` is only needed for `create_pending_run`/`execute` (goal
        -> agent_id resolution). Callers that already know the agent_id —
        e.g. background_execution.py, resuming a run in a fresh session
        after create_pending_run already resolved it once — may omit it and
        call `execute_run` directly."""
        self._db = db
        self._registry = registry
        self._selector = selector

    async def execute(
        self,
        subject: Subject,
        goal: str,
        model: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> Run:
        """Select the agent for `goal`, run it against `subject`, and
        persist a Run + AgentStep. Returns the completed Run row.

        `extras` are merged into the AgentContext.extras dict (alongside
        the always-present 'db' key). Used by the workflow router to pass
        workflow and user_id to deterministic execution agents.

        Raises NotFoundError if `goal` has no registered agent.
        Never swallows exceptions — errors are persisted as
        status="failed" then re-raised so callers can surface them.

        This runs both phases (see `create_pending_run`/`execute_run`)
        back-to-back against the same session — kept for callers that want
        the whole lifecycle synchronously (e.g. tests). API routes that
        need to return to the client before the agent finishes should call
        the two phases separately via `app.orchestrator.background_execution`.
        """
        run, agent_id, agent = await self.create_pending_run(subject, goal, model)
        return await self.execute_run(run, agent_id, agent, subject, goal, model, extras)

    async def create_pending_run(
        self,
        subject: Subject,
        goal: str,
        model: str | None = None,
    ) -> tuple[Run, str, object]:
        """Phase 1: create the Run row (status="queued") and resolve the
        agent for `goal`. Flushes but does not commit on the success path —
        callers that need the queued row durable before returning to a
        client (e.g. to hand back a run_id and dispatch background
        execution) must `await db.commit()` themselves after this returns.

        On selection/lookup failure, persists status="failed", commits,
        and re-raises — matching `execute()`'s error behavior exactly.
        """
        if self._selector is None:
            raise ValueError(
                "create_pending_run requires a selector; construct RunCoordinator with one."
            )

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
            await self._fail_run(
                run, f"Agent '{agent_id}' is registered in the Selector but not in the Registry."
            )
            await self._db.commit()
            from app.core.exceptions import NotFoundError

            raise NotFoundError(f"Agent '{agent_id}' selected but not found in registry.")

        if not self._registry.is_enabled(agent_id):
            await self._fail_run(
                run, f"Agent '{agent_id}' is currently disabled by an administrator."
            )
            await self._db.commit()
            from app.core.exceptions import AgentDisabledError

            raise AgentDisabledError(f"Agent '{agent_id}' is disabled.")

        _manifest, agent = entry
        return run, agent_id, agent

    async def execute_run(
        self,
        run: Run,
        agent_id: str,
        agent: object,
        subject: Subject,
        goal: str,
        model: str | None = None,
        extras: dict[str, Any] | None = None,
        on_pre_commit: Callable[[AsyncSession, Run], Awaitable[None]] | None = None,
    ) -> Run:
        """Phase 2: run the resolved `agent` against `subject` and persist
        the outcome onto `run` (status queued/failed -> running ->
        completed/failed). `run` must already exist in this coordinator's
        session (either freshly created by `create_pending_run` in the same
        session, or re-fetched by id in a different session — see
        `background_execution.py` for the latter).

        `on_pre_commit`, when given, runs immediately before this method's
        own commit and is expected to call `db.commit()` itself — any other
        mutation it makes against the same session (e.g. advancing a
        Workflow's current_stage) lands in the *same* transaction as this
        run's own status change, so a concurrent reader can never observe
        one without the other. Without this, `run.status` and a caller's
        own post-completion bookkeeping were two separate commits, letting
        a fast poller see the run as done before, say, the owning
        workflow's current_stage had advanced — a real race, not
        theoretical (see the flaky test this was found from). A failing
        hook is logged and swallowed, then this method still commits the
        run's own status directly, so a bookkeeping bug can never lose the
        run's result.
        """
        step = AgentStep(
            id=uuid.uuid4(),
            run_id=run.id,
            agent_id=agent_id,
            status="running",
        )
        self._db.add(step)

        run.status = "running"
        run.started_at = datetime.now(UTC)
        await self._db.flush()

        logger.info(
            "agent_run_started run_id=%s agent_id=%s subject_id=%s goal=%s",
            str(run.id),
            agent_id,
            subject.subject_id,
            goal,
        )

        # Inject db session via extras so agents can do Postgres queries
        # per-run without holding a session reference at construction time.
        # user_id rides along too — it's already persisted on `run` by the
        # time execution starts (see agent_runs.py), and agents need it to
        # resolve per-user credentials (e.g. a GitHub OAuth connection)
        # rather than an install-wide config.
        ctx_extras: dict[str, Any] = {"db": self._db, "user_id": run.user_id}
        if extras:
            ctx_extras.update(extras)
        context = AgentContext(subject=subject, goal=goal, model=model, extras=ctx_extras)
        start_ms = time.monotonic()

        try:
            output: AgentOutput = await agent.run(context)  # type: ignore[attr-defined]
        except Exception as exc:
            latency_ms = int((time.monotonic() - start_ms) * 1000)
            await self._fail_step(step, str(exc), latency_ms)
            await self._fail_run(run, str(exc))
            await self._commit_with_hook(run, on_pre_commit)
            logger.error(
                "agent_run_failed run_id=%s agent_id=%s error=%s",
                str(run.id),
                agent_id,
                str(exc),
            )
            raise

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        now = datetime.now(UTC)

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

        await self._commit_with_hook(run, on_pre_commit)

        logger.info(
            "agent_run_completed run_id=%s agent_id=%s subject_id=%s "
            "confidence=%.2f evidence_count=%d latency_ms=%d",
            str(run.id),
            agent_id,
            subject.subject_id,
            output.confidence.score,
            len(output.evidence),
            latency_ms,
        )

        return run

    async def _commit_with_hook(
        self,
        run: Run,
        on_pre_commit: Callable[[AsyncSession, Run], Awaitable[None]] | None,
    ) -> None:
        """Commit `run`'s pending status change, atomically with whatever
        `on_pre_commit` also mutates (see execute_run's docstring). Falls
        back to a plain commit — of the run's own status alone — if the
        hook is absent or raises, so a caller's bookkeeping bug can never
        cost the run its own recorded outcome."""
        if on_pre_commit is None:
            await self._db.commit()
            return
        try:
            await on_pre_commit(self._db, run)
        except Exception:
            logger.exception("run_coordinator_pre_commit_hook_failed run_id=%s", str(run.id))
            await self._db.commit()

    async def _fail_step(self, step: AgentStep, error: str, latency_ms: int) -> None:
        step.status = "failed"
        step.error_message = error
        step.latency_ms = latency_ms
        step.completed_at = datetime.now(UTC)

    async def _fail_run(self, run: Run, error: str) -> None:
        run.status = "failed"
        run.error_message = error
        run.completed_at = datetime.now(UTC)
