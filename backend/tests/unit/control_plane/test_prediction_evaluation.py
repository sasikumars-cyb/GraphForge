"""Phase 10 — unit coverage for `ControlPlane._evaluate_prediction_result`.

Root cause (Phase 10 Design Audit §5/§6): `Prediction.target_observable`
must resolve against the actual TOP-LEVEL `ToolResult` fields the
Capability's declared `output_schema` names (`data`, `summary`,
`evidence_items` — matching `ToolResult`'s own dataclass shape exactly),
never against a key nested inside `tool_result.data`. Pre-Phase-10 code
unconditionally indexed `tool_result.data[...]`, which silently returned
"inconclusive" for every real Tool (no real Tool nests `output_schema`-
declared fields inside `.data`) — a fixture bug in every fake test tool
in this repository masked this for four prior phases.

This is a pure unit test: `_evaluate_prediction_result` is a
`@staticmethod` needing no database, no Capability registry, and no
Control Plane instance — matching `test_control_plane_pipeline.py`'s own
DB-free convention for pipeline-stage tests.
"""

from __future__ import annotations

from app.control_plane.control_plane import ControlPlane
from app.control_plane.model import Prediction
from app.tools.interfaces import ToolResult


def _prediction(target_observable: str = "summary") -> Prediction:
    return Prediction(
        target_observable=target_observable,
        falsification_condition="summary is empty",
        evaluation_procedure="check whether summary is a non-empty string",
        execution_context={},
        necessary_condition_rationale="directly answers the Goal's own description",
    )


def _tool_result(**overrides: object) -> ToolResult:
    defaults: dict[str, object] = {
        "tool_id": "neo4j_graph",
        "tool_name": "Knowledge Graph (Neo4j)",
        "success": True,
    }
    defaults.update(overrides)
    return ToolResult(**defaults)  # type: ignore[arg-type]


class TestEvaluatePredictionResult:
    def test_truthy_top_level_summary_evaluates_true(self) -> None:
        """The core Phase 10 fix: `summary` set as a TOP-LEVEL `ToolResult`
        field (exactly how the real `Neo4jGraphTool` returns it) must be
        found — not silently treated as absent because it isn't nested
        inside `.data`."""
        result = _tool_result(data={"context_text": "..."}, summary="3 repositories found")
        outcome = ControlPlane._evaluate_prediction_result(_prediction("summary"), result)
        assert outcome == "true"

    def test_empty_top_level_summary_evaluates_false(self) -> None:
        """Falsy-but-present must be distinguished from absent — an empty
        string is a legitimate falsification, not an evaluation failure."""
        result = _tool_result(data={}, summary="")
        outcome = ControlPlane._evaluate_prediction_result(_prediction("summary"), result)
        assert outcome == "false"

    def test_target_observable_absent_from_tool_result_evaluates_inconclusive(self) -> None:
        """A `target_observable` naming neither a `ToolResult` attribute
        nor anything in `.data` must remain "inconclusive" — the fix
        must not turn every unresolved observable into a crash or a
        false positive/negative."""
        result = _tool_result(data={}, summary="something")
        outcome = ControlPlane._evaluate_prediction_result(_prediction("nonexistent_field"), result)
        assert outcome == "inconclusive"

    def test_data_dict_itself_can_still_be_the_target_observable(self) -> None:
        """`target_observable="data"` is also a legitimate, declared
        `output_schema` field (Cap §3) — resolving against `ToolResult`'s
        own attributes must still work for `data` itself, not just
        `summary`."""
        result = _tool_result(data={"repositories": ["a"]}, summary="1 repository found")
        outcome = ControlPlane._evaluate_prediction_result(_prediction("data"), result)
        assert outcome == "true"

    def test_empty_data_dict_as_target_observable_evaluates_false(self) -> None:
        result = _tool_result(data={}, summary="")
        outcome = ControlPlane._evaluate_prediction_result(_prediction("data"), result)
        assert outcome == "false"

    def test_unsuccessful_tool_result_evaluates_inconclusive_regardless_of_summary(self) -> None:
        """A failed dispatch must never be evaluated as "true"/"false" via
        Prediction matching — Cap §16.2 steps 1-3 (Blocked/
        ActionOutcomeUnknown/Anomaly) precede Prediction evaluation
        precisely because "an Action that never really ran cannot
        falsify anything." This local check is defense in depth for
        this evaluator specifically."""
        result = _tool_result(success=False, data={}, summary="", error="boom")
        outcome = ControlPlane._evaluate_prediction_result(_prediction("summary"), result)
        assert outcome == "inconclusive"

    def test_pre_phase_10_regression_guard_data_nested_summary_key_is_not_matched(self) -> None:
        """Explicitly proves the OLD (buggy) shape — `summary` nested
        inside `.data` rather than as a `ToolResult` top-level field —
        is no longer what the evaluator looks for. A Tool that (like
        every pre-Phase-10 fake tool) puts `summary` inside `.data`
        while leaving the real top-level `summary` field at its default
        `""` must evaluate as "false" (present-but-empty at the
        top-level field), never "true" via the old nested location."""
        result = _tool_result(data={"summary": "this should NOT be read"}, summary="")
        outcome = ControlPlane._evaluate_prediction_result(_prediction("summary"), result)
        assert outcome == "false"
