"""The `repositories` table — GitHub repositories a user has selected to
track. Only selected repositories are stored; the live GitHub list
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
    github_repo_id: Mapped[str] = mapped_column(String(50), nullable=False)

    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(511), nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "github_repo_id", name="uq_repositories_user_github_repo"),
    )
