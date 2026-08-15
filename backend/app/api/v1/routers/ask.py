"""POST /ask — single-shot "ask GraphForge anything" answer.

Thin HTTP wrapper over `app.services.ask_grounding.ground` — see that
module's own docstring for what actually classifies the question,
resolves the repository, and grounds the answer. Kept as its own
endpoint (rather than folded entirely into the conversational flow)
because it's still the right shape for a caller that wants one answer
with no conversation state to manage; `ConversationService` calls the
same `ground()` function to seed a new investigation topic, so the two
never diverge on what counts as an impact question or which repository
it means.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import AppError
from app.core.rate_limit import check_rate_limit
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.ask import AskRequest, AskResponse
from app.services.ask_grounding import (
    ASK_GROUNDING_RATE_LIMIT,
    ASK_GROUNDING_RATE_WINDOW_SECONDS,
    ask_grounding_rate_limit_key,
    ground,
)

router = APIRouter(prefix="/ask", tags=["ask"])

# H-2 — `/ask` runs a graph traversal per call and seeds the conversational
# loop that follows it; `app.core.rate_limit`'s docstring names exactly
# this endpoint class (OWASP LLM10, unbounded consumption) as what it
# exists to protect, but nothing had wired it in. Deliberately more
# generous than a workflow stage start (10/5min): asking questions is the
# product's main interaction and a curious engineer legitimately asks
# several a minute.
#
# Shared with `POST /conversations`/`POST /conversations/{id}/messages` —
# see `app.services.ask_grounding.ASK_GROUNDING_RATE_LIMIT`'s own
# docstring for why this budget lives there rather than as a separate
# per-router number: both surfaces invoke the same deterministic
# `ground()` cost center, and used to have independent 30/60s budgets,
# letting a caller net ~60 grounding-triggering requests/minute by
# alternating endpoints instead of the intended 30.
#
# KNOWN LIMITATION, stated here rather than only in the limiter: the
# window is in-process (see `app.core.rate_limit`'s own docstring). With
# one backend replica — the current deployment — that is the real budget.
# Run N replicas without a shared store and the effective budget becomes
# N x this number. The architecture has no Redis today, and adding one
# solely for this would be a larger change than the fix; the smallest
# correct upgrade when a second replica is introduced is to back
# `app.core.rate_limit._hits` with a shared store, which is why every
# caller goes through that one function rather than counting its own.


@router.post("", response_model=AskResponse)
async def ask(
    body: AskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AskResponse:
    question = body.question.strip()
    if not question:
        raise AppError(
            "Question must not be empty.", status_code=422, error_code="validation_error"
        )

    check_rate_limit(
        ask_grounding_rate_limit_key(current_user.id),
        max_requests=ASK_GROUNDING_RATE_LIMIT,
        window_seconds=ASK_GROUNDING_RATE_WINDOW_SECONDS,
    )

    return await ground(db, current_user.id, question)
