"""Artifact reader — extract structured results from prior workflow stages.

Used by deterministic execution agents (create_branch, commit_changes)
that need structured data (not text summaries) from prior stage results.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class _HasStepResult(Protocol):
    @property
    def result(self) -> dict[str, Any]: ...


@runtime_checkable
class _HasStageRun(Protocol):
    @property
    def workflow_stage(self) -> str | None: ...
    @property
    def status(self) -> str: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def steps(self) -> Sequence[_HasStepResult]: ...


@runtime_checkable
class HasRuns(Protocol):
    """The only shape `get_stage_result()` actually needs — deliberately
    narrower than the real `Workflow` model (which this function's
    parameter used to be typed as). Structural, not nominal: a real
    `Workflow` still satisfies this with no changes, and so does any
    lightweight stand-in that only ever needs to provide `.runs` (see
    `app.api.v1.routers.agent_runs._StandalonePlanningContext`, which
    predates this Protocol and needs no changes to keep satisfying it).
    Static type-checking is what's added here, not a new runtime check —
    `runtime_checkable` is for `isinstance()` if a caller ever wants it,
    not something this function itself uses.

    Every attribute here (including on `_HasStageRun`/`_HasStepResult`
    above) is declared as a read-only `@property`, not a plain variable —
    mypy treats a Protocol's plain data attributes as read-write and
    therefore invariant, which rejects a real `list[Run]`/`dict[str, Any]`
    against this Protocol even where `Run`/`AgentStep` structurally match,
    because a mutable attribute isn't safely substitutable that way. A
    read-only `@property` is covariant, which is all this function
    actually needs (it only ever reads) and is what makes a real
    `Workflow`/`Run`/`AgentStep` type-check against this Protocol without
    errors at every existing `get_stage_result()` call site.
    """

    @property
    def runs(self) -> Sequence[_HasStageRun]: ...


def _find_latest_completed_step(workflow: HasRuns, stage: str) -> _HasStepResult | None:
    """The single place that decides "which step counts" for a stage —
    both `get_stage_result()` and `get_stage_step_data()` below call this,
    so the two can never disagree about which run/step a stage's data
    comes from."""
    for run in sorted(workflow.runs, key=lambda r: r.created_at, reverse=True):
        if run.workflow_stage == stage and run.status == "completed":
            step = run.steps[0] if run.steps else None
            if step and step.result:
                return step
    return None


def get_stage_result(workflow: HasRuns, stage: str) -> dict[str, Any] | None:
    """Return the *effective* result dict for the most recent completed
    run of `stage`, or None if no completed run exists for that stage.

    The base result is stored in AgentStep.result (a JSONB column) — the
    same dict that `_summarize_previous_output` reads from in text mode.
    "Effective" means: if a human overrode part of this stage's output
    (see the Context Explorer / human-override design), the override is
    merged on top of the base result here, once, so every caller of this
    function automatically sees the corrected view without needing to
    know overrides exist at all. The base `result` itself is never
    mutated — it stays exactly what the agent produced, which is what
    keeps confidence calibration (app.models.confidence_calibration)
    checking a real AI output against the human decision, not an edited
    one. `human_override` is read via `getattr` with a default of None,
    not added to `_HasStepResult`'s Protocol, so structural test fakes
    that predate this field keep satisfying the Protocol unchanged.
    """
    step = _find_latest_completed_step(workflow, stage)
    if step is None:
        return None
    override = getattr(step, "human_override", None)
    if override:
        return {**step.result, **override}
    return dict(step.result)


@dataclass(frozen=True)
class StageStepData:
    """The full picture Report V2's data plumbing needs for one stage —
    `result` (same effective, override-merged dict `get_stage_result()`
    returns), plus its sibling `evidence`/confidence columns that no
    existing reader has asked for before this. See app.agents.
    report_generation.data_plumbing for what normalizes this into
    ReportViewModel-shaped values; this dataclass only carries the raw
    columns, unchanged, so the normalization layer's own tests can
    construct one by hand without touching the database."""

    result: dict[str, Any]
    evidence: list[dict[str, Any]]
    confidence_score: float | None
    confidence_reasoning: str | None


def get_stage_step_data(workflow: HasRuns, stage: str) -> StageStepData | None:
    """Like `get_stage_result()`, but returns the full `StageStepData`
    (result + evidence + confidence) instead of just the result dict.

    Uses the identical step-selection logic as `get_stage_result()` (via
    `_find_latest_completed_step`) so the two functions can never select a
    different step for the same (workflow, stage) pair — a caller that
    needs both a stage's result and its evidence/confidence is guaranteed
    to see them as they existed together on the same persisted row, not
    stitched from two different runs.

    `evidence`/`confidence_score`/`confidence_reasoning` are read via
    `getattr` with conservative defaults (`[]`/`None`/`None`), the same
    defensive pattern `get_stage_result()` already uses for
    `human_override` — a structural test fake that only ever implemented
    `_HasStepResult` (pre-dating this function) still works here without
    modification, it just reports empty evidence and no confidence rather
    than raising.
    """
    step = _find_latest_completed_step(workflow, stage)
    if step is None:
        return None
    override = getattr(step, "human_override", None)
    result = {**step.result, **override} if override else dict(step.result)
    return StageStepData(
        result=result,
        evidence=list(getattr(step, "evidence", None) or []),
        confidence_score=getattr(step, "confidence_score", None),
        confidence_reasoning=getattr(step, "confidence_reasoning", None),
    )
