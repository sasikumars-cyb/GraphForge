"""CSV/Excel test-case uploads — the file-based counterpart to a TestRail
sync (app.api.v1.routers.testrail), for teams whose test cases live in a
spreadsheet rather than TestRail. Same downstream graph
(TestRailProject/TestSuite/TestSection/TestCase nodes) and the same
Testing agent coverage lookup reads both without distinction — see
app.services.test_case_upload_service and app.indexer.graph.
testrail_builder's shared TestRailProjectData.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.test_case_upload import TestCaseUploadResponse
from app.services.test_case_upload_service import create_upload, delete_upload, list_uploads

router = APIRouter(prefix="/test-cases/uploads", tags=["test-case-uploads"])


@router.get("", response_model=list[TestCaseUploadResponse])
async def list_test_case_uploads(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[TestCaseUploadResponse]:
    """Shared across every user, like the TestRail-synced cases — see
    app.models.test_case_upload's own docstring for why this isn't
    filtered to the calling user."""
    uploads = await list_uploads(db)
    return [TestCaseUploadResponse.model_validate(u) for u in uploads]


@router.post("", response_model=TestCaseUploadResponse, status_code=201)
async def upload_test_cases(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TestCaseUploadResponse:
    content = await file.read()
    display_name = (name or "").strip() or (file.filename or "Uploaded test cases")
    upload = await create_upload(
        db,
        current_user,
        filename=file.filename or "upload",
        display_name=display_name,
        content=content,
    )
    return TestCaseUploadResponse.model_validate(upload)


@router.delete("/{upload_id}", status_code=204)
async def remove_test_case_upload(
    upload_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    await delete_upload(db, upload_id)
