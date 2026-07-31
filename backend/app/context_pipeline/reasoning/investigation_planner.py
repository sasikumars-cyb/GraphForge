"""The Investigation Planner — an explicit, deterministic, inspectable
controller sitting above the capability loop, above `capability_priority`
(ADR 0016), and above the LLM synthesis call (ADR 0015).

Why this exists: ADR 0016 gave engineering understanding a real but
implicit lever over investigation — a bare `dict[str, float]` priority
boost, derived from whatever the LLM happened to estimate that round. It
worked, but it wasn't a *plan*: there was no persistent record of what the
investigation intended to learn, why, in what order, or what happened when
a task actually completed. This module makes that plan explicit and
queryable — an `InvestigationTask` graph, seeded differently per
engineering-problem type (a bug investigation and a feature investigation
should not produce the same graph), refreshed deterministically every time
new evidence or a new contradiction shows up, and inspectable after the
fact (expected vs. actual information gain per task).

Everything here is pure, deterministic Python — no LLM call, no I/O. That
is a deliberate architectural choice, not a limitation this module works
around: "which capability should run next" was already established (ADR
0016's own docstring) as a structural property this codebase keeps
reproducible and testable, never something decided fresh by a model at
selection time. The planner reads the LLM's own already-produced signals
(the workspace's hypotheses/contradictions/gain estimates — see
`reasoning.understanding`) the same way `capability_priority` always did;
it does not ask a model to plan directly.

What this explicitly is NOT: a general-purpose task-graph engine capable
of arbitrary node types, hypothesis merge/split operations, or an LLM-
authored graph structure. Only four real capabilities exist to execute
against (`work_item`, `repository`, `architecture`, `documentation` — see
`capabilities.py`), so every task here ultimately either targets one of
those four or is honestly capability-less (a question this system cannot
currently answer by executing anything, e.g. "validate tests" — no test-
execution capability exists yet). See this module's own tests and the ADR
for what is deliberately deferred.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.context_pipeline.reasoning.capabilities import CapabilityAssessment
    from app.context_pipeline.reasoning.understanding import Contradiction

TaskStatus = Literal["pending", "in_progress", "done", "skipped", "rejected"]

EngineeringStrategy = Literal[
    "bug",
    "feature",
    "migration",
    "performance",
    "architecture",
    "security",
    "refactoring",
    "data",
]


class InvestigationTask(BaseModel):
    """One node in the investigation graph — a question the planner
    believes is worth answering, why, through which capability, and what
    it expects that to be worth.

    `confidence_at_creation`/`confidence`/`actual_information_gain` make
    "did this task actually pay off" inspectable after the fact: the gap
    between what was expected (`expected_information_gain`) and what
    actually happened (`actual_information_gain`, set only once the task
    completes) is exactly the self-reflection question this redesign's
    brief asks for — "which investigation produced the highest value?
    which produced none?" — answerable by reading the graph, not by
    re-deriving it.
    """

    task_id: str
    purpose: str
    # "" when no real capability answers this question yet — an honest
    # gap (e.g. "validate tests"), never a fabricated capability key.
    required_capability: str = ""
    dependencies: list[str] = Field(default_factory=list)
    expected_information_gain: float = 0.0
    status: TaskStatus = "pending"
    confidence: float = 0.0
    confidence_at_creation: float = 0.0
    actual_information_gain: float | None = None
    evidence_produced: list[str] = Field(default_factory=list)
    reason_for_creation: str = ""


# ---------------------------------------------------------------------------
# Engineering strategy classification — deterministic, keyword-based, same
# discipline as app.indexer.classification's is_test detection: an explicit,
# checkable string match, never a probabilistic guess (ADR 0007).
# ---------------------------------------------------------------------------

# Order matters: first match wins. More specific problem types (security,
# migration, performance, data, bug) are checked before the general ones
# (refactoring, architecture, feature) so a ticket that happens to contain
# an incidental word isn't misclassified — e.g. "fails to scale under load"
# should read as "performance", not "bug", because the seed graph for
# performance work prioritizes different evidence (profiling/dependency
# evidence over a step-by-step call trace).
_STRATEGY_KEYWORDS: tuple[tuple[EngineeringStrategy, tuple[str, ...]], ...] = (
    (
        "security",
        (
            "vulnerability", "cve", "exploit", "auth bypass", "injection", "xss",
            "secret leak", "security", "unauthorized access", "privilege escalation",
        ),
    ),
    (
        "migration",
        ("migrate", "migration", "cutover", "backfill", "deprecat", "sunset", "upgrade to"),
    ),
    (
        "performance",
        (
            "slow", "latency", "timeout", "performance", "throughput", "memory leak",
            "n+1", "scale", "scalability", "bottleneck",
        ),
    ),
    (
        "data",
        (
            "duplicate record", "data corruption", "data integrity", "incorrect data",
            "data loss", "schema change", "backfill data", "inconsistent data",
        ),
    ),
    (
        "bug",
        ("bug", "defect", "broken", "incorrect behavior", "fails to", "crash", "regression"),
    ),
    (
        "refactoring",
        ("refactor", "tech debt", "technical debt", "simplify", "restructure", "cleanup"),
    ),
    (
        "architecture",
        ("architecture", "design review", "new service", "decompose", "service boundary"),
    ),
)

_DEFAULT_STRATEGY: EngineeringStrategy = "feature"


def classify_engineering_strategy(text: str) -> EngineeringStrategy:
    """Which kind of engineering problem this is, from the request/ticket
    text alone — the thing that decides which investigation graph gets
    seeded (see `seed_tasks`). Falls back to "feature" (add/change
    behavior) when nothing more specific matches, since that is the most
    general category, not because it is the most likely."""
    lowered = (text or "").lower()
    for strategy, keywords in _STRATEGY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return strategy
    return _DEFAULT_STRATEGY


# ---------------------------------------------------------------------------
# Per-strategy seed graphs — deliberately different shapes, not the same
# four tasks reordered: a bug investigation prioritizes tracing the actual
# code path over documentation; a migration prioritizes dependency/
# architecture understanding above all else; a feature investigation
# prioritizes finding what already exists to reuse.
# ---------------------------------------------------------------------------

_SEED_TASK_TEMPLATES: dict[EngineeringStrategy, list[dict[str, Any]]] = {
    "bug": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand the reported vs. expected behavior",
            "required_capability": "work_item",
            "expected_information_gain": 0.9,
            "reason_for_creation": "A bug report is meaningless without knowing what "
            "behavior was actually expected.",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify which repository owns the reported behavior",
            "required_capability": "repository",
            "expected_information_gain": 0.85,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Cannot trace a defect without knowing which "
            "codebase to look in.",
        },
        {
            "task_id": "trace_implementation",
            "purpose": "Trace the call/data path that produces the reported behavior",
            "required_capability": "architecture",
            "expected_information_gain": 0.9,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "A bug's root cause is almost always in the "
            "implementation itself — prioritize tracing the real code path over "
            "reading about it.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check whether documented behavior matches the report",
            "required_capability": "documentation",
            "expected_information_gain": 0.3,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "Lower priority than tracing the code for a bug, "
            "but may reveal a documented constraint that explains the behavior.",
        },
        {
            "task_id": "validate_tests",
            "purpose": "Confirm whether an existing test already covers this behavior",
            "required_capability": "",
            "expected_information_gain": 0.0,
            "dependencies": ["trace_implementation"],
            "reason_for_creation": "No test-execution capability exists yet — this "
            "task is recorded as an honest, currently-unanswerable question rather "
            "than silently skipped.",
        },
    ],
    "feature": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand the business objective the feature serves",
            "required_capability": "work_item",
            "expected_information_gain": 0.85,
            "reason_for_creation": "A feature request only makes engineering sense "
            "relative to the goal it serves.",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify which repository should implement this",
            "required_capability": "repository",
            "expected_information_gain": 0.8,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Ownership decides where new code belongs.",
        },
        {
            "task_id": "find_reusable_components",
            "purpose": "Find existing implementations this feature could reuse",
            "required_capability": "architecture",
            "expected_information_gain": 0.85,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "A feature investigation's highest-value question "
            "is often 'does something like this already exist' — reuse discovery "
            "outranks documentation here.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check design docs/constraints relevant to this feature",
            "required_capability": "documentation",
            "expected_information_gain": 0.5,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "Design decisions and constraints often live only "
            "in documentation, not in the code itself.",
        },
    ],
    "migration": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand what is migrating from/to what, and why",
            "required_capability": "work_item",
            "expected_information_gain": 0.8,
            "reason_for_creation": "A migration's scope and rollback plan depend on "
            "the exact from/to states.",
        },
        {
            "task_id": "map_dependencies",
            "purpose": "Map every dependency that touches the migrating component",
            "required_capability": "architecture",
            "expected_information_gain": 0.95,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "A migration's dominant risk is an unaccounted-for "
            "dependent — architecture understanding is the highest-value work here, "
            "above almost everything else.",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify every repository this migration touches",
            "required_capability": "repository",
            "expected_information_gain": 0.7,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Migrations frequently cross repository "
            "boundaries — ownership must be established for each side.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check for an existing migration/runbook precedent",
            "required_capability": "documentation",
            "expected_information_gain": 0.6,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "A previous migration's documented approach is "
            "high-value precedent, not boilerplate.",
        },
    ],
    "performance": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand the observed performance symptom and target",
            "required_capability": "work_item",
            "expected_information_gain": 0.8,
            "reason_for_creation": "A performance goal is meaningless without a "
            "concrete symptom and target (latency, throughput, memory).",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify which repository owns the slow path",
            "required_capability": "repository",
            "expected_information_gain": 0.75,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Ownership decides where profiling/investigation "
            "effort belongs.",
        },
        {
            "task_id": "trace_hot_path",
            "purpose": "Trace the call/data path along the reported hot path",
            "required_capability": "architecture",
            "expected_information_gain": 0.9,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "Performance root causes live in the actual "
            "call/data path (N+1s, redundant calls) far more often than in docs.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check for documented capacity/SLA constraints",
            "required_capability": "documentation",
            "expected_information_gain": 0.35,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "Lower priority, but a documented SLA changes "
            "what 'fixed' means.",
        },
    ],
    "security": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand the reported vulnerability/exposure and its impact",
            "required_capability": "work_item",
            "expected_information_gain": 0.9,
            "reason_for_creation": "Severity and remediation both depend on the "
            "exact exposure being reported.",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify which repository contains the exposed surface",
            "required_capability": "repository",
            "expected_information_gain": 0.85,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Remediation must land in the exact owning "
            "codebase, not a plausible-looking neighbor.",
        },
        {
            "task_id": "trace_implementation",
            "purpose": "Trace the exact code path that constitutes the exposure",
            "required_capability": "architecture",
            "expected_information_gain": 0.95,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "A security fix must be scoped to the real "
            "vulnerable path — the single highest-value investigation for this "
            "strategy.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check for a documented threat model or prior advisory",
            "required_capability": "documentation",
            "expected_information_gain": 0.4,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "A prior advisory or threat model changes scope "
            "and precedent for remediation.",
        },
    ],
    "refactoring": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand what problem the refactor is meant to solve",
            "required_capability": "work_item",
            "expected_information_gain": 0.7,
            "reason_for_creation": "A refactor with no stated problem risks "
            "changing behavior no one asked to change.",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify which repository contains the code to refactor",
            "required_capability": "repository",
            "expected_information_gain": 0.75,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Ownership decides scope.",
        },
        {
            "task_id": "map_dependencies",
            "purpose": "Map every caller/dependent of the code being refactored",
            "required_capability": "architecture",
            "expected_information_gain": 0.9,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "A refactor's dominant risk is an unaccounted-for "
            "caller — dependency mapping outranks documentation here.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check whether the current design was a deliberate decision",
            "required_capability": "documentation",
            "expected_information_gain": 0.5,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "A documented design decision the refactor "
            "would violate is exactly the kind of contradiction worth surfacing "
            "before, not after, implementation.",
        },
    ],
    "architecture": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand the architectural goal and its drivers",
            "required_capability": "work_item",
            "expected_information_gain": 0.75,
            "reason_for_creation": "An architecture change needs a stated driver "
            "(scale, ownership, coupling) to evaluate against.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check existing architecture decisions and constraints",
            "required_capability": "documentation",
            "expected_information_gain": 0.85,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Architecture work is the one strategy where "
            "documented prior decisions outrank tracing current code — violating "
            "a deliberate past decision is the single biggest risk here.",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify every repository this architectural change touches",
            "required_capability": "repository",
            "expected_information_gain": 0.8,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Architecture changes routinely cross repository "
            "boundaries by definition.",
        },
        {
            "task_id": "map_dependencies",
            "purpose": "Map the current dependency/ownership graph being changed",
            "required_capability": "architecture",
            "expected_information_gain": 0.9,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "Cannot evaluate an architectural change without "
            "the current dependency graph it changes.",
        },
    ],
    "data": [
        {
            "task_id": "understand_objective",
            "purpose": "Understand the exact data symptom (duplication, loss, corruption)",
            "required_capability": "work_item",
            "expected_information_gain": 0.85,
            "reason_for_creation": "Data investigations need a precise symptom — "
            "'duplicate' and 'lost' and 'inconsistent' point at entirely different "
            "root causes.",
        },
        {
            "task_id": "identify_repository",
            "purpose": "Identify which repository owns the data pipeline/store",
            "required_capability": "repository",
            "expected_information_gain": 0.8,
            "dependencies": ["understand_objective"],
            "reason_for_creation": "Ownership decides where the pipeline/schema "
            "logic actually lives.",
        },
        {
            "task_id": "trace_pipeline",
            "purpose": "Trace the data pipeline/merge/ingestion path end to end",
            "required_capability": "architecture",
            "expected_information_gain": 0.95,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "Data bugs are almost always explained by the "
            "actual pipeline path (merge/ingestion/checkpoint logic), the single "
            "highest-value investigation for this strategy.",
        },
        {
            "task_id": "check_documentation",
            "purpose": "Check for a documented schema/data contract",
            "required_capability": "documentation",
            "expected_information_gain": 0.4,
            "dependencies": ["identify_repository"],
            "reason_for_creation": "A documented data contract clarifies what "
            "'correct' means for this data.",
        },
    ],
}


def seed_tasks(strategy: EngineeringStrategy) -> list[InvestigationTask]:
    """The initial investigation graph for a given engineering-problem
    type — deliberately different per strategy (see the module docstring
    and each template's own `reason_for_creation`), not the same four
    tasks reordered. Called exactly once per discovery run, the first time
    `synthesize_engineering_understanding` runs (see that function's own
    docstring) — every subsequent call refreshes this same graph rather
    than reseeding it."""
    templates = _SEED_TASK_TEMPLATES.get(strategy, _SEED_TASK_TEMPLATES[_DEFAULT_STRATEGY])
    return [InvestigationTask.model_validate(template) for template in templates]


# ---------------------------------------------------------------------------
# Task graph lifecycle — pure functions, no I/O, fully re-derivable from
# (tasks, assessments, contradictions) so the graph's evolution is
# reproducible and unit-testable without an LLM in the loop.
# ---------------------------------------------------------------------------


def _contradiction_task_id(description: str) -> str:
    digest = hashlib.sha256(description.encode("utf-8")).hexdigest()[:10]
    return f"resolve_contradiction_{digest}"


def refresh_task_graph(
    tasks: list[InvestigationTask],
    *,
    assessments: list[CapabilityAssessment],
    contradictions: list[Contradiction],
) -> list[InvestigationTask]:
    """Advance the graph one step: complete any task whose capability the
    ledger now considers satisfied (recording the *actual* information
    gain the completion produced — see `InvestigationTask`'s own
    docstring), and spawn a new task for any contradiction not already
    covered by one (contradictions are first-class investigation objects,
    not just a number added to a priority dict — see ADR 0016's own
    self-review of the coarser mechanism this supersedes for the graph-
    aware path).

    Idempotent: calling this twice with the same unresolved contradiction
    produces the same `resolve_contradiction_<hash>` task_id both times
    (`_contradiction_task_id` is a pure function of the contradiction's
    own description), so re-running it never duplicates work.
    """
    score_by_capability = {a.capability: a.score for a in assessments}
    satisfied_by_capability = {a.capability: a.satisfied for a in assessments}

    refreshed: list[InvestigationTask] = []
    for task in tasks:
        if task.status != "pending":
            refreshed.append(task)
            continue
        if task.required_capability and satisfied_by_capability.get(task.required_capability):
            score = score_by_capability.get(task.required_capability, task.confidence)
            refreshed.append(
                task.model_copy(
                    update={
                        "status": "done",
                        "confidence": score,
                        "actual_information_gain": round(score - task.confidence_at_creation, 4),
                    }
                )
            )
        else:
            refreshed.append(task)

    existing_ids = {t.task_id for t in refreshed}
    for contradiction in contradictions:
        if contradiction.resolved:
            continue
        task_id = _contradiction_task_id(contradiction.description)
        if task_id in existing_ids:
            continue
        refreshed.append(
            InvestigationTask(
                task_id=task_id,
                purpose=f"Resolve contradiction: {contradiction.description}",
                required_capability="architecture",
                expected_information_gain=0.6,
                confidence_at_creation=score_by_capability.get("architecture", 0.0),
                reason_for_creation=(
                    f"Unresolved contradiction from investigation: {contradiction.description}"
                ),
            )
        )
        existing_ids.add(task_id)

    return refreshed


def select_next_task(tasks: list[InvestigationTask]) -> InvestigationTask | None:
    """The single highest-value actionable task right now: pending, every
    dependency already done, ranked by expected information gain — the
    planner's own answer to "what is the most valuable thing I can learn
    next?" `None` means nothing is currently actionable (either the graph
    is genuinely exhausted, or every remaining task is blocked on a
    dependency that hasn't completed yet)."""
    done_ids = {t.task_id for t in tasks if t.status == "done"}
    ready = [
        t
        for t in tasks
        if t.status == "pending" and all(dep in done_ids for dep in t.dependencies)
    ]
    if not ready:
        return None
    return sorted(ready, key=lambda t: (-t.expected_information_gain, t.task_id))[0]


def priority_boost_from_tasks(tasks: list[InvestigationTask]) -> dict[str, float]:
    """The graph's own read on which capability to prefer next — the
    highest expected-information-gain among pending, dependency-satisfied
    tasks, per capability. Combined with `capability_priority`'s workspace-
    level read in `plan_priority_boost` below; this function alone answers
    only "what does the explicit task graph want", not the final boost."""
    done_ids = {t.task_id for t in tasks if t.status == "done"}
    boost: dict[str, float] = {}
    for task in tasks:
        if task.status != "pending" or not task.required_capability:
            continue
        if not all(dep in done_ids for dep in task.dependencies):
            continue
        gain = max(0.0, min(1.0, task.expected_information_gain))
        boost[task.required_capability] = max(boost.get(task.required_capability, 0.0), gain)
    return boost


def plan_priority_boost(
    workspace_priority: dict[str, float], tasks: list[InvestigationTask]
) -> dict[str, float]:
    """The final priority boost `engine._select` consults — the union of
    the workspace-level read (`reasoning.understanding.capability_priority`,
    computed by the caller and passed in as `workspace_priority` to avoid a
    circular import between this module and `understanding.py`) and the
    explicit task graph's own read, taking the max per capability. Neither
    signal is discarded in favor of the other: a task graph and an LLM's
    own gain estimate agreeing is not double-counted (max, not sum), but
    either one alone is enough to raise a capability's priority.
    """
    combined = dict(workspace_priority)
    for capability, gain in priority_boost_from_tasks(tasks).items():
        combined[capability] = max(combined.get(capability, 0.0), gain)
    return combined
