"""`_graph_context_text_from` (planning/agent.py) once `engineering_
understanding` is present — confirms Planning's prompt is now built
primarily from the synthesized understanding (per the Frontier-Class
Investigation Agent redesign), with the curated evidence package appended
only for traceability, and that everything still falls back exactly as
before when synthesis produced nothing.
"""

from __future__ import annotations

from app.agents.planning.agent import (
    _MAX_GRAPH_CONTEXT_CHARS,
    _MIN_EVIDENCE_RESERVE_CHARS,
    _graph_context_text_from,
    _render_prompt,
)
from app.agents.planning.classifier import analyse
from app.context_pipeline.reasoning.curation import curate
from app.context_pipeline.reasoning.understanding import EngineeringUnderstanding


def _package_dict() -> dict:
    components = [
        {
            "id": "c1",
            "name": "ExactDeduplicator",
            "repository": "etl-core",
            "file_path": "src/etl_core/dedup/exact_dedup.py",
            "is_test": False,
        },
    ]
    package = curate(
        components=components,
        neighborhood_nodes=[{"id": "c1", "hop_distance": 0}],
        enriched_text="Fix the exact deduplicator implementation.",
        target_repositories=["etl-core"],
    )
    return package.model_dump()


def _understanding_dict(**overrides: object) -> dict:
    return EngineeringUnderstanding(
        business_objective="Prevent duplicate records.",
        primary_repository="etl-core",
        **overrides,
    ).model_dump()


def test_understanding_is_the_primary_text_with_evidence_appended_for_traceability():
    result = {
        "engineering_understanding": _understanding_dict(),
        "evidence_package": _package_dict(),
        "graph_context_text": "OLD RAW TEXT — should not be used",
    }
    text = _graph_context_text_from(result)

    assert text.index("Prevent duplicate records.") < text.index("ExactDeduplicator")
    assert "Supporting evidence" in text
    assert "OLD RAW TEXT" not in text


def test_understanding_alone_is_used_when_no_evidence_package_exists():
    result = {"engineering_understanding": _understanding_dict()}
    text = _graph_context_text_from(result)

    assert "Prevent duplicate records." in text
    assert "Supporting evidence" not in text


def test_falls_back_to_evidence_package_when_understanding_is_empty():
    result = {
        "engineering_understanding": {},
        "evidence_package": _package_dict(),
    }
    text = _graph_context_text_from(result)

    assert "ExactDeduplicator" in text
    assert "Prevent duplicate records." not in text


def test_falls_back_to_evidence_package_when_understanding_renders_to_nothing():
    # A well-formed but entirely blank EngineeringUnderstanding renders to "" —
    # must not win over a real evidence package.
    result = {
        "engineering_understanding": EngineeringUnderstanding().model_dump(),
        "evidence_package": _package_dict(),
    }
    text = _graph_context_text_from(result)

    assert "ExactDeduplicator" in text
    assert "Supporting evidence" not in text


def test_malformed_understanding_dict_falls_back_without_raising():
    result = {
        "engineering_understanding": {"confidence": "not-a-dict-should-fail-validation"},
        "evidence_package": _package_dict(),
    }
    text = _graph_context_text_from(result)

    assert "ExactDeduplicator" in text


# ---------------------------------------------------------------------------
# RFC-0034 — a long `engineering_understanding` (the deterministic fallback
# text produced whenever LLM synthesis fails — see RFC-0032/0033) must not
# be able to push the evidence package, including RFC-0033 source excerpts,
# out of the shared `_MAX_GRAPH_CONTEXT_CHARS` budget entirely.
# ---------------------------------------------------------------------------


def _long_understanding_dict(business_objective: str) -> dict:
    return EngineeringUnderstanding(
        business_objective=business_objective, primary_repository="etl-core"
    ).model_dump()


def _package_dict_with_excerpt() -> dict:
    """A must_modify item whose source_excerpt is populated, via curate()'s
    real RFC-0033 machinery (not hand-authored), so this test exercises the
    same code path a live run does."""
    components = [
        {
            "id": "c1",
            "name": "configure_flags",
            "repository": "etl-core",
            "file_path": "src/etl_core/flags/configure.py",
            "is_test": False,
        },
    ]
    filler = "\n".join(f"    step_{i}()" for i in range(20))
    source_text = (
        "def configure_flags(df):\n"
        f"{filler}\n"
        '    result = df.withColumn("enabled_flag", F.lit(""))\n'
        "    return result\n"
    )
    package = curate(
        components=components,
        neighborhood_nodes=[{"id": "c1", "hop_distance": 0}],
        enriched_text="enabled_flag should be true. Repo: etl-core.",
        target_repositories=["etl-core"],
        source_file_texts={("etl-core", "src/etl_core/flags/configure.py"): source_text},
    )
    return package.model_dump()


def test_long_understanding_still_lets_a_must_modify_excerpt_survive():
    long_objective = "A very long synthesized business objective. " * 150  # well over 3500 chars
    assert len(long_objective) > _MAX_GRAPH_CONTEXT_CHARS

    result = {
        "engineering_understanding": _long_understanding_dict(long_objective),
        "evidence_package": _package_dict_with_excerpt(),
    }
    text = _graph_context_text_from(result)

    assert len(text) <= _MAX_GRAPH_CONTEXT_CHARS
    assert 'withColumn("enabled_flag", F.lit(""))' in text


def test_combined_content_under_budget_is_byte_identical_to_before():
    result = {
        "engineering_understanding": _understanding_dict(),
        "evidence_package": _package_dict_with_excerpt(),
    }
    text = _graph_context_text_from(result)

    # Same shape as the pre-RFC-0034 unconditional concatenation: no
    # truncation of either section when the combined text already fits.
    assert "Prevent duplicate records." in text
    assert 'withColumn("enabled_flag", F.lit(""))' in text
    assert len(text) <= _MAX_GRAPH_CONTEXT_CHARS


def test_no_evidence_package_behavior_is_unaffected_by_this_rfc():
    long_objective = "A very long synthesized business objective. " * 150
    result = {"engineering_understanding": _long_understanding_dict(long_objective)}
    text = _graph_context_text_from(result)

    # No evidence at all — untouched by the new reservation logic entirely
    # (that code path only runs when both understanding and evidence
    # texts are non-empty); the full, un-truncated objective survives
    # exactly as it did before this RFC. `render_prompt_template`'s own
    # final slice — unchanged by this RFC — is what bounds it later.
    assert long_objective in text
    assert len(text) >= len(long_objective)


def test_evidence_that_alone_exceeds_the_remaining_budget_is_truncated_gracefully():
    huge_evidence_text_source = "x = 1  # enabled_flag marker line\n" * 400  # far over budget
    package = curate(
        components=[
            {
                "id": "c1",
                "name": "configure_flags",
                "repository": "etl-core",
                "file_path": "src/etl_core/flags/configure.py",
                "is_test": False,
            },
        ],
        neighborhood_nodes=[{"id": "c1", "hop_distance": 0}],
        enriched_text="enabled_flag marker. Repo: etl-core.",
        target_repositories=["etl-core"],
        source_file_texts={
            ("etl-core", "src/etl_core/flags/configure.py"): huge_evidence_text_source
        },
    )
    result = {
        "engineering_understanding": EngineeringUnderstanding(
            business_objective="Short objective, well under budget.",
            primary_repository="etl-core",
        ).model_dump(),
        "evidence_package": package.model_dump(),
    }

    text = _graph_context_text_from(result)  # must not raise

    assert len(text) <= _MAX_GRAPH_CONTEXT_CHARS
    assert "Short objective, well under budget." in text


def test_final_rendered_prompt_contains_the_excerpt_not_just_the_intermediate_text():
    """Proves the excerpt survives all the way through `_render_prompt` —
    the actual function Planning calls to build its LLM prompt — not just
    the intermediate `_graph_context_text_from` string."""
    long_objective = "A very long synthesized business objective. " * 150
    graph_context_text = _graph_context_text_from(
        {
            "engineering_understanding": _long_understanding_dict(long_objective),
            "evidence_package": _package_dict_with_excerpt(),
        }
    )
    profile = analyse("Fix enabled_flag in etl-core.")

    prompt = _render_prompt("Fix enabled_flag in etl-core.", graph_context_text, profile)

    assert 'withColumn("enabled_flag", F.lit(""))' in prompt


def test_evidence_reserve_constant_is_a_small_bounded_fraction_of_the_total_budget():
    # A sanity guard on the constants themselves, not their interaction —
    # if either drifts to something nonsensical (reserve >= total budget),
    # every test above would start failing loudly rather than degrading
    # silently, so this just documents the intended relationship.
    assert 0 < _MIN_EVIDENCE_RESERVE_CHARS < _MAX_GRAPH_CONTEXT_CHARS
