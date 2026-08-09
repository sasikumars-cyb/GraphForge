"""FastAPI application entrypoint.

Run with: `uvicorn app.main:app --reload` (see scripts/dev.sh).
Swagger UI is served automatically at `/docs` (ReDoc at `/redoc`, the raw
schema at `/openapi.json`) - no extra wiring needed beyond the metadata below.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

# Imported for their module-level `register_handler(...)` side effects, not
# any name used directly here — the durable-queue Worker started below must
# not begin polling before every job_type it might claim has a handler
# registered (see app.orchestrator.worker's registry docstring). Explicit,
# eager imports here (rather than relying on some route handler importing
# them lazily on first request) guarantee that ordering, the same way
# alembic/env.py explicitly imports every model module rather than trusting
# something else to have done it first. Kept below the normal import block
# (not sorted into it) so this ordering rationale stays visually attached
# to the two imports it explains.
import app.orchestrator.background_execution as background_execution  # noqa: E402
from app.agents.setup import register_agents
from app.ai.config import store
from app.api.v1.routers import api_router
from app.core.config import get_settings
from app.core.error_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id_middleware import RequestIDMiddleware
from app.database.session import AsyncSessionLocal, engine
from app.graph.session import close_driver
from app.indexer.workers import index_worker  # noqa: F401, E402
from app.orchestrator.worker import Worker, reclaim_expired_leases_once
from app.tools.setup import register_all_tools, sync_all_knowledge_connections_to_tools

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Liveness/readiness checks used by orchestrators and load balancers.",
    },
    {
        "name": "auth",
        "description": "Registration, login, and the current user. The /auth/github/* routes "
        "are a separate, still-unimplemented login-via-GitHub use case and return 501.",
    },
    {
        "name": "github",
        "description": "'Connect GitHub' for repo access (not login) - OAuth flow and the "
        "live repository list.",
    },
    {
        "name": "repositories",
        "description": "Selected/tracked repositories and their ingested pull requests.",
    },
    {
        "name": "pull-requests",
        "description": "Deterministic pull request impact analysis (Phase 7) - no AI/LLM "
        "involved. Requires the pull request's repository to already be indexed.",
    },
    {
        "name": "webhooks",
        "description": "Inbound webhook receivers. Signature-verified, not JWT-authenticated.",
    },
]


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Run lightweight schema migrations on startup.

        This ensures new columns exist even when alembic hasn't been run
        explicitly. Uses IF NOT EXISTS so it's safe to run repeatedly.
        """
        async with engine.begin() as conn:
            # Ensure the 'role' column exists on users table.
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role "
                    "VARCHAR(32) NOT NULL DEFAULT 'user'"
                )
            )
            # Ensure knowledge_connections table exists.
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS knowledge_connections (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source_type VARCHAR(64) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    transport VARCHAR(32) NOT NULL,
                    auth_method VARCHAR(32) NOT NULL,
                    config JSONB NOT NULL DEFAULT '{}',
                    encrypted_credentials VARCHAR(4096),
                    scope JSONB NOT NULL DEFAULT '{}',
                    enabled BOOLEAN NOT NULL DEFAULT true,
                    status VARCHAR(32) NOT NULL DEFAULT 'unknown',
                    status_detail TEXT,
                    last_sync_at TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    latency_ms INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            )
            await conn.execute(
                text("""
                CREATE INDEX IF NOT EXISTS ix_knowledge_connections_source_type
                ON knowledge_connections (source_type)
            """)
            )
            # No admin-promotion SQL here on purpose — the bootstrap admin
            # account is seeded once, by the b5c6d7e8f9a0 migration, not on
            # every process start. An unconditional
            # `UPDATE users SET role='admin' WHERE email=...` running in
            # every environment (including production) combined with this
            # app's open self-registration meant anyone could register that
            # exact email and be auto-promoted to admin on the next
            # restart/deploy — a real privilege-escalation path, not a
            # hypothetical one. See b5c6d7e8f9a0's own docstring for the
            # production-safety note on that seeded account.

        # KAN-18: requeue any BackgroundJob a previous process's worker
        # abandoned mid-lease (crashed/killed before completing) — the
        # queue-side half of startup recovery. Must run before
        # recover_orphaned_runs, which only fails a Run once it confirms no
        # job can still retry it.
        reclaimed = await reclaim_expired_leases_once()
        if reclaimed:
            logger.warning("reclaimed_expired_job_leases count=%d", reclaimed)

        # Backstop for what even a reclaimed lease can't cover: a Run with
        # no BackgroundJob left able to retry it (dead-lettered, or none
        # ever existed — e.g. a row from before this migration).
        async with AsyncSessionLocal() as db:
            await background_execution.recover_orphaned_runs(db)

        # Activate Tool Registry entries (Jira, Confluence, ...) for any
        # Knowledge Connection (Settings → Integrations) made in an earlier
        # process — new/updated connections sync immediately via
        # knowledge.py, this just covers what already existed at restart.
        async with AsyncSessionLocal() as db:
            await sync_all_knowledge_connections_to_tools(db)

        # Load the AI provider config snapshot (app.ai.config.store) up
        # front, rather than leaving it at its empty, loaded=False default
        # until some ai_workspace request happens to call ensure_loaded()
        # first. Without this, `resolver.resolve()` — synchronous, and
        # called from every agent's LLM call, including the embedded
        # Worker's background job execution below — reads that still-empty
        # snapshot, sees no stored default_provider, and silently falls all
        # the way through to the environment-tier provider (Settings
        # .ai_provider, e.g. "groq" in dev's .env) instead of whatever
        # provider was actually selected in the UI. That's not a transient
        # blip: it lasts from process start until the first ai_workspace
        # GET/PUT, which for the Worker's own background runs may never
        # happen at all — every run until then silently uses the wrong
        # provider with no error, only a confusing downstream failure (e.g.
        # a rate limit on a provider nobody chose). One eager load here,
        # symmetric with the recovery steps above, closes that window.
        async with AsyncSessionLocal() as db:
            await store.refresh(db)

        # KAN-18: an embedded worker, polling the durable queue on this same
        # process — what makes agent runs, resumes, and indexing jobs
        # actually execute now that scheduling them only enqueues a row (see
        # app.orchestrator.background_execution's module docstring). Kept
        # in-process rather than requiring a separate deployed worker
        # service so local dev and today's single-replica deployment need
        # no new infrastructure; nothing here stops a dedicated worker
        # process (a second `Worker().run_forever()` elsewhere, pointed at
        # the same Postgres) from also claiming jobs once one exists — the
        # SELECT ... FOR UPDATE SKIP LOCKED claim is safe for any number of
        # concurrent workers, in this process or another.
        worker_stop_event = asyncio.Event()
        worker_task = asyncio.create_task(Worker().run_forever(worker_stop_event))

        # P2 — the periodic counterpart to `recover_orphaned_runs` above:
        # that one only catches what a *restart* can see (a Run whose job
        # is no longer queued/leased). A Run whose in-process task died
        # silently without a restart — the real incident `RunCoordinator.
        # _commit_or_fail` was added for, and the class of bug it's a
        # backstop against more generally — needs a wall-clock sweep
        # instead. Same stop-event/graceful-shutdown shape as the worker
        # task right above it.
        stale_run_sweep_stop_event = asyncio.Event()
        stale_run_sweep_task = asyncio.create_task(
            background_execution.run_stale_run_sweep_forever(stale_run_sweep_stop_event)
        )

        yield

        # Graceful shutdown: stop claiming new jobs and let any in-flight
        # one finish (Worker.run_forever awaits its own in-flight tasks
        # once the stop event is set) before tearing down the connections
        # those jobs depend on.
        worker_stop_event.set()
        await worker_task
        stale_run_sweep_stop_event.set()
        await stale_run_sweep_task

        # Graceful shutdown: release the Neo4j connection pool and dispose
        # the SQLAlchemy engine's own pool, rather than leaving both open
        # until the OS reclaims them at process exit.
        await close_driver()
        await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        description="AI-grounded change impact analysis: dependency graphs, "
        "UML impact views, and risk-scored evidence for every proposed change.",
        version="0.1.0",
        debug=False,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added after CORSMiddleware so it becomes the outermost layer (Starlette
    # runs middlewares in reverse registration order) — the request id must
    # exist before anything else in the stack has a chance to log.
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    register_all_tools()
    register_agents()

    return app


app = create_app()
