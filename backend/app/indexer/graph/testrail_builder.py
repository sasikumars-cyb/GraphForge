"""Turns raw TestRail API data (project/suites/sections/cases, as returned
by `app.tools.implementations.testrail_tool.TestRailTool`) into the
generic `GraphPayload` `Neo4jTestCaseGraphRepository` persists.

Node id scheme: every id is namespaced `f"testrail:{project_id}:{kind}:
{key}"`, mirroring `app.indexer.graph.builder`'s convention for the code
graph — re-syncing the same project always produces the same ids (MERGE
upserts in place, no duplicate nodes across syncs).

No linkage to `Component`/`Repository` nodes by design for this pass (see
docs/adr and the plan this was built against) — the hierarchy is purely
TestRail's own Project -> Suite -> Section -> Case, connected with the
existing `CONTAINS` relationship type. The Testing agent reasons over
these by relevance-ranked text, not graph traversal to code.
"""

from dataclasses import dataclass, field
from typing import Any

from app.graph.models import GraphEdge, GraphNode, GraphPayload


@dataclass
class TestRailProjectData:
    """Everything one sync pass fetched for one TestRail project — the
    plain-value input this module turns into a GraphPayload. Kept
    DB-independent, mirroring indexer.services.indexing_service's
    index_repository()'s "plain values, not ORM objects" precedent, so
    this stays testable with no database or Neo4j at all.

    `project_id` is `int | str` rather than just `int`: a real TestRail
    sync passes TestRail's own numeric id, but
    `app.services.test_case_upload_service` reuses this exact dataclass
    (and the rest of this module) for a CSV/Excel upload too, keyed by a
    synthetic `f"upload-{uuid4().hex}"` string instead — same graph shape
    (a single-suite "project" containing sections and cases), no reason
    to duplicate this builder for that second source of test cases.
    """

    project_id: int | str
    project_name: str
    suites: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)


def _project_node_id(project_id: int | str) -> str:
    return f"testrail:{project_id}:project"


def _suite_node_id(project_id: int | str, suite_id: int) -> str:
    return f"testrail:{project_id}:suite:{suite_id}"


def _section_node_id(project_id: int | str, section_id: int) -> str:
    return f"testrail:{project_id}:section:{section_id}"


def _case_node_id(project_id: int | str, case_id: int) -> str:
    return f"testrail:{project_id}:case:{case_id}"


def build_graph(data: TestRailProjectData) -> GraphPayload:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    project_id_str = _project_node_id(data.project_id)
    nodes.append(
        GraphNode(
            id=project_id_str,
            labels=["TestRailProject"],
            properties={"name": data.project_name, "testrail_id": data.project_id},
        )
    )

    for suite in data.suites:
        suite_node_id = _suite_node_id(data.project_id, suite["id"])
        nodes.append(
            GraphNode(
                id=suite_node_id,
                labels=["TestSuite"],
                properties={"name": suite["name"], "testrail_id": suite["id"]},
            )
        )
        edges.append(GraphEdge(source_id=project_id_str, target_id=suite_node_id, type="CONTAINS"))

    # Some TestRail projects (suite_mode=1, "single suite") have cases/
    # sections with no real suite at all - synthesize one "Master" suite
    # node so every section/case still has somewhere to attach, rather
    # than requiring the caller to special-case that mode.
    if not data.suites:
        suite_node_id = _suite_node_id(data.project_id, 0)
        nodes.append(
            GraphNode(id=suite_node_id, labels=["TestSuite"], properties={"name": "Master"})
        )
        edges.append(GraphEdge(source_id=project_id_str, target_id=suite_node_id, type="CONTAINS"))
        default_suite_id = 0
    else:
        default_suite_id = data.suites[0]["id"]

    for section in data.sections:
        section_node_id = _section_node_id(data.project_id, section["id"])
        nodes.append(
            GraphNode(
                id=section_node_id,
                labels=["TestSection"],
                properties={"name": section["name"], "testrail_id": section["id"]},
            )
        )
        parent_id = section.get("parent_id")
        if parent_id:
            # Nested section: parent is another TestSection.
            edges.append(
                GraphEdge(
                    source_id=_section_node_id(data.project_id, parent_id),
                    target_id=section_node_id,
                    type="CONTAINS",
                )
            )
        else:
            suite_id = section.get("suite_id") or default_suite_id
            edges.append(
                GraphEdge(
                    source_id=_suite_node_id(data.project_id, suite_id),
                    target_id=section_node_id,
                    type="CONTAINS",
                )
            )

    for case in data.cases:
        case_node_id = _case_node_id(data.project_id, case["id"])
        nodes.append(
            GraphNode(
                id=case_node_id,
                labels=["TestCase"],
                properties={
                    "title": case["title"],
                    "testrail_id": case["id"],
                    "priority_id": case.get("priority_id"),
                    "type_id": case.get("type_id"),
                    "refs": case.get("refs") or "",
                },
            )
        )
        section_id = case.get("section_id")
        if section_id:
            edges.append(
                GraphEdge(
                    source_id=_section_node_id(data.project_id, section_id),
                    target_id=case_node_id,
                    type="CONTAINS",
                )
            )
        else:
            # No section (unusual, but not invalid) - attach directly to
            # its suite so the case is never orphaned from the hierarchy.
            suite_id = case.get("suite_id") or default_suite_id
            edges.append(
                GraphEdge(
                    source_id=_suite_node_id(data.project_id, suite_id),
                    target_id=case_node_id,
                    type="CONTAINS",
                )
            )

    return GraphPayload(nodes=nodes, edges=edges)
