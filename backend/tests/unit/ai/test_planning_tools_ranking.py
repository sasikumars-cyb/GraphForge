"""Tests for app.agents.planning.tools's component ranking — specifically
the fix for production components being silently excluded from the
prompt in favor of their own test classes.

Regression anchor: a real Planning run named `TestSCDType2Merger`/
`TestExactDeduplicator` (test classes) as if they were the production
`SCDType2Merger`/`ExactDeduplicator` — never caught, because
`format_graph_context` only sorted components by relevance score when
`relevance_terms` produced a nonzero match, and a test component's score
(0 * the 0.3 test discount == 0) compared equal to a production
component's also-zero score, so the two fell back to arbitrary traversal
order. Combined with `_component_budget` capping a repository to as few
as 8 entries, a repository's test classes could fill the entire budget
while its production classes never reached the LLM at all.
"""

from __future__ import annotations

from app.agents.planning.tools import (
    PlanningObservation,
    _component_budget,
    _is_test_component,
    format_graph_context,
    rank_score,
)


def _component(name: str, repository: str, file_path: str, is_test: bool | None = None) -> dict:
    comp = {"name": name, "type": "Class", "repository": repository, "file_path": file_path}
    if is_test is not None:
        comp["is_test"] = is_test
    return comp


class TestIsTestComponent:
    def test_prefers_persisted_is_test_property(self):
        # Persisted property wins even if the path/name wouldn't otherwise
        # match the regex fallback — ground truth from indexing time.
        assert _is_test_component({"name": "Anything", "file_path": "src/x.py", "is_test": True})
        assert not _is_test_component(
            {"name": "TestLooking", "file_path": "tests/x.py", "is_test": False}
        )

    def test_falls_back_to_regex_when_property_absent(self):
        # Data indexed before this fix shipped has no `is_test` key at
        # all — must not silently treat that as "not test".
        assert _is_test_component({"name": "TestFoo", "file_path": "tests/unit/test_foo.py"})
        assert not _is_test_component({"name": "Foo", "file_path": "src/foo.py"})


class TestProductionPreferredWithNoRelevanceTerms:
    """The exact scenario that let the real bug through: no relevance
    terms score anything, so nothing but test-vs-production should decide
    which components reach the prompt."""

    def test_production_class_beats_its_own_test_class_with_no_terms(self):
        components = [
            _component("TestSCDType2Merger", "etl-core", "tests/unit/test_scd2.py", is_test=True),
            _component(
                "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py", is_test=False
            ),
        ]
        repos_obs = PlanningObservation(
            tool_name="get_indexed_repositories",
            summary="",
            data={"indexed_repositories": [{"id": "1", "name": "etl-core"}]},
        )
        traverse_obs = PlanningObservation(
            tool_name="traverse_architecture_graph",
            summary="",
            data={"components": components, "kafka_topics": []},
        )

        context = format_graph_context(repos_obs, traverse_obs, relevance_terms=[])

        # Both are within budget here, but production must be listed
        # first — the ordering IS the fix, not just presence.
        prod_pos = context.index("SCDType2Merger (Class)")
        test_pos = context.index("TestSCDType2Merger (Class)")
        assert prod_pos < test_pos

    def test_production_class_still_included_when_budget_would_otherwise_exclude_it(self):
        # A repository whose test classes outnumber its production budget
        # slots — before this fix, production code here would never reach
        # the prompt at all. Budget for `repo_component_count` components
        # is max(8, min(20, ceil(n/100))); use 30 test classes (budget 8)
        # plus one production class that must still make the cut.
        test_components = [
            _component(f"TestHelper{i}", "etl-core", f"tests/unit/test_helper_{i}.py", is_test=True)
            for i in range(30)
        ]
        prod_component = _component(
            "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py", is_test=False
        )
        components = [*test_components, prod_component]
        assert _component_budget(len(components)) == 8  # sanity-check the scenario is realistic

        repos_obs = PlanningObservation(
            tool_name="get_indexed_repositories",
            summary="",
            data={"indexed_repositories": [{"id": "1", "name": "etl-core"}]},
        )
        traverse_obs = PlanningObservation(
            tool_name="traverse_architecture_graph",
            summary="",
            data={"components": components, "kafka_topics": []},
        )

        context = format_graph_context(repos_obs, traverse_obs, relevance_terms=[])

        assert "SCDType2Merger (Class)" in context


class TestMixedRepositoriesSameComponentName:
    """The same bare name existing as a test class in one repository and
    a production class in a different, unrelated repository — ranking in
    one repository must not be influenced by the other's data."""

    def test_each_repository_ranks_its_own_components_independently(self):
        components = [
            _component("Merger", "repo-a", "tests/unit/test_merger.py", is_test=True),
            _component("Merger", "repo-b", "src/app/merger.py", is_test=False),
        ]
        repos_obs = PlanningObservation(
            tool_name="get_indexed_repositories",
            summary="",
            data={
                "indexed_repositories": [
                    {"id": "1", "name": "repo-a"},
                    {"id": "2", "name": "repo-b"},
                ]
            },
        )
        traverse_obs = PlanningObservation(
            tool_name="traverse_architecture_graph",
            summary="",
            data={"components": components, "kafka_topics": []},
        )

        context = format_graph_context(repos_obs, traverse_obs, relevance_terms=[])

        assert "repo-a: Merger (Class)" in context
        assert "repo-b: Merger (Class)" in context


class TestDuplicatedNamesAcrossRepositories:
    def test_duplicate_component_name_in_two_repos_both_listed_correctly(self):
        components = [
            _component("Validator", "repo-a", "src/validator.py", is_test=False),
            _component("Validator", "repo-b", "src/validator.py", is_test=False),
        ]
        repos_obs = PlanningObservation(
            tool_name="get_indexed_repositories",
            summary="",
            data={
                "indexed_repositories": [
                    {"id": "1", "name": "repo-a"},
                    {"id": "2", "name": "repo-b"},
                ]
            },
        )
        traverse_obs = PlanningObservation(
            tool_name="traverse_architecture_graph",
            summary="",
            data={"components": components, "kafka_topics": []},
        )

        context = format_graph_context(repos_obs, traverse_obs, relevance_terms=[])
        assert context.count("Validator (Class)") == 2


class TestSimilarlyNamedTestClasses:
    """`TestFoo` vs `TestFooBar` — a near-miss name must not confuse
    ranking or get treated as the same component."""

    def test_similarly_named_test_classes_ranked_independently(self):
        components = [
            _component("TestFoo", "repo-a", "tests/unit/test_foo.py", is_test=True),
            _component("TestFooBar", "repo-a", "tests/unit/test_foo_bar.py", is_test=True),
            _component("Foo", "repo-a", "src/foo.py", is_test=False),
        ]
        repos_obs = PlanningObservation(
            tool_name="get_indexed_repositories",
            summary="",
            data={"indexed_repositories": [{"id": "1", "name": "repo-a"}]},
        )
        traverse_obs = PlanningObservation(
            tool_name="traverse_architecture_graph",
            summary="",
            data={"components": components, "kafka_topics": []},
        )

        context = format_graph_context(repos_obs, traverse_obs, relevance_terms=[])

        prod_pos = context.index("Foo (Class)")
        test_foo_pos = context.index("TestFoo (Class)")
        test_foobar_pos = context.index("TestFooBar (Class)")
        assert prod_pos < test_foo_pos
        assert prod_pos < test_foobar_pos


class TestRankScoreDiscountStillAppliesWithRealTermOverlap:
    def test_test_component_discounted_relative_to_production_when_terms_match_both(self):
        prod = _component("PaymentValidator", "repo-a", "src/payment_validator.py", is_test=False)
        test = _component(
            "TestPaymentValidator", "repo-a", "tests/unit/test_payment_validator.py", is_test=True
        )
        terms = ["payment", "validator"]
        assert rank_score(prod, terms) > rank_score(test, terms)
