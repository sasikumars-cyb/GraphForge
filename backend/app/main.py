"""FastAPI application entrypoint.

Run with: `uvicorn app.main:app --reload` (see scripts/dev.sh).
Swagger UI is served automatically at `/docs` (ReDoc at `/redoc`, the raw
schema at `/openapi.json`) - no extra wiring needed beyond the metadata below.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.setup import register_agents
from app.api.v1.routers import api_router
from app.core.config import get_settings
from app.core.error_handlers import register_exception_handlers
from app.core.logging import configure_logging

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

    app = FastAPI(
        title=settings.app_name,
        description="AI-grounded change impact analysis: dependency graphs, "
        "UML impact views, and risk-scored evidence for every proposed change.",
        version="0.1.0",
        # Deliberately NOT settings.debug: FastAPI/Starlette's own `debug=True`
        # renders an HTML traceback page for unhandled exceptions and skips
        # our registered `Exception` handler entirely, which would silently
        # defeat the consistent JSON error contract in core.error_handlers.
        # `settings.debug` still controls log verbosity (see core.logging).
        debug=False,
        openapi_tags=OPENAPI_TAGS,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    register_agents()

    return app


app = create_app()
