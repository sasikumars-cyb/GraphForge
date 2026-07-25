"""DTOs for the AI Workspace API.

Security invariant: no response model here has a field that can carry an API
key. Keys are write-only (`api_key` on the request DTO) and are reported back
only as the boolean `api_key_configured`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    id: str
    label: str
    context_window: int | None = None


class ProviderInfo(BaseModel):
    """A provider as the AI Workspace sees it: registry metadata + config +
    health, merged. The UI renders this generically — it holds no
    provider-specific knowledge of its own."""

    key: str
    label: str
    implemented: bool
    notes: str = ""

    # From the registry — never duplicated in the frontend.
    capabilities: list[str] = Field(default_factory=list)
    models: list[ModelInfo] = Field(default_factory=list)
    requires_api_key: bool = True
    default_model: str = ""

    # From stored configuration.
    configured: bool = False
    enabled: bool = True
    api_key_configured: bool = False
    model: str | None = None
    base_url: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    # Health.
    status: str = "unknown"
    status_detail: str | None = None
    last_validated_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None


class ProviderUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Write-only. Omit to leave the stored key untouched; send "" to clear it.
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    enabled: bool | None = None


class ValidationResponse(BaseModel):
    provider_key: str
    ok: bool
    status: str
    model: str
    latency_ms: int
    message: str


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class ProfileInfo(BaseModel):
    slug: str
    name: str
    description: str = ""
    provider_key: str
    provider_label: str = ""
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_level: str | None = None
    structured_output: bool = True
    streaming: bool = False
    fallback_profile_slug: str | None = None
    is_system: bool = False
    # Resolved view so the UI can show what a profile actually runs without
    # re-implementing precedence rules.
    effective_model: str = ""
    provider_status: str = "unknown"


class ProfileUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    provider_key: str
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_level: str | None = None
    structured_output: bool = True
    streaming: bool = False
    fallback_profile_slug: str | None = None


# ---------------------------------------------------------------------------
# Settings / defaults / mapping / fallback
# ---------------------------------------------------------------------------


class AIWorkspaceSettings(BaseModel):
    default_profile_slug: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    # stage -> {"profile": slug} | {"provider": key, "model": id}
    stage_overrides: dict = Field(default_factory=dict)
    fallback_order: list[str] = Field(default_factory=list)
    fallback_enabled: bool = False


class AIWorkspaceSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default_profile_slug: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stage_overrides: dict | None = None
    fallback_order: list[str] | None = None
    fallback_enabled: bool | None = None


class StageMapping(BaseModel):
    stage: str
    label: str
    profile_slug: str | None = None
    profile_name: str | None = None
    # What this stage resolves to right now, after full precedence.
    effective_provider: str = ""
    effective_model: str = ""
    source: str = ""


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class ProviderUsage(BaseModel):
    provider_key: str
    requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_limit_events: int = 0
    auth_failures: int = 0
    average_latency_ms: int | None = None
    last_request_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_rate_limit_at: datetime | None = None
    last_error: str | None = None


class AIWorkspaceOverview(BaseModel):
    """Everything the AI Workspace needs in one round trip."""

    providers: list[ProviderInfo]
    profiles: list[ProfileInfo]
    settings: AIWorkspaceSettings
    stages: list[StageMapping]
    usage: list[ProviderUsage]
