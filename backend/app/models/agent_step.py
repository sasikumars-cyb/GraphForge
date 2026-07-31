"""The `agent_steps` table — one row per agent executed within a Run.

Stores the AgentOutput envelope (serialised as JSON columns) plus
per-step metrics (latency, token cost, retry count) for calibration
tracking. A Run with one agent has exactly one AgentStep; future
parallel/sequential chains produce multiple.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.run import Run


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # "queued" | "running" | "completed" | "partial" | "failed"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")

    # AgentOutput fields — stored as JSON for schema flexibility
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    graph_facts_written: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    output_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ADR 0011, OD-1 — orchestrator-produced, pre-execution WARNING-severity
    # pre-flight results (e.g. "Jira unreachable"), kept deliberately
    # separate from `evidence` above: `evidence` is the agent's own audit
    # trail of what *it* observed, while this is produced by RunCoordinator
    # before the agent ever runs. Each entry is shaped exactly
    # `{code, dependency, message, checked_at}` (see
    # app.orchestrator.preflight.PreflightWarning) — never written to
    # directly; always appended via
    # app.orchestrator.preflight.record_preflight_warnings, which preserves
    # execution order and any warnings already present. Non-nullable with an
    # empty-list default so every existing row (and every row from a run
    # with no warnings, the overwhelming common case) reads as `[]`, never
    # `None` — no caller needs an existence check.
    preflight_warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Evaluation metrics (Phase 1 — latency + retry count captured here;
    # token cost and confidence calibration are Phase 2)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Human override — a small, additive sidecar rather than a parallel
    # persistence model (see the Context Explorer architecture review):
    # `result` above stays exactly what the agent produced, untouched,
    # so confidence calibration (app.models.confidence_calibration) keeps
    # checking a real AI output against the human's approve/reject
    # decision, never a human-edited one. `human_override` holds only the
    # fields a human actually changed (a partial dict, merged on top of
    # `result` at read time — see get_stage_result() in
    # app.agents.git_ops._artifact_reader); nullable/None means "no
    # override was made," the overwhelmingly common case. `overridden_by`/
    # `overridden_at` are the audit trail for when one was.
    human_override: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    overridden_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship("Run", back_populates="steps")
