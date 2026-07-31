"""The `test_case_uploads` table — one row per CSV/Excel file a user has
uploaded under Settings -> Integrations -> Test Cases. Shared/visible to
every user once uploaded (matches the existing TestRail/Jira/Confluence
precedent of install-wide knowledge, not GitHub's per-user isolation —
test case content isn't a credential, and the whole point is that every
user's Testing agent runs can see it). `uploaded_by_user_id` is tracked
for display/audit only, never used to filter visibility.

`id` doubles as the graph's `testrail_project_id` scoping value (as
`f"upload-{id}"`) for the TestCase nodes this upload wrote — see
`app.services.test_case_upload_service`.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TestCaseUpload(Base):
    __tablename__ = "test_case_uploads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)

    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
