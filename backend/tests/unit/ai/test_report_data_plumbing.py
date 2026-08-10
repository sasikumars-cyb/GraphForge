"""Unit tests for Report V2 Phase 1 — data plumbing.

Covers: get_stage_step_data() (raw fetch), and every pure mapping
function in app.agents.report_generation.data_plumbing. No LLM calls, no
database — all fixtures are SimpleNamespace-based fakes, same pattern as
tests/unit/ai/test_engineering_review_agent.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.agents.git_ops._artifact_reader import get_stage_result, get_stage_step_data
from app.agents.report_generation import data_plumbing as dp
from app.agents.report_generation.contracts import (
    Availability,
    FileRole,
    Readiness,
    RiskSeverity,
    SynthesisRunState,
    SynthesisStatus,
    VerificationStatus,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_step(
    result: dict | None,
    evidence: list[dict] | None = None,
    confidence_score: float | None = None,
    confidence_reasoning: str | None = None,
    human_override: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        result=result or {},
        evidence=evidence or [],
        confidence_score=confidence_score,
        confidence_reasoning=confidence_reasoning,
        human_override=human_override,
    )


def _make_run(stage: str, status: str, step: SimpleNamespace | None) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_stage=stage,
        status=status,
        steps=[step] if step is not None else [],
        created_at=datetime.now(UTC),
    )


def _make_workflow(runs: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(runs=runs)


# ---------------------------------------------------------------------------
# get_stage_step_data — raw fetch
# ---------------------------------------------------------------------------


class TestGetStageStepData:
    def test_missing_stage_returns_none(self):
        workflow = _make_workflow([])
        assert get_stage_step_data(workflow, "planning") is None

    def test_returns_result_evidence_and_confidence_together(self):
        step = _make_step(
            result={"executive_summary": "x"},
            evidence=[{"kind": "tool_call", "reference": "r", "summary": "s"}],
            confidence_score=0.9,
            confidence_reasoning="high",
        )
        workflow = _make_workflow([_make_run("planning", "completed", step)])
        data = get_stage_step_data(workflow, "planning")
        assert data is not None
        assert data.result == {"executive_summary": "x"}
        assert data.evidence == [{"kind": "tool_call", "reference": "r", "summary": "s"}]
        assert data.confidence_score == 0.9
        assert data.confidence_reasoning == "high"

    def test_only_completed_status_counts(self):
        step = _make_step(result={"x": 1})
        workflow = _make_workflow([_make_run("planning", "running", step)])
        assert get_stage_step_data(workflow, "planning") is None

    def test_human_override_merged_into_result_but_not_evidence(self):
        step = _make_step(
            result={"a": 1, "b": 2},
            evidence=[{"kind": "tool_call", "reference": "r", "summary": "s"}],
            human_override={"b": 99},
        )
        workflow = _make_workflow([_make_run("planning", "completed", step)])
        data = get_stage_step_data(workflow, "planning")
        assert data is not None
        assert data.result == {"a": 1, "b": 99}
        # Evidence/confidence are never overridden — only `result` fields are.
        assert data.evidence == [{"kind": "tool_call", "reference": "r", "summary": "s"}]

    def test_agrees_with_get_stage_result_on_which_step_is_selected(self):
        old_step = _make_step(result={"x": "old"})
        new_step = _make_step(result={"x": "new"}, confidence_score=0.7)
        workflow = _make_workflow(
            [
                SimpleNamespace(
                    workflow_stage="planning",
                    status="completed",
                    steps=[old_step],
                    created_at=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                SimpleNamespace(
                    workflow_stage="planning",
                    status="completed",
                    steps=[new_step],
                    created_at=datetime(2024, 1, 1, tzinfo=UTC),
                ),
            ]
        )
        assert get_stage_result(workflow, "planning") == {"x": "new"}
        data = get_stage_step_data(workflow, "planning")
        assert data is not None
        assert data.result == {"x": "new"}
        assert data.confidence_score == 0.7

    def test_missing_evidence_confidence_columns_default_gracefully(self):
        # A structural fake that only implements the pre-existing
        # _HasStepResult shape (result only) — getattr defaults must kick in.
        class _OldStyleStep:
            result = {"x": 1}

        workflow = _make_workflow(
            [
                SimpleNamespace(
                    workflow_stage="planning",
                    status="completed",
                    steps=[_OldStyleStep()],
                    created_at=datetime.now(UTC),
                )
            ]
        )
        data = get_stage_step_data(workflow, "planning")
        assert data is not None
        assert data.evidence == []
        assert data.confidence_score is None
        assert data.confidence_reasoning is None


# ---------------------------------------------------------------------------
# map_availability
# ---------------------------------------------------------------------------


class TestMapAvailability:
    def test_unavailable_when_stage_missing(self):
        av = dp.map_availability("planning", None)
        assert av.status == Availability.UNAVAILABLE
        assert av.reason and "Planning" in av.reason

    def test_available_when_required_fields_present(self):
        bundle = SimpleNamespace(
            result={"blueprint": {"diagrams": []}},
            evidence=[],
            confidence_score=None,
            confidence_reasoning=None,
        )
        av = dp.map_availability("planning", bundle, required_fields=("blueprint",))
        assert av.status == Availability.AVAILABLE
        assert av.reason is None

    def test_degraded_when_required_field_missing(self):
        bundle = SimpleNamespace(
            result={}, evidence=[], confidence_score=None, confidence_reasoning=None
        )
        av = dp.map_availability("planning", bundle, required_fields=("blueprint",))
        assert av.status == Availability.DEGRADED
        assert av.reason and "blueprint" in av.reason

    def test_section_availability_requires_reason_when_not_available(self):
        from app.agents.report_generation.contracts import SectionAvailability

        try:
            SectionAvailability(Availability.DEGRADED, reason=None)
            raised = False
        except ValueError:
            raised = True
        assert raised


# ---------------------------------------------------------------------------
# map_readiness
# ---------------------------------------------------------------------------


class TestMapReadiness:
    def test_unknown_when_stage_missing(self):
        assert dp.map_readiness(None) == Readiness.UNKNOWN

    def test_maps_each_real_value(self):
        for raw, expected in [
            ("ready", Readiness.READY),
            ("needs_revision", Readiness.NEEDS_REVISION),
            ("not_ready", Readiness.NOT_READY),
        ]:
            bundle = SimpleNamespace(result={"readiness_status": raw})
            assert dp.map_readiness(bundle) == expected

    def test_unrecognized_value_is_unknown_not_guessed(self):
        bundle = SimpleNamespace(result={"readiness_status": "something_new"})
        assert dp.map_readiness(bundle) == Readiness.UNKNOWN

    def test_missing_field_is_unknown(self):
        bundle = SimpleNamespace(result={})
        assert dp.map_readiness(bundle) == Readiness.UNKNOWN


# ---------------------------------------------------------------------------
# map_confidence_journey
# ---------------------------------------------------------------------------


class TestMapConfidenceJourney:
    def _bundle(self, score: float | None) -> SimpleNamespace | None:
        if score is None:
            return None
        return SimpleNamespace(confidence_score=score)

    def test_complete_workflow_all_six_points_present(self):
        bundles = {
            "context_discovery": self._bundle(1.0),
            "planning": self._bundle(0.9),
            "development": self._bundle(0.9),
            "testing": self._bundle(0.93),
            "documentation_planning": self._bundle(0.8),
            "engineering_review": self._bundle(0.5),
        }
        journey = dp.map_confidence_journey(bundles)
        assert len(journey.points) == 6
        assert journey.points[0].confidence == 1.0
        assert journey.points[0].delta_from_previous is None  # first point, no prior

    def test_missing_stage_produces_none_confidence_point_not_omission(self):
        bundles = {stage: self._bundle(None) for stage in dp.STAGE_ORDER}
        bundles["context_discovery"] = self._bundle(1.0)
        journey = dp.map_confidence_journey(bundles)
        assert len(journey.points) == 6
        planning_point = journey.points[1]
        assert planning_point.stage == "planning"
        assert planning_point.confidence is None
        assert planning_point.dropped is False

    def test_drop_detected_and_summarized(self):
        bundles = {stage: self._bundle(None) for stage in dp.STAGE_ORDER}
        bundles["context_discovery"] = self._bundle(1.0)
        bundles["planning"] = self._bundle(0.9)
        journey = dp.map_confidence_journey(bundles)
        assert journey.points[1].dropped is True
        assert journey.points[1].delta_from_previous is not None
        assert round(journey.points[1].delta_from_previous, 2) == -0.1
        assert "Planning" in journey.summary_sentence
        normalized = journey.summary_sentence.replace("→", " ")
        assert "100" in normalized or "90" in journey.summary_sentence

    def test_no_drops_gives_steady_summary(self):
        bundles = {stage: self._bundle(0.9) for stage in dp.STAGE_ORDER}
        journey = dp.map_confidence_journey(bundles)
        assert "steady or improved" in journey.summary_sentence


# ---------------------------------------------------------------------------
# map_evidence_summary
# ---------------------------------------------------------------------------


class TestMapEvidenceSummary:
    def test_empty_evidence(self):
        assert dp.map_evidence_summary([]) == []

    def test_counts_by_kind(self):
        evidence = [
            {"kind": "tool_call"},
            {"kind": "tool_call"},
            {"kind": "graph_traversal"},
            {"kind": "llm_reasoning"},
        ]
        counts = dp.map_evidence_summary(evidence)
        as_dict = {c.kind: c.count for c in counts}
        assert as_dict == {"tool_call": 2, "graph_traversal": 1, "llm_reasoning": 1}

    def test_bounded_output_regardless_of_input_size(self):
        # Large-repository safety: 5000 items still produce at most 5 rows
        # (the number of distinct Evidence.kind literals).
        evidence = [{"kind": "tool_call"} for _ in range(5000)]
        counts = dp.map_evidence_summary(evidence)
        assert len(counts) == 1
        assert counts[0].count == 5000


# ---------------------------------------------------------------------------
# map_investigation_timeline
# ---------------------------------------------------------------------------


class TestMapInvestigationTimeline:
    def test_unavailable_when_context_discovery_missing(self):
        entries, av = dp.map_investigation_timeline(None)
        assert entries == []
        assert av.status == Availability.UNAVAILABLE

    def test_unavailable_when_no_investigation_recorded(self):
        bundle = SimpleNamespace(result={"discovery_report": {}})
        entries, av = dp.map_investigation_timeline(bundle)
        assert entries == []
        assert av.status == Availability.UNAVAILABLE

    def test_real_entries_mapped_verbatim(self):
        bundle = SimpleNamespace(
            result={
                "discovery_report": {
                    "investigation": [
                        {
                            "evidence_id": "ev_1",
                            "provider": "graph",
                            "action": "survey_architecture",
                            "outcome": "success",
                            "summary": "Looked up indexed repositories: 17 found.",
                            "intent": "understand scope",
                            "iteration": 2,
                        }
                    ]
                }
            }
        )
        entries, av = dp.map_investigation_timeline(bundle)
        assert av.status == Availability.AVAILABLE
        assert len(entries) == 1
        assert entries[0].cycle == 2
        assert entries[0].summary == "Looked up indexed repositories: 17 found."


# ---------------------------------------------------------------------------
# Knowledge ledger — verification axis
# ---------------------------------------------------------------------------


class TestMapKnowledgeLedgerRows:
    def test_empty_planning_gives_no_rows(self):
        assert dp.map_knowledge_ledger_rows(None) == []

    def test_verified_repository_usage_row(self):
        bundle = SimpleNamespace(
            result={"repository_usage": [{"name": "acme/repo", "verified": True}]}
        )
        rows = dp.map_knowledge_ledger_rows(bundle)
        assert len(rows) == 1
        assert rows[0].verification_status == VerificationStatus.VERIFIED
        assert rows[0].synthesis_status is None  # not populated in Phase 1
        assert rows[0].source_field == "repository_usage[0]"

    def test_unverified_repository_usage_row(self):
        bundle = SimpleNamespace(
            result={"repository_usage": [{"name": "acme/repo", "verified": False}]}
        )
        rows = dp.map_knowledge_ledger_rows(bundle)
        assert rows[0].verification_status == VerificationStatus.UNVERIFIED

    def test_verification_finding_always_unverified(self):
        bundle = SimpleNamespace(
            result={
                "verification_findings": [
                    {
                        "message": "Repository not found",
                        "category": "repository_not_found",
                        "blocking": True,
                    }
                ]
            }
        )
        rows = dp.map_knowledge_ledger_rows(bundle)
        assert len(rows) == 1
        assert rows[0].verification_status == VerificationStatus.UNVERIFIED
        assert rows[0].source_field == "verification_findings[0]"

    def test_two_axis_independence_no_confirmed_unresolved_bucketing(self):
        # A LedgerRow never groups into a shared bucket — each row carries
        # its own independent pair of statuses. This test locks in that the
        # data shape has no such grouping field at all.
        bundle = SimpleNamespace(
            result={
                "repository_usage": [
                    {"name": "a", "verified": True},
                    {"name": "b", "verified": False},
                ]
            }
        )
        rows = dp.map_knowledge_ledger_rows(bundle)
        assert not hasattr(rows[0], "confirmed")
        assert not hasattr(rows[0], "bucket")
        assert {r.verification_status for r in rows} == {
            VerificationStatus.VERIFIED,
            VerificationStatus.UNVERIFIED,
        }

    def test_findings_aggregated_across_planning_development_and_testing(self):
        # Regression test for a real gap found while validating Phase 1
        # against an actual completed workflow (74f8b66a...): the only
        # blocking finding that run had came from TESTING, not Planning —
        # a ledger built from Planning alone would have silently omitted
        # the one claim that actually mattered.
        planning = SimpleNamespace(result={})
        development = SimpleNamespace(
            result={
                "verification_findings": [
                    {"message": "dev finding", "category": "component_not_found", "blocking": True}
                ]
            }
        )
        testing = SimpleNamespace(
            result={
                "verification_findings": [
                    {"message": "test finding", "category": "component_not_found", "blocking": True}
                ]
            }
        )
        rows = dp.map_knowledge_ledger_rows(planning, development, testing)
        stages = {r.source_stage for r in rows}
        assert stages == {"development", "testing"}
        assert len(rows) == 2

    def test_hypothesis_rows_carry_synthesis_status_not_verification_status(self):
        # Requirement 5 (upstream reasoning-persistence fix): the ledger must
        # be able to represent a synthesis-only row (no code check ran
        # against it) alongside verification-only rows above.
        cd_bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": "Handler lives in acme/repo",
                            "status": "supported",
                            "confidence": 0.8,
                            "supporting_evidence": ["file listing shows handler.py"],
                            "contradicting_evidence": [],
                        }
                    ],
                    "contradictions": [],
                }
            }
        )
        rows = dp.map_knowledge_ledger_rows(None, context_discovery_bundle=cd_bundle)
        assert len(rows) == 1
        assert rows[0].source_stage == "context_discovery"
        assert rows[0].source_field == "reasoning_summary.hypotheses[0]"
        assert rows[0].synthesis_status == SynthesisStatus.SUPPORTED
        assert rows[0].verification_status is None

    def test_hypothesis_rows_and_verification_rows_coexist(self):
        planning = SimpleNamespace(result={"repository_usage": [{"name": "a", "verified": True}]})
        cd_bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": "h",
                            "status": "rejected",
                            "confidence": 0.2,
                            "supporting_evidence": [],
                            "contradicting_evidence": ["x"],
                        }
                    ],
                    "contradictions": [],
                }
            }
        )
        rows = dp.map_knowledge_ledger_rows(planning, context_discovery_bundle=cd_bundle)
        assert len(rows) == 2
        by_stage = {r.source_stage: r for r in rows}
        assert by_stage["planning"].verification_status == VerificationStatus.VERIFIED
        assert by_stage["planning"].synthesis_status is None
        assert by_stage["context_discovery"].synthesis_status == SynthesisStatus.CONTRADICTED
        assert by_stage["context_discovery"].verification_status is None

    def test_no_context_discovery_bundle_adds_no_hypothesis_rows(self):
        rows = dp.map_knowledge_ledger_rows(None)
        assert rows == []


class TestMapSynthesisStatus:
    def test_supported_maps_to_supported(self):
        assert dp.map_synthesis_status("supported") == SynthesisStatus.SUPPORTED

    def test_rejected_maps_to_contradicted(self):
        assert dp.map_synthesis_status("rejected") == SynthesisStatus.CONTRADICTED

    def test_unknown_maps_to_unknown(self):
        assert dp.map_synthesis_status("unknown") == SynthesisStatus.UNKNOWN

    def test_unrecognized_value_falls_back_to_unknown(self):
        assert dp.map_synthesis_status("something_new") == SynthesisStatus.UNKNOWN

    def test_never_produces_inferred(self):
        # HypothesisStatus has no state corresponding to INFERRED — locking
        # this in so a future edit can't quietly start guessing it.
        for raw in ("supported", "rejected", "unknown", "", "garbage"):
            assert dp.map_synthesis_status(raw) != SynthesisStatus.INFERRED


class TestMapSynthesisRunState:
    def test_no_bundle_is_not_run(self):
        assert dp.map_synthesis_run_state(None) == SynthesisRunState.NOT_RUN

    def test_missing_reasoning_summary_is_not_run(self):
        bundle = SimpleNamespace(result={})
        assert dp.map_synthesis_run_state(bundle) == SynthesisRunState.NOT_RUN

    def test_missing_synthesis_state_key_is_not_run(self):
        # A reasoning_summary that predates this field (or the legacy
        # always-{} projection) — same honest default as no bundle at all.
        bundle = SimpleNamespace(result={"reasoning_summary": {"hypotheses": []}})
        assert dp.map_synthesis_run_state(bundle) == SynthesisRunState.NOT_RUN

    def test_unrecognized_value_falls_back_to_not_run(self):
        bundle = SimpleNamespace(result={"reasoning_summary": {"synthesis_state": "garbage"}})
        assert dp.map_synthesis_run_state(bundle) == SynthesisRunState.NOT_RUN

    def test_recognizes_all_four_real_values(self):
        for raw, expected in [
            ("not_run", SynthesisRunState.NOT_RUN),
            ("failed", SynthesisRunState.FAILED),
            ("completed_empty", SynthesisRunState.COMPLETED_EMPTY),
            ("completed", SynthesisRunState.COMPLETED),
        ]:
            bundle = SimpleNamespace(result={"reasoning_summary": {"synthesis_state": raw}})
            assert dp.map_synthesis_run_state(bundle) == expected


class TestMapHypotheses:
    def test_no_context_discovery_bundle_is_not_run_and_unavailable(self):
        entries, av, run_state = dp.map_hypotheses(None)
        assert entries == []
        assert av.status == Availability.UNAVAILABLE
        assert av.reason
        assert run_state == SynthesisRunState.NOT_RUN

    def test_missing_reasoning_summary_is_not_run_and_unavailable(self):
        bundle = SimpleNamespace(result={})
        entries, av, run_state = dp.map_hypotheses(bundle)
        assert entries == []
        assert av.status == Availability.UNAVAILABLE
        assert av.reason
        assert run_state == SynthesisRunState.NOT_RUN

    def test_failed_synthesis_is_degraded_not_unavailable(self):
        # The crux of the Phase 2 refinement: a real technical failure gets
        # its own Availability (DEGRADED), distinct from "never ran".
        bundle = SimpleNamespace(
            result={"reasoning_summary": {"synthesis_state": "failed", "hypotheses": []}}
        )
        entries, av, run_state = dp.map_hypotheses(bundle)
        assert entries == []
        assert av.status == Availability.DEGRADED
        assert "not the same as" in av.reason
        assert run_state == SynthesisRunState.FAILED

    def test_completed_empty_is_available_with_zero_items(self):
        # Synthesis genuinely ran and found nothing — this must be
        # AVAILABLE, never bucketed with NOT_RUN/FAILED.
        bundle = SimpleNamespace(
            result={"reasoning_summary": {"synthesis_state": "completed_empty", "hypotheses": []}}
        )
        entries, av, run_state = dp.map_hypotheses(bundle)
        assert entries == []
        assert av.status == Availability.AVAILABLE
        assert run_state == SynthesisRunState.COMPLETED_EMPTY

    def test_real_hypotheses_are_available_and_projected(self):
        bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": "The bug is in the parser",
                            "status": "supported",
                            "confidence": 0.75,
                            "supporting_evidence": ["trace shows parser raising"],
                            "contradicting_evidence": [],
                        },
                        {
                            "description": "The bug is in the renderer",
                            "status": "rejected",
                            "confidence": 0.1,
                            "supporting_evidence": [],
                            "contradicting_evidence": ["renderer never touched the bad input"],
                        },
                    ],
                }
            }
        )
        entries, av, run_state = dp.map_hypotheses(bundle)
        assert av.status == Availability.AVAILABLE
        assert run_state == SynthesisRunState.COMPLETED
        assert len(entries) == 2
        assert entries[0].statement == "The bug is in the parser"
        assert entries[0].status == SynthesisStatus.SUPPORTED
        assert entries[0].confidence == 0.75
        assert entries[1].status == SynthesisStatus.CONTRADICTED

    def test_evidence_is_prose_not_id_shaped(self):
        # Requirement 3: supporting_evidence/contradicting_evidence must
        # never be treated as stable Evidence IDs — they stay plain prose
        # strings, copied verbatim.
        bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": "h",
                            "status": "unknown",
                            "confidence": 0.5,
                            "supporting_evidence": ["the README mentions this explicitly"],
                            "contradicting_evidence": [],
                        }
                    ],
                }
            }
        )
        entries, _, _ = dp.map_hypotheses(bundle)
        assert entries[0].supporting_evidence == ["the README mentions this explicitly"]
        for item in entries[0].supporting_evidence:
            assert isinstance(item, str)


class TestMapContradictions:
    def test_no_context_discovery_bundle_is_not_run_and_unavailable(self):
        entries, av, run_state = dp.map_contradictions(None)
        assert entries == []
        assert av.status == Availability.UNAVAILABLE
        assert av.reason
        assert run_state == SynthesisRunState.NOT_RUN

    def test_failed_synthesis_is_degraded_not_unavailable(self):
        bundle = SimpleNamespace(
            result={"reasoning_summary": {"synthesis_state": "failed", "contradictions": []}}
        )
        entries, av, run_state = dp.map_contradictions(bundle)
        assert entries == []
        assert av.status == Availability.DEGRADED
        assert run_state == SynthesisRunState.FAILED

    def test_completed_empty_is_available_with_zero_items(self):
        bundle = SimpleNamespace(
            result={
                "reasoning_summary": {"synthesis_state": "completed_empty", "contradictions": []}
            }
        )
        entries, av, run_state = dp.map_contradictions(bundle)
        assert entries == []
        assert av.status == Availability.AVAILABLE
        assert run_state == SynthesisRunState.COMPLETED_EMPTY

    def test_real_contradiction_is_available_and_projected(self):
        bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "contradictions": [
                        {
                            "description": "Two sources disagree on the owning team",
                            "evidence_for": ["CODEOWNERS lists team-a"],
                            "evidence_against": ["ticket says team-b owns it"],
                            "resolved": False,
                            "resolution_note": "",
                        }
                    ],
                }
            }
        )
        entries, av, run_state = dp.map_contradictions(bundle)
        assert av.status == Availability.AVAILABLE
        assert run_state == SynthesisRunState.COMPLETED
        assert len(entries) == 1
        assert entries[0].statement == "Two sources disagree on the owning team"
        assert entries[0].evidence_for == ["CODEOWNERS lists team-a"]
        assert entries[0].evidence_against == ["ticket says team-b owns it"]
        assert entries[0].resolved is False


# ---------------------------------------------------------------------------
# Scope / FileRole
# ---------------------------------------------------------------------------


class TestMapFileRole:
    def test_defaults_to_modified(self):
        component = {"file_path": "src/x.py"}
        assert dp.map_file_role(component, []) == FileRole.MODIFIED

    def test_proposed_unverified_when_cross_referenced_usage_unverified(self):
        component = {"file_path": "src/x.py"}
        usage = [{"name": "acme/repo", "verified": False, "files_affected": ["src/x.py"]}]
        assert dp.map_file_role(component, usage) == FileRole.PROPOSED_UNVERIFIED

    def test_modified_when_matching_usage_is_verified(self):
        component = {"file_path": "src/x.py"}
        usage = [{"name": "acme/repo", "verified": True, "files_affected": ["src/x.py"]}]
        assert dp.map_file_role(component, usage) == FileRole.MODIFIED


class TestMapScope:
    def test_missing_development_falls_back_to_planning(self):
        planning = SimpleNamespace(
            result={
                "repository_usage": [
                    {"name": "acme/repo", "verified": True, "files_affected": ["a.py"]}
                ]
            }
        )
        entries, source = dp.map_scope(None, planning)
        assert source == "planning_fallback"
        assert len(entries) == 1
        assert entries[0].role == FileRole.MODIFIED

    def test_development_present_uses_development_and_cross_references(self):
        planning = SimpleNamespace(
            result={
                "repository_usage": [
                    {"name": "acme/repo", "verified": False, "files_affected": ["a.py"]}
                ]
            }
        )
        development = SimpleNamespace(
            result={"components": [{"file_path": "a.py", "repository": "acme/repo"}]}
        )
        entries, source = dp.map_scope(development, planning)
        assert source == "development"
        assert entries[0].role == FileRole.PROPOSED_UNVERIFIED

    def test_both_missing_empty_scope(self):
        entries, source = dp.map_scope(None, None)
        assert entries == []
        assert source is None


# ---------------------------------------------------------------------------
# Architecture / grounded
# ---------------------------------------------------------------------------


class TestMapArchitectureDiagrams:
    def test_no_blueprint_empty_list(self):
        assert dp.map_architecture_diagrams(None, "planning") == []

    def test_grounded_true_diagram(self):
        bundle = SimpleNamespace(
            result={
                "blueprint": {
                    "diagrams": [
                        {
                            "id": "repo_reuse",
                            "title": "Repository Reuse",
                            "metadata": {"grounded": True},
                        }
                    ]
                }
            }
        )
        refs = dp.map_architecture_diagrams(bundle, "planning")
        assert len(refs) == 1
        assert refs[0].grounded is True
        assert refs[0].grounded_label == "Graph-grounded"

    def test_grounded_false_diagram(self):
        bundle = SimpleNamespace(
            result={
                "blueprint": {
                    "diagrams": [
                        {
                            "id": "architecture",
                            "title": "Solution Architecture",
                            "metadata": {"grounded": False},
                        }
                    ]
                }
            }
        )
        refs = dp.map_architecture_diagrams(bundle, "planning")
        assert refs[0].grounded is False
        assert refs[0].grounded_label == "Conceptual — not graph-derived"

    def test_missing_grounded_key_defaults_to_conceptual_not_authoritative(self):
        bundle = SimpleNamespace(
            result={"blueprint": {"diagrams": [{"id": "x", "title": "X", "metadata": {}}]}}
        )
        refs = dp.map_architecture_diagrams(bundle, "planning")
        assert refs[0].grounded is False


# ---------------------------------------------------------------------------
# Risks
# ---------------------------------------------------------------------------


class TestMapRisks:
    def test_no_risks(self):
        assert dp.map_risks(None, None) == []

    def test_development_risk_has_no_mitigation_judgment(self):
        development = SimpleNamespace(
            result={"risks": [{"description": "d", "severity": "high", "mitigation": "m"}]}
        )
        risks = dp.map_risks(development, None)
        assert risks[0].mitigated is None
        assert risks[0].severity == RiskSeverity.HIGH

    def test_engineering_review_risk_has_real_mitigated_bool(self):
        er = SimpleNamespace(
            result={"risk_assessment": [{"description": "d", "adequately_mitigated": False}]}
        )
        risks = dp.map_risks(None, er)
        assert risks[0].mitigated is False

    def test_unmitigated_high_severity_sorts_first(self):
        development = SimpleNamespace(
            result={
                "risks": [
                    {"description": "low", "severity": "low"},
                    {"description": "high", "severity": "high"},
                ]
            }
        )
        risks = dp.map_risks(development, None)
        assert risks[0].description == "high"

    def test_no_fuzzy_merge_across_stages(self):
        development = SimpleNamespace(result={"risks": [{"description": "same text"}]})
        er = SimpleNamespace(
            result={"risk_assessment": [{"description": "same text", "adequately_mitigated": True}]}
        )
        risks = dp.map_risks(development, er)
        # Two separate entries, never merged into one.
        assert len(risks) == 2


# ---------------------------------------------------------------------------
# Open questions
# ---------------------------------------------------------------------------


class TestMapOpenQuestions:
    def test_none_when_nothing_present(self):
        assert dp.map_open_questions(None, None) == []

    def test_blocking_issues_are_blocking(self):
        er = SimpleNamespace(result={"blocking_issues": ["fix X"]})
        qs = dp.map_open_questions(None, er)
        assert qs[0].is_blocking is True
        assert qs[0].source_stage == "engineering_review"

    def test_open_gap_included_claimed_included_refuted_excluded(self):
        cd = SimpleNamespace(
            result={
                "discovery_report": {
                    "gaps": [
                        {
                            "gap_id": "g1",
                            "summary": "open one",
                            "status": "open",
                            "severity": "advisory",
                        },
                        {
                            "gap_id": "g2",
                            "summary": "claimed one",
                            "status": "claimed",
                            "severity": "blocking",
                        },
                        {
                            "gap_id": "g3",
                            "summary": "refuted one",
                            "status": "refuted",
                            "severity": "blocking",
                        },
                    ]
                }
            }
        )
        qs = dp.map_open_questions(cd, None)
        texts = {q.text for q in qs}
        assert "open one" in texts
        assert "claimed one" in texts
        assert "refuted one" not in texts
        claimed = next(q for q in qs if q.text == "claimed one")
        assert claimed.is_blocking is True


# ---------------------------------------------------------------------------
# fetch_all_stage_bundles — full/missing/large-repo workflow shapes
# ---------------------------------------------------------------------------


class TestFetchAllStageBundles:
    def test_complete_workflow_all_stages_present(self):
        runs = [
            _make_run(
                stage,
                "completed",
                _make_step(result={"executive_summary": stage}, confidence_score=0.9),
            )
            for stage in dp.STAGE_ORDER
        ]
        workflow = _make_workflow(runs)
        bundles = dp.fetch_all_stage_bundles(workflow)
        assert all(bundles[s] is not None for s in dp.STAGE_ORDER)

    def test_missing_context_discovery_only_that_stage_is_none(self):
        runs = [
            _make_run(stage, "completed", _make_step(result={"x": 1}))
            for stage in dp.STAGE_ORDER
            if stage != "context_discovery"
        ]
        workflow = _make_workflow(runs)
        bundles = dp.fetch_all_stage_bundles(workflow)
        assert bundles["context_discovery"] is None
        assert bundles["planning"] is not None

    def test_failed_stage_treated_same_as_missing(self):
        runs = [_make_run("planning", "failed", _make_step(result={}))]
        workflow = _make_workflow(runs)
        bundles = dp.fetch_all_stage_bundles(workflow)
        assert bundles["planning"] is None

    def test_partial_degraded_stage_has_result_but_thin_confidence(self):
        # A stage that "completed" with a sparse result — plumbing must
        # not treat this as missing; downstream map_availability decides
        # DEGRADED vs AVAILABLE based on required_fields, not this layer.
        runs = [
            _make_run(
                "documentation_planning",
                "completed",
                _make_step(result={"executive_summary": "thin"}, confidence_score=0.3),
            )
        ]
        workflow = _make_workflow(runs)
        bundles = dp.fetch_all_stage_bundles(workflow)
        assert bundles["documentation_planning"] is not None
        assert bundles["documentation_planning"].confidence_score == 0.3

    def test_multiple_repositories_scope(self):
        planning = SimpleNamespace(
            result={
                "repository_usage": [
                    {"name": "acme/repo-a", "verified": True, "files_affected": ["a.py"]},
                    {"name": "acme/repo-b", "verified": True, "files_affected": ["b.py"]},
                ]
            }
        )
        entries, _ = dp.map_scope(None, planning)
        repos = {e.repository for e in entries}
        assert repos == {"acme/repo-a", "acme/repo-b"}

    def test_1000_plus_graph_nodes_evidence_summary_stays_bounded(self):
        evidence = [{"kind": "graph_traversal"} for _ in range(1500)] + [
            {"kind": "tool_call"} for _ in range(200)
        ]
        counts = dp.map_evidence_summary(evidence)
        # Bounded to distinct kinds, never one row per evidence item.
        assert len(counts) == 2
        total = sum(c.count for c in counts)
        assert total == 1700
