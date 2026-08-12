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
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.ask import AskRequest, AskResponse
from app.services.ask_grounding import ground

router = APIRouter(prefix="/ask", tags=["ask"])


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

    return await ground(db, current_user.id, question)
