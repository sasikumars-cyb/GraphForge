"""Parse -> build graph -> persist -> track, for a CSV/Excel test-case
upload. Synchronous, unlike the TestRail sync job's background-task
pipeline (app.services.testrail_service): a spreadsheet is a bounded,
already-local file (capped at 5000 rows/5MB — see
test_case_upload_parser), so parsing and writing it takes well under a
request timeout, and a job-polling UI would be pure ceremony here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.graph.models import GraphPayload
from app.graph.session import get_driver
from app.graph.test_case_repository import Neo4jTestCaseGraphRepository
from app.indexer.graph.test_case_upload_parser import parse_test_case_file
from app.indexer.graph.testrail_builder import TestRailProjectData, build_graph
from app.models.test_case_upload import TestCaseUpload
from app.models.user import User


def _graph_project_id(upload_id: uuid.UUID) -> str:
    return f"upload-{upload_id}"


async def create_upload(
    db: AsyncSession, user: User, filename: str, display_name: str, content: bytes
) -> TestCaseUpload:
    raw_cases = parse_test_case_file(filename, content)

    # Group into sections so the graph gets real TestSection nodes (one
    # per distinct `section` value found in the file, defaulting to
    # "Uploaded Test Cases" — see test_case_upload_parser._rows_to_cases)
    # rather than every case hanging directly off the synthesized Master
    # suite. Matches TestRail's own Suite -> Section -> Case shape.
    section_ids_by_name: dict[str, int] = {}
    sections: list[dict[str, object]] = []
    cases: list[dict[str, object]] = []
    for i, raw in enumerate(raw_cases, start=1):
        section_name = raw["section"]
        section_id = section_ids_by_name.get(section_name)
        if section_id is None:
            section_id = len(section_ids_by_name) + 1
            section_ids_by_name[section_name] = section_id
            sections.append({"id": section_id, "name": section_name, "parent_id": None})
        cases.append(
            {
                "id": i,
                "title": raw["title"],
                "section_id": section_id,
                "priority_id": None,
                "type_id": None,
                "refs": raw["refs"],
            }
        )

    upload_id = uuid.uuid4()
    graph_project_id = _graph_project_id(upload_id)
    graph = build_graph(
        TestRailProjectData(
            project_id=graph_project_id,
            project_name=display_name,
            suites=[],
            sections=sections,
            cases=cases,
        )
    )
    graph_repository = Neo4jTestCaseGraphRepository(get_driver())
    await graph_repository.replace_project_test_cases(graph_project_id, graph)

    upload = TestCaseUpload(
        id=upload_id,
        filename=filename,
        display_name=display_name,
        case_count=len(cases),
        uploaded_by_user_id=user.id,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)
    return upload


async def list_uploads(db: AsyncSession) -> list[TestCaseUpload]:
    result = await db.execute(select(TestCaseUpload).order_by(TestCaseUpload.created_at.desc()))
    return list(result.scalars().all())


async def delete_upload(db: AsyncSession, upload_id: uuid.UUID) -> None:
    upload = await db.get(TestCaseUpload, upload_id)
    if upload is None:
        raise NotFoundError("Upload not found.")

    graph_repository = Neo4jTestCaseGraphRepository(get_driver())
    await graph_repository.replace_project_test_cases(_graph_project_id(upload.id), GraphPayload())
    await db.delete(upload)
    await db.commit()
