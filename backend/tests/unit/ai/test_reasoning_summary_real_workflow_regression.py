"""Regression test for the Report V2 upstream reasoning-persistence fix
(build_reasoning_summary / map_hypotheses / map_contradictions /
map_knowledge_ledger_rows), required by point 8 of that task: prove, with
a REAL captured artifact rather than only a synthetic fixture, that after
Context Discovery completes, hypotheses and contradictions survive
persistence and can be reconstructed by report generation.

Provenance of the fixture (see the JSON file's own `_provenance` field for
the full note): captured from a real, standalone Context Discovery run —
same repository (sasikumars-cyb/prompt-library) and same underlying
objective as the workflow used throughout Phase 1
(74f8b66a-1e0f-4845-bc97-b63fc7e1ce82) — with a real DeepSeek synthesis
LLM call that actually completed (workflow 74f8b66a's own persisted
context_discovery result predates this fix and cannot be reused directly;
its status/current_stage were deliberately left untouched, per the
decision to keep this verification standalone). The run's context_
discovery `AgentStep.result` was read directly from Postgres and trimmed
to just the `reasoning_summary` key this test needs — nothing here is
hand-written.

This complements (does not replace) `test_reasoning_summary_projection.py`
(synthetic WorkingContext fixtures covering edge cases) and the
`map_hypotheses`/`map_contradictions` unit tests in
`test_report_data_plumbing.py` (synthetic input covering every branch).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.git_ops._artifact_reader import StageStepData
from app.agents.report_generation import data_plumbing as dp
from app.agents.report_generation.contracts import Availability, SynthesisRunState, SynthesisStatus

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "real_context_discovery_reasoning_summary.json"
)


def _load_real_context_discovery_bundle() -> StageStepData:
    payload = json.loads(_FIXTURE_PATH.read_text())
    assert "_provenance" in payload, "fixture must document it is a real captured artifact"
    return StageStepData(
        result={"reasoning_summary": payload["reasoning_summary"]},
        evidence=[],
        confidence_score=None,
        confidence_reasoning=None,
    )


class TestRealWorkflowReasoningSummaryRegression:
    def test_fixture_is_a_real_non_empty_reasoning_summary(self) -> None:
        # Sanity check on the fixture itself, not the mapping code — if
        # this ever fails, the fixture was replaced with something that no
        # longer proves anything.
        bundle = _load_real_context_discovery_bundle()
        assert bundle.result["reasoning_summary"]["hypotheses"]
        assert bundle.result["reasoning_summary"]["contradictions"]

    def test_real_hypotheses_survive_persistence_and_reconstruct(self) -> None:
        bundle = _load_real_context_discovery_bundle()
        entries, availability, run_state = dp.map_hypotheses(bundle)

        assert availability.status == Availability.AVAILABLE
        assert run_state == SynthesisRunState.COMPLETED
        assert len(entries) == 5  # exact count from the real captured run

        # Every entry must be a real, non-empty statement produced by the
        # actual synthesis call — never a placeholder.
        for entry in entries:
            assert entry.statement
            assert isinstance(entry.status, SynthesisStatus)
            assert 0.0 <= entry.confidence <= 1.0

        # At least one hypothesis was genuinely rejected by real reasoning
        # (not every hypothesis in a real investigation survives) —
        # confirms the 3-way status mapping round-trips real data, not
        # just synthetic "supported" cases.
        statuses = {e.status for e in entries}
        assert SynthesisStatus.CONTRADICTED in statuses  # "rejected" -> CONTRADICTED
        assert SynthesisStatus.UNKNOWN in statuses

    def test_real_contradictions_survive_persistence_and_reconstruct(self) -> None:
        bundle = _load_real_context_discovery_bundle()
        entries, availability, run_state = dp.map_contradictions(bundle)

        assert availability.status == Availability.AVAILABLE
        assert run_state == SynthesisRunState.COMPLETED
        assert len(entries) == 1
        assert entries[0].statement
        assert entries[0].evidence_for
        assert entries[0].evidence_against
        assert entries[0].resolved is False

    def test_real_hypotheses_reach_the_knowledge_ledger_with_synthesis_status_only(self) -> None:
        bundle = _load_real_context_discovery_bundle()
        rows = dp.map_knowledge_ledger_rows(None, context_discovery_bundle=bundle)

        assert len(rows) == 5
        for row in rows:
            assert row.source_stage == "context_discovery"
            assert row.source_field.startswith("reasoning_summary.hypotheses[")
            assert row.synthesis_status is not None
            assert row.verification_status is None  # reasoning-derived, never code-checked

    def test_evidence_stays_prose_not_id_shaped_on_real_data(self) -> None:
        # Requirement 3, checked against the real fixture (not just a
        # synthetic string): supporting/contradicting evidence must never
        # look like a stable Evidence ID (e.g. a UUID or "ev_" prefix) —
        # it is prose copied verbatim from the LLM's own account.
        bundle = _load_real_context_discovery_bundle()
        entries, _, _ = dp.map_hypotheses(bundle)
        for entry in entries:
            for item in [*entry.supporting_evidence, *entry.contradicting_evidence]:
                assert isinstance(item, str)
                assert len(item) > 15, "real evidence prose, not a short ID-like token"
