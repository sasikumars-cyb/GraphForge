"""Artifact reader — extract structured results from prior workflow stages.

Used by deterministic execution agents (create_branch, commit_changes)
that need structured data (not text summaries) from prior stage results.
"""

from __future__ import annotations

from typing import Any

from app.models.workflow import Workflow


def get_stage_result(workflow: Workflow, stage: str) -> dict[str, Any] | None:
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
