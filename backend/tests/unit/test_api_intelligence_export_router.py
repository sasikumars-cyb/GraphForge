"""Unit tests for app.api.v1.routers.api_intelligence's export logic —
`_load_completed_result` and the format dispatch in `export_api_intelligence`.

Real DB (Run/AgentStep rows via the transactional `db_session` fixture) —
no HTTP layer needed to exercise the actual query/validation logic that
matters here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.api_intelligence import (
    UnsupportedExportFormatError,
    _load_completed_result,
    export_api_intelligence,
)
from app.core.exceptions import NotFoundError
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.models.user import User


async def _make_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", hashed_password="x", full_name="Test User"
    )
    db.add(user)
    await db.flush()
    return user


async def _make_run(db: AsyncSession, user: User, *, status: str, with_result: bool) -> Run:
    run = Run(
        id=uuid.uuid4(),
        subject_id=f"repo:{uuid.uuid4()}",
        subject_type="repository",
        display_name="acme/widgets",
        goal="analyze_api_intelligence",
        status=status,
        user_id=user.id,
        created_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    if with_result:
        step = AgentStep(
            id=uuid.uuid4(),
            run_id=run.id,
            agent_id="api_intelligence",
            status="completed",
            result={"repository_full_name": "acme/widgets", "executive_summary": "An API."},
        )
        db.add(step)
        await db.flush()
    return run


@pytest.mark.asyncio
async def test_load_completed_result_rejects_unknown_run(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await _load_completed_result(db_session, uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_load_completed_result_rejects_a_run_owned_by_another_user(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    run = await _make_run(db_session, user, status="completed", with_result=True)

    with pytest.raises(NotFoundError):
        await _load_completed_result(db_session, run.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_load_completed_result_rejects_a_not_yet_completed_run(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    run = await _make_run(db_session, user, status="running", with_result=False)

    with pytest.raises(UnsupportedExportFormatError):
        await _load_completed_result(db_session, run.id, user.id)


@pytest.mark.asyncio
async def test_load_completed_result_returns_the_persisted_result(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    run = await _make_run(db_session, user, status="completed", with_result=True)

    result = await _load_completed_result(db_session, run.id, user.id)

    assert result.repository_full_name == "acme/widgets"
    assert result.executive_summary == "An API."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("export_format", "content_type"),
    [
        ("openapi", "application/yaml"),
        ("postman", "application/json"),
        ("markdown", "text/markdown"),
        ("html", "text/html"),
        ("json", "application/json"),
    ],
)
async def test_export_endpoint_renders_every_format(
    db_session: AsyncSession, export_format: str, content_type: str
) -> None:
    user = await _make_user(db_session)
    run = await _make_run(db_session, user, status="completed", with_result=True)

    response = await export_api_intelligence(run.id, export_format, user, db_session)  # type: ignore[arg-type]

    assert response.media_type == content_type
    assert len(response.body) > 0
    if export_format == "openapi":
        yaml.safe_load(response.body)
