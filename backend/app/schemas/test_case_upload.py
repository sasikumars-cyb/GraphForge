"""Request/response schemas for CSV/Excel test-case uploads."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TestCaseUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    display_name: str
    case_count: int
    uploaded_by_user_id: uuid.UUID
    created_at: datetime
