"""Conversations API — the Home page's conversational investigation
loop. Thin HTTP wrapper over `ConversationService`; see that module's own
docstring for the actual grounding/reasoning logic.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import AppError
from app.database.session import get_db_session
from app.models.conversation import Conversation, ConversationMessage
from app.models.user import User
from app.schemas.conversation import (
    ConversationMessageResponse,
    ConversationResponse,
    ConversationSummary,
    PostMessageRequest,
    StartConversationRequest,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=str(conversation.id),
        title=conversation.title,
        mode=conversation.mode,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_response(message: ConversationMessage) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        id=str(message.id),
        role=message.role,  # type: ignore[arg-type]
        content=message.content,
        payload=message.payload,
        created_at=message.created_at,
    )


def _conversation_response(
    conversation: Conversation, messages: list[ConversationMessage]
) -> ConversationResponse:
    return ConversationResponse(
        id=str(conversation.id),
        title=conversation.title,
        mode=conversation.mode,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_message_response(m) for m in messages],
    )


@router.post("", response_model=ConversationResponse, status_code=201)
async def start_conversation(
    body: StartConversationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    question = body.question.strip()
    if not question:
        raise AppError(
            "Question must not be empty.", status_code=422, error_code="validation_error"
        )

    service = ConversationService(db)
    conversation, _assistant_message = await service.start(
        current_user, question, mode=body.mode
    )
    _conversation, messages = await service.get_conversation(conversation.id, current_user.id)
    return _conversation_response(conversation, messages)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = Query(5, ge=1, le=50, description="Most recently active first."),
    mode: str | None = Query(
        None, description="Filter to one mode (e.g. 'migration') — omit for all."
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[ConversationSummary]:
    service = ConversationService(db)
    conversations = await service.list_recent(current_user.id, limit=limit, mode=mode)
    return [_summary(c) for c in conversations]


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    service = ConversationService(db)
    conversation, messages = await service.get_conversation(conversation_id, current_user.id)
    return _conversation_response(conversation, messages)


@router.post("/{conversation_id}/messages", response_model=ConversationResponse)
async def post_message(
    conversation_id: uuid.UUID,
    body: PostMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationResponse:
    text = body.message.strip()
    if not text:
        raise AppError(
            "Message must not be empty.", status_code=422, error_code="validation_error"
        )

    service = ConversationService(db)
    await service.post_message(conversation_id, current_user.id, text)
    conversation, messages = await service.get_conversation(conversation_id, current_user.id)
    return _conversation_response(conversation, messages)
