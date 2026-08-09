"""Unit tests for `build_reasoning_summary` (Report V2 upstream reasoning-
persistence fix, extended in Phase 2 with the `synthesis_state` four-value
model — ADR 0024 §11) — the minimal, always-populated projection of
`InvestigationWorkspace.hypotheses`/`.contradictions` into the persisted
Context Discovery result, added at
app.context_pipeline.reasoning.projection.

Pure function of `WorkingContext`, so every case here constructs a
`WorkingContext` directly rather than running a full discovery cycle.
"""

from __future__ import annotations

from app.context_pipeline.reasoning.memory import DiscoveryMetadata, WorkingContext
from app.context_pipeline.reasoning.projection import build_reasoning_summary
from app.context_pipeline.reasoning.understanding import (
    Contradiction,
    Hypothesis,
    InvestigationWorkspace,
)


class TestBuildReasoningSummary:
    def test_no_investigation_workspace_at_all_returns_empty_dict(self) -> None:
        # The one case that predates `synthesis_state` entirely — a
        # persisted result from before this addition shipped, or any call
        # site that never stashes a workspace. Data plumbing treats this
        # identically to synthesis_state == "not_run".
        state = WorkingContext()
        assert build_reasoning_summary(state) == {}

    def test_malformed_investigation_workspace_degrades_to_empty_dict(self) -> None:
        state = WorkingContext()
        # Not a valid InvestigationWorkspace shape at all — must not raise.
        state.derived["investigation_workspace"] = {"hypotheses": "not-a-list"}
        assert build_reasoning_summary(state) == {}

    def test_not_run_state_is_carried_even_with_an_empty_workspace(self) -> None:
        # The zero-evidence short-circuit: a workspace IS stashed (empty),
        # but investigation_workspace_run_state == "not_run" — this must
        # still return real content (synthesis_state), not the old `{}`.
        state = WorkingContext()
        state.derived["investigation_workspace"] = InvestigationWorkspace().model_dump()
        state.derived["investigation_workspace_run_state"] = "not_run"
        summary = build_reasoning_summary(state)
        assert summary["synthesis_state"] == "not_run"
        assert summary["hypotheses"] == []
        assert summary["contradictions"] == []

    def test_missing_run_state_signal_defaults_to_not_run(self) -> None:
        # A workspace exists but investigation_workspace_run_state was
        # never set (a hypothetical future call site that forgets to, or
        # any state this test suite hasn't anticipated) — the safe default
        # is "we don't know reasoning ran," never a guess at "completed".
        state = WorkingContext()
        state.derived["investigation_workspace"] = InvestigationWorkspace().model_dump()
        summary = build_reasoning_summary(state)
        assert summary["synthesis_state"] == "not_run"

    def test_failed_state_is_carried_regardless_of_workspace_contents(self) -> None:
        state = WorkingContext()
        workspace = InvestigationWorkspace(
            reasoning_notes=["Synthesis call failed or returned an invalid response."]
        )
        state.derived["investigation_workspace"] = workspace.model_dump()
        state.derived["investigation_workspace_run_state"] = "failed"
        summary = build_reasoning_summary(state)
        assert summary["synthesis_state"] == "failed"
        assert summary["hypotheses"] == []
        assert summary["contradictions"] == []

    def test_completed_with_empty_lists_is_completed_empty_not_failed(self) -> None:
        # The crux of the Phase 2 refinement: synthesis genuinely ran and
        # succeeded, and simply found nothing to hypothesize about — this
        # must be distinguishable from both "not_run" and "failed".
        state = WorkingContext()
        state.derived["investigation_workspace"] = InvestigationWorkspace().model_dump()
        state.derived["investigation_workspace_run_state"] = "completed"
        summary = build_reasoning_summary(state)
        assert summary["synthesis_state"] == "completed_empty"
        assert summary["hypotheses"] == []
        assert summary["contradictions"] == []

    def test_completed_with_real_hypotheses_and_contradictions_are_projected(self) -> None:
        state = WorkingContext(metadata=DiscoveryMetadata(iteration=3))
        workspace = InvestigationWorkspace(
            hypotheses=[
                Hypothesis(
                    description="The regression is in the payment handler",
                    supporting_evidence=["trace shows handler raising"],
                    contradicting_evidence=[],
                    confidence=0.7,
                    status="supported",
                )
            ],
            contradictions=[
                Contradiction(
                    description="Two sources disagree on ownership",
                    evidence_for=["CODEOWNERS"],
                    evidence_against=["ticket"],
                    resolved=False,
                    resolution_note="",
                )
            ],
            # Fields deliberately NOT expected to reach the projection —
            # asserted absent below.
            open_questions=["is this reproducible in staging?"],
            reasoning_notes=["internal scratch note"],
        )
        state.derived["investigation_workspace"] = workspace.model_dump()
        state.derived["investigation_workspace_run_state"] = "completed"

        summary = build_reasoning_summary(state)

        assert summary["synthesis_state"] == "completed"
        assert summary["iteration"] == 3
        assert len(summary["hypotheses"]) == 1
        assert summary["hypotheses"][0]["description"] == "The regression is in the payment handler"
        assert summary["hypotheses"][0]["status"] == "supported"
        assert len(summary["contradictions"]) == 1
        assert summary["contradictions"][0]["description"] == "Two sources disagree on ownership"

        # Only the report-safe fields cross the boundary — everything else
        # on InvestigationWorkspace stays internal-scratch, per this
        # function's own docstring.
        assert set(summary.keys()) == {
            "synthesis_state",
            "hypotheses",
            "contradictions",
            "iteration",
        }

    def test_only_hypotheses_no_contradictions_still_projects(self) -> None:
        state = WorkingContext()
        workspace = InvestigationWorkspace(
            hypotheses=[Hypothesis(description="h", status="unknown")],
        )
        state.derived["investigation_workspace"] = workspace.model_dump()
        state.derived["investigation_workspace_run_state"] = "completed"
        summary = build_reasoning_summary(state)
        assert summary["synthesis_state"] == "completed"
        assert summary["hypotheses"]
        assert summary["contradictions"] == []
