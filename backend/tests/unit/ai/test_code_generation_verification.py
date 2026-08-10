"""Deterministic test coverage (Part 8) for
app.agents.code_generation.verification — repository matching and file
matching, including the normalization-driven false-negative fixes.
"""

from __future__ import annotations

from app.agents.code_generation.verification import (
    _is_safe_destination,
    validate_file_operations,
)

_REPO = "demo-org/api-gateway"


# ---------------------------------------------------------------------------
# Repository matching (well-formedness — the deterministic gate every
# repository claim passes through before workflow-scope/tracked checks)
# ---------------------------------------------------------------------------


class TestRepositoryWellFormedness:
    def test_exact_owner_repo_format_is_well_formed(self):
        from app.agents.code_generation.verification import _is_well_formed

        assert _is_well_formed("demo-org/api-gateway") is True

    def test_missing_slash_is_not_well_formed(self):
        from app.agents.code_generation.verification import _is_well_formed

        assert _is_well_formed("demo-org-api-gateway") is False

    def test_empty_owner_or_repo_is_not_well_formed(self):
        from app.agents.code_generation.verification import _is_well_formed

        assert _is_well_formed("/api-gateway") is False
        assert _is_well_formed("demo-org/") is False

    def test_nested_slash_in_repo_segment_is_not_well_formed(self):
        from app.agents.code_generation.verification import _is_well_formed

        assert _is_well_formed("demo-org/api/gateway") is False

    def test_similar_but_different_repository_names_are_distinct(self):
        """A repository name that merely *resembles* the real one (e.g. a
        typo, or a sibling tenant's repo — see app.agents.verification's
        check_entity_mismatch for the full tenant-mismatch story) must
        never be treated as a match: well-formedness and scope checks
        operate on exact strings, not fuzzy similarity, by design (Part 9:
        avoid fuzzy matching)."""
        from app.agents.code_generation.verification import _collect_known_repositories

        known = _collect_known_repositories(
            workflow=None,
            source_workflow=_FakeWorkflow(
                {"repositories_consulted": ["demo-org/api-gateway"]}
            ),
        )
        assert "demo-org/api-gatewayy" not in known
        assert "demo-org/api-gateway" in known


class _FakeStep:
    def __init__(self, result):
        self.result = result


class _FakeRun:
    def __init__(self, stage, result):
        self.workflow_stage = stage
        self.status = "completed"
        self.steps = [_FakeStep(result)]
        import datetime

        self.created_at = datetime.datetime.now(datetime.UTC)


class _FakeWorkflow:
    def __init__(self, development_result):
        self.runs = [_FakeRun("development", development_result)]


# ---------------------------------------------------------------------------
# File matching
# ---------------------------------------------------------------------------


class TestFileOperationValidation:
    def test_nested_path_modify_matches_known_nested_file(self):
        known = {_REPO: {"src/main/java/com/example/deep/nested/Service.java"}}
        files = [
            {
                "path": "src/main/java/com/example/deep/nested/Service.java",
                "operation": "modify",
            }
        ]
        assert validate_file_operations(files, _REPO, known) == []

    def test_leading_dot_slash_does_not_cause_false_rejection(self):
        """The exact false negative this task fixes: known file paths
        recorded without a leading './' must still match a claim that
        includes one (or vice versa)."""
        known = {_REPO: {"./src/main/Service.java"}}
        files = [{"path": "src/main/Service.java", "operation": "modify"}]
        assert validate_file_operations(files, _REPO, known) == []

    def test_backslash_path_does_not_cause_false_rejection(self):
        known = {_REPO: {"src/main/Service.java"}}
        files = [{"path": "src\\main\\Service.java", "operation": "modify"}]
        assert validate_file_operations(files, _REPO, known) == []

    def test_renamed_file_not_in_known_set_is_rejected(self):
        """A 'modify' claim against a file that was renamed (the known set
        reflects the OLD name; the LLM claims to modify a name that never
        existed) must still be rejected — normalization fixes formatting
        false negatives, it must never mask an actual hallucinated path."""
        known = {_REPO: {"src/main/OldServiceName.java"}}
        files = [{"path": "src/main/NewServiceName.java", "operation": "modify"}]
        violations = validate_file_operations(files, _REPO, known)
        assert len(violations) == 1
        assert violations[0].path == "src/main/NewServiceName.java"

    def test_generated_file_create_is_not_checked_against_known_set(self):
        """'create' operations are only path-safety checked, never
        existence-checked against known paths — a brand-new generated file
        is expected not to be in the known set."""
        known = {_REPO: {"src/main/Existing.java"}}
        files = [{"path": "src/main/BrandNewGeneratedFile.java", "operation": "create"}]
        assert validate_file_operations(files, _REPO, known) == []

    def test_delete_of_unknown_file_is_rejected(self):
        known = {_REPO: {"src/main/Existing.java"}}
        files = [{"path": "src/main/DoesNotExist.java", "operation": "delete"}]
        violations = validate_file_operations(files, _REPO, known)
        assert len(violations) == 1
        assert violations[0].operation == "delete"

    def test_delete_of_known_file_passes(self):
        known = {_REPO: {"src/main/Existing.java"}}
        files = [{"path": "src/main/Existing.java", "operation": "delete"}]
        assert validate_file_operations(files, _REPO, known) == []

    def test_no_known_files_for_repository_rejects_modify(self):
        """ADR 0027 correction: an empty verified set for `repository` —
        whether because Development reported no components at all, or
        because every proposed component failed verification (e.g. all
        UNVERIFIED due to a repository/file mismatch) — must now reject
        modify/delete, not silently allow it. The pre-ADR-0027 behavior
        (skip the existence check when `known` is empty) was a real gap:
        once `known_file_paths` only contains VERIFIED entries, an empty
        set is exactly the case Invariant 1 requires to fail closed."""
        files = [{"path": "src/main/Anything.java", "operation": "modify"}]
        violations = validate_file_operations(files, _REPO, {})
        assert len(violations) == 1
        assert violations[0].operation == "modify"

    def test_case_sensitivity_is_preserved_for_file_paths(self):
        """Unlike claim-text matching, file path case is a real
        correctness distinction on case-sensitive filesystems — squashing
        case here would be a false POSITIVE risk, not a fix."""
        known = {_REPO: {"src/main/Service.java"}}
        files = [{"path": "src/main/service.java", "operation": "modify"}]
        violations = validate_file_operations(files, _REPO, known)
        assert len(violations) == 1


class TestCollectKnownFilePathsVerificationFiltering:
    """ADR 0027 — `_collect_known_file_paths` must only include components
    whose `file_path_verification` is exactly "verified"."""

    def test_only_verified_components_are_included(self):
        from app.agents.code_generation.verification import _collect_known_file_paths

        development_result = {
            "components": [
                {
                    "repository": _REPO,
                    "file_path": "src/Verified.java",
                    "file_path_verification": "verified",
                },
                {
                    "repository": _REPO,
                    "file_path": "src/Unverified.java",
                    "file_path_verification": "unverified",
                },
                {
                    "repository": _REPO,
                    "file_path": "src/NotChecked.java",
                    "file_path_verification": "not_checked",
                },
            ]
        }
        known = _collect_known_file_paths(
            workflow=None, source_workflow=_FakeWorkflow(development_result)
        )
        assert known[_REPO] == {"src/Verified.java"}

    def test_legacy_result_missing_the_field_is_excluded(self):
        """Case 10 — a Development result persisted before this field
        existed must fail closed, never be silently treated as verified."""
        from app.agents.code_generation.verification import _collect_known_file_paths

        development_result = {
            "components": [{"repository": _REPO, "file_path": "src/Legacy.java"}]
        }
        known = _collect_known_file_paths(
            workflow=None, source_workflow=_FakeWorkflow(development_result)
        )
        assert known.get(_REPO, set()) == set()

    def test_wrong_repository_component_excluded_even_though_file_exists_under_another_repo(self):
        """Case 2, full outcome at the Code Generation boundary — a
        component the Development stage itself marked UNVERIFIED for a
        cross-repository mismatch must never appear as known for either
        repository."""
        from app.agents.code_generation.verification import _collect_known_file_paths

        other_repo = "demo-org/other-repo"
        development_result = {
            "components": [
                {
                    "repository": other_repo,
                    "file_path": "src/Shared.java",
                    "file_path_verification": "verified",
                },
                {
                    "repository": _REPO,
                    "file_path": "src/Shared.java",
                    "file_path_verification": "unverified",
                },
            ]
        }
        known = _collect_known_file_paths(
            workflow=None, source_workflow=_FakeWorkflow(development_result)
        )
        assert known[other_repo] == {"src/Shared.java"}
        assert known.get(_REPO, set()) == set()


class TestModifyDeleteCannotBypassVerificationViaEmptyKnownSet:
    """The ADR 0027 correction to validate_file_operations: an empty
    verified set for `repository` must reject modify/delete, never fall
    through to path-safety-only, regardless of *why* it's empty."""

    def test_modify_rejected_when_repository_had_zero_reported_components(self):
        files = [{"path": "src/Anything.java", "operation": "modify"}]
        violations = validate_file_operations(files, _REPO, {})
        assert len(violations) == 1

    def test_modify_rejected_when_repository_had_components_but_none_verified(self):
        """The specific scenario the correction targets: `known` is empty
        not because nothing was reported, but because everything reported
        for this repository failed verification."""
        files = [{"path": "src/Anything.java", "operation": "modify"}]
        # An empty set specifically for _REPO, as _collect_known_file_paths
        # would now produce when every component for it was UNVERIFIED.
        violations = validate_file_operations(files, _REPO, {_REPO: set()})
        assert len(violations) == 1

    def test_create_is_never_affected_by_an_empty_known_set(self):
        """Invariant 2 — create must remain unaffected regardless of
        why/whether the verified set is empty."""
        files = [{"path": "src/BrandNewFile.java", "operation": "create"}]
        assert validate_file_operations(files, _REPO, {}) == []
        assert validate_file_operations(files, _REPO, {_REPO: set()}) == []


class TestAntiLaunderingAtCodeGenerationBoundary:
    """Case 19 — Code Generation cannot regain trust for an UNVERIFIED
    Development component merely by proposing the same
    (repository, file_path) pair itself."""

    def test_code_generation_cannot_launder_an_unverified_path_by_repeating_it(self):
        from app.agents.code_generation.verification import _collect_known_file_paths

        development_result = {
            "components": [
                {
                    "repository": _REPO,
                    "file_path": "src/Hallucinated.java",
                    "file_path_verification": "unverified",
                }
            ]
        }
        known = _collect_known_file_paths(
            workflow=None, source_workflow=_FakeWorkflow(development_result)
        )
        # Code Generation's own LLM output proposes a modify at the exact
        # same path Development already marked UNVERIFIED — this must
        # still be rejected; nothing in Code Generation can promote it.
        files = [{"path": "src/Hallucinated.java", "operation": "modify"}]
        violations = validate_file_operations(files, _REPO, known)
        assert len(violations) == 1

    def test_verified_component_stays_verified_and_usable(self):
        """Positive control (case 20) — a genuinely VERIFIED component's
        status survives unchanged through to the write gate."""
        from app.agents.code_generation.verification import _collect_known_file_paths

        development_result = {
            "components": [
                {
                    "repository": _REPO,
                    "file_path": "src/Real.java",
                    "file_path_verification": "verified",
                }
            ]
        }
        known = _collect_known_file_paths(
            workflow=None, source_workflow=_FakeWorkflow(development_result)
        )
        files = [{"path": "src/Real.java", "operation": "modify"}]
        assert validate_file_operations(files, _REPO, known) == []


class TestSafeDestination:
    def test_safe_relative_path(self):
        assert _is_safe_destination("src/main/Foo.java") is True

    def test_rejects_absolute_path(self):
        assert _is_safe_destination("/etc/passwd") is False

    def test_rejects_home_relative_path(self):
        assert _is_safe_destination("~/secrets.txt") is False

    def test_rejects_parent_traversal(self):
        assert _is_safe_destination("../../etc/passwd") is False

    def test_rejects_parent_traversal_disguised_with_leading_dot_slash(self):
        assert _is_safe_destination("./../etc/passwd") is False

    def test_allows_nested_safe_path(self):
        assert _is_safe_destination("a/b/c/d/e.py") is True
