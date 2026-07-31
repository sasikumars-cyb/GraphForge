"""Pre-flight checks run before a stage's agent is invoked
(`RunCoordinator.execute_run`) — catching missing infrastructure before a
run starts rather than after it fails mid-execution.

**BLOCKING** (implemented): LLM provider credentials
(`check_llm_provider_configured`) and Neo4j reachability
(`check_neo4j_reachable`). The run/step are persisted `failed` and
`agent.run()` is never invoked.

**WARNING** (implemented, ADR 0011): `WARNING_CHECKS` +
`collect_preflight_warnings` — GitHub write availability for the git_ops
agents (`DEPENDENCY_GITHUB_WRITE`, PR3). Execution proceeds regardless; a
result is persisted via `record_preflight_warnings` (ADR 0011 OD-1) so a
degraded run is explainable. See `WARNING_CHECKS`'s own comment for why
Jira/Confluence are not (yet) in this registry.

Deliberately NOT implemented here, each for a distinct, evidence-based
reason — not an oversight:

- **PostgreSQL**: no check is possible or meaningful. By the time
  `RunCoordinator.execute_run`/`resume_step` runs, a `Run`/`AgentStep` row
  has already been flushed to Postgres in the same session (see
  `execute_run`'s own body) — reaching this point already proves Postgres
  is reachable. A redundant "ping Postgres" check here could never
  actually fire: if Postgres were down, the earlier flush would already
  have raised before any pre-flight code ran.

- **GitHub, the `github` ITool specifically**: grepped across `app/agents/`
  and `app/context_pipeline/` — zero agents call the `github` ITool via
  `ToolRegistry`. The agents that actually touch GitHub for writes (the
  git_ops agents: create_branch, commit_changes, create_pull_request,
  run_tests) do so through a different mechanism entirely
  (`GitHubConnection`, a per-user OAuth row) — see `_check_github_write_
  available` below, which checks *that* signal instead, not this tool's
  health.

- **Jira / Confluence**: real usage exists (`context_discovery`'s
  investigators and `planning`'s `confluence_context.py` both call these
  tools), but that usage is *request-conditional* — an investigator only
  calls Jira/Confluence when the request text actually references them,
  which can't be known before the agent's own investigation runs, and
  therefore can never be represented in `AgentManifest.required_dependencies`
  (ADR 0011 OD-3's own scope note). `WARNING_CHECKS`' applicability model
  (`agent_requires`, driven entirely by `required_dependencies`) has no way
  to gate a request-conditional dependency truthfully — adding one requires
  a second, request-content-aware applicability mechanism, a real, separate
  design decision, not something to invent silently inside this increment.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentManifest
from app.agents.llm import default_stage_for_agent
from app.ai.config.resolver import resolve
from app.core.exceptions import AppError
from app.models.agent_step import AgentStep
from app.services.github_service import get_decrypted_access_token
from app.tools.interfaces import ToolHealth
from app.tools.registry import get_tool_registry

# ---------------------------------------------------------------------------
# ADR 0011, OD-3 — dependency identifiers.
#
# The closed, centralized set of values `AgentManifest.required_dependencies`
# may contain. Centralized here rather than scattered as string literals
# across every `manifest.py` (there is no other natural home: this is the
# module every static-dependency pre-flight check already lives in, and
# `app.agents._contract` must stay dependency-free per its own docstring, so
# it cannot own these constants itself).
#
# Only the three dependencies this codebase can actually determine
# statically (agent identity / manifest, never request content — see ADR
# 0011's non-negotiable rule) get a constant. Jira and Confluence are
# deliberately absent: their usage is request-conditional, so no manifest
# could ever declare them truthfully — see that ADR's OD-3 resolution.
# ---------------------------------------------------------------------------

DEPENDENCY_LLM = "llm"
DEPENDENCY_NEO4J = "neo4j"
DEPENDENCY_GITHUB_WRITE = "github_write"

ALL_DEPENDENCIES: frozenset[str] = frozenset(
    {DEPENDENCY_LLM, DEPENDENCY_NEO4J, DEPENDENCY_GITHUB_WRITE}
)


def agent_requires(manifest: AgentManifest, dependency: str) -> bool:
    """Generic dependency-membership test (ADR 0011, OD-3) — the data-driven
    replacement a *future* check's `applies_to` predicate uses instead of
    hardcoding its own bespoke signal (e.g. today's `max_graph_hops > 0` for
    Neo4j, `default_stage_for_agent(...) is not None` for the LLM — both
    still in place, unaffected by this function; see this module's
    docstring). Trivial by design: the value of OD-3 is having exactly one
    place a new check's applicability logic goes, not in this function's own
    complexity.
    """
    return dependency in manifest.required_dependencies


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
    is empty (the common case: every applicable check passing, or an agent
    with no WARNING-checked dependency at all — see `WARNING_CHECKS` below).

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


# ---------------------------------------------------------------------------
# ADR 0011 — WARNING-producing checks.
#
# Only one exists: GitHub write availability, for the git_ops agents
# (`DEPENDENCY_GITHUB_WRITE`). This is the sole dependency that is both (a)
# already represented in `AgentManifest.required_dependencies` (ADR 0011
# OD-3) and (b) statically determinable per-agent, which is what makes a
# `required_dependencies`-driven check possible at all.
#
# Jira and Confluence are deliberately still absent, not an oversight:
# their usage is *request-conditional* (an investigator only calls either
# when the request text actually references it — see this module's own
# docstring above), which is exactly what OD-3's own scope note excludes
# from `required_dependencies` ("Jira and Confluence stay request-content-
# conditional... and are never represented here"). There is no manifest
# field this check registry could key off of for either without either
# violating that decision or inventing a second, request-content-aware
# applicability mechanism alongside this one — both out of scope here; see
# this module's own docstring and ADR 0011 for the full reasoning. Adding
# either is a real, separate design decision for a future PR, not an
# extension of this registry.
# ---------------------------------------------------------------------------


async def _check_github_write_available(
    db: AsyncSession, user_id: object
) -> PreflightWarning | None:
    """`None` when `user_id` has a usable GitHub connection, otherwise a
    `PreflightWarning`. Deliberately a connection-*presence* check — the
    same signal `create_branch_agent`'s own existing guard already uses
    (`get_decrypted_access_token(...) is not None`) — not a live GitHub API
    reachability probe. A live probe is a real option (see ADR 0011's OD-4,
    still open) but adding one here would be deciding OD-4 unilaterally
    inside this PR; presence-only keeps this check's cost identical to the
    agent's own existing guard (one Postgres read, no network I/O) while
    still moving the same signal earlier, before any GitHub-writing work
    starts.
    """
    token = await get_decrypted_access_token(db, user_id)  # type: ignore[arg-type]
    if token:
        return None
    return PreflightWarning(
        code="github_write_available",
        dependency="GitHub",
        message=("No GitHub connection found. Connect GitHub before running execution workflows."),
        checked_at=datetime.now(UTC).isoformat(),
    )


@dataclass(frozen=True)
class WarningCheck:
    """One entry in `WARNING_CHECKS` — the same declarative-tuple shape
    this codebase already uses in three other places (`CROSS_REPO_LINK_RULES`,
    `LEDGER_RESYNC_HOOKS`, the `ProviderSpec` registry; see ADR 0011's own
    Framework section, which names all three as the precedent). Adding a
    future WARNING check (once Jira/Confluence's applicability question is
    separately resolved) is one more tuple entry, not a change to
    `collect_preflight_warnings` or to `RunCoordinator`.
    """

    dependency: str  # one of ALL_DEPENDENCIES — gates via agent_requires()
    run: Callable[[AsyncSession, object], Awaitable[PreflightWarning | None]]


WARNING_CHECKS: tuple[WarningCheck, ...] = (
    WarningCheck(dependency=DEPENDENCY_GITHUB_WRITE, run=_check_github_write_available),
)


async def collect_preflight_warnings(
    manifest: AgentManifest, db: AsyncSession, user_id: object
) -> list[PreflightWarning]:
    """Run every `WARNING_CHECKS` entry applicable to `manifest` (via
    `agent_requires` — ADR 0011 OD-3's generic membership test) and return
    whatever warnings they produce, in registry order. Never raises for an
    unavailable dependency — that is the entire point of WARNING severity;
    only a genuine bug in a check itself would propagate, exactly as a bug
    in `check_llm_provider_configured`/`check_neo4j_reachable` already can
    today (see `RunCoordinator`'s own comment on why pre-flight runs inside
    the same `try` block as `agent.run()`).

    Independent per check: one check's result never affects whether another
    runs, and the order checks appear in `WARNING_CHECKS` is the order
    warnings are returned in (and, via `record_preflight_warnings`, the
    order they're persisted in).
    """
    warnings: list[PreflightWarning] = []
    for check in WARNING_CHECKS:
        if not agent_requires(manifest, check.dependency):
            continue
        warning = await check.run(db, user_id)
        if warning is not None:
            warnings.append(warning)
    return warnings
