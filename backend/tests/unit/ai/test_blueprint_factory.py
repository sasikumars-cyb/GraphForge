"""Tests for app.agents.blueprint.factory's repository-reuse diagram
builder — specifically the `verified` signal, which used to be computed
by the Planning Agent's verification block and then dropped: nothing in
the diagram distinguished a repository whose files_affected claims
checked out from one whose claims were entirely fabricated.
"""

from __future__ import annotations

from app.agents.blueprint.factory import (
    _build_data_model,
    _build_grounded_architecture,
    _build_implementation_roadmap,
    _build_repository_reuse,
    _build_risk_matrix,
    _require_grounded_flag,
    _truncate_at_word,
)
from app.agents.blueprint.models import Diagram, DiagramType


def _repo_usage(**overrides: object) -> dict:
    base: dict = {
        "name": "ds-team-widget-svc",
        "stars": 4,
        "purpose": "Widget processing",
        "reusable_components": ["WidgetLoader"],
        "reason": "Handles widget ingestion already",
        "estimated_reuse_pct": 60,
        "files_affected": [],
        "verified": False,
    }
    base.update(overrides)
    return base


class TestRepositoryReuseVerifiedSignal:
    def test_verified_repo_gets_a_positive_note(self):
        diagram = _build_repository_reuse([_repo_usage(verified=True)])
        node = diagram.nodes[0]
        assert node.metadata["verified"] is True
        assert node.properties["affected_component"].endswith("✓ verified")

    def test_unverified_repo_with_files_reports_the_count(self):
        diagram = _build_repository_reuse(
            [_repo_usage(verified=False, files_affected=["a.py", "b.py"])]
        )
        node = diagram.nodes[0]
        assert node.metadata["verified"] is False
        assert node.properties["affected_component"].endswith(
            "⚠ unverified — 2 file(s) unconfirmed"
        )

    def test_unverified_repo_with_no_file_claims_still_gets_a_note(self):
        diagram = _build_repository_reuse(
            [
                _repo_usage(
                    verified=False,
                    files_affected=[],
                    reusable_components=[],
                    # `reason` alone keeps this past the "meaningful" filter
                    # above without contributing to `detail` (only
                    # components/reuse_pct do), so `detail` stays empty and
                    # the note is left to stand on its own.
                    reason="Handles widget ingestion already",
                    estimated_reuse_pct=0,
                )
            ]
        )
        node = diagram.nodes[0]
        # No reuse_pct/components detail to prefix, so the note stands alone.
        assert node.properties["affected_component"] == "⚠ unverified"

    def test_missing_verified_key_defaults_to_unverified(self):
        # Fails closed, matching RepositoryUsage.verified's own default
        # (see app.agents.planning.schemas) — a result that somehow
        # reached this builder without the field set must not read as
        # trusted.
        usage = _repo_usage()
        del usage["verified"]
        diagram = _build_repository_reuse([usage])
        assert diagram.nodes[0].metadata["verified"] is False
        assert diagram.nodes[0].properties["affected_component"].endswith("⚠ unverified")


class TestRequireGroundedFlag:
    """E3 regression: `grounded` used to be set by only 2 of ~20 diagram
    builders — "End-to-End Data Flow" and "Data Model" carried no flag at
    all, so the frontend had no explicit signal to distinguish "narrative,
    deliberately unmarked" from "narrative, someone forgot to mark it"."""

    def test_backfills_missing_grounded_as_false(self):
        d = Diagram(id="x", title="X", type=DiagramType.FLOW, metadata={})
        (out,) = _require_grounded_flag([d])
        assert out.metadata["grounded"] is False

    def test_does_not_override_an_explicit_true(self):
        d = Diagram(id="x", title="X", type=DiagramType.FLOW, metadata={"grounded": True})
        (out,) = _require_grounded_flag([d])
        assert out.metadata["grounded"] is True

    def test_does_not_override_an_explicit_false(self):
        d = Diagram(id="x", title="X", type=DiagramType.FLOW, metadata={"grounded": False})
        (out,) = _require_grounded_flag([d])
        assert out.metadata["grounded"] is False


class TestDataModelGhostNodes:
    """E4 regression: an entity referenced only inside a relationship
    string ("has_many OrderItems") but never declared in `entities` used
    to render pixel-identical to a real, declared entity — `synthesized:
    True` was recorded in metadata, but this diagram has no per-node
    detail panel, so nothing on screen ever showed it. A real run
    produced entities this way (e.g. "ManifestEntries") that were never
    once declared.
    """

    def test_synthesized_entity_is_labelled_as_inferred(self):
        entities = [
            {
                "name": "Manifest",
                "key_attributes": ["id"],
                "relationships": ["has_many ManifestEntries"],
            }
        ]
        diagram = _build_data_model(entities)
        synthesized = next(n for n in diagram.nodes if n.label != "Manifest")
        assert synthesized.metadata["synthesized"] is True
        assert "(inferred)" in synthesized.label
        assert "inferred from a relationship" in synthesized.properties["affected_component"]

    def test_declared_entity_is_not_marked_synthesized(self):
        entities = [
            {"name": "Manifest", "key_attributes": ["id"], "relationships": ["has_many Entry"]},
            {"name": "Entry", "key_attributes": ["id"], "relationships": []},
        ]
        diagram = _build_data_model(entities)
        manifest = next(n for n in diagram.nodes if n.label == "Manifest")
        entry = next(n for n in diagram.nodes if n.label == "Entry")
        assert not manifest.metadata.get("synthesized")
        assert not entry.metadata.get("synthesized")


class TestGroundedArchitectureDirectoryLabels:
    """E5 regression: a directory containing an affected file was typed
    `"risk"` — the same orange/red styling a genuinely risky *file* node
    gets — merely for holding one, mislabelling plain folders like
    `scripts` or `tests/unittest` as themselves risky.
    """

    def test_directory_containing_affected_file_is_not_typed_risk(self):
        components = [
            {
                "repository": "repo-a",
                "name": "widget_loader",
                "type": "Function",
                "file_path": "src/widget_loader.py",
            }
        ]
        diagram = _build_grounded_architecture(
            components,
            "repo-a",
            verified_names={"widget_loader"},
            verified_file_paths=set(),
        )
        dir_node = next(n for n in diagram.nodes if n.metadata.get("kind") == "directory")
        file_node = next(n for n in diagram.nodes if n.metadata.get("kind") == "file")
        assert dir_node.type == "component", "a directory is a container, not itself a risk"
        assert file_node.type == "risk", "the actual affected file keeps the risk signal"
        assert "contains affected file" in dir_node.properties["affected_component"]


class TestTruncateAtWord:
    """E6 regression: a bare `text[:N]` slice cuts mid-word with no
    indication anything was cut — "...from job run 7" reads as a
    complete, if odd, sentence rather than a truncated one.
    """

    def test_short_text_is_returned_unchanged(self):
        assert _truncate_at_word("short", 80) == "short"

    def test_long_text_is_cut_at_a_word_boundary_with_ellipsis(self):
        text = "Re-run the failed batch from job run 7823 after the fix lands"
        truncated = _truncate_at_word(text, 30)
        assert truncated.endswith("…")
        # The core assertion: truncation never splits a word. Everything
        # before the ellipsis must be an exact prefix of the original
        # text, and whatever comes right after that prefix in the
        # original must be a word boundary (a space), not more letters —
        # otherwise the cut happened mid-word, silently gluing two
        # fragments into what reads as one real (wrong) word.
        core = truncated[:-1]
        assert text.startswith(core)
        assert text[len(core) : len(core) + 1] in (" ", "")

    def test_roadmap_deliverable_no_longer_splits_mid_word(self):
        # The exact real-world symptom: a deliverable citing a job run
        # number sliced at a fixed 80 chars mid-digit, so "...job run
        # 78234921" read as "...from job run 7" — a complete-looking
        # sentence that was actually cut off two words early.
        deliverable = (
            "Confirm the manifest chunking fix resolves the failure reported from job run 78234921"
        )
        diagram = _build_implementation_roadmap(
            [{"name": "Rollout", "order": 1, "deliverables": [deliverable]}]
        )
        step = diagram.nodes[0].properties["steps"][0]
        assert step.endswith("…")
        core = step[:-1]
        assert deliverable.startswith(core)
        assert deliverable[len(core) : len(core) + 1] in (" ", "")


class TestRiskMatrixStructuredFields:
    """E7 regression: likelihood/impact/mitigation/evidence used to be
    flattened into the node `label` with " — " separators, producing one
    run-on paragraph per risk — unreadable as a matrix. They're real
    `metadata` fields; the label carries only the risk statement.
    """

    def test_label_carries_only_the_risk_statement(self):
        risks = [
            {
                "description": "Manifest parsing may exceed the taskValues size limit",
                "category": "architecture",
                "likelihood": "high",
                "impact": "critical",
                "mitigation": "Spill the manifest to Delta before passing a pointer",
                "evidence": "taskValues enforces a 48 KiB per-value limit",
            }
        ]
        diagram = _build_risk_matrix(risks)
        node = diagram.nodes[0]
        assert "Manifest parsing may exceed" in node.label
        assert "Mitigation:" not in node.label
        assert "Evidence:" not in node.label
        assert "Likelihood:" not in node.label

    def test_likelihood_impact_mitigation_evidence_survive_as_metadata(self):
        risks = [
            {
                "description": "Manifest parsing may exceed the taskValues size limit",
                "category": "architecture",
                "likelihood": "high",
                "impact": "critical",
                "mitigation": "Spill the manifest to Delta before passing a pointer",
                "evidence": "taskValues enforces a 48 KiB per-value limit",
            }
        ]
        diagram = _build_risk_matrix(risks)
        node = diagram.nodes[0]
        assert node.metadata["likelihood"] == "high"
        assert node.metadata["impact"] == "critical"
        assert "Spill the manifest" in node.metadata["mitigation"]
        assert "48 KiB" in node.metadata["evidence"]
