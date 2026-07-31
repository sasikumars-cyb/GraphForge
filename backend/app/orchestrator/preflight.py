"""Pre-flight checks run before a stage's agent is invoked
(`RunCoordinator.execute_run`) — catching missing infrastructure before a
run starts rather than after it fails mid-execution.

Implemented here: LLM provider credentials (`check_llm_provider_configured`)
and Neo4j reachability (`check_neo4j_reachable`), both blocking.

Deliberately NOT implemented here, each for a distinct, evidence-based
reason — not an oversight:

- **PostgreSQL**: no check is possible or meaningful. By the time
  `RunCoordinator.execute_run`/`resume_step` runs, a `Run`/`AgentStep` row
  has already been flushed to Postgres in the same session (see
  `execute_run`'s own body) — reaching this point already proves Postgres
  is reachable. A redundant "ping Postgres" check here could never
  actually fire: if Postgres were down, the earlier flush would already
  have raised before any pre-flight code ran.

- **GitHub**: grepped across `app/agents/` and `app/context_pipeline/` —
  zero agents call the `github` ITool via `ToolRegistry`. The agents that
  actually touch GitHub (the git_ops agents: create_branch, commit_changes,
  create_pull_request) do so through a different mechanism entirely
  (`GitHubConnection`, a per-user OAuth row), not the `github` tool this
  module could check. Checking the `github` tool's health here would test
  a signal those agents don't actually depend on — worse than no check.

- **Jira / Confluence**: real usage exists (`context_discovery`'s
  investigators and `planning`'s `confluence_context.py` both call these
  tools), but that usage is *request-conditional* — an investigator only
  calls Jira/Confluence when the request text actually references them,
  which can't be known before the agent's own investigation runs. A
  *blocking* pre-flight check here would false-positive-block work that
  never needed either tool this run. The original design (Part 3 of the
  redesign request) explicitly separates "Blocking failures" from
  "Warnings" — Jira/Confluence reachability belongs in the latter
  category, which does not exist yet in this module's plumbing (today's
  checks are binary: `None` or a blocking failure string). Adding a
  non-blocking warning channel is a small but real, separate design
  decision, not something to invent silently inside this increment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.llm import default_stage_for_agent
from app.ai.config.resolver import resolve
from app.core.exceptions import AppError
from app.models.agent_step import AgentStep
from app.tools.interfaces import ToolHealth
from app.tools.registry import get_tool_registry


class PreFlightCheckFailed(AppError):
    """Raised by `RunCoordinator.execute_run` when a pre-flight check fails
    — the run/step are already persisted as `failed` (same as any other
    execution failure) by the time this is raised; it exists so callers
    can distinguish a pre-flight rejection from a mid-execution one if they
    need to (e.g. to render a different message), the same way
    `GraphHopBudgetExceeded` lets a caller distinguish that failure mode."""

    status_code = 422
    error_code = "preflight_check_failed"


def resolve_preflight_stage(agent_id: str, workflow_stage: str | None) -> str | None:
    """The stage key this run's LLM call will resolve AI configuration
    under — mirrors `app.agents.llm.stage_for`'s own precedence exactly
    (the real `workflow_stage` first, then the agent's own default), so
    this check asks the identical question the agent's own call will ask.

    `None` when the agent makes no LLM call at all (the deterministic
    git_ops agents — see `default_stage_for_agent`) or for an unrecognized
    `agent_id` with no `workflow_stage` either — nothing to check.
    """
    return workflow_stage or default_stage_for_agent(agent_id)


def check_llm_provider_configured(agent_id: str, workflow_stage: str | None) -> str | None:
    """`None` when the LLM provider that would serve this run's stage has
    credentials configured (or needs none — e.g. Bedrock's IAM-role auth),
    otherwise a human-readable failure reason.

    Never raises and never performs network I/O — `resolve()` reads only
    the local configuration snapshot, the same one the agent's own call
    would read moments later.
    """
    stage = resolve_preflight_stage(agent_id, workflow_stage)
    if stage is None:
        return None

    resolved = resolve(stage=stage)
    if resolved.spec.requires_api_key and not resolved.config.api_key:
        return (
            f"No API key is configured for the '{resolved.spec.label}' provider "
            f"(stage '{stage}'). Configure a provider in AI Settings before "
            "starting this workflow stage."
        )
    return None


async def check_neo4j_reachable(max_graph_hops: int) -> str | None:
    """`None` when this agent never reads the graph at all (`max_graph_hops
    <= 0` — the same per-agent signal `app.graph.hop_budget` already
    enforces at call time, see its own module docstring for the full
    per-agent table), or when Neo4j is confirmed live via the same real
    connectivity probe the Tools admin UI already uses
    (`Neo4jGraphTool.health_check()` — a real `RETURN 1` query, not a
    credentials-only check; reached here through `ToolRegistry.check_health`,
    which never raises). A human-readable failure reason otherwise.

    Deliberately reuses the existing tool-health infrastructure rather than
    opening a second Neo4j connectivity path — see this module's own
    docstring for why GitHub/Jira/Confluence aren't checked here yet, and
    why PostgreSQL needs no check of its own at all.
    """
    if max_graph_hops <= 0:
        return None

    health = await get_tool_registry().check_health("neo4j_graph")
    if health != ToolHealth.HEALTHY:
        return (
            f"The knowledge graph (Neo4j) is not reachable (status: "
            f"'{health.value}'). This stage needs to query it."
        )
    return None


@dataclass(frozen=True)
class PreflightWarning:
    """A WARNING-severity pre-flight result (ADR 0011, OD-1) — the
    non-blocking counterpart to the `str | None` failure reason the two
    BLOCKING checks above return. Persisted verbatim (as
    `{code, dependency, message, checked_at}`) via
    `record_preflight_warnings`; no additional fields are invented here —
    this is exactly the shape ADR 0011 decided on, no more.

    `code` must be the producing check's own stable identifier (never
    derived from `message`, which is free text and may be reworded).
    `checked_at` is an ISO 8601 timestamp, set by the caller at the moment
    the check ran.

    Nothing in this codebase constructs one of these yet — no WARNING-
    severity check exists (Jira/Confluence/GitHub reachability checks are
    future work, out of scope for ADR 0011 OD-1). This type and
    `record_preflight_warnings` are the persistence mechanism a future
    check wires into; they do not themselves decide when a warning fires.
    """

    code: str
    dependency: str
    message: str
    checked_at: str


def record_preflight_warnings(step: AgentStep, warnings: Sequence[PreflightWarning]) -> None:
    """Append `warnings` to `step.preflight_warnings`, in the order given,
    without discarding whatever was already there. A no-op when `warnings`
    is empty (the case for every run today, since no WARNING-producing
    check exists yet).

    Reassigns the whole list rather than calling `.append()` on the
    existing one in place: SQLAlchemy's JSON column type does not track
    in-place mutation of the Python list/dict it deserializes to, so an
    in-place `.append()` would silently never be flushed. Reassignment is
    what makes the column dirty and included in the next flush.

    Does not flush or commit — RunCoordinator remains the sole transaction
    owner (ADR 0011's Framework section, and the same discipline ADR 0012's
    `persist_llm_invocation` already follows); this function only mutates
    the in-session object graph.
    """
    if not warnings:
        return
    step.preflight_warnings = [
        # `or []`: guards a not-yet-flushed AgentStep, where the column
        # default hasn't been applied yet and the attribute reads `None`
        # (the same guard `_step_response` already applies to `evidence`
        # for the identical reason).
        *(step.preflight_warnings or []),
        *(
            {
                "code": w.code,
                "dependency": w.dependency,
                "message": w.message,
                "checked_at": w.checked_at,
            }
            for w in warnings
        ),
    ]
