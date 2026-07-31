"""Provider usage counters and health recording.

Records only what the platform actually observed: request counts, outcomes,
latency, and the last error of each kind. Quota is deliberately *not*
estimated — if a vendor does not report remaining quota, the dashboard says
nothing rather than guessing.

Writes are best-effort. A counter update must never fail the AI request it is
describing, so every entry point swallows its own errors.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.errors import AIProviderAuthError, AIProviderRateLimitError
from app.models.ai_profile import AIProviderUsage
from app.models.ai_provider_config import AIProviderConfig

logger = logging.getLogger(__name__)


# Not every vendor uses 401 for a bad credential — Gemini answers 400 with an
# "API key not valid" body, which would otherwise be reported as "offline" and
# send an operator hunting a network fault instead of fixing their key. These
# markers are a secondary heuristic applied *after* type-based classification,
# kept here so no provider-specific string matching leaks into the providers.
_AUTH_MARKERS = (
    "api key not valid",
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "unauthorized",
    "unauthenticated",
    "permission denied",
    "api key expired",
)


def classify_status(exc: Exception | None) -> tuple[str, str]:
    """Map an outcome to a provider health status and a short detail."""
    if exc is None:
        return "ready", ""

    detail = str(getattr(exc, "message", exc))[:500]

    if isinstance(exc, AIProviderRateLimitError):
        return "rate_limited", detail
    if isinstance(exc, AIProviderAuthError):
        return "auth_failed", detail

    lowered = detail.lower()
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return "auth_failed", detail

    return "offline", detail


async def _row_for(db: AsyncSession, provider_key: str) -> AIProviderUsage:
    row = (
        await db.execute(
            select(AIProviderUsage).where(AIProviderUsage.provider_key == provider_key)
        )
    ).scalar_one_or_none()
    if row is None:
        row = AIProviderUsage(provider_key=provider_key)
        db.add(row)
        await db.flush()
    return row


async def record_outcome(
    db: AsyncSession,
    *,
    provider_key: str,
    latency_ms: int,
    error: Exception | None = None,
) -> None:
    """Record one provider request outcome and refresh its health status.

    Flushes, does not commit: `db` is a caller-owned session that may
    already hold other pending, uncommitted changes (ADR 0012 —
    `app.agents.llm.persist_llm_invocation` calls this from inside the
    same shared transaction `RunCoordinator` will commit once, atomically,
    with the rest of that run's own persistence). Committing here would
    end that transaction early and out from under the caller. Never had a
    caller before ADR 0012's implementation, so this is a genuine fix, not
    a behavior change relied on elsewhere (confirmed via a repo-wide search
    for other call sites before making this change).

    Also does not roll back on its own failure, for the identical reason:
    a rollback here would discard every other pending change in the
    caller's shared transaction, not just this function's own. Telemetry
    must never take down the request it is describing (see the original
    docstring below) — that now means "swallow and log," never "roll back
    someone else's uncommitted work."
    """
    try:
        now = datetime.now(UTC)
        row = await _row_for(db, provider_key)

        row.requests += 1
        row.last_request_at = now

        if error is None:
            row.successes += 1
            row.total_latency_ms += max(0, latency_ms)
            row.last_success_at = now
        else:
            row.failures += 1
            row.last_failure_at = now
            row.last_error = str(getattr(error, "message", error))[:1000]
            if isinstance(error, AIProviderRateLimitError):
                row.rate_limit_events += 1
                row.last_rate_limit_at = now
            elif isinstance(error, AIProviderAuthError):
                row.auth_failures += 1

        # Mirror the outcome onto the provider config so the Health view can
        # be read without joining usage — the config row is the single thing
        # the providers list already loads.
        status, detail = classify_status(error)
        config = (
            await db.execute(
                select(AIProviderConfig).where(AIProviderConfig.provider_key == provider_key)
            )
        ).scalar_one_or_none()
        if config is not None:
            config.status = status
            config.status_detail = detail or None
            if error is None:
                config.last_success_at = now
                config.latency_ms = latency_ms

        await db.flush()
    except Exception:
        # Telemetry must never take down the request it is describing.
        logger.warning("ai_usage_record_failed provider=%s", provider_key, exc_info=True)


async def usage_for(db: AsyncSession, provider_key: str) -> AIProviderUsage | None:
    return (
        await db.execute(
            select(AIProviderUsage).where(AIProviderUsage.provider_key == provider_key)
        )
    ).scalar_one_or_none()


async def all_usage(db: AsyncSession) -> dict[str, AIProviderUsage]:
    rows = (await db.execute(select(AIProviderUsage))).scalars().all()
    return {r.provider_key: r for r in rows}
