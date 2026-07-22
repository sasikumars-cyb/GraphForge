"""The `pull_requests` table — PR metadata ingested from GitHub webhook
`pull_request` events. Metadata only: no diff content, no risk scoring, no
AI analysis (that's a later feature).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # GitHub's own numeric PR id (stable) - distinct from `number`, which is
    # only unique within one repository and can be reused across repos.
    github_pr_id: Mapped[str] = mapped_column(String(50), nullable=False)
    number: Mapped[int] = mapped_column(nullable=False)

    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    # GitHub's pull_request.state is "open" or "closed"; "merged" is derived
    # from the separate `merged` boolean GitHub includes on close events.
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    author_login: Mapped[str] = mapped_column(String(255), nullable=False)
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    head_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    github_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    github_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "repository_id", "github_pr_id", name="uq_pull_requests_repository_github_pr"
        ),
    )
