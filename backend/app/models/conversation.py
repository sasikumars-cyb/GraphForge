"""The `conversations` and `conversation_messages` tables — the Home
page's "engineering investigation" state.

Deliberately not built on `EngineeringSession`/RFC-001 (Belief/Hypothesis/
Decision, propose-then-commit, Participants): that aggregate models a
much heavier, human-reviewed investigation lifecycle that nothing in this
codebase's agents actually read from today (see `ConversationService`'s
own docstring). A conversation here is the opposite shape on purpose —
`investigation_state` is *recomputed from the message history on every
turn*, never persisted as its own mutable copy (the same "recompute,
don't accumulate" discipline `UnderstandingService` already applies to
`WorkingUnderstanding`), so there is exactly one place a follow-up's
context can come from and no cache-invalidation problem to get wrong.

`ConversationMessage.payload` carries the structured half of an assistant
turn (evidence, impact, provenance, actions — the same shape `AskResponse`
already defines) alongside `content`, the short conversational text a
human reads. A user message has `payload=None`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

MESSAGE_ROLES: tuple[str, ...] = ("user", "assistant")
# "general" — Ask GraphForge's own free-text investigation. "migration" —
# Migration Assistant: same tables, same recompute-from-history state
# model, only the grounding/prompt `ConversationService` applies differs
# (see that module's own docstring) — a mode flag, not a second schema.
CONVERSATION_MODES: tuple[str, ...] = ("general", "migration")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The first user message, truncated — a stable label for a history
    # list, same role `Run.title` plays for agent runs. Never re-derived
    # after creation, so it keeps naming the investigation even once later
    # turns have moved on to a different sub-topic.
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="general")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured evidence/impact/actions/provenance for an assistant turn
    # (see module docstring) — an `AskResponse`-shaped dict, minus
    # `question`/`status`. Null for a user message and for an assistant
    # message that only carries plain text (e.g. an error fallback).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
