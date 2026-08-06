"""The `background_jobs` table — KAN-18's durable queue.

Replaces `asyncio.create_task`/FastAPI `BackgroundTasks` as the mechanism
that survives an API process crash or restart: the *fact* that work is
outstanding lives in this table, not only in a process's memory, so a
`Worker` (this process's own embedded poll loop today, or a dedicated
worker process once one exists) can find and retry it after a restart
instead of the work simply vanishing — the exact failure mode
`app.orchestrator.background_execution`'s own module docstring names as
this codebase's accepted, temporary trade-off.

`job_type`/`payload` are an open vocabulary, same "open, not a closed enum"
discipline `EvidenceItem.kind` already uses elsewhere in this codebase — but
every producer/consumer pair here is still a matched, hand-written contract
(see `app.orchestrator.job_queue` and the handlers registered in
`app.orchestrator.worker`), never a generic RPC layer accepting arbitrary
job types.

Claim/lease fields (`leased_by`/`leased_at`/`lease_expires_at`) implement
the standard durable-queue pattern: `JobQueue.claim_next` uses
`SELECT ... FOR UPDATE SKIP LOCKED` so multiple workers never claim the same
row, and a row whose lease has expired without completing (the worker that
leased it crashed) becomes claimable again by any worker — see
`JobQueue.reclaim_expired_leases`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Open vocabulary — see module docstring. Indexed: a worker's claim
    # query and any operator debugging a specific job class both filter on
    # this.
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # JSON-safe args for the registered handler — see
    # `app.orchestrator.job_queue.JobQueue.enqueue`'s docstring for what
    # "JSON-safe" means here and why it is enforced at the boundary, not
    # trusted of the caller.
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)

    # "queued" | "leased" | "completed" | "failed" | "dead_letter" — a
    # dead-lettered job has exhausted `max_attempts` and needs operator
    # attention; it is never silently retried again.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # An opaque worker identity (e.g. "hostname:pid"), recorded for
    # debugging "which process was holding this when it died" — never read
    # by the claim logic itself, which only ever compares against
    # `lease_expires_at`.
    leased_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Free-text, for log correlation only (e.g. a run_id or repository_id as
    # a string) — never parsed or matched on by the queue itself, which
    # only ever dispatches on `job_type`/`payload`.
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
