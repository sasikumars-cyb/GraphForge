"""Artifact reader — extract structured results from prior workflow stages.

Used by deterministic execution agents (create_branch, commit_changes)
that need structured data (not text summaries) from prior stage results.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def get_stage_result(workflow: HasRuns, stage: str) -> dict[str, Any] | None:
    """Return the result dict from the most recent completed run for
    `stage`, or None if no completed run exists for that stage.

    The result is stored in AgentStep.result (a JSONB column) — the same
    dict that `_summarize_previous_output` reads from in text mode.
    """
    for run in sorted(workflow.runs, key=lambda r: r.created_at, reverse=True):
        if run.workflow_stage == stage and run.status == "completed":
            step = run.steps[0] if run.steps else None
            if step and step.result:
                return dict(step.result)
    return None
