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

    def test_no_known_files_for_repository_skips_existence_check(self):
        """No ground truth at all for this repository (Development stage
        absent, or reported no components) — existence cannot be asserted
        either way, so only path-safety applies (documented limitation,
        not a silent pass — see validate_file_operations' docstring)."""
        files = [{"path": "src/main/Anything.java", "operation": "modify"}]
        assert validate_file_operations(files, _REPO, {}) == []

    def test_case_sensitivity_is_preserved_for_file_paths(self):
        """Unlike claim-text matching, file path case is a real
        correctness distinction on case-sensitive filesystems — squashing
        case here would be a false POSITIVE risk, not a fix."""
        known = {_REPO: {"src/main/Service.java"}}
        files = [{"path": "src/main/service.java", "operation": "modify"}]
        violations = validate_file_operations(files, _REPO, known)
        assert len(violations) == 1


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
