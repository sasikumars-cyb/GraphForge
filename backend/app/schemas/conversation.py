"""Request/response shapes for `POST /conversations` and
`POST /conversations/{id}/messages` — the Home page's conversational
investigation loop. See `app.services.conversation_service`'s own
docstring for the state model these serialize.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ask import (
    MAX_QUESTION_LENGTH,
    AskAction,
    AskEvidenceItem,
    AskImpact,
    AskRepositoryCandidate,
)
from app.schemas.migration import MigrationScope
from app.schemas.refinement import RefinementPlan

CONVERSATION_MODES = Literal["general", "migration", "refinement"]


class ConversationEntityRef(BaseModel):
    """One system the investigation has named so far, with a stable
    short-hand (`ref`, e.g. "A") a follow-up can point back at ("what if
    I only fix A and B?") without repeating the real identifier."""

    ref: str
    name: str
    impact_level: Literal["low", "medium", "high"] | None = None


class ConversationTurnPayload(BaseModel):
    """The structured half of an assistant turn — same evidence/impact/
    action vocabulary `AskResponse` already defines (see that schema's
    docstring), plus `entities`, the running reference registry follow-up
    questions resolve against."""

    intent: str
    resolved_repository_id: str | None = None
    resolved_repository_name: str | None = None
    why: str = ""
    evidence: list[AskEvidenceItem] = Field(default_factory=list)
    impact: AskImpact | None = None
    actions: list[AskAction] = Field(default_factory=list)
    entities: list[ConversationEntityRef] = Field(default_factory=list)
    # C-1 — this turn could not confidently identify which system the
    # question is about, so it asks instead of answering. Carries no
    # evidence and no impact by construction; `candidates` is what the
    # user can pick from. The frontend renders a clarification prompt,
    # never the grounded-answer treatment.
    needs_clarification: bool = False
    candidates: list[AskRepositoryCandidate] = Field(default_factory=list)
    # Only set in a "migration" mode conversation, and only once a source
    # technology has actually been grounded — see
    # `app.services.migration_grounding`.
    migration: MigrationScope | None = None
    # Only set in a "refinement" mode conversation — the full current
    # refinement plan (work items, dependency graph, readiness, ...),
    # replaced wholesale each turn rather than diffed — see
    # `app.services.refinement_grounding`.
    refinement: RefinementPlan | None = None
    # Set when the LLM synthesis step itself failed (misconfigured
    # provider, malformed response, ...) — `content` still carries a
    # honest, non-fabricated fallback message in that case; the frontend
    # uses this to skip rendering an "AI Insight" tag on a turn that never
    # actually reasoned about anything.
    degraded: bool = False


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    payload: ConversationTurnPayload | None = None
    created_at: datetime


class ConversationResponse(BaseModel):
    id: str
    title: str
    mode: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageResponse]


class ConversationSummary(BaseModel):
    id: str
    title: str
    mode: str
    created_at: datetime
    updated_at: datetime


class StartConversationRequest(BaseModel):
    # H-2 — same server-side ceiling as `AskRequest.question`; see
    # `app.schemas.ask.MAX_QUESTION_LENGTH`.
    question: str = Field(max_length=MAX_QUESTION_LENGTH)
    # "migration" opts a conversation into Migration Assistant's grounding/
    # prompt (see `ConversationService`) — same tables, same state model,
    # just a different lens on the same underlying graph. Defaults to
    # "general" so every existing Ask GraphForge caller is unaffected.
    mode: CONVERSATION_MODES = "general"


class PostMessageRequest(BaseModel):
    message: str = Field(max_length=MAX_QUESTION_LENGTH)
