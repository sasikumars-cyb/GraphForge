"""Behaviour-driven tests for the engineering understanding mapper.

Each test verifies a single aspect of ``map_to_dto()`` per the RFC-003
mapping validation table.  All tests are synchronous (no I/O) and use
plain model construction — no mocks, no DB, no fixtures beyond helpers
defined here.
"""

from __future__ import annotations

import pytest

from app.context_pipeline.reasoning.curation import EvidenceItem, EvidencePackage
from app.context_pipeline.reasoning.understanding import EngineeringUnderstanding
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
        assert len(complete_dto.relevant_areas) >= 1
        auth_area = next(
            a
            for a in complete_dto.relevant_areas
            if a.name == "Authentication"
        )
        assert "AuthController" in auth_area.components

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
    """Area grouping → topic-based clusters + 'Other' for unmatched."""

    def test_groups_by_topic(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[TopicProjection(name="Auth")],
                graph_components=[
                    ComponentProjection(name="LoginCtrl", topic="Auth"),
                    ComponentProjection(name="SessionMgr", topic="Auth"),
                ],
            )
        )
        assert len(dto.relevant_areas) == 1
        assert dto.relevant_areas[0].name == "Auth"
        assert set(dto.relevant_areas[0].components) == {
            "LoginCtrl",
            "SessionMgr",
        }

    def test_case_insensitive_matching(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[TopicProjection(name="Authentication")],
                graph_components=[
                    ComponentProjection(
                        name="Ctrl", topic="authentication",
                    ),
                ],
            )
        )
        assert dto.relevant_areas[0].name == "Authentication"
        assert "Ctrl" in dto.relevant_areas[0].components

    def test_unmatched_goes_to_other(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[TopicProjection(name="Auth")],
                graph_components=[
                    ComponentProjection(
                        name="Orphan", topic="nonexistent",
                    ),
                ],
            )
        )
        other = next(a for a in dto.relevant_areas if a.name == "Other")
        assert "Orphan" in other.components

    def test_topic_with_no_components_still_appears(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[TopicProjection(name="EmptyTopic")],
                graph_components=[],
            )
        )
        assert any(a.name == "EmptyTopic" for a in dto.relevant_areas)

    def test_empty_topics_and_components(self):
        dto = map_to_dto(_minimal_input())
        assert dto.relevant_areas == []


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


class TestDuplicateTopics:
    """Duplicate topics (case-insensitive) → last wins for canonical name."""

    def test_duplicate_topic_same_case(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[
                    TopicProjection(name="Auth"),
                    TopicProjection(name="Auth"),
                ],
                graph_components=[
                    ComponentProjection(name="Ctrl", topic="Auth"),
                ],
            )
        )
        auth_areas = [a for a in dto.relevant_areas if a.name == "Auth"]
        assert len(auth_areas) == 1
        assert "Ctrl" in auth_areas[0].components

    def test_duplicate_topic_different_case(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[
                    TopicProjection(name="Auth"),
                    TopicProjection(name="auth"),
                ],
                graph_components=[
                    ComponentProjection(name="Ctrl", topic="AUTH"),
                ],
            )
        )
        # Components should be grouped under one cluster
        total_components = sum(
            len(a.components) for a in dto.relevant_areas
        )
        ctrl_found = any(
            "Ctrl" in a.components for a in dto.relevant_areas
        )
        assert total_components == 1
        assert ctrl_found


class TestUnicode:
    """Unicode characters in topics, components, and unknowns."""

    def test_unicode_topic_and_component(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[TopicProjection(name="認証")],
                graph_components=[
                    ComponentProjection(name="ログインCtrl", topic="認証"),
                ],
            )
        )
        assert dto.relevant_areas[0].name == "認証"
        assert "ログインCtrl" in dto.relevant_areas[0].components

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


class TestEmptyComponentNames:
    """Components with empty names are silently skipped."""

    def test_empty_name_skipped(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[TopicProjection(name="Auth")],
                graph_components=[
                    ComponentProjection(name="", topic="Auth"),
                    ComponentProjection(name="Valid", topic="Auth"),
                ],
            )
        )
        auth_area = next(
            a for a in dto.relevant_areas if a.name == "Auth"
        )
        assert auth_area.components == ["Valid"]

    def test_all_empty_names(self):
        dto = map_to_dto(
            _minimal_input(
                graph_topics=[TopicProjection(name="Auth")],
                graph_components=[
                    ComponentProjection(name="", topic="Auth"),
                ],
            )
        )
        auth_area = next(
            a for a in dto.relevant_areas if a.name == "Auth"
        )
        assert auth_area.components == []


class TestLargeGraphs:
    """Large input sets process correctly without issues."""

    def test_many_topics_and_components(self):
        topics = [
            TopicProjection(name=f"Topic{i}") for i in range(100)
        ]
        components = [
            ComponentProjection(
                name=f"Comp{i}_{j}", topic=f"Topic{i}",
            )
            for i in range(100)
            for j in range(10)
        ]
        dto = map_to_dto(
            _minimal_input(
                graph_topics=topics,
                graph_components=components,
            )
        )
        assert len(dto.relevant_areas) == 100
        for area in dto.relevant_areas:
            assert len(area.components) == 10

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
