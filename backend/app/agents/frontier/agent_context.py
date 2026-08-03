"""Typed accessors over the existing, frozen `app.agents._contract.AgentContext`.

Deliberately NOT a new context type — `AgentContext` (subject/goal/model/
extras) is the one shape `IAgent.run()` accepts and `RunCoordinator`
constructs directly; a second `AgentContext` class here would either
collide by name or become a shadow object every agent has to convert
to/from. These functions read the same `extras` dict `RunCoordinator`
already populates (`app.orchestrator.run_coordinator`: `db`, `user_id`,
`stage`, `run_id`, `agent_step_id`, and — only when the agent's manifest
declares `max_graph_hops > 0` — a pre-built, hop-budgeted
`graph_repository`), just without every agent re-typing the same
`context.extras["..."]` access.

Note on scope: the platform has no `Organization` model and no
`branch`/`change` extras key today — only `repository_id` (derived from
`context.subject.subject_id`, the same `"repo:<uuid>"` convention
`app.agents.documentation_health` uses) and `workflow` (optional,
workflow-stage runs only) are real. This module exposes what exists; it
does not add placeholder fields for what doesn't.
"""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext
from app.core.exceptions import NotFoundError
from app.graph.interfaces import IGraphRepository


def get_db(context: AgentContext) -> AsyncSession:
    return cast("AsyncSession", context.extras["db"])


def get_user_id(context: AgentContext) -> uuid.UUID | None:
    return context.extras.get("user_id")


def get_graph_repository(context: AgentContext) -> IGraphRepository | None:
    """`None` when the agent's manifest declares `max_graph_hops=0` — the
    same "no Neo4j dependency" signal `documentation_health`'s manifest
    uses; a caller that needs graph access must declare a nonzero
    `max_graph_hops` for `graph_repository` to be injected at all."""
    return context.extras.get("graph_repository")


def get_stage(context: AgentContext, default: str) -> str:
    from app.agents.llm import stage_for

    return stage_for(context.extras, default)


def get_repository_id(context: AgentContext) -> uuid.UUID:
    """`Subject.subject_id` in the `"repo:<uuid>"` form every repository-
    scoped Workspace agent uses (see
    `app.agents.documentation_health.agent.resolve_repository_subject`)."""
    subject_id = context.subject.subject_id
    if not subject_id.startswith("repo:"):
        raise NotFoundError(f"Expected subject_id 'repo:<uuid>', got '{subject_id}'.")
    raw = subject_id[len("repo:") :]
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(
            f"subject_id '{subject_id}' does not contain a valid UUID after 'repo:': {exc}"
        ) from exc


def get_workflow(context: AgentContext) -> object | None:
    """The run's `Workflow`, when this agent was invoked as a workflow
    stage — `None` for a standalone Workspace run (every Engineering
    Intelligence agent's expected mode). Typed `object` rather than
    importing `app.models.workflow.Workflow` here to avoid this small,
    frequently-imported accessor module pulling in the ORM model graph;
    callers that need the real type import it themselves and narrow."""
    return context.extras.get("workflow")
