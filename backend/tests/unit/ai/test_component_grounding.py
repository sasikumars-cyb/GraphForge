"""Tests for app.agents.component_grounding — the shared test-vs-
production check called independently by Planning, Development, Testing,
and Documentation Planning.

Six required regression scenarios (per the redesign brief):
  1. test class confusion
  2. production class selection
  3. mixed repositories
  4. duplicated names
  5. similarly named test classes
  6. task genuinely about tests (the check must not fire)
"""

from __future__ import annotations

from app.agents.component_grounding import (
    ComponentWarning,
    check_test_used_as_production,
    is_task_test_related,
)


def _comp(name: str, repository: str, file_path: str, is_test: bool) -> dict:
    return {"name": name, "repository": repository, "file_path": file_path, "is_test": is_test}


NPT_29_TASK = (
    "SCD2 merge produces duplicate current records when source contains "
    "duplicate keys from Kafka redelivery or when multiple jobs target "
    "the same partition simultaneously."
)


class TestTestClassConfusion:
    """The exact real-world scenario: TestSCDType2Merger/TestExactDeduplicator
    named as if they were the production implementation."""

    def test_rejects_and_replaces_test_class_with_production_sibling(self):
        components = [
            _comp("TestSCDType2Merger", "etl-core", "tests/unit/test_scd2.py", is_test=True),
            _comp("SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py", is_test=False),
            _comp("TestExactDeduplicator", "etl-core", "tests/unit/test_dedup.py", is_test=True),
            _comp(
                "ExactDeduplicator", "etl-core", "src/etl_core/dedup/exact_dedup.py", is_test=False
            ),
        ]
        claims = ["TestSCDType2Merger", "TestExactDeduplicator"]

        corrected, warnings = check_test_used_as_production(claims, components, NPT_29_TASK)

        assert corrected == ["SCDType2Merger", "ExactDeduplicator"]
        assert len(warnings) == 2
        assert all(w.warning_type == "test_used_as_production" for w in warnings)
        assert {w.suggested_replacement for w in warnings} == {
            "SCDType2Merger",
            "ExactDeduplicator",
        }

    def test_rejects_with_no_replacement_when_no_production_sibling_indexed(self):
        components = [
            _comp("TestOrphanHelper", "etl-core", "tests/unit/test_orphan.py", is_test=True),
        ]
        corrected, warnings = check_test_used_as_production(
            ["TestOrphanHelper"], components, NPT_29_TASK
        )

        assert corrected == []
        assert len(warnings) == 1
        assert warnings[0].suggested_replacement is None
        assert "removed" in warnings[0].message


class TestProductionClassSelection:
    """A claim that already names the real production class must pass
    through untouched — this check only acts on test-only claims."""

    def test_production_claim_untouched(self):
        components = [
            _comp("SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py", is_test=False),
            _comp("TestSCDType2Merger", "etl-core", "tests/unit/test_scd2.py", is_test=True),
        ]
        corrected, warnings = check_test_used_as_production(
            ["SCDType2Merger"], components, NPT_29_TASK
        )
        assert corrected == ["SCDType2Merger"]
        assert warnings == []

    def test_unindexed_claim_passed_through_for_verify_claims_to_handle(self):
        # A claim naming nothing this run indexed at all — that's
        # verify_claims's job to flag as unverified, not this function's.
        corrected, warnings = check_test_used_as_production(
            ["TotallyFabricatedClass"], components=[], task_text=NPT_29_TASK
        )
        assert corrected == ["TotallyFabricatedClass"]
        assert warnings == []


class TestMixedRepositories:
    """The same bare name existing as a production class in one repo and
    a test class in a different, unrelated repo — must not cross-
    contaminate the verdict for either."""

    def test_name_shared_across_repos_one_test_one_production(self):
        components = [
            _comp("Merger", "repo-a", "tests/unit/test_merger.py", is_test=True),
            _comp("Merger", "repo-b", "src/app/merger.py", is_test=False),
        ]
        # Because at least one component named "Merger" anywhere in this
        # evidence pool is production code, the claim passes — this
        # function only rejects when EVERY match is test-classified.
        corrected, warnings = check_test_used_as_production(["Merger"], components, NPT_29_TASK)
        assert corrected == ["Merger"]
        assert warnings == []


class TestDuplicatedNames:
    """The identical (name, is_test) pair appearing twice — e.g. indexed
    once per traversal path — must not produce duplicate warnings or
    corrections."""

    def test_duplicate_test_only_entries_produce_one_warning(self):
        components = [
            _comp("TestFoo", "repo-a", "tests/unit/test_foo.py", is_test=True),
            _comp("TestFoo", "repo-a", "tests/unit/test_foo.py", is_test=True),  # duplicate record
        ]
        corrected, warnings = check_test_used_as_production(["TestFoo"], components, NPT_29_TASK)
        # One claim in, at most one outcome for it — this function
        # iterates claims, not components, so duplicate component records
        # can't fan out into duplicate warnings for a single claim.
        assert len(warnings) == 1
        assert corrected == []


class TestSimilarlyNamedTestClasses:
    """`TestFoo` vs `TestFooBar` must resolve independently — a near-miss
    name must never be treated as the same claim."""

    def test_similarly_named_test_classes_resolved_independently(self):
        components = [
            _comp("TestFoo", "repo-a", "tests/unit/test_foo.py", is_test=True),
            _comp("Foo", "repo-a", "src/foo.py", is_test=False),
            _comp("TestFooBar", "repo-a", "tests/unit/test_foo_bar.py", is_test=True),
            # No production "FooBar" indexed — this one has no sibling.
        ]
        corrected, warnings = check_test_used_as_production(
            ["TestFoo", "TestFooBar"], components, NPT_29_TASK
        )
        assert corrected == ["Foo"]
        assert len(warnings) == 2
        by_claim = {w.claim: w for w in warnings}
        assert by_claim["TestFoo"].suggested_replacement == "Foo"
        assert by_claim["TestFooBar"].suggested_replacement is None


class TestTaskGenuinelyAboutTests:
    def test_check_is_exempted_when_task_is_about_writing_tests(self):
        components = [
            _comp("TestSCDType2Merger", "etl-core", "tests/unit/test_scd2.py", is_test=True),
        ]
        task = "Add regression tests for the SCD2 merge to cover concurrent writes."
        corrected, warnings = check_test_used_as_production(
            ["TestSCDType2Merger"], components, task
        )
        assert corrected == ["TestSCDType2Merger"]
        assert warnings == []


class TestIsTaskTestRelated:
    def test_bug_report_mentioning_test_once_is_not_test_related(self):
        assert not is_task_test_related(
            "SCD2 merge produces duplicates; no test covers this concurrent case."
        )

    def test_add_tests_phrasing_is_test_related(self):
        assert is_task_test_related("Add unit tests for the deduplication transformer.")

    def test_fix_flaky_test_phrasing_is_test_related(self):
        assert is_task_test_related("Fix flaky tests in the CI pipeline for etl-core.")

    def test_test_coverage_phrasing_is_test_related(self):
        assert is_task_test_related("Improve test coverage for the SCD2 merge module.")

    def test_empty_text_is_not_test_related(self):
        assert not is_task_test_related("")


def test_component_warning_is_a_plain_frozen_dataclass():
    warning = ComponentWarning(
        claim="TestFoo", warning_type="test_used_as_production", message="msg"
    )
    assert warning.suggested_replacement is None
