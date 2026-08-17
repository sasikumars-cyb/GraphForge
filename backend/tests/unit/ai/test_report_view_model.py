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


def _header(workflow, bundles):
    """`_build_header` is handed the already-derived readiness pair by
    `build_report_view_model` (so the badge can never disagree with the
    outcome section) — these header tests only care about question/
    repository, so they pass the same value `map_readiness` would."""
    reported = dp.map_readiness(bundles.get("engineering_review"))
    return vm._build_header(workflow, bundles, reported, reported)


class TestBuildHeader:
    def test_uses_context_discovery_original_request_when_present(self):
        cd = _bundle({"original_request": "the real question"})
        header = _header(_workflow(original_prompt="fallback"), _bundles(context_discovery=cd))
        assert header.question == "the real question"

    def test_falls_back_to_workflow_original_prompt(self):
        header = _header(_workflow(original_prompt="fallback text"), _bundles())
        assert header.question == "fallback text"

    def test_repository_from_selected_entry(self):
        cd = _bundle(
            {"repositories": [{"name": "a", "selected": False}, {"name": "b", "selected": True}]}
        )
        header = _header(_workflow(), _bundles(context_discovery=cd))
        assert header.repository == "b"

    def test_repository_falls_back_to_ranked_names(self):
        cd = _bundle({"ranked_repository_names": ["legacy-repo"]})
        header = _header(_workflow(), _bundles(context_discovery=cd))
        assert header.repository == "legacy-repo"

    def test_no_repository_signal_is_none(self):
        header = _header(_workflow(), _bundles())
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
        assert section.items[0].entry.statement == "conflict"


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


# ---------------------------------------------------------------------------
# The post–Engineering Review document contract: confirmed vs hypothetical,
# hypothesis vs overall confidence, contradictions, blocking vs advisory,
# and the review outcome itself. Every test below builds a whole
# `ReportViewModel` through the real public entry point — these are
# properties of the generated document, not of one private helper.
# ---------------------------------------------------------------------------


def _cd_bundle(
    *,
    hypotheses: list[dict] | None = None,
    contradictions: list[dict] | None = None,
    gaps: list[dict] | None = None,
    findings: list[dict] | None = None,
    confidence_score: float | None = None,
    synthesis_state: str = "completed",
) -> SimpleNamespace:
    return _bundle(
        {
            "original_request": "Why are KWH rows exported?",
            "reasoning_summary": {
                "synthesis_state": synthesis_state,
                "hypotheses": hypotheses or [],
                "contradictions": contradictions or [],
            },
            "discovery_report": {"gaps": gaps or [], "findings": findings or []},
        },
        confidence_score=confidence_score,
    )


def _er_bundle(readiness: str, blocking: list[str] | None = None) -> SimpleNamespace:
    return _bundle(
        {"readiness_status": readiness, "blocking_issues": blocking or []},
        confidence_score=0.45,
    )


def _hyp(description: str, confidence: float, status: str = "supported") -> dict:
    return {
        "description": description,
        "status": status,
        "confidence": confidence,
        "supporting_evidence": ["some prose"],
        "contradicting_evidence": [],
    }


class TestConfirmedVersusHypothetical:
    def test_a_high_confidence_unverified_hypothesis_is_never_a_confirmed_finding(self):
        """The core separation: 95% confidence buys a hypothesis nothing.
        Only verification moves a claim into Confirmed findings."""
        cd = _cd_bundle(hypotheses=[_hyp("the filtering logic lives in x.py", 0.95)])
        model = vm.build_report_view_model(_workflow(), _bundles(context_discovery=cd))

        assert model.hypotheses.items[0].entry.confidence == 0.95
        assert model.findings.items == []
        assert model.findings.availability.status == Availability.DEGRADED
        statements = [f.statement for f in model.findings.items]
        assert "the filtering logic lives in x.py" not in statements

    def test_verified_facts_become_confirmed_findings_unverified_ones_do_not(self):
        cd = _cd_bundle(
            findings=[
                {
                    "kind": "repository",
                    "total": 2,
                    "items": [
                        {
                            "subject": "soco_ingest",
                            "verified": True,
                            "evidence": {"summary": "indexed in the graph"},
                        },
                        {"subject": "unverified-repo", "verified": False, "evidence": None},
                    ],
                }
            ]
        )
        model = vm.build_report_view_model(_workflow(), _bundles(context_discovery=cd))

        statements = [f.statement for f in model.findings.items]
        assert statements == ["repository: soco_ingest"]
        assert model.findings.items[0].evidence_summary == "indexed in the graph"
        assert model.findings.availability.status == Availability.AVAILABLE

    def test_verified_ledger_rows_are_confirmed_findings(self):
        planning = _bundle(
            {"repository_usage": [{"name": "soco_ingest", "verified": True, "files_affected": []}]}
        )
        model = vm.build_report_view_model(_workflow(), _bundles(planning=planning))
        assert any("soco_ingest" in f.statement for f in model.findings.items)
        assert all(f.source_stage == "planning" for f in model.findings.items)


class TestHypothesisConfidenceVersusOverallConfidence:
    def test_the_two_numbers_are_labelled_separately_and_the_gap_is_explained(self):
        cd = _cd_bundle(hypotheses=[_hyp("filtering logic located in x.py", 0.95)])
        er = _er_bundle("needs_revision")
        model = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=er)
        )
        b = model.confidence.breakdown

        assert b.top_hypothesis_confidence == 0.95
        assert b.overall == 0.45
        assert b.top_hypothesis_label == "Root-cause candidate confidence"
        assert b.overall_label == "Overall resolution confidence"
        assert b.divergence_note is not None
        assert "95%" in b.divergence_note and "45%" in b.divergence_note
        assert "different things" in b.divergence_note
        assert "ready for implementation" in b.divergence_note

    def test_no_divergence_note_when_the_two_numbers_agree(self):
        cd = _cd_bundle(hypotheses=[_hyp("h", 0.5)])
        er = _bundle({"readiness_status": "ready"}, confidence_score=0.55)
        model = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=er)
        )
        assert model.confidence.breakdown.divergence_note is None

    def test_overall_basis_states_what_overall_confidence_measures(self):
        er = _er_bundle("needs_revision")
        model = vm.build_report_view_model(_workflow(), _bundles(engineering_review=er))
        assert "45%" in model.confidence.breakdown.overall_basis
        assert "understood well enough to implement" in model.confidence.breakdown.overall_basis


class TestContradictoryEvidence:
    def _model(self):
        cd = _cd_bundle(
            hypotheses=[_hyp("h", 0.95)],
            contradictions=[
                {
                    "description": "Jira says KWH is exported; the raw file has no plain kWh UOM",
                    "evidence_for": ["steps to reproduce list KWH"],
                    "evidence_against": ["raw readings contain only kWh F+R and kWh F"],
                    "resolved": False,
                    "resolution_note": "",
                }
            ],
        )
        return vm.build_report_view_model(_workflow(), _bundles(context_discovery=cd))

    def test_an_unresolved_contradiction_carries_impact_and_required_resolution(self):
        c = self._model().contradictions.items[0]
        assert c.is_blocking is True
        assert "Blocks the outcome" in c.impact
        assert "1 supporting item" in c.required_resolution
        assert "1 conflicting item" in c.required_resolution

    def test_an_unresolved_contradiction_becomes_a_blocking_open_item(self):
        model = self._model()
        promoted = [
            q
            for q in model.next_actions.questions
            if q.kind == vm.OpenItemKind.UNRESOLVED_CONTRADICTION
        ]
        assert len(promoted) == 1
        assert promoted[0].is_blocking is True
        assert model.next_actions.blocking_count == 1

    def test_an_unresolved_contradiction_appears_in_the_outcome_reasons(self):
        reasons = " ".join(self._model().review_outcome.reasons)
        assert "Unresolved contradiction" in reasons

    def test_a_resolved_contradiction_is_not_blocking_and_adds_no_open_item(self):
        cd = _cd_bundle(
            contradictions=[
                {
                    "description": "two sources disagreed",
                    "evidence_for": ["a"],
                    "evidence_against": ["b"],
                    "resolved": True,
                    "resolution_note": "Source A was confirmed later.",
                }
            ]
        )
        model = vm.build_report_view_model(_workflow(), _bundles(context_discovery=cd))
        assert model.contradictions.items[0].is_blocking is False
        assert "Source A was confirmed later." in model.contradictions.items[0].impact
        assert model.next_actions.questions == []
        assert model.next_actions.blocking_count == 0


class TestBlockingVersusAdvisoryIsOneSourceOfTruth:
    def test_every_section_reports_the_same_counts(self):
        """The regression this whole change exists for: one section said
        'one open/blocking item remains' while another said '0 blocking,
        1 advisory'. All counts now come from one list."""
        cd = _cd_bundle(
            gaps=[
                {
                    "gap_id": "g1",
                    "summary": "where is the UOM introduced?",
                    "status": "open",
                    "severity": "advisory",
                },
            ],
            contradictions=[
                {
                    "description": "Jira contradicts the raw input",
                    "evidence_for": ["a"],
                    "evidence_against": ["b"],
                    "resolved": False,
                    "resolution_note": "",
                }
            ],
        )
        er = _er_bundle("needs_revision")
        model = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=er)
        )

        assert model.next_actions.blocking_count == 1  # the contradiction
        assert model.next_actions.advisory_count == 1  # the open gap
        assert model.review_outcome.blocking_count == model.next_actions.blocking_count
        assert model.review_outcome.advisory_count == model.next_actions.advisory_count
        assert len(model.next_actions.questions) == 2
        assert sum(1 for q in model.next_actions.questions if q.is_blocking) == 1

    def test_engineering_review_blocking_issues_are_blocking(self):
        er = _er_bundle("needs_revision", blocking=["upstream creation not investigated"])
        model = vm.build_report_view_model(_workflow(), _bundles(engineering_review=er))
        assert model.next_actions.blocking_count == 1
        assert model.next_actions.questions[0].kind == vm.OpenItemKind.BLOCKING_ISSUE

    def test_a_ready_verdict_with_a_blocking_item_is_downgraded_not_shown_as_contradictory(self):
        er = _er_bundle("ready", blocking=["a real blocker slipped through"])
        model = vm.build_report_view_model(_workflow(), _bundles(engineering_review=er))
        assert model.header.reported_readiness == vm.Readiness.READY
        assert model.header.readiness == vm.Readiness.NEEDS_REVISION
        assert model.review_outcome.readiness == model.header.readiness
        assert "reported 'ready'" in " ".join(model.review_outcome.reasons)


class TestEngineeringReviewOutcome:
    def test_needs_revision_states_the_outcome_reasons_and_a_do_not_implement_recommendation(self):
        cd = _cd_bundle(
            hypotheses=[
                _hyp("filtering logic is in interval_usage.py", 0.95),
                _hyp("records are created upstream in ingest_raw_data.py", 0.6),
            ],
            gaps=[
                {
                    "gap_id": "g1",
                    "summary": "origin of KWH rows",
                    "status": "open",
                    "severity": "advisory",
                }
            ],
            contradictions=[
                {
                    "description": "Jira says KWH exported; raw input has no plain kWh",
                    "evidence_for": ["a"],
                    "evidence_against": ["b"],
                    "resolved": False,
                    "resolution_note": "",
                }
            ],
        )
        er = _er_bundle("needs_revision")
        outcome = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=er)
        ).review_outcome

        assert outcome.outcome_label == "Needs Revision"
        assert "did not approve implementation" in outcome.outcome_statement
        reasons = " ".join(outcome.reasons)
        assert "not confirmed" in reasons
        assert "2 competing explanations" in reasons
        assert "Unresolved contradiction" in reasons
        assert "1 knowledge gap" in reasons

        # Requirement 6: the document must not imply "go modify a file"
        # while a competing hypothesis says the cause is somewhere else.
        assert outcome.recommendation.startswith("Do not implement the proposed change yet.")
        assert "competing explanations" in outcome.recommendation
        assert "Reconcile the unresolved contradiction" in outcome.recommendation
        assert "re-run engineering review" in outcome.recommendation.lower()

    def test_ready_and_clean_is_an_approval_with_a_proceed_recommendation(self):
        cd = _cd_bundle(
            hypotheses=[_hyp("h", 0.9)],
            findings=[
                {
                    "kind": "component",
                    "total": 1,
                    "items": [{"subject": "exporter", "verified": True, "evidence": None}],
                }
            ],
        )
        er = _bundle({"readiness_status": "ready", "blocking_issues": []}, confidence_score=0.9)
        model = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=er)
        )
        outcome = model.review_outcome

        assert outcome.readiness == vm.Readiness.READY
        assert outcome.outcome_label == "Approved"
        assert outcome.recommendation.startswith("Proceed with implementation as reviewed.")
        assert outcome.blocking_count == 0
        assert "1 finding was confirmed" in " ".join(outcome.reasons)

    def test_ready_with_advisory_items_says_track_them_without_blocking(self):
        cd = _cd_bundle(
            gaps=[
                {"gap_id": "g", "summary": "nice to know", "status": "open", "severity": "advisory"}
            ]
        )
        er = _bundle({"readiness_status": "ready"}, confidence_score=0.8)
        outcome = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=er)
        ).review_outcome
        assert outcome.readiness == vm.Readiness.READY
        assert outcome.blocking_count == 0
        assert outcome.advisory_count == 1
        assert "Track 1 advisory item" in outcome.recommendation

    def test_review_never_ran_is_not_reviewed_never_an_approval(self):
        model = vm.build_report_view_model(_workflow(), _bundles())
        outcome = model.review_outcome
        assert outcome.readiness == vm.Readiness.UNKNOWN
        assert outcome.outcome_label == "Not Reviewed"
        assert outcome.availability.status == Availability.UNAVAILABLE
        assert "Run Engineering Review" in outcome.recommendation
        assert "no readiness verdict" in " ".join(outcome.reasons)

    def test_not_ready_is_never_upgraded(self):
        er = _bundle({"readiness_status": "not_ready"}, confidence_score=0.2)
        model = vm.build_report_view_model(_workflow(), _bundles(engineering_review=er))
        assert model.header.readiness == vm.Readiness.NOT_READY
        assert model.review_outcome.outcome_label == "Not Ready"
        assert model.review_outcome.recommendation.startswith("Do not implement")


class TestGenericAcrossDifferentInvestigations:
    def test_no_output_text_is_tied_to_any_specific_ticket_repository_or_file(self):
        """Same builder, two unrelated investigations — the derived prose
        must differ only where the underlying data differs."""
        a = vm.build_report_view_model(
            _workflow(title="A"),
            _bundles(
                context_discovery=_cd_bundle(hypotheses=[_hyp("cache eviction is too eager", 0.8)]),
                engineering_review=_er_bundle("needs_revision"),
            ),
        )
        b = vm.build_report_view_model(
            _workflow(title="B"),
            _bundles(
                context_discovery=_cd_bundle(
                    hypotheses=[_hyp("the CSV parser drops a column", 0.7)]
                ),
                engineering_review=_er_bundle("needs_revision"),
            ),
        )
        for model in (a, b):
            assert model.review_outcome.outcome_label == "Needs Revision"
            assert model.review_outcome.recommendation.startswith("Do not implement")
        # The only ticket-specific string anywhere is the hypothesis text
        # the source data itself carried.
        assert "cache eviction" in a.hypotheses.items[0].entry.statement
        assert "cache eviction" not in " ".join(b.review_outcome.reasons)

    def test_empty_workflow_still_produces_a_complete_document(self):
        model = vm.build_report_view_model(_workflow(), _bundles())
        assert model.review_outcome.reasons  # never empty
        assert model.review_outcome.recommendation
        assert model.findings.items == []
        assert model.next_actions.blocking_count == 0
        assert model.confidence.breakdown.overall is None
        assert "has not been quantified" in model.confidence.breakdown.overall_basis


class TestConfidenceBasisCitesAConcernNotAPositive:
    def test_a_low_overall_score_is_explained_by_what_is_holding_it_back(self):
        """Regression from reviewing a real generated document: the basis
        used to append the first reason, which is the *confirmed findings*
        line — so a 45% score read as "45% … 2 findings were confirmed",
        a non-sequitur."""
        cd = _cd_bundle(
            hypotheses=[_hyp("h", 0.95)],
            findings=[
                {
                    "kind": "repository",
                    "total": 1,
                    "items": [{"subject": "repo-a", "verified": True, "evidence": None}],
                }
            ],
        )
        er = _er_bundle("needs_revision")
        model = vm.build_report_view_model(
            _workflow(), _bundles(context_discovery=cd, engineering_review=er)
        )
        basis = model.confidence.breakdown.overall_basis

        assert "was confirmed by verified evidence" not in basis
        assert "not confirmed" in basis
        # The positive finding is still stated — as an outcome reason,
        # where it belongs.
        assert "1 finding was confirmed by verified evidence" in model.review_outcome.reasons[0]

    def test_a_single_knowledge_gap_reads_as_singular(self):
        cd = _cd_bundle(
            gaps=[{"gap_id": "g", "summary": "one gap", "status": "open", "severity": "advisory"}]
        )
        er = _er_bundle("needs_revision")
        reasons = " ".join(
            vm.build_report_view_model(
                _workflow(), _bundles(context_discovery=cd, engineering_review=er)
            ).review_outcome.reasons
        )
        assert "1 knowledge gap remains open" in reasons
