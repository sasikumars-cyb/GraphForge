"""API Intelligence Agent export endpoints — re-render a completed
`analyze_api_intelligence` run in every output format the agent's spec
asks for (OpenAPI YAML, Postman collection, Markdown summary, HTML
dashboard, JSON), without re-invoking the LLM.

Deliberately a thin, read-only follow-up router — same shape as
`app.api.v1.routers.documentation`'s create-PR endpoint, minus any write:
this agent never proposes or applies changes (Phase 1 is Markdown-in,
report-out only), so there is nothing here but export/rendering.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.api_intelligence.renderers import (
    render_html_dashboard,
    render_json,
    render_markdown_summary,
    render_openapi_yaml,
    render_postman_collection,
)
from app.agents.api_intelligence.schemas import ApiIntelligenceResult
from app.api.v1.dependencies import get_current_user
from app.core.exceptions import AppError, NotFoundError
from app.database.session import get_db_session
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.models.user import User

router = APIRouter(prefix="/api-intelligence", tags=["api-intelligence"])

ExportFormat = Literal["openapi", "postman", "markdown", "html", "json"]

_CONTENT_TYPES: dict[ExportFormat, str] = {
    "openapi": "application/yaml",
    "postman": "application/json",
    "markdown": "text/markdown",
    "html": "text/html",
    "json": "application/json",
}
_FILE_EXTENSIONS: dict[ExportFormat, str] = {
    "openapi": "yaml",
    "postman": "json",
    "markdown": "md",
    "html": "html",
    "json": "json",
}


class UnsupportedExportFormatError(AppError):
    status_code = 400
    error_code = "api_intelligence_unsupported_export_format"


async def _load_completed_result(
    db: AsyncSession, run_id: uuid.UUID, user_id: uuid.UUID
) -> ApiIntelligenceResult:
    run_result = await db.execute(
        select(Run).where(
            Run.id == run_id, Run.user_id == user_id, Run.goal == "analyze_api_intelligence"
        )
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"API Intelligence run '{run_id}' not found for this account.")
    if run.status != "completed":
        raise UnsupportedExportFormatError(
            f"Run '{run_id}' is not completed (status: {run.status})."
        )

    step_result = await db.execute(
        select(AgentStep)
        .where(AgentStep.run_id == run.id)
        .order_by(AgentStep.created_at.desc())
        .limit(1)
    )
    step = step_result.scalar_one_or_none()
    if step is None or not step.result:
        raise NotFoundError(f"No result recorded for run '{run_id}'.")
    return ApiIntelligenceResult.model_validate(step.result)


@router.get("/runs/{run_id}/export/{export_format}")
async def export_api_intelligence(
    run_id: uuid.UUID,
    export_format: ExportFormat,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Render a completed run as one of: openapi (YAML), postman (JSON
    collection), markdown (summary report), html (the dashboard), or json
    (the raw result). Pure rendering of already-persisted data — no LLM
    call, no side effect, safe to call repeatedly."""
    result = await _load_completed_result(db, run_id, user.id)

    if export_format == "openapi":
        body = render_openapi_yaml(result)
    elif export_format == "postman":
        body = json.dumps(render_postman_collection(result), indent=2)
    elif export_format == "markdown":
        body = render_markdown_summary(result)
    elif export_format == "html":
        body = render_html_dashboard(result)
    else:
        body = render_json(result)

    filename = f"api-intelligence-{run_id}.{_FILE_EXTENSIONS[export_format]}"
    return Response(
        content=body,
        media_type=_CONTENT_TYPES[export_format],
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
