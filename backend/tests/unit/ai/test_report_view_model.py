"""Unit tests for Report V2 Phase 2's `ReportViewModel` builder
(app.agents.report_generation.view_model). Same `SimpleNamespace`-based
fixture pattern as `test_report_data_plumbing.py` — every `StageStepData`
here is hand-built, no database.

Covers: each section builder independently, the scale caps (§12), the
hypothesis/ledger verification-status correlation (§7), and the full
four-value `SynthesisRunState` matrix (§11) end to end through
`build_report_view_model`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.agents.report_generation import data_plumbing as dp
from app.agents.report_generation import view_model as vm
from app.agents.report_generation.contracts import (
    Availability,
    SynthesisRunState,
    SynthesisStatus,
    VerificationStatus,
)


def _bundle(
    result: dict | None = None,
    evidence: list[dict] | None = None,
    confidence_score: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        result=result or {}, evidence=evidence or [], confidence_score=confidence_score
    )


def _workflow(
    title: str = "Fix the flaky timeout", original_prompt: str = "Fix it"
) -> SimpleNamespace:
    return SimpleNamespace(
        title=title, original_prompt=original_prompt, updated_at=datetime(2026, 1, 1, tzinfo=UTC)
    )


def _bundles(**overrides: SimpleNamespace | None) -> dict[str, SimpleNamespace | None]:
    base: dict[str, SimpleNamespace | None] = {
        "context_discovery": None,
        "planning": None,
        "development": None,
        "testing": None,
        "documentation_planning": None,
        "engineering_review": None,
    }
    base.update(overrides)
    return base


class TestBuildHeader:
    def test_uses_context_discovery_original_request_when_present(self):
        cd = _bundle({"original_request": "the real question"})
        header = vm._build_header(
            _workflow(original_prompt="fallback"), _bundles(context_discovery=cd)
        )
        assert header.question == "the real question"

    def test_falls_back_to_workflow_original_prompt(self):
        header = vm._build_header(_workflow(original_prompt="fallback text"), _bundles())
        assert header.question == "fallback text"

    def test_repository_from_selected_entry(self):
        cd = _bundle(
            {"repositories": [{"name": "a", "selected": False}, {"name": "b", "selected": True}]}
        )
        header = vm._build_header(_workflow(), _bundles(context_discovery=cd))
        assert header.repository == "b"

    def test_repository_falls_back_to_ranked_names(self):
        cd = _bundle({"ranked_repository_names": ["legacy-repo"]})
        header = vm._build_header(_workflow(), _bundles(context_discovery=cd))
        assert header.repository == "legacy-repo"

    def test_no_repository_signal_is_none(self):
        header = vm._build_header(_workflow(), _bundles())
        assert header.repository is None


class TestBuildTimeline:
    def test_caps_at_max_rows_and_reports_truncated_count(self):
        entries = [
            {
                "iteration": i,
                "provider": "graph",
                "action": "a",
                "outcome": "success",
                "summary": "s",
                "intent": "i",
            }
            for i in range(1, 13)
        ]
        cd = _bundle({"discovery_report": {"investigation": entries}})
        section = vm._build_timeline(_bundles(context_discovery=cd))
        assert len(section.steps) == vm._MAX_TIMELINE_ROWS
        assert section.truncated_count == 12 - vm._MAX_TIMELINE_ROWS
        assert [s.cycle for s in section.steps] == sorted(s.cycle for s in section.steps)

    def test_no_context_discovery_is_unavailable(self):
        section = vm._build_timeline(_bundles())
        assert section.availability.status == Availability.UNAVAILABLE
        assert section.steps == []
        assert section.truncated_count == 0


class TestBuildKnowledge:
    def test_known_summarizes_finding_groups(self):
        cd = _bundle(
            {
                "discovery_report": {
                    "findings": [
                        {"kind": "repository", "total": 17, "items": [{"verified": True}] * 17},
                    ],
                    "gaps": [],
                }
            }
        )
        section = vm._build_knowledge(_bundles(context_discovery=cd))
        assert section.availability.status == Availability.AVAILABLE
        assert "17 repositorys recorded, 17 verified." in section.known
        assert section.unknown == []

    def test_unknown_only_includes_open_claimed_unresolvable_gaps(self):
        cd = _bundle(
            {
                "discovery_report": {
                    "findings": [],
                    "gaps": [
                        {"summary": "still open", "status": "open"},
                        {"summary": "resolved already", "status": "refuted"},
                    ],
                }
            }
        )
        section = vm._build_knowledge(_bundles(context_discovery=cd))
        assert section.unknown == ["still open"]

    def test_no_context_discovery_is_unavailable(self):
        section = vm._build_knowledge(_bundles())
        assert section.availability.status == Availability.UNAVAILABLE
        assert section.known == []
        assert section.unknown == []


class TestBuildHypotheses:
    def _cd_with_hypotheses(self, run_state: str, hypotheses: list[dict]) -> SimpleNamespace:
        return _bundle(
            {"reasoning_summary": {"synthesis_state": run_state, "hypotheses": hypotheses}}
        )

    def test_not_run_state(self):
        section = vm._build_hypotheses(_bundles(), [])
        assert section.synthesis_state == SynthesisRunState.NOT_RUN
        assert section.availability.status == Availability.UNAVAILABLE
        assert section.items == []

    def test_failed_state_is_degraded(self):
        cd = self._cd_with_hypotheses("failed", [])
        section = vm._build_hypotheses(_bundles(context_discovery=cd), [])
        assert section.synthesis_state == SynthesisRunState.FAILED
        assert section.availability.status == Availability.DEGRADED
        assert section.items == []

    def test_completed_empty_state_is_available_with_no_items(self):
        cd = self._cd_with_hypotheses("completed_empty", [])
        section = vm._build_hypotheses(_bundles(context_discovery=cd), [])
        assert section.synthesis_state == SynthesisRunState.COMPLETED_EMPTY
        assert section.availability.status == Availability.AVAILABLE
        assert section.items == []

    def test_completed_state_sorts_by_confidence_descending(self):
        cd = self._cd_with_hypotheses(
            "completed",
            [
                {"description": "low", "status": "unknown", "confidence": 0.1},
                {"description": "high", "status": "supported", "confidence": 0.9},
                {"description": "mid", "status": "unknown", "confidence": 0.5},
            ],
        )
        section = vm._build_hypotheses(_bundles(context_discovery=cd), [])
        assert [item.entry.statement for item in section.items] == ["high", "mid", "low"]

    def test_caps_at_max_hypothesis_cards(self):
        hyps = [
            {"description": f"h{i}", "status": "unknown", "confidence": 0.1 * i} for i in range(10)
        ]
        cd = self._cd_with_hypotheses("completed", hyps)
        section = vm._build_hypotheses(_bundles(context_discovery=cd), [])
        assert len(section.items) == vm._MAX_HYPOTHESIS_CARDS
        assert section.truncated_count == 10 - vm._MAX_HYPOTHESIS_CARDS

    def test_correlation_mechanism_works_if_a_matching_ledger_row_ever_exists(self):
        # MECHANISM test only, using a hand-built LedgerRow — this proves
        # `_build_hypotheses`'s positional-match code is correct in
        # isolation. As of ADR 0025 (Phase 3), the real pipeline DOES
        # produce such a row for a claim-type-gated, exact-match
        # `subject_entity` — see `test_hypothesis_verification_
        # correlation.py` for that real path, and
        # TestHypothesesWithoutSubjectEntityNeverCorrelate below for the
        # (still real, still true) case of a hypothesis with no
        # `subject_entity`, which never correlates.
        from app.agents.report_generation.contracts import LedgerRow

        cd = self._cd_with_hypotheses(
            "completed",
            [
                {"description": "checked", "status": "supported", "confidence": 0.9},
                {"description": "unchecked", "status": "unknown", "confidence": 0.5},
            ],
        )
        ledger_rows = [
            LedgerRow(
                claim="checked",
                source_stage="context_discovery",
                source_field="reasoning_summary.hypotheses[0]",
                synthesis_status=SynthesisStatus.SUPPORTED,
                verification_status=VerificationStatus.VERIFIED,
            )
        ]
        section = vm._build_hypotheses(_bundles(context_discovery=cd), ledger_rows)
        by_statement = {item.entry.statement: item.verification_status for item in section.items}
        assert by_statement["checked"] == VerificationStatus.VERIFIED.value
        assert by_statement["unchecked"] is None  # renders as NOT_CHECKED, never inferred


class TestHypothesesWithoutSubjectEntityNeverCorrelate:
    """Traces reasoning_summary -> map_knowledge_ledger_rows (the REAL
    function, not a hand-built LedgerRow) -> _build_hypotheses.

    **Updated for ADR 0025 (Phase 3).** Before Phase 3, this class's
    tests proved correlation was universally impossible — `map_knowledge_
    ledger_rows` hardcoded `verification_status=None` on every hypothesis
    row, unconditionally. That is no longer true: `map_verification_
    status_for_subject_entity` (data_plumbing.py) now correlates a
    hypothesis to Planning's `repository_usage[]` when it carries a
    claim-type-gated, exact-match `subject_entity` — see
    `test_hypothesis_verification_correlation.py` for that positive path,
    proven row-by-row against ADR 0025 §9a's False Positive Matrix.

    What THIS class still proves, and will always remain true: a
    hypothesis with **no** `subject_entity` — the correct, common case
    for any hypothesis that isn't itself an existence/attribution claim —
    never correlates to anything, no matter how closely its prose
    resembles a verification finding's text, and no matter what
    verification data exists elsewhere in the same workflow. Text
    similarity was never, and is still never, a path to `VERIFIED`.
    """

    def test_even_with_matching_verification_findings_text_no_correlation_occurs(self):
        # A verification_findings entry whose message is byte-for-byte the
        # same claim as a hypothesis's statement — the most favorable case
        # a text-similarity heuristic could hope for — still produces zero
        # correlation, because the hypothesis has no subject_entity and
        # verification_findings is never consulted for correlation at all
        # (only repository_usage is — see map_verification_status_for_
        # subject_entity's own docstring).
        claim_text = "The handler is in payment_service.py"
        cd = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": claim_text,
                            "status": "supported",
                            "confidence": 0.9,
                            "supporting_evidence": [],
                            "contradicting_evidence": [],
                        }
                    ],
                    "contradictions": [],
                },
                "verification_findings": [
                    {"message": claim_text, "category": "component_not_found", "blocking": False}
                ],
            },
            evidence=[],
            confidence_score=None,
        )
        ledger_rows = dp.map_knowledge_ledger_rows(cd, context_discovery_bundle=cd)

        # The real ledger DOES contain both a synthesis-only row (from the
        # hypothesis) and a verification-only row (from the finding) —
        # confirming both axes are populated somewhere in the ledger...
        synthesis_rows = [r for r in ledger_rows if r.synthesis_status is not None]
        verification_rows = [r for r in ledger_rows if r.verification_status is not None]
        assert len(synthesis_rows) == 1
        assert len(verification_rows) == 1
        # ...but never on the SAME row, even though the claim text matches
        # exactly — proving no text-based or any other correlation exists.
        assert synthesis_rows[0] is not verification_rows[0]
        assert synthesis_rows[0].verification_status is None
        assert verification_rows[0].synthesis_status is None

        section = vm._build_hypotheses(_bundles(context_discovery=cd), ledger_rows)
        assert section.items[0].verification_status is None  # NOT_CHECKED — always, today

    def test_hypotheses_without_subject_entity_stay_not_checked_even_with_real_verified_data(self):
        # Same assertion, built the way build_report_view_model actually
        # builds it (through the full ledger-rows aggregation, not a
        # hand-picked subset). `planning` below has a REAL verified
        # repository_usage entry for "acme/repo" — proving the mere
        # presence of verified data elsewhere in the workflow still isn't
        # enough; only an exact subject_entity match activates it.
        cd = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {"description": "h1", "status": "supported", "confidence": 0.9},
                        {"description": "h2", "status": "rejected", "confidence": 0.1},
                    ],
                    "contradictions": [],
                }
            },
            evidence=[],
            confidence_score=None,
        )
        planning = SimpleNamespace(
            result={"repository_usage": [{"name": "acme/repo", "verified": True}]},
            evidence=[],
            confidence_score=None,
        )
        model = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, planning=planning)
        )
        assert len(model.hypotheses.items) == 2
        for item in model.hypotheses.items:
            assert item.verification_status is None


class TestBuildContradictions:
    def test_not_run_state_is_unavailable(self):
        section = vm._build_contradictions(_bundles())
        assert section.synthesis_state == SynthesisRunState.NOT_RUN
        assert section.availability.status == Availability.UNAVAILABLE

    def test_completed_with_items(self):
        cd = _bundle(
            {
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "contradictions": [
                        {
                            "description": "conflict",
                            "evidence_for": ["a"],
                            "evidence_against": ["b"],
                            "resolved": False,
                            "resolution_note": "",
                        }
                    ],
                }
            }
        )
        section = vm._build_contradictions(_bundles(context_discovery=cd))
        assert section.synthesis_state == SynthesisRunState.COMPLETED
        assert len(section.items) == 1
        assert section.items[0].statement == "conflict"


class TestBuildEvidence:
    def test_categories_and_total(self):
        cd = _bundle(
            evidence=[
                {"kind": "graph_traversal"},
                {"kind": "graph_traversal"},
                {"kind": "tool_call"},
            ]
        )
        section = vm._build_evidence(_bundles(context_discovery=cd))
        assert section.total == 3
        assert section.availability.status == Availability.AVAILABLE

    def test_no_context_discovery_is_unavailable(self):
        section = vm._build_evidence(_bundles())
        assert section.availability.status == Availability.UNAVAILABLE
        assert section.total == 0


class TestBuildReportViewModelIntegration:
    def test_assembles_every_section_without_error(self):
        cd = _bundle(
            result={
                "original_request": "why is the timeout flaky",
                "repositories": [{"name": "agent-runtime", "selected": True}],
                "discovery_report": {
                    "investigation": [
                        {
                            "iteration": 1,
                            "provider": "graph",
                            "action": "a",
                            "outcome": "success",
                            "summary": "s",
                            "intent": "i",
                        }
                    ],
                    "findings": [],
                    "gaps": [],
                },
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [{"description": "h1", "status": "supported", "confidence": 0.8}],
                    "contradictions": [],
                },
            },
            evidence=[{"kind": "graph_traversal"}],
        )
        engineering_review = _bundle({"readiness_status": "ready", "blocking_issues": []})
        model = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=engineering_review)
        )
        assert model.header.question == "why is the timeout flaky"
        assert model.header.repository == "agent-runtime"
        assert model.hypotheses.items[0].entry.statement == "h1"
        assert model.executive_summary is None  # not filled in by the builder itself


class TestToJsonDict:
    def test_round_trips_through_real_json_dumps(self):
        import dataclasses
        import json

        model = vm.build_report_view_model(_workflow(), _bundles())
        model = dataclasses.replace(model, executive_summary="a short summary")
        payload = vm.to_json_dict(model)
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        assert decoded["executive_summary"] == "a short summary"
        # Every StrEnum value must round-trip as its plain string, not an
        # "EnumName.MEMBER" repr or a raise.
        assert decoded["header"]["readiness"] == "unknown"
        assert decoded["hypotheses"]["synthesis_state"] == "not_run"
