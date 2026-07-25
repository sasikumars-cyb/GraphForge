"""System status endpoint: aggregates platform health for the Control Center."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse, summary="Platform status")
async def system_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SystemStatusResponse:
    settings = get_settings()

    # ── AI Providers ────────────────────────────────────────────────
    active_provider = settings.ai_provider.lower()
    providers = [
        ProviderStatus(
            name="openai",
            configured=settings.openai_api_key is not None,
            active=active_provider == "openai",
            model=settings.openai_model if active_provider == "openai" else None,
        ),
        ProviderStatus(
            name="gemini",
            configured=settings.gemini_api_key is not None,
            active=active_provider == "gemini",
            model=settings.gemini_model if active_provider == "gemini" else None,
        ),
        ProviderStatus(
            name="groq",
            configured=settings.groq_api_key is not None,
            active=active_provider == "groq",
            model=settings.groq_model if active_provider == "groq" else None,
        ),
        ProviderStatus(
            name="deepseek",
            configured=settings.deepseek_api_key is not None,
            active=active_provider == "deepseek",
            model=settings.deepseek_model if active_provider == "deepseek" else None,
        ),
    ]
    current_provider = next((p for p in providers if p.active), providers[0])

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

    # Jira
    connections.append(
        ConnectionStatus(
            name="Jira",
            status="configured" if settings.jira_base_url else "not_configured",
            detail=settings.jira_base_url,
        )
    )

    # ── Knowledge Base ──────────────────────────────────────────────
    repo_count_result = await db.execute(
        select(func.count()).select_from(Repository).where(
            Repository.user_id == current_user.id
        )
    )
    repos_tracked = repo_count_result.scalar() or 0

    # Count repos with at least one completed indexing job
    indexed_result = await db.execute(
        select(func.count(func.distinct(IndexingJob.repository_id))).where(
            IndexingJob.status == "completed"
        )
    )
    repos_indexed = indexed_result.scalar() or 0

    # Count repos with pending/running indexing
    pending_result = await db.execute(
        select(func.count(func.distinct(IndexingJob.repository_id))).where(
            IndexingJob.status.in_(["pending", "running"])
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
