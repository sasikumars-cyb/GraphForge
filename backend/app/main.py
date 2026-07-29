"""FastAPI application entrypoint.

Run with: `uvicorn app.main:app --reload` (see scripts/dev.sh).
Swagger UI is served automatically at `/docs` (ReDoc at `/redoc`, the raw
schema at `/openapi.json`) - no extra wiring needed beyond the metadata below.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.agents.setup import register_agents
from app.api.v1.routers import api_router
from app.core.config import get_settings
from app.core.error_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id_middleware import RequestIDMiddleware
from app.database.session import AsyncSessionLocal, engine
from app.graph.session import close_driver
from app.orchestrator.background_execution import recover_orphaned_runs
from app.tools.setup import register_all_tools, sync_all_knowledge_connections_to_tools

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
            await conn.execute(text("""
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
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_knowledge_connections_source_type
                ON knowledge_connections (source_type)
            """))
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

        # Recover any Run left "running" by a previous process — background
        # execution (see app.orchestrator.background_execution) runs on
        # asyncio.create_task, which does not survive a restart.
        async with AsyncSessionLocal() as db:
            await recover_orphaned_runs(db)

        # Activate Tool Registry entries (Jira, Confluence, ...) for any
        # Knowledge Connection (Settings → Integrations) made in an earlier
        # process — new/updated connections sync immediately via
        # knowledge.py, this just covers what already existed at restart.
        async with AsyncSessionLocal() as db:
            await sync_all_knowledge_connections_to_tools(db)

        yield

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
