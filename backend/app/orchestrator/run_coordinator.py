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
from app.orchestrator.preflight import (
    PreFlightCheckFailed,
    check_llm_provider_configured,
    check_neo4j_reachable,
    collect_preflight_warnings,
    record_preflight_warnings,
)
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

        manifest, agent = entry

        # Manifest enforcement, centralized here (the one dispatch point
        # every caller already goes through) rather than duplicated per
        # agent: an unsupported subject_type fails deterministically and
        # the agent — and its LLM call — is never invoked.
        if subject.subject_type not in manifest.accepted_subject_types:
            await self._fail_run(
                run,
                f"Agent '{agent_id}' does not accept subject_type "
                f"'{subject.subject_type}'. Accepted: {sorted(manifest.accepted_subject_types)}.",
            )
            await self._db.commit()
            from app.core.exceptions import SubjectTypeMismatchError

            raise SubjectTypeMismatchError(
                f"Agent '{agent_id}' does not accept subject_type "
                f"'{subject.subject_type}'. Accepted: {sorted(manifest.accepted_subject_types)}."
            )

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
        # Commit (not just flush) so this transition is visible to the
        # polling GET /agent-runs/{id} request, which runs in its own DB
        # session/connection. A flush alone is only visible inside this
        # same transaction — under READ COMMITTED, a poller reading from a
        # different connection kept seeing status="queued" for the agent's
        # entire execution (which can exceed the frontend's 2-minute poll
        # window), because nothing committed until execute_run's final
        # commit at completion. Confirmed live: runs sat at "queued" in
        # Postgres the whole time despite this line having already run.
        await self._db.commit()

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
        # `stage` rides along so agents can resolve AI configuration under the
        # stage key the AI Workspace stores overrides against (see
        # app.agents.llm.stage_for). It is the run's real `workflow_stage`
        # when there is one; a standalone run has none, and each agent falls
        # back to its own default stage key. This is what makes the review
        # agent resolve as `review` vs `ai_pr_review` correctly — the same
        # agent, two separately configurable stages.
        ctx_extras: dict[str, Any] = {
            "db": self._db,
            "user_id": run.user_id,
            "stage": run.workflow_stage,
            # ADR 0012: the only two identifiers `invoke_llm_json`'s
            # persistence pathway needs and cannot derive itself — only the
            # orchestrator knows which Run/AgentStep it just created.
            "run_id": run.id,
            "agent_step_id": step.id,
        }
        if extras:
            ctx_extras.update(extras)

        # Looked up once, unconditionally — used both for the graph_repository
        # wiring below (only when the caller hasn't already injected one) and
        # for the Neo4j pre-flight check, which needs max_graph_hops
        # regardless of whether graph_repository injection happens.
        manifest_entry = self._registry.get(agent_id)
        max_graph_hops = manifest_entry[0].max_graph_hops if manifest_entry is not None else 0

        # --- Manifest-driven context preparation (Part 2: max_graph_hops).
        # Built once per run from the dispatched agent's own manifest —
        # never from anything the agent or caller supplies — so every
        # graph-reading agent gets the same enforcement without
        # agent-specific wiring. Only fills the slot when the caller
        # hasn't already provided one: tests routinely inject a stub/mock
        # `graph_repository` via `extras`, and that must keep working
        # unbudgeted rather than being silently replaced.
        if "graph_repository" not in ctx_extras and manifest_entry is not None:
            from app.graph.hop_budget import build_hop_budgeted_repository
            from app.graph.neo4j_repository import Neo4jGraphRepository
            from app.graph.session import get_driver

            ctx_extras["graph_repository"] = build_hop_budgeted_repository(
                Neo4jGraphRepository(get_driver()), max_graph_hops, agent_id
            )

        context = AgentContext(subject=subject, goal=goal, model=model, extras=ctx_extras)
        start_ms = time.monotonic()

        try:
            # Pre-flight: catch missing/misconfigured infrastructure before
            # ever calling agent.run() — see app.orchestrator.preflight for
            # each check's own docstring (LLM credentials: synchronous,
            # no-I/O; Neo4j: a real connectivity probe reused from the Tools
            # admin UI). Deliberately *inside* this try block, not before
            # it: check_llm_provider_configured() calls resolve(), which is
            # not guaranteed exception-free (require_provider_spec() raises
            # UnsupportedProviderError for a stale/invalid stored provider
            # key) — placing the call outside this try left that failure
            # mode unguarded, escaping past _fail_step/_fail_run entirely
            # and leaving the run/step at whatever status they reverted to
            # when the session closed without a commit (confirmed live:
            # silently back to "queued"/"awaiting_input" with no error
            # message, not "failed"). Raising inside this try instead
            # reuses the exact same handling agent.run()'s own failures
            # already get — no separate code path to keep in sync.
            preflight_failure = check_llm_provider_configured(
                agent_id, run.workflow_stage
            ) or await check_neo4j_reachable(max_graph_hops)
            if preflight_failure is not None:
                raise PreFlightCheckFailed(f"Pre-flight check failed: {preflight_failure}")

            # WARNING-severity pre-flight (ADR 0011, OD-1/OD-3/PR3): only
            # reached once the BLOCKING check above has already passed, so
            # this can never turn into a blocking failure — `collect_
            # preflight_warnings` returns `[]` (a no-op for `record_
            # preflight_warnings`) whenever no applicable dependency is
            # unavailable, or the dispatched agent has no manifest entry at
            # all. Recorded onto `step` now but not flushed/committed here —
            # it rides along in whichever commit already happens below,
            # success or failure (RunCoordinator remains the sole
            # transaction owner; see record_preflight_warnings's own
            # docstring).
            if manifest_entry is not None:
                warnings = await collect_preflight_warnings(
                    manifest_entry[0], self._db, run.user_id
                )
                record_preflight_warnings(step, warnings)

            output: AgentOutput = await agent.run(context)  # type: ignore[attr-defined]
        except Exception as exc:
            latency_ms = int((time.monotonic() - start_ms) * 1000)
            await self._fail_step(step, str(exc), latency_ms)
            await self._fail_run(run, str(exc))
            await self._commit_with_hook(run, on_pre_commit)
            logger.error(
                "agent_run_preflight_failed run_id=%s agent_id=%s error=%s"
                if isinstance(exc, PreFlightCheckFailed)
                else "agent_run_failed run_id=%s agent_id=%s error=%s",
                str(run.id),
                agent_id,
                str(exc),
            )
            raise

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        await self._apply_agent_output(step, run, output, latency_ms, on_pre_commit)

        logger.info(
            "agent_run_completed run_id=%s agent_id=%s subject_id=%s "
            "confidence=%.2f evidence_count=%d latency_ms=%d awaiting_input=%s",
            str(run.id),
            agent_id,
            subject.subject_id,
            output.confidence.score,
            len(output.evidence),
            latency_ms,
            output.awaiting_input,
        )

        return run

    async def resume_step(
        self,
        run: Run,
        step: AgentStep,
        agent_id: str,
        agent: object,
        subject: Subject,
        goal: str,
        model: str | None = None,
        extras: dict[str, Any] | None = None,
        on_pre_commit: Callable[[AsyncSession, Run], Awaitable[None]] | None = None,
    ) -> Run:
        """Re-invoke `agent` against an existing, previously-paused `step`
        (status "awaiting_input") instead of creating a new one — how a
        `POST /workflows/{id}/clarify` answer resumes Context Discovery from
        where it left off. `run`/`step` must already exist in this
        coordinator's session (fetched by the caller under
        `get_workflow_for_update`'s row lock).

        Mirrors `execute_run`'s tail exactly (same `_apply_agent_output`
        branch, same failure handling) — the only difference is that no new
        `AgentStep` row is created here.
        """
        step.status = "running"
        run.status = "running"
        # See execute_run's identical commit for why this must be a commit,
        # not just a flush — otherwise a poller reading from a different
        # DB session never observes this transition until the whole run
        # finishes.
        await self._db.commit()

        ctx_extras: dict[str, Any] = {
            "db": self._db,
            "user_id": run.user_id,
            "stage": run.workflow_stage,
            "run_id": run.id,
            "agent_step_id": step.id,
        }
        if extras:
            ctx_extras.update(extras)

        manifest_entry = self._registry.get(agent_id)
        max_graph_hops = manifest_entry[0].max_graph_hops if manifest_entry is not None else 0

        if "graph_repository" not in ctx_extras and manifest_entry is not None:
            from app.graph.hop_budget import build_hop_budgeted_repository
            from app.graph.neo4j_repository import Neo4jGraphRepository
            from app.graph.session import get_driver

            ctx_extras["graph_repository"] = build_hop_budgeted_repository(
                Neo4jGraphRepository(get_driver()), max_graph_hops, agent_id
            )

        context = AgentContext(subject=subject, goal=goal, model=model, extras=ctx_extras)
        start_ms = time.monotonic()

        try:
            # Same pre-flight gate as execute_run, and same reason it lives
            # *inside* this try block rather than before it — see that
            # method's comment for the full rationale (a bare call here
            # left resolve()'s non-credential-missing failure modes, e.g.
            # UnsupportedProviderError from a stale stored provider key,
            # unguarded and able to escape past _fail_step/_fail_run).
            preflight_failure = check_llm_provider_configured(
                agent_id, run.workflow_stage
            ) or await check_neo4j_reachable(max_graph_hops)
            if preflight_failure is not None:
                raise PreFlightCheckFailed(f"Pre-flight check failed: {preflight_failure}")

            # WARNING-severity pre-flight (ADR 0011, OD-1/OD-3/PR3) — same
            # placement and reasoning as execute_run's own copy of this
            # block; see that method's comment for the full rationale.
            if manifest_entry is not None:
                warnings = await collect_preflight_warnings(
                    manifest_entry[0], self._db, run.user_id
                )
                record_preflight_warnings(step, warnings)

            output: AgentOutput = await agent.run(context)  # type: ignore[attr-defined]
        except Exception as exc:
            latency_ms = int((time.monotonic() - start_ms) * 1000)
            await self._fail_step(step, str(exc), latency_ms)
            await self._fail_run(run, str(exc))
            await self._commit_with_hook(run, on_pre_commit)
            logger.error(
                "agent_run_resume_preflight_failed run_id=%s agent_id=%s error=%s"
                if isinstance(exc, PreFlightCheckFailed)
                else "agent_run_resume_failed run_id=%s agent_id=%s error=%s",
                str(run.id),
                agent_id,
                str(exc),
            )
            raise

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        await self._apply_agent_output(step, run, output, latency_ms, on_pre_commit)

        logger.info(
            "agent_run_resumed run_id=%s agent_id=%s subject_id=%s "
            "confidence=%.2f latency_ms=%d awaiting_input=%s",
            str(run.id),
            agent_id,
            subject.subject_id,
            output.confidence.score,
            latency_ms,
            output.awaiting_input,
        )

        return run

    async def _apply_agent_output(
        self,
        step: AgentStep,
        run: Run,
        output: AgentOutput,
        latency_ms: int,
        on_pre_commit: Callable[[AsyncSession, Run], Awaitable[None]] | None,
    ) -> None:
        """Persist `output` onto `step`/`run`, branching on whether the agent
        is pausing for human input (`output.awaiting_input`) or has actually
        finished. Shared by `execute_run` and `resume_step` so both paths
        agree on exactly what "completed" vs "awaiting_input" means.
        """
        now = datetime.now(UTC)

        step.confidence_score = output.confidence.score
        step.confidence_reasoning = output.confidence.reasoning
        step.evidence = [e.model_dump() for e in output.evidence]
        step.result = output.result
        step.graph_facts_written = output.graph_facts_written
        step.prompt_version = output.prompt_version
        step.output_ref = output.output_ref
        step.latency_ms = latency_ms

        if output.awaiting_input:
            step.status = "awaiting_input"
            run.status = "awaiting_input"
        else:
            step.status = "completed"
            step.completed_at = now
            run.status = "completed"
            run.completed_at = now

        await self._commit_with_hook(run, on_pre_commit)

    async def _commit_with_hook(
        self,
        run: Run,
        on_pre_commit: Callable[[AsyncSession, Run], Awaitable[None]] | None,
    ) -> None:
        """Commit `run`'s pending status change, atomically with whatever
        `on_pre_commit` also mutates (see execute_run's docstring). Falls
        back to a plain commit — of the run's own status alone — if the
        hook is absent or raises, so a caller's bookkeeping bug can never
        cost the run its own recorded outcome.

        `run_id` is captured *before* anything that could fail, never read
        off `run` again inside a failure branch: once a commit has failed,
        `run`'s attributes may be expired, and reading one (even just for a
        log message) issues a query against a session SQLAlchemy has
        already flagged as needing an explicit `rollback()` first — which
        raised `PendingRollbackError` right out of the exception handler
        that was trying to report the *original* failure, so the run was
        never marked failed at all and sat at status="running" forever.
        See `_commit_or_fail` for the guarantee this method now makes:
        `run` always ends this call in an explicit terminal state if
        persisting its real outcome didn't succeed.
        """
        run_id = run.id
        if on_pre_commit is None:
            await self._commit_or_fail(run, run_id)
            return
        try:
            await on_pre_commit(self._db, run)
        except Exception:
            logger.exception("run_coordinator_pre_commit_hook_failed run_id=%s", run_id)
            await self._commit_or_fail(run, run_id)

    async def _commit_or_fail(self, run: Run, run_id: uuid.UUID) -> None:
        """Commit `run`'s pending changes. If the commit itself fails (a
        non-JSON-serializable value slipping into a JSON column being the
        one confirmed real-world case — see `context_pipeline.reasoning.
        engine._apply_memory_priority_boost`'s own docstring — but this
        must hold for *any* commit failure, not just that one), the
        transaction is rolled back and a second, minimal attempt persists
        an explicit failure onto `run` alone. If even that fails, a brand
        new session — entirely independent of whatever state poisoned this
        one — is used as the last resort. One of these three always
        succeeds short of the database itself being unreachable, which
        `recover_orphaned_runs`/the periodic stale-run sweep (see
        `app.orchestrator.background_execution`) exists to catch instead.

        The one guarantee this whole method exists for: `run` never exits
        `_commit_with_hook` sitting at status="running"/"awaiting_input"
        with its *real* outcome silently lost.
        """
        try:
            await self._db.commit()
            return
        except Exception as exc:
            logger.exception("run_coordinator_commit_failed run_id=%s", run_id)
            first_error = exc

        try:
            await self._db.rollback()
            run.status = "failed"
            run.error_message = f"Internal error while saving results: {first_error}"
            run.completed_at = datetime.now(UTC)
            await self._db.commit()
            return
        except Exception:
            logger.exception("run_coordinator_failure_persist_failed run_id=%s", run_id)
            try:
                await self._db.rollback()
            except Exception:
                logger.exception("run_coordinator_rollback_failed run_id=%s", run_id)

        await self._force_fail_run(run_id, str(first_error))

    async def _force_fail_run(self, run_id: uuid.UUID, error: str) -> None:
        """Last resort: mark `run_id` failed through a brand new session,
        bypassing this coordinator's own — which, by the time this is
        called, has failed to commit twice in a row and cannot be trusted
        not to fail a third time on whatever state it's still carrying. A
        plain Core `UPDATE` against only `agent_runs`, not the ORM object
        graph that got this coordinator into trouble, and not touching
        `agent_steps` at all — the step's own bad data stays exactly as
        broken as it already was; this only guarantees the *run* stops
        lying about being "running" forever."""
        from sqlalchemy import update

        from app.database.session import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                stuck_statuses = ("running", "awaiting_input", "queued")
                await db.execute(
                    update(Run)
                    .where(Run.id == run_id, Run.status.in_(stuck_statuses))
                    .values(
                        status="failed",
                        error_message=f"Internal error while saving results: {error}",
                        completed_at=datetime.now(UTC),
                    )
                )
                await db.commit()
        except Exception:
            # Truly last resort — the database itself may be unreachable.
            # `recover_orphaned_runs` (startup) and the periodic stale-run
            # sweep are the remaining backstops; nothing further to do from
            # inside this request/task.
            logger.critical(
                "run_coordinator_force_fail_failed run_id=%s — run may remain stuck at a "
                "non-terminal status until the next orphan sweep",
                run_id,
            )

    async def _fail_step(self, step: AgentStep, error: str, latency_ms: int) -> None:
        step.status = "failed"
        step.error_message = error
        step.latency_ms = latency_ms
        step.completed_at = datetime.now(UTC)

    async def _fail_run(self, run: Run, error: str) -> None:
        run.status = "failed"
        run.error_message = error
        run.completed_at = datetime.now(UTC)
