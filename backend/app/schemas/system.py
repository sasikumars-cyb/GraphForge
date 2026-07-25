"""Response schemas for the system status endpoint."""

from pydantic import BaseModel


class ProviderStatus(BaseModel):
    name: str
    configured: bool
    active: bool
    model: str | None = None


class ConnectionStatus(BaseModel):
    name: str
    status: str  # "connected" | "configured" | "not_configured"
    detail: str | None = None


class KnowledgeBaseStatus(BaseModel):
    repositories_tracked: int
    repositories_indexed: int
    repositories_pending: int


class SystemStatusResponse(BaseModel):
    platform_status: str  # "healthy" | "degraded" | "error"
    environment: str
    version: str
    ai_provider: ProviderStatus
    ai_providers: list[ProviderStatus]
    connections: list[ConnectionStatus]
    knowledge_base: KnowledgeBaseStatus
