"""Contract tests for `app.control_plane.human_approval` — content-hash
pinning and scope-equality staleness detection (Engineering State
contract §13)."""

from __future__ import annotations

from app.control_plane.human_approval import HumanApprovalRecord, is_approval_still_valid_for_scope


def _approval(scope: dict[str, object]) -> HumanApprovalRecord:
    return HumanApprovalRecord(
        approval_id="approval-1",
        approved_scope=scope,
        approved_by="jane@example.com",
        approved_at="2026-08-17T00:00:00Z",
    )


def test_content_hash_is_deterministic_for_identical_scope() -> None:
    a = _approval({"capability_id": "query_knowledge_graph"})
    b = _approval({"capability_id": "query_knowledge_graph"})
    assert a.content_hash == b.content_hash


def test_content_hash_changes_with_scope() -> None:
    a = _approval({"capability_id": "query_knowledge_graph"})
    b = _approval({"capability_id": "commit_changes"})
    assert a.content_hash != b.content_hash


def test_approval_valid_for_unchanged_scope() -> None:
    approval = _approval({"capability_id": "query_knowledge_graph"})
    assert is_approval_still_valid_for_scope(
        approval, current_scope={"capability_id": "query_knowledge_graph"}
    )


def test_approval_invalid_for_widened_scope() -> None:
    """§13: "Approved scope changes ... MUST cause Execution Authorization
    to fail." A scope discovered to differ from what was approved — even
    if it looks like a superset — must not silently pass."""
    approval = _approval({"capability_id": "query_knowledge_graph"})
    assert not is_approval_still_valid_for_scope(
        approval,
        current_scope={"capability_id": "query_knowledge_graph", "extra": "widened"},
    )


def test_approval_record_is_immutable() -> None:
    approval = _approval({"capability_id": "query_knowledge_graph"})
    try:
        approval.approval_id = "different"  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised, "HumanApprovalRecord must be frozen — no field may be reassigned."
