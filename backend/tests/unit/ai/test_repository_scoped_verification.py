"""ADR 0027 — Development Component Verification Enforcement.

Deterministic, no-LLM coverage of the new repository-scoped file-pair
verification primitives (`app.agents.verification`), independent of the
existing `verify_claims` mechanism, which stays covered by its own tests
unmodified. Test names reference the ADR's adversarial case numbers
(§7) where a direct mapping exists.
"""

from __future__ import annotations

from app.agents.verification import (
    BLOCKING_CATEGORIES,
    NON_BLOCKING_CATEGORIES,
    NOT_CHECKED,
    UNVERIFIED,
    VERIFIED,
    build_repository_scoped_evidence,
    file_path_exists_in_any_repository,
    verify_file_path_pair,
)


class TestBuildRepositoryScopedEvidence:
    def test_partitions_by_repository(self):
        components = [
            {"repository": "repo-a", "file_path": "src/x.py"},
            {"repository": "repo-b", "file_path": "src/y.py"},
        ]
        evidence = build_repository_scoped_evidence(components)
        assert evidence == {"repo-a": {"src/x.py"}, "repo-b": {"src/y.py"}}

    def test_same_file_in_two_repositories_stays_separate(self):
        """Repository A contains X, repository B also contains X — each
        must be tracked independently, never merged into one shared set
        (ADR 0027 §4.3, Invariant E)."""
        components = [
            {"repository": "repo-a", "file_path": "src/x.py"},
            {"repository": "repo-b", "file_path": "src/x.py"},
        ]
        evidence = build_repository_scoped_evidence(components)
        assert evidence["repo-a"] == {"src/x.py"}
        assert evidence["repo-b"] == {"src/x.py"}
        assert evidence["repo-a"] is not evidence["repo-b"]

    def test_normalizes_paths(self):
        components = [{"repository": "repo-a", "file_path": "./src/x.py"}]
        evidence = build_repository_scoped_evidence(components)
        assert "src/x.py" in evidence["repo-a"]

    def test_skips_entries_missing_repository_or_file_path(self):
        components = [
            {"repository": "", "file_path": "src/x.py"},
            {"repository": "repo-a", "file_path": ""},
            {"file_path": "src/x.py"},
            {"repository": "repo-a"},
        ]
        assert build_repository_scoped_evidence(components) == {}

    def test_empty_input_yields_empty_evidence(self):
        assert build_repository_scoped_evidence([]) == {}


class TestVerifyFilePathPair:
    """Case numbers reference ADR 0027 §7."""

    def test_no_evidence_pool_at_all_is_not_checked(self):
        """No repository-scoped evidence existed to check against at all
        — case 9."""
        assert verify_file_path_pair("repo-a", "src/x.py", {}) == NOT_CHECKED

    def test_correct_pair_is_verified(self):
        """Case 21 — repository A contains X; component correctly claims
        repository A + X."""
        evidence = {"repo-a": {"src/x.py"}}
        assert verify_file_path_pair("repo-a", "src/x.py", evidence) == VERIFIED

    def test_real_file_wrong_repository_is_unverified_not_verified(self):
        """Case 2 — repository A contains X; component claims repository
        B + X. Must never verify merely because X exists somewhere."""
        evidence = {"repo-a": {"src/x.py"}}
        assert verify_file_path_pair("repo-b", "src/x.py", evidence) == UNVERIFIED

    def test_path_exists_nowhere_is_unverified(self):
        """Case 1 / case 6 / case 22 — evidence pool existed and was
        checked; the path simply wasn't in it, regardless of the reason
        (hallucination or a legitimate new-file proposal)."""
        evidence = {"repo-a": {"src/other.py"}}
        assert verify_file_path_pair("repo-a", "src/x.py", evidence) == UNVERIFIED

    def test_directory_shaped_path_is_unverified(self):
        """Case 5 — no file-shaped evidence entry can ever match a bare
        directory path."""
        evidence = {"repo-a": {"src/x.py"}}
        assert verify_file_path_pair("repo-a", "src", evidence) == UNVERIFIED

    def test_and_shaped_reimplementation_regression(self):
        """Invariant E — repository A is genuinely in scope (present as a
        key) but X exists only under repository B. A naive
        'repository in scope AND file_path in <global pool>' reimplementation
        would incorrectly verify this; the real joint lookup must not."""
        evidence = {"repo-a": {"src/other.py"}, "repo-b": {"src/x.py"}}
        assert verify_file_path_pair("repo-a", "src/x.py", evidence) == UNVERIFIED

    def test_repo_a_and_repo_b_both_contain_x_verify_independently(self):
        """Case 15's positive control — each component must be verified
        strictly against its own claimed repository's evidence, never a
        merged pool."""
        evidence = {"repo-a": {"src/x.py"}, "repo-b": {"src/x.py"}}
        assert verify_file_path_pair("repo-a", "src/x.py", evidence) == VERIFIED
        assert verify_file_path_pair("repo-b", "src/x.py", evidence) == VERIFIED
        assert verify_file_path_pair("repo-c", "src/x.py", evidence) == UNVERIFIED

    def test_empty_repository_or_file_path_is_unverified_when_evidence_exists(self):
        evidence = {"repo-a": {"src/x.py"}}
        assert verify_file_path_pair("", "src/x.py", evidence) == UNVERIFIED
        assert verify_file_path_pair("repo-a", "", evidence) == UNVERIFIED

    def test_no_fuzzy_bare_filename_matching(self):
        """ADR 0027 explicitly excludes 'filename alone' as sufficient —
        unlike verify_claims's deliberately more tolerant matching, a bare
        filename must NOT match a full path here."""
        evidence = {"repo-a": {"src/main/config.py"}}
        assert verify_file_path_pair("repo-a", "config.py", evidence) == UNVERIFIED

    def test_llm_asserting_verified_has_no_effect(self):
        """Case 16 — nothing about this function's signature or behavior
        can be influenced by an LLM claiming 'verified': the function
        never reads any such field; only the two positional args + the
        real evidence structure matter."""
        evidence = {"repo-a": {"src/x.py"}}
        # Simulate an LLM-influenced caller trying to pass a "verified"
        # claim through an unrelated channel — there is no parameter for
        # this function to accept it through in the first place.
        assert verify_file_path_pair("repo-b", "src/x.py", evidence) == UNVERIFIED


class TestFilePathExistsInAnyRepository:
    def test_exists_under_a_different_repository(self):
        evidence = {"repo-a": {"src/x.py"}}
        assert file_path_exists_in_any_repository("src/x.py", evidence) is True

    def test_does_not_exist_anywhere(self):
        evidence = {"repo-a": {"src/other.py"}}
        assert file_path_exists_in_any_repository("src/x.py", evidence) is False

    def test_empty_file_path_is_false(self):
        assert file_path_exists_in_any_repository("", {"repo-a": {"src/x.py"}}) is False

    def test_empty_evidence_is_false(self):
        assert file_path_exists_in_any_repository("src/x.py", {}) is False


class TestBlockingCategoryClassification:
    def test_component_repository_mismatch_is_blocking(self):
        assert "component_repository_mismatch" in BLOCKING_CATEGORIES
        assert "component_repository_mismatch" not in NON_BLOCKING_CATEGORIES

    def test_component_repository_mismatch_blocks_by_default_rule(self):
        """The runtime blocking decision is `category not in
        NON_BLOCKING_CATEGORIES` (see app.agents.verification's own
        module docstring) — confirm the new category actually satisfies
        that rule, not just that it's listed for documentation."""
        from app.agents.verification import VerificationFinding

        finding = VerificationFinding(
            message="mismatch", category="component_repository_mismatch"
        )
        assert finding.blocking is True

    def test_mutual_exclusivity_is_a_property_of_the_caller_not_this_module(self):
        """This module has no mechanism preventing both categories from
        being assigned to the same claim — mutual exclusivity is a
        property of how development/agent.py calls these functions (only
        emits component_repository_mismatch when
        file_path_exists_in_any_repository is True, which is exactly the
        condition under which the old verify_claims-based
        component_not_found check would NOT have fired). Documented here
        so the invariant's actual location is not mistaken for living in
        this module."""
        assert True
