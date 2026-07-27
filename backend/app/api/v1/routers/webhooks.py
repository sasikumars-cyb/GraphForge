"""Receives GitHub webhook deliveries. Signature-verified (HMAC, shared
secret), not JWT-authenticated — GitHub is the caller, not a logged-in user.
"""

import json
import logging

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError, UnauthorizedError
from app.database.session import get_db_session
from app.services.webhook_service import handle_pull_request_event, verify_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookNotConfiguredError(AppError):
    status_code = 503
    error_code = "webhook_not_configured"


@router.post("/github", summary="GitHub webhook receiver (pull_request events)")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, str]:
    settings = get_settings()
    if not settings.github_webhook_secret:
        raise WebhookNotConfiguredError(
            "GITHUB_WEBHOOK_SECRET is not configured; refusing to trust any delivery."
        )

    raw_body = await request.body()
    if not verify_signature(raw_body, x_hub_signature_256, settings.github_webhook_secret):
        raise UnauthorizedError("Invalid webhook signature.")

    if x_github_event == "ping":
        return {"status": "pong"}

    if x_github_event != "pull_request":
        logger.info("Ignoring unhandled GitHub event type: %s", x_github_event)
        return {"status": "ignored", "event": x_github_event or "unknown"}

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        # A malformed body (not a KeyError further down — the signature
        # already verified this came from GitHub, so this is "GitHub sent
        # something that isn't valid JSON," not tampering) must produce a
        # clean 400, not an unhandled crash. GitHub treats any non-2xx
        # response as a delivery failure and retries — an unhandled 500
        # here would retry the same unparseable payload indefinitely.
        raise AppError(
            f"Webhook payload is not valid JSON: {exc}",
            status_code=400,
            error_code="invalid_webhook_payload",
        ) from exc
    if not isinstance(payload, dict):
        raise AppError(
            "Webhook payload must be a JSON object.",
            status_code=400,
            error_code="invalid_webhook_payload",
        )
    updated = await handle_pull_request_event(db, payload)
    logger.info(
        "Processed pull_request event: action=%s repo=%s prs_updated=%d",
        payload.get("action"),
        payload.get("repository", {}).get("full_name"),
        len(updated),
    )
    return {"status": "ok", "pull_requests_updated": str(len(updated))}
