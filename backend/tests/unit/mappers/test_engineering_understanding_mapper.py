"""Behaviour-driven tests for the engineering understanding mapper.

Each test verifies a single aspect of ``map_to_dto()`` per the RFC-003
mapping validation table.  All tests are synchronous (no I/O) and use
plain model construction — no mocks, no DB, no fixtures beyond helpers
defined here.
"""

from __future__ import annotations

import pytest

from app.context_pipeline.reasoning.curation import EvidenceItem, EvidencePackage
from app.context_pipeline.reasoning.understanding import (
    Contradiction,
    EngineeringUnderstanding,
    Hypothesis,
    InvestigationWorkspace,
)
from app.mappers.engineering_understanding_mapper import map_to_dto
from app.schemas.engineering_understanding import (
    CapabilityFactor,
    ComponentProjection,
    DebugBundleDTO,
    EngineeringUnderstandingDTO,
    ProjectionInput,
    TopicProjection,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_input(**overrides) -> ProjectionInput:
    """Build a ``ProjectionInput`` with sensible empty defaults."""
    defaults: dict = {
        "understanding": EngineeringUnderstanding(),
        "evidence_package": EvidencePackage(),
    }
    defaults.update(overrides)
    return ProjectionInput(**defaults)


def _evidence_item(name: str, tier: str, **kw) -> EvidenceItem:
    """Shorthand for an ``EvidenceItem`` with required fields filled."""
    defaults: dict = {
        "name": name,
        "repository": "org/repo",
        "tier": tier,
        "relevance_score": 0.9,
        "proximity_score": 0.8,
        "repository_bonus": 0.0,
        "test_penalty": 0.0,
        "composite_score": 0.85,
        "confidence": 0.9,
        "reason": "test evidence",
    }
    defaults.update(kw)
    return EvidenceItem(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyInput:
    """Empty input → sensible defaults, no crash."""

    def test_returns_dto(self):
        dto = map_to_dto(_minimal_input())
        assert isinstance(dto, EngineeringUnderstandingDTO)

    def test_business_goal_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.business_goal == ""

    def test_planning_defaults_blocked(self):
        dto = map_to_dto(_minimal_input())
        assert dto.planning_assessment.status == "BLOCKED"

    def test_debug_bundle_none(self):
        dto = map_to_dto(_minimal_input())
        assert dto.debug_bundle is None

    def test_relevant_areas_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.relevant_areas == []

    def test_unknowns_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.unknowns == []

    def test_evidence_summary_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.evidence_summary == []


class TestMinimalInput:
    """Minimal input → fallback business_goal from original_request."""

    def test_fallback_business_goal(self):
        dto = map_to_dto(_minimal_input(original_request="Add Kafka consumer"))
        assert dto.business_goal == "Add Kafka consumer"

    def test_explicit_objective_overrides_fallback(self):
        dto = map_to_dto(
            _minimal_input(
                understanding=EngineeringUnderstanding(
                    business_objective="Implement SSO",
                ),
                original_request="Add SSO support",
            )
        )
        assert dto.business_goal == "Implement SSO"


class TestCompleteInput:
    """Complete input → all 16 DTO fields correctly mapped."""

    @pytest.fixture
    def complete_dto(self) -> EngineeringUnderstandingDTO:
        understanding = EngineeringUnderstanding(
            business_objective="Implement SSO",
            current_behavior="Basic auth only",
            desired_behavior="SAML-based SSO",
            primary_repository="org/auth-service",
            supporting_repositories=["org/gateway"],
            implementation_ownership=["auth-team"],
            architecture_relationships=[
                "auth-service → gateway",
                "gateway → IdP",
            ],
            constraints=["Must support SAML 2.0"],
            remaining_unknowns=["IdP certificate rotation"],
            rejected_assumptions=["OAuth is sufficient"],
            engineering_insights=["Reuse existing session manager"],
            risks=["IdP downtime affects all logins"],
        )
        evidence = EvidencePackage(
            items=[
                _evidence_item("AuthController", "must_modify"),
                _evidence_item("SessionManager", "reusable_component"),
            ],
        )
        inp = _minimal_input(
            understanding=understanding,
            evidence_package=evidence,
            original_request="Add SSO",
            readiness="PARTIAL",
            blocking_reasons=["IdP integration pending"],
            graph_topics=[TopicProjection(name="Authentication")],
            graph_components=[
                ComponentProjection(
                    name="AuthController", topic="Authentication",
                ),
            ],
            capability_factors=[
                CapabilityFactor(
                    capability="code_understanding",
                    label="Code understanding",
                    satisfied=True,
                ),
                CapabilityFactor(
                    capability="documentation",
                    label="Documentation",
                    satisfied=False,
                ),
            ],
            gap_summaries=["Missing IdP config docs"],
            unavailable_gaps=["SAML metadata endpoint"],
            documentation_status=(
                "Documentation for IdP integration is missing."
            ),
            next_step="Resolve blocking issues: IdP integration pending",
        )
        return map_to_dto(inp)

    def test_business_goal(self, complete_dto):
        assert complete_dto.business_goal == "Implement SSO"

    def test_current_situation(self, complete_dto):
        assert complete_dto.current_situation == "Basic auth only"

    def test_expected_outcome(self, complete_dto):
        assert complete_dto.expected_outcome == "SAML-based SSO"

    def test_repository_summary(self, complete_dto):
        assert complete_dto.repository_summary.primary == "org/auth-service"
        assert complete_dto.repository_summary.supporting == ["org/gateway"]
        assert complete_dto.repository_summary.ownership == ["auth-team"]

    def test_architecture_summary(self, complete_dto):
        assert "auth-service → gateway" in complete_dto.architecture_summary
        assert "gateway → IdP" in complete_dto.architecture_summary

    def test_relevant_areas(self, complete_dto):
        # Tier-based (Production Code / Architecture / Reusable Components /
        # Tests), sourced from the curated EvidencePackage — not the raw
        # graph_topics/graph_components grouping this DTO no longer reads.
        assert len(complete_dto.relevant_areas) >= 1
        production = next(
            a for a in complete_dto.relevant_areas if a.name == "Production Code"
        )
        assert "AuthController" in production.components

    def test_files_to_review(self, complete_dto):
        assert complete_dto.files_to_review == []  # _evidence_item has no path by default

    def test_known_constraints(self, complete_dto):
        assert "Must support SAML 2.0" in complete_dto.known_constraints

    def test_missing_information(self, complete_dto):
        assert "Missing IdP config docs" in complete_dto.missing_information
        assert "IdP certificate rotation" in complete_dto.missing_information

    def test_unknowns(self, complete_dto):
        categories = {u.category for u in complete_dto.unknowns}
        assert "unknown" in categories
        assert "known" in categories
        assert "unavailable" in categories

    def test_evidence_summary(self, complete_dto):
        assert len(complete_dto.evidence_summary) >= 1

    def test_recommendations(self, complete_dto):
        assert "Reuse existing session manager" in complete_dto.recommendations
        risk_items = [
            r for r in complete_dto.recommendations if r.startswith("Risk:")
        ]
        assert len(risk_items) == 1

    def test_planning_assessment(self, complete_dto):
        assert complete_dto.planning_assessment.status == "PARTIAL"
        assert len(complete_dto.planning_assessment.reasons) >= 1

    def test_confidence_explanation(self, complete_dto):
        assert "Completed" in complete_dto.confidence_explanation
        assert "Outstanding" in complete_dto.confidence_explanation

    def test_documentation_status(self, complete_dto):
        assert "missing" in complete_dto.documentation_status.lower()

    def test_next_step(self, complete_dto):
        assert "blocking" in complete_dto.next_step.lower()

    def test_debug_bundle_none_by_default(self, complete_dto):
        assert complete_dto.debug_bundle is None


class TestMissingOptional:
    """Missing optional fields → graceful defaults."""

    def test_empty_architecture(self):
        dto = map_to_dto(_minimal_input())
        assert dto.architecture_summary == ""

    def test_empty_constraints(self):
        dto = map_to_dto(_minimal_input())
        assert dto.known_constraints == []

    def test_empty_recommendations(self):
        dto = map_to_dto(_minimal_input())
        assert dto.recommendations == []

    def test_documentation_status_default_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.documentation_status == ""

    def test_next_step_default_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.next_step == ""


class TestRepositoryMapping:
    """Repository mapping → primary/supporting/ownership correct."""

    def test_primary_repo(self):
        u = EngineeringUnderstanding(primary_repository="org/main")
        dto = map_to_dto(_minimal_input(understanding=u))
        assert dto.repository_summary.primary == "org/main"

    def test_supporting_repos(self):
        u = EngineeringUnderstanding(
            supporting_repositories=["org/lib-a", "org/lib-b"],
        )
        dto = map_to_dto(_minimal_input(understanding=u))
        assert dto.repository_summary.supporting == ["org/lib-a", "org/lib-b"]

    def test_ownership(self):
        u = EngineeringUnderstanding(
            implementation_ownership=["team-alpha", "team-beta"],
        )
        dto = map_to_dto(_minimal_input(understanding=u))
        assert dto.repository_summary.ownership == ["team-alpha", "team-beta"]


class TestAreaGrouping:
    """Area grouping → tiered clusters from the curated EvidencePackage,
    ranked and capped — the P1 fix for the audit's "hundreds of ungrouped
    test-function names" finding."""

    def test_groups_by_tier(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item("LoginCtrl", "must_modify"),
                _evidence_item("SessionMgr", "must_modify"),
                _evidence_item("test_login", "relevant_test"),
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        names = {a.name for a in dto.relevant_areas}
        assert names == {"Production Code", "Tests"}
        production = next(a for a in dto.relevant_areas if a.name == "Production Code")
        assert set(production.components) == {"LoginCtrl", "SessionMgr"}
        tests = next(a for a in dto.relevant_areas if a.name == "Tests")
        assert tests.components == ["test_login"]

    def test_every_tier_gets_its_own_honest_label(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item("A", "must_modify"),
                _evidence_item("B", "architecture_dependency"),
                _evidence_item("C", "reusable_component"),
                _evidence_item("D", "relevant_test"),
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        labels = [a.name for a in dto.relevant_areas]
        # Fixed order: must_modify, architecture_dependency,
        # reusable_component, relevant_test (same order curate() emits).
        assert labels == [
            "Production Code",
            "Architecture",
            "Reusable Components",
            "Tests",
        ]

    def test_a_tier_with_zero_items_is_omitted_not_shown_empty(self):
        evidence = EvidencePackage(items=[_evidence_item("A", "must_modify")])
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert [a.name for a in dto.relevant_areas] == ["Production Code"]

    def test_empty_evidence_package(self):
        dto = map_to_dto(_minimal_input())
        assert dto.relevant_areas == []

    def test_a_large_tier_is_capped_with_an_honest_total(self):
        # The exact real-world shape the audit found: hundreds of test
        # functions in one tier. Must be capped for display, never dumped
        # as a wall of text, with the true count always stated alongside.
        evidence = EvidencePackage(
            items=[_evidence_item(f"test_{i}", "relevant_test") for i in range(340)],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        tests = next(a for a in dto.relevant_areas if a.name == "Tests")
        assert len(tests.components) <= 12
        assert tests.total == 340


class TestUnknownCategorisation:
    """Unknown categorisation → 3 buckets (unknown/known/unavailable)."""

    def test_remaining_unknowns_categorised(self):
        u = EngineeringUnderstanding(remaining_unknowns=["What is X?"])
        dto = map_to_dto(_minimal_input(understanding=u))
        assert any(
            x.category == "unknown" and x.description == "What is X?"
            for x in dto.unknowns
        )

    def test_rejected_assumptions_categorised(self):
        u = EngineeringUnderstanding(rejected_assumptions=["OAuth works"])
        dto = map_to_dto(_minimal_input(understanding=u))
        assert any(
            x.category == "known" and x.description == "OAuth works"
            for x in dto.unknowns
        )

    def test_unavailable_gaps_categorised(self):
        dto = map_to_dto(
            _minimal_input(unavailable_gaps=["SAML endpoint"]),
        )
        assert any(
            x.category == "unavailable"
            and x.description == "SAML endpoint"
            for x in dto.unknowns
        )


class TestEvidenceSummary:
    """Evidence summary → per-tier prose with examples and counts."""

    def test_single_tier(self):
        evidence = EvidencePackage(
            items=[_evidence_item("Ctrl", "must_modify")],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert any("Must-modify" in s for s in dto.evidence_summary)
        assert any("Ctrl" in s for s in dto.evidence_summary)

    def test_multiple_tiers(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item("A", "must_modify"),
                _evidence_item("B", "reusable_component"),
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert any("Must-modify" in s for s in dto.evidence_summary)
        assert any("Reusable component" in s for s in dto.evidence_summary)

    def test_more_than_three_shows_count(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item(f"Item{i}", "must_modify") for i in range(5)
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        line = next(
            s for s in dto.evidence_summary if "Must-modify" in s
        )
        assert "and 2 more" in line

    def test_excluded_count_message(self):
        evidence = EvidencePackage(
            items=[_evidence_item("A", "must_modify")],
            excluded_count=10,
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert any("10 additional" in s for s in dto.evidence_summary)


class TestPlanningAssessment:
    """Planning assessment → uses CapabilityFactor.satisfied."""

    def test_ready_status(self):
        dto = map_to_dto(_minimal_input(readiness="READY"))
        assert dto.planning_assessment.status == "READY"

    def test_partial_status(self):
        dto = map_to_dto(_minimal_input(readiness="PARTIAL"))
        assert dto.planning_assessment.status == "PARTIAL"

    def test_blocked_status(self):
        dto = map_to_dto(_minimal_input(readiness="BLOCKED"))
        assert dto.planning_assessment.status == "BLOCKED"

    def test_factors_mapped_to_reasons(self):
        factors = [
            CapabilityFactor(
                capability="code_understanding",
                label="Code analysis",
                satisfied=True,
            ),
            CapabilityFactor(
                capability="documentation",
                label="Documentation",
                satisfied=False,
            ),
        ]
        dto = map_to_dto(_minimal_input(capability_factors=factors))
        satisfied_reasons = [
            r for r in dto.planning_assessment.reasons if r.satisfied
        ]
        unsatisfied_reasons = [
            r for r in dto.planning_assessment.reasons if not r.satisfied
        ]
        assert len(satisfied_reasons) >= 1
        assert len(unsatisfied_reasons) >= 1

    def test_blocking_reasons_added(self):
        dto = map_to_dto(
            _minimal_input(blocking_reasons=["Missing graph index"]),
        )
        descs = [r.description for r in dto.planning_assessment.reasons]
        assert "Missing graph index" in descs


class TestConfidenceExplanation:
    """Confidence explanation → readable prose."""

    def test_all_satisfied(self):
        factors = [
            CapabilityFactor(
                capability="code_understanding",
                label="Alpha",
                satisfied=True,
            ),
            CapabilityFactor(
                capability="architecture",
                label="Beta",
                satisfied=True,
            ),
        ]
        dto = map_to_dto(_minimal_input(capability_factors=factors))
        assert "Completed" in dto.confidence_explanation
        assert "Outstanding" not in dto.confidence_explanation

    def test_mixed(self):
        factors = [
            CapabilityFactor(
                capability="code_understanding",
                label="Alpha",
                satisfied=True,
            ),
            CapabilityFactor(
                capability="documentation",
                label="Beta",
                satisfied=False,
            ),
        ]
        dto = map_to_dto(_minimal_input(capability_factors=factors))
        assert "Completed: Alpha" in dto.confidence_explanation
        assert "Outstanding: Beta" in dto.confidence_explanation

    def test_none_satisfied(self):
        factors = [
            CapabilityFactor(
                capability="code_understanding",
                label="Alpha",
                satisfied=False,
            ),
        ]
        dto = map_to_dto(_minimal_input(capability_factors=factors))
        assert "Outstanding" in dto.confidence_explanation
        assert "Completed" not in dto.confidence_explanation


class TestDebugBundle:
    """Debug disabled → None; debug enabled → pass-through."""

    def test_debug_disabled_returns_none(self):
        bundle = DebugBundleDTO(investigation_trail=[{"step": 1}])
        dto = map_to_dto(
            _minimal_input(debug_bundle=bundle),
            include_debug=False,
        )
        assert dto.debug_bundle is None

    def test_debug_enabled_returns_bundle(self):
        bundle = DebugBundleDTO(investigation_trail=[{"step": 1}])
        dto = map_to_dto(
            _minimal_input(debug_bundle=bundle),
            include_debug=True,
        )
        assert dto.debug_bundle is not None
        assert dto.debug_bundle.investigation_trail == [{"step": 1}]

    def test_debug_enabled_no_bundle_returns_none(self):
        dto = map_to_dto(_minimal_input(), include_debug=True)
        assert dto.debug_bundle is None


class TestDTOSerialization:
    """DTO serialization → model_dump() round-trips."""

    def test_round_trip(self):
        u = EngineeringUnderstanding(
            business_objective="Test objective",
            primary_repository="org/repo",
        )
        inp = _minimal_input(understanding=u)
        dto = map_to_dto(inp)
        dumped = dto.model_dump()
        restored = EngineeringUnderstandingDTO(**dumped)
        assert restored.business_goal == dto.business_goal
        assert (
            restored.repository_summary.primary
            == dto.repository_summary.primary
        )

    def test_round_trip_with_debug(self):
        bundle = DebugBundleDTO(
            investigation_trail=[{"step": "search"}],
            findings=[{"type": "component", "name": "Ctrl"}],
        )
        dto = map_to_dto(
            _minimal_input(debug_bundle=bundle),
            include_debug=True,
        )
        dumped = dto.model_dump()
        restored = EngineeringUnderstandingDTO(**dumped)
        assert restored.debug_bundle is not None
        assert restored.debug_bundle.investigation_trail == [
            {"step": "search"},
        ]


class TestMissingInformationDedup:
    """missing_information deduplicates gap_summaries + unknowns."""

    def test_deduplicates(self):
        u = EngineeringUnderstanding(remaining_unknowns=["Gap A", "Gap B"])
        dto = map_to_dto(
            _minimal_input(
                understanding=u,
                gap_summaries=["Gap A", "Gap C"],
            ),
        )
        assert dto.missing_information.count("Gap A") == 1
        assert set(dto.missing_information) == {"Gap A", "Gap B", "Gap C"}

    def test_preserves_order(self):
        u = EngineeringUnderstanding(remaining_unknowns=["B", "C"])
        dto = map_to_dto(
            _minimal_input(
                understanding=u,
                gap_summaries=["A", "B"],
            ),
        )
        # gap_summaries come first, then remaining_unknowns (deduped)
        assert dto.missing_information == ["A", "B", "C"]


class TestDocumentationStatus:
    """documentation_status is a caller-derived pass-through."""

    def test_pass_through(self):
        dto = map_to_dto(
            _minimal_input(
                documentation_status="API docs are outdated.",
            ),
        )
        assert dto.documentation_status == "API docs are outdated."

    def test_default_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.documentation_status == ""


class TestNextStep:
    """next_step is a caller-derived pass-through."""

    def test_pass_through(self):
        dto = map_to_dto(
            _minimal_input(
                next_step="Resolve blocking issues: Missing graph",
            ),
        )
        assert dto.next_step == "Resolve blocking issues: Missing graph"

    def test_default_empty(self):
        dto = map_to_dto(_minimal_input())
        assert dto.next_step == ""


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


class TestUnicode:
    """Unicode characters in components, and unknowns."""

    def test_unicode_component_name(self):
        evidence = EvidencePackage(items=[_evidence_item("ログインCtrl", "must_modify")])
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        production = next(a for a in dto.relevant_areas if a.name == "Production Code")
        assert "ログインCtrl" in production.components

    def test_unicode_unknowns(self):
        u = EngineeringUnderstanding(
            remaining_unknowns=["¿Qué es X?"],
        )
        dto = map_to_dto(_minimal_input(understanding=u))
        assert any(
            x.description == "¿Qué es X?" for x in dto.unknowns
        )

    def test_unicode_business_goal(self):
        u = EngineeringUnderstanding(business_objective="Über-Feature")
        dto = map_to_dto(_minimal_input(understanding=u))
        assert dto.business_goal == "Über-Feature"


class TestDeterministicOrdering:
    """Identical input → identical output, every time."""

    def test_multiple_calls_same_result(self):
        inp = _minimal_input(
            understanding=EngineeringUnderstanding(
                business_objective="Stable",
                remaining_unknowns=["A", "B"],
                rejected_assumptions=["C"],
                engineering_insights=["I1", "I2"],
                risks=["R1"],
            ),
            capability_factors=[
                CapabilityFactor(
                    capability="code_understanding",
                    label="Code",
                    satisfied=True,
                ),
                CapabilityFactor(
                    capability="documentation",
                    label="Docs",
                    satisfied=False,
                ),
            ],
            gap_summaries=["G1"],
            unavailable_gaps=["U1"],
        )
        results = [map_to_dto(inp).model_dump() for _ in range(10)]
        assert all(r == results[0] for r in results)


class TestLargeGraphs:
    """Large input sets process correctly without issues."""

    def test_many_components_across_every_tier(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item(f"Comp{i}", "must_modify" if i % 2 == 0 else "relevant_test")
                for i in range(1000)
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert len(dto.relevant_areas) == 2
        for area in dto.relevant_areas:
            assert area.total == 500
            assert len(area.components) <= 12


class TestFilesToReview:
    """files_to_review → ranked production file paths, never test paths."""

    def test_reads_must_modify_paths(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item("rate_association", "must_modify", path="soco/rate_association.py"),
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert dto.files_to_review == ["soco/rate_association.py"]

    def test_never_includes_test_or_reusable_paths(self):
        # The exact real-world bug: "Files to Review" used to list only
        # test files because it read the raw, unranked component list.
        evidence = EvidencePackage(
            items=[
                _evidence_item("test_rate_association", "relevant_test", path="tests/test_ra.py"),
                _evidence_item("string_utils", "reusable_component", path="soco/utils.py"),
                _evidence_item("rate_association", "must_modify", path="soco/rate_association.py"),
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert dto.files_to_review == ["soco/rate_association.py"]

    def test_falls_back_to_architecture_dependency_to_fill_the_cap(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item("a", "must_modify", path="a.py"),
                _evidence_item("b", "architecture_dependency", path="b.py"),
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert dto.files_to_review == ["a.py", "b.py"]

    def test_deduplicates_and_caps_at_eight(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item(f"item{i}", "must_modify", path=f"path{i % 5}.py")
                for i in range(20)
            ],
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert dto.files_to_review == [f"path{i}.py" for i in range(5)]

    def test_items_without_a_path_are_skipped(self):
        evidence = EvidencePackage(items=[_evidence_item("no_path", "must_modify")])
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        assert dto.files_to_review == []

    def test_empty_evidence_package(self):
        dto = map_to_dto(_minimal_input())
        assert dto.files_to_review == []

    def test_many_evidence_items(self):
        evidence = EvidencePackage(
            items=[
                _evidence_item(f"Item{i}", "must_modify")
                for i in range(200)
            ],
            excluded_count=500,
        )
        dto = map_to_dto(_minimal_input(evidence_package=evidence))
        line = next(
            s for s in dto.evidence_summary if "Must-modify" in s
        )
        assert "(200)" in line
        assert "and 197 more" in line

    def test_many_unknowns(self):
        u = EngineeringUnderstanding(
            remaining_unknowns=[f"Unknown{i}" for i in range(500)],
        )
        dto = map_to_dto(_minimal_input(understanding=u))
        assert len(dto.unknowns) == 500
        assert all(x.category == "unknown" for x in dto.unknowns)


# ---------------------------------------------------------------------------
# reasoning_summary — hypotheses/contradictions projection
# ---------------------------------------------------------------------------


class TestReasoningSummaryEmpty:
    """No workspace at all → an honest empty state, never a crash."""

    def test_has_reasoning_false(self):
        dto = map_to_dto(_minimal_input())
        assert dto.reasoning_summary.has_reasoning is False

    def test_no_hypotheses_or_contradictions(self):
        dto = map_to_dto(_minimal_input())
        assert dto.reasoning_summary.hypotheses == []
        assert dto.reasoning_summary.contradictions == []

    def test_degraded_false_by_default(self):
        """Genuinely nothing to reason about is NOT the same as a failed
        synthesis call — both must be distinguishable, and the default
        (empty workspace, no notes) must read as the former."""
        dto = map_to_dto(_minimal_input())
        assert dto.reasoning_summary.degraded is False

    def test_strongest_hypothesis_id_none(self):
        dto = map_to_dto(_minimal_input())
        assert dto.reasoning_summary.strongest_hypothesis_id is None


class TestReasoningSummaryHypotheses:
    def test_no_hypotheses(self):
        workspace = InvestigationWorkspace(hypotheses=[])
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert dto.reasoning_summary.hypotheses == []
        assert dto.reasoning_summary.strongest_hypothesis_id is None

    def test_one_hypothesis_is_strongest(self):
        workspace = InvestigationWorkspace(
            hypotheses=[
                Hypothesis(description="X causes Y", status="supported", confidence=0.8),
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert len(dto.reasoning_summary.hypotheses) == 1
        assert dto.reasoning_summary.hypotheses[0].id == "hyp_0"
        assert dto.reasoning_summary.hypotheses[0].is_strongest is True
        assert dto.reasoning_summary.strongest_hypothesis_id == "hyp_0"

    def test_strongest_is_highest_confidence_among_non_rejected(self):
        workspace = InvestigationWorkspace(
            hypotheses=[
                Hypothesis(description="weak", status="unknown", confidence=0.3),
                Hypothesis(description="strong", status="supported", confidence=0.9),
                Hypothesis(description="medium", status="unknown", confidence=0.6),
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        strongest = next(h for h in dto.reasoning_summary.hypotheses if h.is_strongest)
        assert strongest.description == "strong"
        assert dto.reasoning_summary.strongest_hypothesis_id == "hyp_1"
        # Exactly one hypothesis is ever flagged strongest.
        assert sum(1 for h in dto.reasoning_summary.hypotheses if h.is_strongest) == 1

    def test_a_high_confidence_rejected_hypothesis_is_never_strongest(self):
        """A hypothesis the model itself eliminated must never be crowned
        "strongest" just because it once scored high before rejection."""
        workspace = InvestigationWorkspace(
            hypotheses=[
                Hypothesis(description="eliminated", status="rejected", confidence=0.95),
                Hypothesis(description="survives", status="unknown", confidence=0.4),
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        strongest = next(h for h in dto.reasoning_summary.hypotheses if h.is_strongest)
        assert strongest.description == "survives"

    def test_all_rejected_means_no_strongest(self):
        workspace = InvestigationWorkspace(
            hypotheses=[
                Hypothesis(description="a", status="rejected", confidence=0.9),
                Hypothesis(description="b", status="rejected", confidence=0.7),
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert dto.reasoning_summary.strongest_hypothesis_id is None
        assert all(not h.is_strongest for h in dto.reasoning_summary.hypotheses)

    def test_supporting_and_contradicting_evidence_pass_through(self):
        workspace = InvestigationWorkspace(
            hypotheses=[
                Hypothesis(
                    description="X",
                    status="unknown",
                    confidence=0.5,
                    supporting_evidence=["ticket says X", "PR #12 implements X"],
                    contradicting_evidence=["doc says not-X"],
                )
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        h = dto.reasoning_summary.hypotheses[0]
        assert h.supporting_evidence == ["ticket says X", "PR #12 implements X"]
        assert h.contradicting_evidence == ["doc says not-X"]
        assert dto.reasoning_summary.has_reasoning is True


class TestReasoningSummaryContradictions:
    def test_no_contradictions(self):
        workspace = InvestigationWorkspace(contradictions=[])
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert dto.reasoning_summary.contradictions == []
        assert dto.reasoning_summary.open_contradiction_count == 0
        assert dto.reasoning_summary.resolved_contradiction_count == 0

    def test_one_unresolved_contradiction(self):
        workspace = InvestigationWorkspace(
            contradictions=[
                Contradiction(
                    description="Ticket says X, code does Y",
                    evidence_for=["ticket text"],
                    evidence_against=["current implementation"],
                    resolved=False,
                )
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert len(dto.reasoning_summary.contradictions) == 1
        c = dto.reasoning_summary.contradictions[0]
        assert c.id == "contra_0"
        assert c.resolved is False
        assert dto.reasoning_summary.open_contradiction_count == 1
        assert dto.reasoning_summary.resolved_contradiction_count == 0

    def test_one_resolved_contradiction(self):
        workspace = InvestigationWorkspace(
            contradictions=[
                Contradiction(
                    description="Two docs disagreed",
                    resolved=True,
                    resolution_note="Newer doc confirmed as authoritative.",
                )
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert dto.reasoning_summary.resolved_contradiction_count == 1
        assert dto.reasoning_summary.open_contradiction_count == 0
        assert dto.reasoning_summary.contradictions[0].resolution_note == (
            "Newer doc confirmed as authoritative."
        )

    def test_multiple_contradictions_mixed_resolution(self):
        workspace = InvestigationWorkspace(
            contradictions=[
                Contradiction(description="a", resolved=True),
                Contradiction(description="b", resolved=False),
                Contradiction(description="c", resolved=False),
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert len(dto.reasoning_summary.contradictions) == 3
        assert dto.reasoning_summary.resolved_contradiction_count == 1
        assert dto.reasoning_summary.open_contradiction_count == 2
        assert [c.id for c in dto.reasoning_summary.contradictions] == [
            "contra_0",
            "contra_1",
            "contra_2",
        ]


class TestReasoningSummaryDegraded:
    def test_degraded_detected_from_history_entry(self):
        workspace = InvestigationWorkspace(
            reasoning_notes=[
                "Synthesis call failed or returned an invalid response; falling back to a "
                "deterministic, evidence-only summary."
            ],
            investigation_history=[
                "Cycle 1: synthesis degraded to a deterministic summary over 3 evidence record(s)."
            ],
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert dto.reasoning_summary.degraded is True

    def test_last_update_reflects_final_history_entry(self):
        workspace = InvestigationWorkspace(
            investigation_history=[
                "Cycle 1: re-synthesized over 2 evidence record(s) — 1 hypothesis/es.",
                "Cycle 2: re-synthesized over 5 evidence record(s) — 2 hypothesis/es.",
            ]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert dto.reasoning_summary.last_update == (
            "Cycle 2: re-synthesized over 5 evidence record(s) — 2 hypothesis/es."
        )


class TestReasoningSummaryDeadEndsAndNextInvestigation:
    def test_dead_ends_pass_through(self):
        workspace = InvestigationWorkspace(
            dead_ends=["Ruled out: caching layer — no cache in path."]
        )
        dto = map_to_dto(_minimal_input(workspace=workspace))
        assert dto.reasoning_summary.dead_ends == [
            "Ruled out: caching layer — no cache in path."
        ]

    def test_next_investigation_ranked_highest_first(self):
        dto = map_to_dto(
            _minimal_input(
                investigation_priority={"architecture": 0.4, "documentation": 0.9, "work_item": 0.1}
            )
        )
        labels = [i.capability for i in dto.reasoning_summary.next_investigation]
        assert labels == ["documentation", "architecture", "work_item"]
        assert dto.reasoning_summary.next_investigation[0].label == "Documentation"

    def test_unknown_capability_key_is_dropped_not_crashed(self):
        dto = map_to_dto(
            _minimal_input(investigation_priority={"not_a_real_capability": 0.9})
        )
        assert dto.reasoning_summary.next_investigation == []

    def test_next_investigation_empty_by_default(self):
        dto = map_to_dto(_minimal_input())
        assert dto.reasoning_summary.next_investigation == []


# ---------------------------------------------------------------------------
# completion_status — pass-through, source of truth stays the backend
# ---------------------------------------------------------------------------


class TestCompletionStatus:
    @pytest.mark.parametrize(
        "status",
        ["COMPLETED", "BUDGET_EXHAUSTED", "PROVIDERS_EXHAUSTED", "BLOCKED", "PARTIAL"],
    )
    def test_passes_through_unchanged(self, status):
        dto = map_to_dto(_minimal_input(completion_status=status))
        assert dto.completion_status == status

    def test_defaults_to_partial(self):
        dto = map_to_dto(_minimal_input())
        assert dto.completion_status == "PARTIAL"
