"""Regression tests for the Context Discovery confidence audit.

The audit traced `ContextDiscoveryResult.confidence` and confirmed it is an
evidence/context-*completeness* score (necessity-weighted mean over
capability signals — see `app.context_pipeline.reasoning.capabilities.
overall_confidence`), never engineering confidence, and that no downstream
stage (Planning, Engineering Review, ...) mathematically inherits it. These
tests pin that architecture:

1. `context_completeness` is exposed alongside the legacy `confidence` key,
   same value, purely additive — never a recomputation.
2. A PARTIAL readiness with a non-trivial completeness score is a real,
   supported state (not a contradiction the model needs "fixing").
3. Missing `documentation` (a `recommended`, not `required`, capability)
   does not block readiness and is not treated as an engineering-confidence
   penalty anywhere downstream.
4. Engineering Review's own confidence is computed purely from its own
   `readiness_status` + verification-warning penalty — never from
   `context_discovery_result["confidence"]` / `["context_completeness"]`,
   however that number is set.
5. A result persisted before `context_completeness` existed (the field
   simply absent from the dict) still deserializes and the readiness gate
   still reads a correct completeness value via its own fallback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.context_discovery.schemas import ContextDiscoveryResult
from app.agents.engineering_review.agent import EngineeringReviewAgent
from app.context_pipeline.reasoning.capabilities import CapabilityAssessment, ConfidenceSignal
from app.context_pipeline.reasoning.memory import WorkingContext
from app.context_pipeline.reasoning.projection import build_discovery_report, build_result

# Only the two Engineering Review tests near the bottom are actually async
# (they call the agent's `run()`); the rest are synchronous unit checks over
# `build_result`/`build_discovery_report`/schema defaults, marked
# individually rather than via a blanket `pytestmark` so pytest-asyncio
# doesn't warn about a mark with nothing to await.


def _assessment(
    capability: str, necessity: str, satisfied_weight: float, total_weight: float
) -> CapabilityAssessment:
    """One capability with a single signal carrying the given satisfied/
    total weight ratio — enough to drive `overall_confidence` to a chosen
    score without running the full investigation engine."""
    return CapabilityAssessment(
        capability=capability,
        label=capability.replace("_", " ").title(),
        necessity=necessity,  # type: ignore[arg-type]
        score=round(satisfied_weight / total_weight, 4),
        signals=[
            ConfidenceSignal(label="satisfied part", satisfied=True, weight=satisfied_weight),
            ConfidenceSignal(
                label="unsatisfied part",
                satisfied=False,
                weight=total_weight - satisfied_weight,
            ),
        ]
        if satisfied_weight < total_weight
        else [ConfidenceSignal(label="fully satisfied", satisfied=True, weight=total_weight)],
    )


def _partial_state() -> WorkingContext:
    """Mirrors PROT-5749's shape: three required capabilities satisfied,
    one recommended capability (documentation) partially satisfied —
    overall PARTIAL readiness, confidence well below 100%."""
    state = WorkingContext()
    state.assessments = [
        _assessment("work_item", "required", 4, 4),
        _assessment("repository", "required", 7, 8),
        _assessment("architecture", "required", 6, 7),
        _assessment("documentation", "recommended", 1, 3),
        _assessment("runtime_execution", "not_applicable", 0, 1),
    ]
    return state


# ---------------------------------------------------------------------------
# 1 & 5 — context_completeness is additive, and backward compatible
# ---------------------------------------------------------------------------


def test_context_completeness_is_the_same_value_as_the_legacy_confidence_field() -> None:
    state = _partial_state()
    result = build_result(state)

    assert result["confidence"] == pytest.approx(0.8282, abs=1e-4)
    assert result["context_completeness"] == result["confidence"]

    # Also true of the nested discovery_report the Context Explorer UI reads.
    report = build_discovery_report(state)
    assert report["context_completeness"] == report["confidence"]


def test_context_discovery_result_schema_defaults_both_fields_identically() -> None:
    # A fresh, empty result (e.g. a brand-new run) must not report the two
    # fields as disagreeing.
    result = ContextDiscoveryResult(original_request="x", enriched_text="x")
    assert result.confidence == 0.0
    # `None`, not `0.0` — see the field's own docstring. `context_completeness`
    # defaults to "unknown," never a fabricated zero that a real 0.0 (e.g. a
    # genuinely BLOCKED run) would be indistinguishable from.
    assert result.context_completeness is None


def test_context_completeness_none_default_never_collides_with_a_genuine_zero_score() -> None:
    """The exact bug this default is designed to make impossible: a run
    that legitimately scored 0.0 (e.g. BLOCKED, nothing satisfied at all)
    must remain distinguishable from a pre-migration row that simply never
    had this field — `None` vs `0.0`, never both `0.0`."""
    genuinely_zero = ContextDiscoveryResult.model_validate(
        {
            "original_request": "x",
            "enriched_text": "x",
            "confidence": 0.0,
            "context_completeness": 0.0,
        }
    )
    missing_field_entirely = ContextDiscoveryResult.model_validate(
        {"original_request": "x", "enriched_text": "x", "confidence": 0.0}
    )
    assert genuinely_zero.context_completeness == 0.0
    assert missing_field_entirely.context_completeness is None
    assert genuinely_zero.context_completeness != missing_field_entirely.context_completeness


def test_a_result_persisted_before_context_completeness_existed_still_deserializes() -> None:
    """Every already-completed workflow's `AgentStep.result` JSON predates
    this field — it simply won't have the key. Must not fail to load, and
    a reader falling back to the legacy `confidence` key must get the
    correct number, whether it reads the raw persisted dict (as every
    current call site does) or a `ContextDiscoveryResult` built from it."""
    legacy_persisted_dict = {
        "original_request": "Investigate PROT-5749",
        "enriched_text": "Investigate PROT-5749",
        "readiness": "PARTIAL",
        "confidence": 0.8282,
        # no "context_completeness" key at all
    }
    result = ContextDiscoveryResult.model_validate(legacy_persisted_dict)
    assert result.confidence == pytest.approx(0.8282)
    # `None` — an unambiguous "this row predates the field" signal, not a
    # value that could be mistaken for a real, computed 0.0.
    assert result.context_completeness is None

    # The fallback every real reader (the readiness-gate message, the
    # frontend gauge) applies, whether reading the raw dict directly or the
    # validated model — both must resolve to the same real number.
    raw_fallback = legacy_persisted_dict.get(
        "context_completeness", legacy_persisted_dict.get("confidence", 0.0)
    )
    model_fallback = (
        result.context_completeness
        if result.context_completeness is not None
        else result.confidence
    )
    assert raw_fallback == pytest.approx(0.8282)
    assert model_fallback == pytest.approx(0.8282)
    assert raw_fallback == model_fallback


# ---------------------------------------------------------------------------
# 2 & 3 — PARTIAL readiness with a real completeness score is a supported,
# non-contradictory state; a missing *recommended* capability is not a
# blocking or engineering-confidence penalty.
# ---------------------------------------------------------------------------


def test_partial_readiness_with_high_completeness_is_not_a_contradiction() -> None:
    state = _partial_state()
    result = build_result(state)

    assert result["readiness"] == "PARTIAL"
    assert 0.80 < result["context_completeness"] < 0.90
    # Nothing required is missing — only the recommended capability.
    assert result["blocking_reasons"] == []


def test_missing_recommended_documentation_does_not_block_readiness() -> None:
    state = _partial_state()
    doc = next(a for a in state.assessments if a.capability == "documentation")
    assert doc.necessity == "recommended"
    assert doc.satisfied is False

    result = build_result(state)
    assert result["readiness"] != "BLOCKED"
    assert result["blocking_reasons"] == []


# ---------------------------------------------------------------------------
# 4 — Engineering Review's confidence is independent of Context Discovery's
# number, however that number is set.
# ---------------------------------------------------------------------------


def _make_context_discovery_result(confidence: float, completeness: float) -> dict:
    return {
        "confidence": confidence,
        "context_completeness": completeness,
        "readiness": "PARTIAL",
        "repositories": [],
    }


def _make_workflow(context_discovery_result: dict) -> AgentContext:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    step = SimpleNamespace(result=context_discovery_result)
    run = SimpleNamespace(
        workflow_stage="context_discovery",
        status="completed",
        steps=[step],
        created_at=datetime.now(UTC),
    )
    planning_step = SimpleNamespace(
        result={
            "goal": "plan_freeform",
            "executive_summary": "Fix the uom filter in the interval_usage transform.",
            "implementation_steps": [],
            "affected_components": ["transform_interval_usage"],
            "repository_relationships": [],
        }
    )
    planning_run = SimpleNamespace(
        workflow_stage="planning",
        status="completed",
        steps=[planning_step],
        created_at=datetime.now(UTC),
    )
    workflow = SimpleNamespace(runs=[run, planning_run])
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="Fix it")
    return AgentContext(
        subject=subject,
        goal="review_readiness",
        extras={"db": AsyncMock(), "workflow": workflow},
    )


def _make_llm_response(readiness_status: str = "ready") -> str:
    import json

    return json.dumps(
        {
            "executive_summary": "The blueprint is complete and internally consistent.",
            "readiness_status": readiness_status,
            "completeness_findings": [],
            "repository_review": [],
            "component_review": [],
            "risk_assessment": [],
            "dependency_assessment": [],
            "test_strategy_review": [],
            "blocking_issues": [],
            "recommendations": [],
        }
    )


@pytest.mark.asyncio
async def test_engineering_review_confidence_does_not_track_context_discovery_confidence() -> None:
    """However low or high Context Discovery's own number is, Engineering
    Review's confidence must depend only on its own readiness_status +
    verification warnings — never move in lockstep with it."""
    low_cd = _make_context_discovery_result(confidence=0.10, completeness=0.10)
    high_cd = _make_context_discovery_result(confidence=0.99, completeness=0.99)

    async def _run(context_discovery_result: dict) -> float:
        agent = EngineeringReviewAgent()
        with patch(
            "app.agents.engineering_review.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response()),
        ):
            output = await agent.run(_make_workflow(context_discovery_result))
        return output.confidence.score

    score_with_low_cd_confidence = await _run(low_cd)
    score_with_high_cd_confidence = await _run(high_cd)

    # Identical readiness_status ("ready") and no verification warnings in
    # both cases -> identical Engineering Review confidence, regardless of
    # how wildly Context Discovery's own number differs between the two runs.
    assert score_with_low_cd_confidence == score_with_high_cd_confidence == 0.9


def test_engineering_review_never_reads_context_discovery_confidence_key() -> None:
    """Static confirmation alongside the behavioral test above: the
    confidence computation itself never looks up `confidence` or
    `context_completeness` on the context discovery result."""
    import inspect

    from app.agents.engineering_review import agent as engineering_review_agent

    source = inspect.getsource(engineering_review_agent)
    # The only legitimate appearances of "confidence" tied to
    # context_discovery_result are the unrelated per-repository-candidate
    # `RepositoryCandidate.confidence` string marker ("structural"/
    # "heuristic") used when formatting the relationships block — not the
    # numeric Context Discovery score.
    calc_call_start = source.index("calculate_weighted_confidence(")
    calc_call_block = source[calc_call_start : calc_call_start + 400]
    assert "context_discovery" not in calc_call_block
