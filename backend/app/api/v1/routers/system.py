"""System status endpoint: aggregates platform health for the Control Center."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.config.resolver import env_credentials_for
from app.ai.providers.registry import all_providers, get_provider_spec
from app.api.v1.dependencies import get_current_user
from app.core.config import get_settings
from app.database.session import get_db_session
from app.models.indexing_job import IndexingJob
from app.models.repository import Repository
from app.models.user import User
from app.schemas.system import (
    ConnectionStatus,
    KnowledgeBaseStatus,
    ProviderStatus,
    SystemStatusResponse,
)
from app.tools.registry import get_tool_registry

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse, summary="Platform status")
async def system_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SystemStatusResponse:
    settings = get_settings()

    # ── AI Providers ────────────────────────────────────────────────
    # Derived from the provider registry rather than a hardcoded list. The
    # registry is explicitly "the single place a new AI provider is
    # declared" (see app.ai.providers.registry), and this endpoint used to
    # violate that with its own openai/gemini/groq triple. The cost was not
    # theoretical: Bedrock — which needs no API key because it uses the AWS
    # credential chain — was absent entirely, so a working Bedrock install
    # fell through to `providers[0]` (openai, unconfigured) and the Control
    # Center reported "Degraded / AI Provider: none" while every agent run
    # was in fact succeeding. Reading the registry means any provider added
    # there is reported here automatically.
    active_spec = get_provider_spec(settings.ai_provider)
    active_key = active_spec.key if active_spec else settings.ai_provider.strip().lower()

    providers: list[ProviderStatus] = []
    for spec in all_providers():
        if not spec.implemented:
            # Declared-but-unbuildable providers are roadmap entries, not
            # health signals — listing them here would imply the platform
            # is missing configuration it can't actually accept yet.
            continue
        api_key, env_model = env_credentials_for(spec.key, settings)
        # A provider with no API key requirement (Bedrock's AWS credential
        # chain, a local Ollama) is configured as soon as it's declared —
        # there is no key to check, and the credential chain can only be
        # validated by making a real call, which a status endpoint must not do.
        configured = True if not spec.requires_api_key else bool(api_key)
        is_active = spec.key == active_key
        providers.append(
            ProviderStatus(
                name=spec.key,
                configured=configured,
                active=is_active,
                model=(env_model or spec.resolve_default_model()) if is_active else None,
            )
        )

    # Fall back to the *declared active* provider rather than whichever spec
    # happens to sort first, so an unknown/misconfigured AI_PROVIDER value
    # is reported as itself-unconfigured instead of silently blaming openai.
    current_provider = next(
        (p for p in providers if p.active),
        ProviderStatus(name=active_key, configured=False, active=False, model=None),
    )

    # ── Connections ─────────────────────────────────────────────────
    connections: list[ConnectionStatus] = []

    # GitHub
    github_configured = settings.github_client_id is not None
    connections.append(
        ConnectionStatus(
            name="GitHub",
            status="configured" if github_configured else "not_configured",
            detail="OAuth app configured" if github_configured else None,
        )
    )

    # Neo4j
    connections.append(
        ConnectionStatus(
            name="Neo4j",
            status="configured",
            detail=settings.neo4j_uri,
        )
    )

    # PostgreSQL
    connections.append(
        ConnectionStatus(
            name="PostgreSQL",
            status="connected",
            detail="Primary datastore",
        )
    )

    # Jira — configured via a Knowledge Connection (Settings → Integrations),
    # not the global JIRA_BASE_URL env var, so ask the tool registry (which
    # tools/setup.py syncs from Knowledge Connections) instead of settings.
    jira_configured = get_tool_registry().is_enabled("jira")
    connections.append(
        ConnectionStatus(
            name="Jira",
            status="configured" if jira_configured else "not_configured",
            detail=(
                (settings.jira_base_url or "Via Knowledge Connection") if jira_configured else None
            ),
        )
    )

    # ── Knowledge Base ──────────────────────────────────────────────
    repo_count_result = await db.execute(
        select(func.count()).select_from(Repository).where(Repository.user_id == current_user.id)
    )
    repos_tracked = repo_count_result.scalar() or 0

    # Count repos with at least one completed indexing job — joined through
    # to Repository.user_id, same as repos_tracked above. Without this join,
    # this counted every user's indexing jobs: user A's dashboard showed
    # progress that included user B's repositories, both an incorrect
    # figure and a minor cross-tenant data leak (counts only, not content).
    indexed_result = await db.execute(
        select(func.count(func.distinct(IndexingJob.repository_id)))
        .select_from(IndexingJob)
        .join(Repository, IndexingJob.repository_id == Repository.id)
        .where(
            IndexingJob.status == "completed",
            Repository.user_id == current_user.id,
        )
    )
    repos_indexed = indexed_result.scalar() or 0

    # Count repos with pending/running indexing — same per-user scoping.
    pending_result = await db.execute(
        select(func.count(func.distinct(IndexingJob.repository_id)))
        .select_from(IndexingJob)
        .join(Repository, IndexingJob.repository_id == Repository.id)
        .where(
            IndexingJob.status.in_(["pending", "running"]),
            Repository.user_id == current_user.id,
        )
    )
    repos_pending = pending_result.scalar() or 0

    knowledge_base = KnowledgeBaseStatus(
        repositories_tracked=repos_tracked,
        repositories_indexed=repos_indexed,
        repositories_pending=repos_pending,
    )

    # ── Overall status ──────────────────────────────────────────────
    has_active_provider = current_provider.configured
    platform_status = "healthy" if has_active_provider else "degraded"

    return SystemStatusResponse(
        platform_status=platform_status,
        environment=settings.environment,
        version="0.1.0",
        ai_provider=current_provider,
        ai_providers=providers,
        connections=connections,
        knowledge_base=knowledge_base,
    )
