"""Tool Registry API — list, health-check, and configure registered tools.

GET  /api/v1/tools          → list all tool specs + health + enabled status
GET  /api/v1/tools/{id}     → single tool detail
POST /api/v1/tools/{id}/health → run a live health check
PUT  /api/v1/tools/{id}     → enable/disable + update config (not yet persisted to DB)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.v1.dependencies import require_admin
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.tools.interfaces import ToolHealth
from app.tools.registry import ToolSpec, get_tool_registry

router = APIRouter(prefix="/tools", tags=["tools"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ToolResponse(BaseModel):
    tool_id: str
    display_name: str
    description: str
    category: str
    capabilities: list[str]
    requires_auth: bool
    auth_fields: list[str]
    default_enabled: bool
    enabled: bool
    health: str
    icon: str
    notes: str


class HealthCheckResponse(BaseModel):
    tool_id: str
    health: str


class ConfigureToolRequest(BaseModel):
    enabled: bool
    config: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec_to_response(spec: ToolSpec) -> ToolResponse:
    registry = get_tool_registry()
    return ToolResponse(
        tool_id=spec.tool_id,
        display_name=spec.display_name,
        description=spec.description,
        category=spec.category.value,
        capabilities=spec.capabilities,
        requires_auth=spec.requires_auth,
        auth_fields=spec.auth_fields,
        default_enabled=spec.default_enabled,
        enabled=registry.is_enabled(spec.tool_id),
        health=registry.get_health(spec.tool_id).value,
        icon=spec.icon,
        notes=spec.notes,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ToolResponse])
async def list_tools(_: User = Depends(require_admin)) -> list[ToolResponse]:
    """Return all registered tool specs with their current health and enabled status."""
    registry = get_tool_registry()
    return [_spec_to_response(spec) for spec in registry.all_specs()]


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(tool_id: str, _: User = Depends(require_admin)) -> ToolResponse:
    """Return a single tool's spec, health, and enabled status."""
    registry = get_tool_registry()
    specs = {s.tool_id: s for s in registry.all_specs()}
    spec = specs.get(tool_id)
    if spec is None:
        raise NotFoundError(f"Tool '{tool_id}' not found.")
    return _spec_to_response(spec)


@router.post("/{tool_id}/health", response_model=HealthCheckResponse)
async def check_health(tool_id: str, _: User = Depends(require_admin)) -> HealthCheckResponse:
    """Run a live health check for one tool and cache the result."""
    registry = get_tool_registry()
    specs = {s.tool_id: s for s in registry.all_specs()}
    if tool_id not in specs:
        raise NotFoundError(f"Tool '{tool_id}' not found.")
    health: ToolHealth = await registry.check_health(tool_id)
    return HealthCheckResponse(tool_id=tool_id, health=health.value)


@router.put("/{tool_id}", response_model=ToolResponse)
async def configure_tool(tool_id: str, body: ConfigureToolRequest, _: User = Depends(require_admin)) -> ToolResponse:
    """Enable or disable a tool and apply runtime configuration.

    Note: in this release configuration is applied in-memory only and is
    not persisted to the database. A full persistence layer (DB model +
    migration) is tracked as a follow-up.
    """
    registry = get_tool_registry()
    specs = {s.tool_id: s for s in registry.all_specs()}
    spec = specs.get(tool_id)
    if spec is None:
        raise NotFoundError(f"Tool '{tool_id}' not found.")
    registry.configure(tool_id, enabled=body.enabled, config=body.config)
    return _spec_to_response(spec)
