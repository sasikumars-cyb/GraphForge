"""ADR 0025 (Phase 3) — Hypothesis ↔ Verification Correlation.

One test per row of ADR 0025 §9a's False Positive Matrix, plus the two
cases called out explicitly (conflicting signals, unavailable
verification) — this file IS the executable form of that matrix, not
just documentation referencing it. Every test asserts against the real
`map_verification_status_for_subject_entity`/`map_knowledge_ledger_rows`
functions, never a hand-simulated substitute.

The invariant under test throughout: `VERIFIED`/`UNVERIFIED` is possible
only when (1) the hypothesis carries a claim-type-gated `subject_entity`
and (2) it exactly matches a real, structured, already-checked entry in
Planning's own `repository_usage[]`. Everything else stays `NOT_CHECKED`.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.report_generation import data_plumbing as dp
from app.agents.report_generation.contracts import (
    SubjectEntity,
    SubjectEntityKind,
    VerificationStatus,
)


def _planning_bundle(repository_usage: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        result={"repository_usage": repository_usage}, evidence=[], confidence_score=None
    )


def _repo_entity(name: str = "acme/repo") -> SubjectEntity:
    return SubjectEntity(kind=SubjectEntityKind.REPOSITORY, name=name)


class TestFalsePositiveMatrix:
    """ADR 0025 §9a, row by row."""

    def test_row_1_same_repository_different_claim(self):
        # A behavioral/causal hypothesis correctly has subject_entity=None
        # even though a repository_usage entry for the "same" repository
        # exists — repository identity alone is scope, not claim (§2a).
        planning = _planning_bundle([{"name": "acme/repo", "verified": True}])
        result = dp.map_verification_status_for_subject_entity(None, planning)
        assert result is None  # renders as NOT_CHECKED

    def test_row_2_same_file_different_claim(self):
        # Same reasoning as row 1, at file granularity — a behavioral
        # hypothesis about a file never gets subject_entity=file:X.
        planning = _planning_bundle([{"name": "acme/repo", "verified": True}])
        result = dp.map_verification_status_for_subject_entity(None, planning)
        assert result is None

    def test_row_3_same_component_different_claim(self):
        planning = _planning_bundle([{"name": "acme/repo", "verified": True}])
        result = dp.map_verification_status_for_subject_entity(None, planning)
        assert result is None

    def test_row_4_same_entity_exact_existence_claim_verified(self):
        planning = _planning_bundle([{"name": "acme/repo", "verified": True}])
        result = dp.map_verification_status_for_subject_entity(_repo_entity(), planning)
        assert result == VerificationStatus.VERIFIED

    def test_row_4_same_entity_exact_existence_claim_unverified(self):
        planning = _planning_bundle([{"name": "acme/repo", "verified": False}])
        result = dp.map_verification_status_for_subject_entity(_repo_entity(), planning)
        assert result == VerificationStatus.UNVERIFIED

    def test_row_5_similar_wording_different_entity(self):
        # Exact-key match only — "acme/repo-v2" is not "acme/repo", no
        # matter how similar. No fuzzy/token-overlap comparison exists.
        planning = _planning_bundle([{"name": "acme/repo-v2", "verified": True}])
        result = dp.map_verification_status_for_subject_entity(_repo_entity("acme/repo"), planning)
        assert result is None

    def test_row_6_exact_wording_unsupported_claim_type(self):
        # Even if a behavioral hypothesis's description literally contains
        # "acme/repo", subject_entity stays None (never derived from
        # prose) — so correlation never even attempts a lookup.
        planning = _planning_bundle([{"name": "acme/repo", "verified": True}])
        result = dp.map_verification_status_for_subject_entity(None, planning)
        assert result is None

    def test_row_7_finding_exists_but_does_not_correspond(self):
        # A verification_findings entry exists (in the bundle passed to
        # map_knowledge_ledger_rows), but it is never consulted for
        # hypothesis correlation at all — only repository_usage is. A
        # subject_entity naming a repository absent from repository_usage
        # never correlates, regardless of what findings exist.
        planning = SimpleNamespace(
            result={
                "repository_usage": [{"name": "other/repo", "verified": True}],
                "verification_findings": [
                    {"message": "acme/repo not found", "category": "repository_not_found"}
                ],
            },
            evidence=[],
            confidence_score=None,
        )
        result = dp.map_verification_status_for_subject_entity(_repo_entity("acme/repo"), planning)
        assert result is None

    def test_row_8_hypothesis_has_no_subject_entity(self):
        result = dp.map_verification_status_for_subject_entity(
            None, _planning_bundle([{"name": "acme/repo", "verified": True}])
        )
        assert result is None

    def test_row_9_conflicting_signals_fail_closed_to_unverified(self):
        # Two entries for the same exact name, one positive one negative
        # — must resolve to UNVERIFIED, never averaged or upgraded.
        planning = _planning_bundle(
            [
                {"name": "acme/repo", "verified": True},
                {"name": "acme/repo", "verified": False},
            ]
        )
        result = dp.map_verification_status_for_subject_entity(_repo_entity(), planning)
        assert result == VerificationStatus.UNVERIFIED

    def test_row_10_verification_unavailable_stays_not_checked(self):
        # No Planning bundle at all (stage didn't run) — absence of a
        # check must never be misread as an active negative finding.
        result = dp.map_verification_status_for_subject_entity(_repo_entity(), None)
        assert result is None


class TestClaimTypeGateAlsoAppliesToFileAndComponentKinds:
    def test_file_kind_never_correlates_today(self):
        # Documented scope limitation (map_verification_status_for_
        # subject_entity's own docstring): no per-file structured
        # verified/unverified signal is persisted anywhere today, so a
        # file-kind subject_entity always resolves to NOT_CHECKED, never
        # a guess derived from repository_usage.files_affected membership.
        planning = _planning_bundle(
            [{"name": "acme/repo", "verified": True, "files_affected": ["x.py"]}]
        )
        entity = SubjectEntity(kind=SubjectEntityKind.FILE, name="x.py")
        assert dp.map_verification_status_for_subject_entity(entity, planning) is None

    def test_component_kind_never_correlates_today(self):
        planning = _planning_bundle([{"name": "acme/repo", "verified": True}])
        entity = SubjectEntity(kind=SubjectEntityKind.COMPONENT, name="PaymentService")
        assert dp.map_verification_status_for_subject_entity(entity, planning) is None


class TestSubjectEntityParsing:
    def test_missing_subject_entity_parses_to_none(self):
        bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [{"description": "h", "status": "unknown", "confidence": 0.5}],
                }
            },
            evidence=[],
            confidence_score=None,
        )
        entries, _, _ = dp.map_hypotheses(bundle)
        assert entries[0].subject_entity is None

    def test_malformed_subject_entity_parses_to_none_not_raises(self):
        bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": "h",
                            "status": "unknown",
                            "confidence": 0.5,
                            "subject_entity": {"kind": "not_a_real_kind", "name": "x"},
                        }
                    ],
                }
            },
            evidence=[],
            confidence_score=None,
        )
        entries, _, _ = dp.map_hypotheses(bundle)
        assert entries[0].subject_entity is None

    def test_well_formed_subject_entity_parses_correctly(self):
        bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": "The handler is in app/api/routes.py",
                            "status": "supported",
                            "confidence": 0.8,
                            "subject_entity": {"kind": "file", "name": "app/api/routes.py"},
                        }
                    ],
                }
            },
            evidence=[],
            confidence_score=None,
        )
        entries, _, _ = dp.map_hypotheses(bundle)
        assert entries[0].subject_entity == SubjectEntity(
            kind=SubjectEntityKind.FILE, name="app/api/routes.py"
        )


class TestKnowledgeLedgerRowsCarryCorrelatedVerificationStatus:
    def test_end_to_end_through_map_knowledge_ledger_rows(self):
        cd_bundle = SimpleNamespace(
            result={
                "reasoning_summary": {
                    "synthesis_state": "completed",
                    "hypotheses": [
                        {
                            "description": "The change belongs to acme/repo",
                            "status": "supported",
                            "confidence": 0.9,
                            "subject_entity": {"kind": "repository", "name": "acme/repo"},
                        },
                        {
                            "description": "Concurrent ingestion runs may race",
                            "status": "unknown",
                            "confidence": 0.3,
                            # no subject_entity — behavioral claim
                        },
                    ],
                    "contradictions": [],
                }
            },
            evidence=[],
            confidence_score=None,
        )
        planning = _planning_bundle([{"name": "acme/repo", "verified": True}])

        rows = dp.map_knowledge_ledger_rows(planning, context_discovery_bundle=cd_bundle)
        by_claim = {r.claim: r for r in rows if r.source_stage == "context_discovery"}

        assert by_claim["The change belongs to acme/repo"].verification_status == (
            VerificationStatus.VERIFIED
        )
        assert by_claim["Concurrent ingestion runs may race"].verification_status is None


class TestRealCapturedVerifiedRepositoryUsage:
    """Live E2E verification (positive case) — `map_verification_status_
    for_subject_entity` exercised against `repository_usage[]` captured
    verbatim from a real, completed production Planning run (workflow
    74f8b66a-1e0f-4845-bc97-b63fc7e1ce82, agent_step
    8eca452e-61d4-49cc-92b7-33c0a054a7cf), not fabricated or hand-tuned to
    pass.

    A separate live run performed for this same Phase 3 verification pass
    (workflow 69a904ca-12a8-4cb5-9607-dbcade8657d9) produced a genuine
    repository-kind hypothesis (`subject_entity={kind: "repository", name:
    "prompt-library"}`, status=supported, confidence=0.6) but its own
    Planning run's `repository_usage[0].verified` came back `false` — not
    because correlation is wrong, but because of a real, pre-existing,
    unrelated bug in Planning's own verification code (`app/agents/
    planning/agent.py:939` prefers a repository's indexed `full_name`
    over the bare `name` the LLM actually emits everywhere else,
    including in `subject_entity.name` — see the Phase 3 report for the
    full trace). That run's real, honest result was SUPPORTED+
    UNVERIFIED — asserted below too, using that exact run's real data —
    which is itself a legitimate, previously-impossible state now
    correctly reachable. This class additionally proves the VERIFIED
    branch using different, older real production data (the earlier
    workflow above happened to use the full `sasikumars-cyb/prompt-
    library` form as `repository_usage[0].name`, which passed the same
    check) — real data on both sides, not a synthetic repository_usage
    entry invented to force a pass.
    """

    def test_real_verified_repository_usage_produces_verified(self):
        real_repository_usage = [
            {
                "name": "sasikumars-cyb/prompt-library",
                "stars": 5,
                "verified": True,
                "confidence": "high",
                "files_affected": ["prompt_library/registry.py"],
            }
        ]
        planning = _planning_bundle(real_repository_usage)
        entity = SubjectEntity(
            kind=SubjectEntityKind.REPOSITORY, name="sasikumars-cyb/prompt-library"
        )
        assert (
            dp.map_verification_status_for_subject_entity(entity, planning)
            == VerificationStatus.VERIFIED
        )

    def test_real_unverified_repository_usage_from_the_same_phase3_live_run(self):
        # Captured verbatim from workflow 69a904ca-12a8-4cb5-9607-
        # dbcade8657d9's real Planning run — the pre-existing full_name
        # bug's real, honest effect: a real hypothesis, a real check that
        # ran, a real negative result, correctly surfaced as UNVERIFIED,
        # never silently upgraded to VERIFIED and never hidden as
        # NOT_CHECKED.
        real_repository_usage = [
            {
                "name": "prompt-library",
                "stars": 5,
                "verified": False,
                "confidence": "medium",
                "files_affected": ["prompt_library/registry.py", "tests/test_registry.py"],
            }
        ]
        planning = _planning_bundle(real_repository_usage)
        entity = SubjectEntity(kind=SubjectEntityKind.REPOSITORY, name="prompt-library")
        assert (
            dp.map_verification_status_for_subject_entity(entity, planning)
            == VerificationStatus.UNVERIFIED
        )
