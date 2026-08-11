"""Tests for app.context_pipeline.reasoning.curation — the projection
that replaces a raw, unranked `graph_components` dump with a bounded,
tiered, explainable `EvidencePackage`.

Regression anchor: a real Planning run named `TestSCDType2Merger`/
`TestExactDeduplicator` (test classes) as if they were the production
`SCDType2Merger`/`ExactDeduplicator` they test, because nothing ever
ranked or tiered the 238 raw components Context Discovery returned.
`curate()` must place the production classes in `must_modify` and never
place their test classes in any of the three production tiers.
"""

from __future__ import annotations

from app.context_pipeline.reasoning.curation import (
    TIER_BUDGETS,
    curate,
    render_evidence_package_text,
    select_anchor_ids,
)


def _component(
    node_id: str,
    name: str,
    repository: str,
    file_path: str,
    *,
    is_test: bool = False,
    symbol_type: str = "class",
) -> dict:
    return {
        "id": node_id,
        "name": name,
        "type": "Class",
        "repository": repository,
        "file_path": file_path,
        "is_test": is_test,
        "confidence": 1.0 if is_test else 0.95,
        "symbol_type": symbol_type,
        "component_type": "Class",
    }


def _neighbor(node_id: str, hop_distance: int) -> dict:
    return {"id": node_id, "hop_distance": hop_distance}


NPT_29_TEXT = (
    "SCD2 merge produces duplicate current records when source contains "
    "duplicate keys from Kafka redelivery or when multiple jobs target "
    "the same partition simultaneously. Repo: etl-core."
)


class TestNPT29Regression:
    def test_production_class_lands_in_must_modify(self):
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
            _component(
                "c2",
                "TestSCDType2Merger",
                "etl-core",
                "tests/unit/test_scd2.py",
                is_test=True,
            ),
        ]
        # SCDType2Merger is the anchor (hop_distance 0 — it's the seed the
        # neighborhood fetch was scoped from); its test class is far
        # enough away (different module family) that it fell outside the
        # bounded neighborhood entirely — absent from neighborhood_nodes.
        neighborhood = [_neighbor("c1", 0)]

        package = curate(
            components=components,
            neighborhood_nodes=neighborhood,
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )

        must_modify_names = {item.name for item in package.by_tier("must_modify")}
        assert "SCDType2Merger" in must_modify_names
        assert "TestSCDType2Merger" not in must_modify_names
        # The test class must not appear in ANY production tier, however
        # it scored — this is a hard invariant, not a ranking outcome.
        assert "TestSCDType2Merger" not in {
            i.name
            for i in (
                *package.by_tier("must_modify"),
                *package.by_tier("architecture_dependency"),
                *package.by_tier("reusable_component"),
            )
        }

    def test_test_class_within_neighborhood_lands_in_relevant_test_not_must_modify(self):
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
            _component(
                "c2", "TestSCDType2Merger", "etl-core", "tests/unit/test_scd2.py", is_test=True
            ),
        ]
        # This time the test class IS within the bounded neighborhood
        # (e.g. a short import path connects them) — it must still never
        # reach a production tier, only relevant_test.
        neighborhood = [_neighbor("c1", 0), _neighbor("c2", 2)]

        package = curate(
            components=components,
            neighborhood_nodes=neighborhood,
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )

        test_tier_names = {item.name for item in package.by_tier("relevant_test")}
        assert "TestSCDType2Merger" in test_tier_names
        assert "TestSCDType2Merger" not in {i.name for i in package.by_tier("must_modify")}

    def test_reason_explains_why_anchor_was_selected(self):
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py")
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[_neighbor("c1", 0)],
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )
        item = package.by_tier("must_modify")[0]
        assert "directly in the request" in item.reason.lower()


class TestTiering:
    def test_one_hop_neighbor_lands_in_architecture_dependency(self):
        components = [
            _component("anchor", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
            _component(
                "neighbor", "DeltaTableManager", "etl-core", "src/etl_core/delta/table_manager.py"
            ),
        ]
        neighborhood = [_neighbor("anchor", 0), _neighbor("neighbor", 1)]

        package = curate(
            components=components,
            neighborhood_nodes=neighborhood,
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )

        arch_dep_names = {item.name for item in package.by_tier("architecture_dependency")}
        assert "DeltaTableManager" in arch_dep_names

    def test_unreachable_unrelated_component_is_excluded(self):
        components = [
            _component("anchor", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
            _component("unrelated", "SomeUnrelatedThing", "etl-core", "src/etl_core/misc/other.py"),
        ]
        # unrelated never appears in the neighborhood at all, and its
        # name/path share no tokens with the ticket text.
        neighborhood = [_neighbor("anchor", 0)]

        package = curate(
            components=components,
            neighborhood_nodes=neighborhood,
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )

        all_names = {item.name for item in package.items}
        assert "SomeUnrelatedThing" not in all_names
        assert package.excluded_count >= 1

    def test_reuse_shaped_name_lands_in_reusable_component(self):
        components = [
            _component("anchor", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
            _component("util", "BaseTransformer", "etl-core", "src/etl_core/transformers/base.py"),
        ]
        # Not in the neighborhood (no path within max_hops), but its
        # path/name is reuse-shaped ("base") and it's in the target repo
        # — should surface as a reusable component, not be silently lost.
        neighborhood = [_neighbor("anchor", 0)]

        package = curate(
            components=components,
            neighborhood_nodes=neighborhood,
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )

        reusable_names = {item.name for item in package.by_tier("reusable_component")}
        assert "BaseTransformer" in reusable_names

    def test_component_outside_target_repository_gets_no_ownership_bonus(self):
        components = [
            _component("anchor", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
            _component("other", "SomeClass", "other-repo", "src/other/some_class.py"),
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[_neighbor("anchor", 0)],
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )
        other_item = next((i for i in package.items if i.name == "SomeClass"), None)
        assert other_item is None or other_item.repository_bonus == 0.0


class TestBudgetEnforcement:
    def test_must_modify_never_exceeds_its_budget(self):
        components = [
            _component(f"c{i}", f"Anchor{i}", "etl-core", f"src/etl_core/a{i}.py")
            for i in range(TIER_BUDGETS["must_modify"] + 10)
        ]
        neighborhood = [_neighbor(f"c{i}", 0) for i in range(len(components))]

        package = curate(
            components=components,
            neighborhood_nodes=neighborhood,
            enriched_text="Anchor0 Anchor1 Anchor2",
            target_repositories=["etl-core"],
        )

        assert len(package.by_tier("must_modify")) <= TIER_BUDGETS["must_modify"]

    def test_relevant_test_never_exceeds_its_budget(self):
        anchor = _component("anchor", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py")
        tests = [
            _component(
                f"t{i}", f"TestThing{i}", "etl-core", f"tests/unit/test_thing_{i}.py", is_test=True
            )
            for i in range(TIER_BUDGETS["relevant_test"] + 5)
        ]
        neighborhood = [_neighbor("anchor", 0)] + [_neighbor(f"t{i}", 1) for i in range(len(tests))]

        package = curate(
            components=[anchor, *tests],
            neighborhood_nodes=neighborhood,
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )

        assert len(package.by_tier("relevant_test")) <= TIER_BUDGETS["relevant_test"]

    def test_excluded_count_plus_included_equals_total(self):
        components = [
            _component(f"c{i}", f"Thing{i}", "etl-core", f"src/etl_core/thing_{i}.py")
            for i in range(30)
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[],
            enriched_text="nothing matches anything here",
            target_repositories=["etl-core"],
        )
        assert package.total_candidates == 30
        assert package.excluded_count + len(package.items) == 30


class TestEmptyInputs:
    def test_no_components_returns_empty_package(self):
        package = curate(
            components=[], neighborhood_nodes=[], enriched_text="anything", target_repositories=[]
        )
        assert package.items == []
        assert package.excluded_count == 0
        assert package.total_candidates == 0


class TestPartialTokenMatching:
    """Self-review finding: exact-token matching alone missed common word-
    form variants a real ticket uses ("dedup" vs. `ExactDeduplicator`,
    "merge" vs. `SCDType2Merger`'s "merger"). Prefix-based partial credit
    closes the common case; the SCD2-abbreviation case stays a documented,
    known limitation requiring semantic similarity (an explicit, separate
    decision — see the architecture review), not something this test
    pretends is solved.
    """

    def test_dedup_matches_deduplicator_via_prefix(self):
        components = [
            _component("c1", "ExactDeduplicator", "etl-core", "src/etl_core/dedup/exact_dedup.py"),
        ]
        anchors = select_anchor_ids(
            components, "Fix the dedup logic for concurrent Kafka writes.", "etl-core"
        )
        assert anchors == ["c1"]

    def test_merge_matches_merger_via_prefix(self):
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
        ]
        anchors = select_anchor_ids(
            components, "The merge step is producing duplicate rows.", "etl-core"
        )
        assert anchors == ["c1"]

    def test_exact_match_still_outranks_partial_match(self):
        exact = _component("c1", "ExactDeduplicator", "etl-core", "src/etl_core/dedup/exact.py")
        partial = _component("c2", "Deduplicator", "etl-core", "src/etl_core/dedup/other.py")
        # "exactdeduplicator"/"deduplicator" both relate to "deduplicator"
        # the ticket word; c1 also exactly contains "exact" if the ticket
        # says it — construct so c1 has an exact hit c2 doesn't.
        anchors = select_anchor_ids(
            [exact, partial], "Fix the exact deduplicator implementation.", "etl-core", limit=1
        )
        assert anchors == ["c1"]

    def test_short_tokens_never_partial_match_to_avoid_false_positives(self):
        # "id" (2 chars) must never be treated as related to "identifier"
        # or anything else via prefix matching — too short to be
        # meaningful evidence of relatedness, only noise.
        components = [_component("c1", "IdMapper", "etl-core", "src/etl_core/id_mapper.py")]
        # tokenize drops sub-3-char tokens entirely, so this also confirms
        # no crash/false match occurs on short fragments.
        anchors = select_anchor_ids(components, "Fix the id handling.", "etl-core")
        assert anchors == []

    def test_a_ticket_mentioning_merge_still_finds_the_merger_class(self):
        """The real NPT-29 ticket's actual text ("SCD2 merge produces
        duplicate current records...") does connect to `SCDType2Merger`
        after all — not via "SCD2" (see the next test), but via "merge"
        prefix-relating to "merger". A realistic ticket sentence, unlike
        the isolated single-word fixtures elsewhere in this class."""
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py")
        ]
        anchors = select_anchor_ids(
            components, "SCD2 merge produces duplicate current records.", "etl-core"
        )
        assert anchors == ["c1"]

    def test_scd2_domain_abbreviation_alone_is_a_documented_known_limitation(self):
        """Deliberately asserts CURRENT (limited) behavior, not desired
        behavior — a regression test in the opposite direction from
        usual: if this ever starts passing (anchors == ["c1"]), that
        means token matching changed in a way that should be reviewed
        deliberately (e.g. semantic similarity was added), not silently.

        Isolated from any other matching word (unlike the previous test,
        where "merge" happens to also be in the same sentence and
        carries the match on its own) — "SCD2" and "SCDType2Merger"
        share no exact token and no qualifying prefix relationship
        ("scd2" vs "scdtype2" diverge at the 4th character). Closing
        this specific abbreviation case needs domain knowledge or
        embeddings, out of scope for deterministic token matching.
        """
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py")
        ]
        anchors = select_anchor_ids(components, "SCD2 needs attention.", "etl-core")
        assert anchors == []  # NOT ["c1"] — see docstring


class TestSelectAnchorIds:
    """`select_anchor_ids` is exact-token matching (see `tokenize`'s own
    docstring) — deliberately, per the architecture review's discussion
    of semantic similarity as an explicit, separate, not-yet-decided
    trade-off. Real ticket text that names a class only by a loosely
    related domain term (e.g. "SCD2 merge" vs. a class literally named
    `SCDType2Merger`, whose camelCase splitting produces the single
    glued token "scdtype2", not "scd"+"2") will genuinely score zero
    here — that is today's real, known, and intentional limitation, not
    a bug in this function. These fixtures use ticket text that shares
    real, exact tokens with the components, which is what this
    deterministic matching is actually meant to catch.
    """

    TICKET_TEXT = (
        "The exact_dedup module in etl-core needs a fix for duplicate "
        "keys during concurrent writes."
    )

    def test_selects_best_matching_component_in_primary_repository(self):
        components = [
            _component("c1", "ExactDeduplicator", "etl-core", "src/etl_core/dedup/exact_dedup.py"),
            _component("c2", "UnrelatedThing", "etl-core", "src/etl_core/misc/whatever.py"),
        ]
        anchors = select_anchor_ids(components, self.TICKET_TEXT, "etl-core")
        assert anchors == ["c1"]

    def test_ignores_components_outside_primary_repository(self):
        components = [
            _component("c1", "ExactDeduplicator", "other-repo", "src/other/exact_dedup.py"),
        ]
        anchors = select_anchor_ids(components, self.TICKET_TEXT, "etl-core")
        assert anchors == []

    def test_repository_name_alone_does_not_manufacture_an_anchor(self):
        # Every component in etl-core shares the "etl_core" path segment;
        # none of them should become an anchor purely from that, only
        # from matching something ELSE in the ticket text.
        components = [
            _component("c1", "SomeClass", "etl-core", "src/etl_core/misc/some_class.py"),
        ]
        anchors = select_anchor_ids(
            components, "Repo: etl-core. Nothing else relevant.", "etl-core"
        )
        assert anchors == []

    def test_caps_at_limit(self):
        components = [
            _component(f"c{i}", f"ExactDeduplicator{i}", "etl-core", f"src/etl_core/dedup/m{i}.py")
            for i in range(20)
        ]
        anchors = select_anchor_ids(components, self.TICKET_TEXT, "etl-core", limit=5)
        assert len(anchors) == 5

    def test_no_components_at_all_returns_empty(self):
        assert select_anchor_ids([], self.TICKET_TEXT, "etl-core") == []


class TestScoreExplainability:
    def test_every_item_carries_its_full_score_breakdown(self):
        components = [
            _component("anchor", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py")
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[_neighbor("anchor", 0)],
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )
        item = package.items[0]
        # composite_score must equal the sum of its own parts (minus the
        # test penalty), not an independently-computed number.
        expected = max(
            0.0,
            item.relevance_score + item.proximity_score + item.repository_bonus - item.test_penalty,
        )
        assert abs(item.composite_score - round(expected, 4)) < 1e-6


class TestRenderEvidencePackageText:
    def test_empty_package_states_so_explicitly(self):
        components = [
            _component(f"c{i}", f"Thing{i}", "etl-core", f"src/etl_core/thing_{i}.py")
            for i in range(5)
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[],
            enriched_text="nothing matches anything",
            target_repositories=["etl-core"],
        )
        text = render_evidence_package_text(package)
        assert "No components scored as relevant" in text
        assert "5 indexed" in text

    def test_renders_tier_headings_and_item_details(self):
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py")
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[_neighbor("c1", 0)],
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )
        text = render_evidence_package_text(package)
        assert "Must modify" in text
        assert "SCDType2Merger" in text
        assert "etl-core" in text
        assert "confidence" in text

    def test_empty_tiers_are_omitted_not_printed_as_none(self):
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py")
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[_neighbor("c1", 0)],
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )
        text = render_evidence_package_text(package)
        assert "Relevant tests" not in text  # no test components at all in this fixture

    def test_excluded_count_is_stated_when_nonzero(self):
        components = [
            _component("c1", "SCDType2Merger", "etl-core", "src/etl_core/scd/scd_type2.py"),
            *[
                _component(f"u{i}", f"Unrelated{i}", "etl-core", f"src/etl_core/x{i}.py")
                for i in range(3)
            ],
        ]
        package = curate(
            components=components,
            neighborhood_nodes=[_neighbor("c1", 0)],
            enriched_text=NPT_29_TEXT,
            target_repositories=["etl-core"],
        )
        text = render_evidence_package_text(package)
        assert "scored below the relevance floor" in text
