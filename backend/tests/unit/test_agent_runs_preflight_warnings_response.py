"""ADR 0011, OD-1 — `_step_response` serialization of `preflight_warnings`.

Direct unit tests of the API-layer mapping (`AgentStep.preflight_warnings`
JSON -> `PreflightWarningResponse`), no DB — same in-memory-model pattern
`test_agent_runs_standalone_planning.py` already establishes for this
router.
"""

from __future__ import annotations

import uuid

from app.api.v1.routers.agent_runs import _step_response
from app.models.agent_step import AgentStep


def _step(preflight_warnings: list[dict] | None) -> AgentStep:
    step = AgentStep(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        agent_id="planning",
        status="completed",
        result={},
        # Column defaults (evidence/prompt_version/etc.) only apply at
        # flush/INSERT time, not on a bare Python instantiation — this
        # object never touches the DB, so StepResponse's required fields
        # need explicit values here, matching what a real flushed row
        # would already have.
        prompt_version="1.0",
    )
    step.preflight_warnings = preflight_warnings
    return step


def test_step_response_empty_warnings_serializes_as_empty_list() -> None:
    response = _step_response(_step([]))
    assert response.preflight_warnings == []


def test_step_response_legacy_none_warnings_serializes_as_empty_list() -> None:
    """A row that predates this migration (or, defensively, any row where
    the column somehow reads None rather than the DB's NOT NULL default)
    must still serialize as an empty list, never null or a crash — the same
    backward-compatibility guarantee `evidence` already has via the
    identical `or []` guard."""
    response = _step_response(_step(None))
    assert response.preflight_warnings == []


def test_step_response_serializes_warning_fields_verbatim() -> None:
    response = _step_response(
        _step(
            [
                {
                    "code": "jira_reachable",
                    "dependency": "Jira",
                    "message": "Jira is not reachable.",
                    "checked_at": "2026-07-31T00:00:00Z",
                }
            ]
        )
    )
    assert len(response.preflight_warnings) == 1
    warning = response.preflight_warnings[0]
    assert warning.code == "jira_reachable"
    assert warning.dependency == "Jira"
    assert warning.message == "Jira is not reachable."
    assert warning.checked_at == "2026-07-31T00:00:00Z"


def test_step_response_serializes_multiple_warnings_in_order() -> None:
    response = _step_response(
        _step(
            [
                {
                    "code": "jira_reachable",
                    "dependency": "Jira",
                    "message": "m1",
                    "checked_at": "t0",
                },
                {
                    "code": "confluence_reachable",
                    "dependency": "Confluence",
                    "message": "m2",
                    "checked_at": "t1",
                },
            ]
        )
    )
    assert [w.code for w in response.preflight_warnings] == [
        "jira_reachable",
        "confluence_reachable",
    ]
