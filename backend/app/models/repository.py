"""The `repositories` table — repositories a user has selected to track,
either from GitHub (`source="github"`) or a local filesystem path
(`source="local"` — see app.services.local_repository_service). Only
selected repositories are stored; the live GitHub list
(`GET /api/v1/github/repositories`) is never persisted wholesale.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # GitHub's own numeric repo id, as a string - stable even if the repo is
    # renamed or transferred to a different owner. Unique across all users:
    # if two users both select the same repo, each gets their own row (their
    # own tracking relationship), so this is NOT a global unique constraint -
    # see the (user_id, github_repo_id) unique constraint below instead.
    #
    # For source="local" rows there is no real GitHub id - the local
    # repository service synthesizes "local:<name>" here so this column
    # (and the uniqueness constraint on it) keeps working unmodified for
    # both sources rather than needing a separate nullable id column.
    github_repo_id: Mapped[str] = mapped_column(String(50), nullable=False)

    # "github" (default) or "local" - which path created this row. Purely
    # descriptive: every downstream consumer (indexer, PR analysis, the
    # UI's Source badge) branches on this only where the two sources
    # actually differ (e.g. PR ingestion is GitHub-only); indexing itself
    # doesn't need to check it at all, since html_url already works for
    # both (see indexer.scanner.repository_cloner).
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="github")

    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(511), nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    # KAN-32 incremental indexing: what a successful indexing run (full or
    # incremental) last actually indexed. Null until the first successful
    # run — that absence is the signal `run_indexing` uses to require a
    # full index (there is nothing to diff a first index against).
    # `last_indexed_language` mirrors `DetectedLanguage`'s value (e.g.
    # "python") rather than being a foreign key to anything — the
    # incremental path needs to know which parser to use without re-
    # cloning the repository just to run `detect_language` again.
    last_indexed_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_indexed_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ADR 0023 — repository grouping for the Architecture summary/domain
    # view. Manual only: nothing in this codebase infers it. NULL means
    # "ungrouped", surfaced by the summary endpoint as its own bucket
    # rather than treated as an error or omitted.
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "github_repo_id", name="uq_repositories_user_github_repo"),
    )
