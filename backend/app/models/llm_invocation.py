"""The `llm_invocations` table — one row per LLM call, append-only.

ADR 0012. Populated exclusively from `app.agents.llm._fill_invocation_
metadata` — the single choke point every agent already routes its LLM
calls through via `invoke_llm_json`. No agent writes to this table
directly.

A row is written once, complete, after the call finishes (success or
failure) — never created pending and later updated. Once written, no code
should ever mutate a row; it is a historical record of what happened, not
a live-updated status (contrast with `AgentStep`, which is legitimately
mutated as a step progresses).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.agent_step import AgentStep
    from app.models.run import Run


class LLMInvocation(Base):
    __tablename__ = "llm_invocations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    agent_step_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_steps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized from agent_step_id (ADR 0012 "Schema Design" — every
    # stated analytics goal filters by run first; this avoids a join
    # through agent_steps, a wide table carrying full result/evidence
    # JSON payloads, for the single most common query shape).
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # "initial" | "reflection" | future kinds. `sequence` is 0 for the
    # first call an AgentStep makes, 1 for the next, and so on — together
    # these let a reader distinguish reflection's two calls (see
    # app.agents.reflection) without relying on row/timestamp order.
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="initial")
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # "completed" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Computed and stored once, at write time, from whichever pricing table
    # was current then — never recomputed at read time (ADR 0012: a later
    # price change must not silently restate historical spend).
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # Failed provider attempts that preceded the successful one (see
    # app.ai.config.fallback) — not an orchestrator-level re-run count.
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Ordered provider keys tried before the one that succeeded, when
    # known. Nullable/absent today — populating it requires threading
    # complete_with_fallback's own attempts list further than this
    # increment does; see ADR 0012's Architectural Questions.
    attempted_providers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Row-write time, distinct from started_at/finished_at, for
    # retention/archival queries (ADR 0012 Scalability).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent_step: Mapped[AgentStep] = relationship("AgentStep")
    run: Mapped[Run] = relationship("Run")
