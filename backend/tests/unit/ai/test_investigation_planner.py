"""Tests for `reasoning.investigation_planner` — the explicit, deterministic
Investigation Planner: strategy classification, per-strategy seed graphs,
task-graph refresh (capability completion + contradiction-driven task
spawning), next-task selection, and the priority boost it contributes to
`engine._select` (combined with `understanding.capability_priority` in
`understanding.synthesize_engineering_understanding` — see
test_understanding.py for that integration).
"""

from __future__ import annotations

from app.context_pipeline.reasoning.capabilities import CapabilityAssessment
from app.context_pipeline.reasoning.investigation_planner import (
    InvestigationTask,
    classify_engineering_strategy,
    plan_priority_boost,
    priority_boost_from_tasks,
    refresh_task_graph,
    seed_tasks,
    select_next_task,
)


def _assessment(capability: str, *, satisfied: bool) -> CapabilityAssessment:
    # `satisfied` is a derived property computed from `signals` in the real
    # model (weight-based, via `from_signals`) — a single load-bearing
    # signal's own `satisfied` flag drives both `.satisfied` and `.score`
    # here (score becomes 1.0 or 0.0 accordingly), so tests build the
    # signal rather than trying to set the derived fields directly.
    from app.context_pipeline.reasoning.capabilities import LOAD_BEARING_WEIGHT, ConfidenceSignal

    signal = ConfidenceSignal(label="x", satisfied=satisfied, weight=LOAD_BEARING_WEIGHT, detail="")
    return CapabilityAssessment.from_signals(
        capability=capability, label=capability, necessity="required", signals=[signal]
    )


# ---------------------------------------------------------------------------
# Strategy classification
# ---------------------------------------------------------------------------


def test_classify_engineering_strategy_recognizes_each_category():
    cases = {
        "There is a known CVE in our auth middleware": "security",
        "We need to migrate the legacy billing table to the new schema": "migration",
        "API latency has doubled under load, throughput is way down": "performance",
        "Duplicate records appear after checkpoint replay, data integrity is broken": "data",
        "The service crashes with a null pointer regression after the last release": "bug",
        "This module has a lot of tech debt and needs a refactor": "refactoring",
        "Proposing a new service boundary — an architecture design review": "architecture",
    }
    for text, expected in cases.items():
        assert classify_engineering_strategy(text) == expected, text


def test_classify_engineering_strategy_defaults_to_feature_for_unrecognized_text():
    assert classify_engineering_strategy("Add support for exporting reports as CSV") == "feature"
    assert classify_engineering_strategy("") == "feature"


def test_seed_tasks_produce_a_genuinely_different_graph_per_strategy():
    bug_tasks = {t.task_id for t in seed_tasks("bug")}
    feature_tasks = {t.task_id for t in seed_tasks("feature")}
    migration_tasks = {t.task_id for t in seed_tasks("migration")}
    # Not identical graphs reordered — each strategy has at least one task
    # the others don't.
    assert bug_tasks != feature_tasks
    assert migration_tasks != feature_tasks
    assert "trace_implementation" in bug_tasks
    assert "find_reusable_components" in feature_tasks
    assert "map_dependencies" in migration_tasks


def test_seed_tasks_falls_back_to_default_strategy_for_an_unknown_key():
    # Defensive: a strategy string that isn't one of the known keys (e.g. a
    # stale persisted value) must not raise — falls back to the default.
    assert seed_tasks("not-a-real-strategy") == seed_tasks("feature")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# select_next_task — dependency-aware, gain-ranked
# ---------------------------------------------------------------------------


def test_select_next_task_respects_dependencies():
    tasks = [
        InvestigationTask(task_id="a", purpose="p", expected_information_gain=0.9),
        InvestigationTask(
            task_id="b", purpose="p", expected_information_gain=0.99, dependencies=["a"]
        ),
    ]
    # "b" scores higher but is blocked on "a", which hasn't completed yet.
    chosen = select_next_task(tasks)
    assert chosen is not None
    assert chosen.task_id == "a"


def test_select_next_task_picks_highest_gain_among_ready_tasks():
    tasks = [
        InvestigationTask(task_id="low", purpose="p", expected_information_gain=0.2),
        InvestigationTask(task_id="high", purpose="p", expected_information_gain=0.8),
    ]
    chosen = select_next_task(tasks)
    assert chosen is not None
    assert chosen.task_id == "high"


def test_select_next_task_returns_none_when_nothing_is_ready():
    tasks = [
        InvestigationTask(task_id="a", purpose="p", status="done"),
        InvestigationTask(
            task_id="b", purpose="p", dependencies=["c"]
        ),  # blocked on a task that doesn't exist / isn't done
    ]
    assert select_next_task(tasks) is None


# ---------------------------------------------------------------------------
# refresh_task_graph — completion + contradiction-driven spawning
# ---------------------------------------------------------------------------


def test_refresh_marks_a_task_done_once_its_capability_is_satisfied():
    tasks = [InvestigationTask(task_id="a", purpose="p", required_capability="repository")]
    # A single satisfied, load-bearing signal makes `CapabilityAssessment.
    # from_signals` compute score=1.0 (satisfied weight / total weight).
    assessments = [_assessment("repository", satisfied=True)]

    refreshed = refresh_task_graph(tasks, assessments=assessments, contradictions=[])

    assert refreshed[0].status == "done"
    assert refreshed[0].confidence == 1.0
    assert refreshed[0].actual_information_gain == 1.0  # confidence_at_creation defaulted to 0.0


def test_refresh_leaves_a_task_pending_while_its_capability_is_unsatisfied():
    tasks = [InvestigationTask(task_id="a", purpose="p", required_capability="repository")]
    assessments = [_assessment("repository", satisfied=False)]

    refreshed = refresh_task_graph(tasks, assessments=assessments, contradictions=[])

    assert refreshed[0].status == "pending"


def test_refresh_never_reopens_a_completed_task():
    tasks = [
        InvestigationTask(
            task_id="a",
            purpose="p",
            required_capability="repository",
            status="done",
            confidence=0.9,
        )
    ]
    # Even if a later re-assessment somehow scored it lower, a done task stays done.
    assessments = [_assessment("repository", satisfied=False)]

    refreshed = refresh_task_graph(tasks, assessments=assessments, contradictions=[])

    assert refreshed[0].status == "done"
    assert refreshed[0].confidence == 0.9


def test_refresh_spawns_a_task_for_an_unresolved_contradiction():
    from app.context_pipeline.reasoning.understanding import Contradiction

    contradiction = Contradiction(description="Docs say X, code does Y", resolved=False)
    refreshed = refresh_task_graph([], assessments=[], contradictions=[contradiction])

    assert len(refreshed) == 1
    assert refreshed[0].required_capability == "architecture"
    assert "Docs say X, code does Y" in refreshed[0].purpose


def test_refresh_ignores_a_resolved_contradiction():
    from app.context_pipeline.reasoning.understanding import Contradiction

    contradiction = Contradiction(description="Already explained", resolved=True)
    refreshed = refresh_task_graph([], assessments=[], contradictions=[contradiction])
    assert refreshed == []


def test_refresh_is_idempotent_for_the_same_unresolved_contradiction():
    from app.context_pipeline.reasoning.understanding import Contradiction

    contradiction = Contradiction(description="Docs say X, code does Y", resolved=False)
    once = refresh_task_graph([], assessments=[], contradictions=[contradiction])
    twice = refresh_task_graph(once, assessments=[], contradictions=[contradiction])
    assert len(twice) == 1
    assert twice[0].task_id == once[0].task_id


# ---------------------------------------------------------------------------
# Priority boost derivation
# ---------------------------------------------------------------------------


def test_priority_boost_from_tasks_only_counts_ready_pending_tasks():
    tasks = [
        InvestigationTask(
            task_id="blocked",
            purpose="p",
            required_capability="architecture",
            expected_information_gain=0.9,
            dependencies=["not-done"],
        ),
        InvestigationTask(
            task_id="ready",
            purpose="p",
            required_capability="documentation",
            expected_information_gain=0.4,
        ),
        InvestigationTask(
            task_id="done", purpose="p", required_capability="repository", status="done"
        ),
    ]
    boost = priority_boost_from_tasks(tasks)
    assert boost == {"documentation": 0.4}


def test_priority_boost_from_tasks_clamps_to_unit_interval():
    tasks = [
        InvestigationTask(
            task_id="a",
            purpose="p",
            required_capability="architecture",
            expected_information_gain=5.0,
        )
    ]
    assert priority_boost_from_tasks(tasks) == {"architecture": 1.0}


def test_plan_priority_boost_combines_workspace_and_graph_signals_via_max():
    tasks = [
        InvestigationTask(
            task_id="a",
            purpose="p",
            required_capability="architecture",
            expected_information_gain=0.3,
        )
    ]
    combined = plan_priority_boost({"architecture": 0.7, "documentation": 0.2}, tasks)
    # Graph says 0.3 for architecture, workspace says 0.7 — max wins, not sum.
    assert combined == {"architecture": 0.7, "documentation": 0.2}


def test_plan_priority_boost_lets_the_graph_contribute_a_capability_absent_from_workspace():
    tasks = [
        InvestigationTask(
            task_id="a",
            purpose="p",
            required_capability="repository",
            expected_information_gain=0.6,
        )
    ]
    combined = plan_priority_boost({}, tasks)
    assert combined == {"repository": 0.6}
