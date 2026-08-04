"""Frozen Agent Contract — GraphForge Agent Framework.

Every agent, the Orchestrator (registry/selector/run_coordinator), and the
API router (`agent-runs`) build against the types defined here. This file
is the single source of truth for those shared shapes.

Rule: this file MUST NOT import from app.agents.* (individual agents) or
app.orchestrator.* (implementations). It is deliberately dependency-free
within the new packages — only stdlib and Pydantic.

Matches API_CONTRACTS.md DTOs (Subject, Evidence, Confidence) verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Shared DTOs — field names match API_CONTRACTS.md exactly
# ---------------------------------------------------------------------------


class Subject(BaseModel):
    """Canonical resolved entry point: one or more graph node IDs plus a
    human-readable display name. Subject is the unit of work for every
    agent run."""

    subject_id: str
    subject_type: str  # "pull_request" | "freetext" | "story" | ...
    graph_node_ids: list[str] = Field(default_factory=list)
    display_name: str = ""


class Evidence(BaseModel):
    """Formalised tool observation — the code-level enforcement of
    "evidence over assertion" (PRODUCT_VISION.md).

    At least one entry per AgentOutput must be kind="graph_traversal" or
    kind="tool_call". An output with only "llm_reasoning" entries fails
    the Planning Agent's Definition of Done.
    """

    # "human_input" records something a person told the agent. It is
    # deliberately its own kind: a human answer is not a tool call, not a graph
    # fact, and not the model's own reasoning, and filing it under any of those
    # would misrepresent where the claim came from in the one place the product
    # promises provenance. It never satisfies the tool/graph requirement below.
    kind: Literal["graph_traversal", "tool_call", "graph_fact", "llm_reasoning", "human_input"]
    reference: str  # graph node id, tool name, or fact id
    summary: str
    # Optional, additive: what actually happened, distinct from `kind` (which
    # only says what *category* of evidence this is). Added so a UI can tell
    # "the provider was reached and found nothing relevant" apart from
    # "the provider isn't configured" apart from "the call failed" without
    # parsing `summary`'s free text — see `to_evidence()` (planning/tools.py)
    # and ConfluenceProvider (context_pipeline/providers.py) for where this
    # is populated. None for older/other evidence that predates this field
    # or never needed the distinction (e.g. graph traversals).
    status: Literal["success", "not_found", "unavailable", "failed"] | None = None


class ComponentWarning(BaseModel):
    """A structured verification finding — distinct from the free-text
    `verification_warnings: list[str]` every planning-family agent already
    returns (kept, unchanged, for backward compatibility). This is the
    pydantic mirror of `app.agents.component_grounding.ComponentWarning`
    (a plain dataclass, so that module has no pydantic/schema dependency);
    each agent converts to this type when populating its own result.

    `warning_type` lets a caller (the UI, a future automated gate) branch
    on the *kind* of problem without parsing prose — today's producers:
    "test_used_as_production" (see app.agents.component_grounding),
    "nonexistent_file", "nonexistent_class", "nonexistent_method"
    (see each agent's post-generation verification section).
    """

    claim: str
    warning_type: str
    message: str
    suggested_replacement: str | None = None


class Confidence(BaseModel):
    """Numeric confidence in [0.0, 1.0] with a mandatory reasoning
    string. Mirrors the existing ConfidenceScore but is the contract-level
    type so agents don't accidentally import from app.ai.schemas."""

    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class AgentOutput(BaseModel):
    """Uniform envelope wrapping every agent's result. The Orchestrator,
    Shared Memory, and the Agents UI handle any agent's output generically
    through this shape — no type switch per agent.

    `result` holds the agent-specific payload (a plain dict for
    flexibility; strongly-typed agents cast it themselves).
    `graph_facts_written` lists fact ids for traceability (populated after
    GraphWriter integration; empty is valid for Phase 1).
    """

    agent_id: str
    subject_id: str
    confidence: Confidence
    evidence: list[Evidence]
    result: dict[str, Any] = Field(default_factory=dict)
    graph_facts_written: list[str] = Field(default_factory=list)
    prompt_version: str = "1.0"
    output_ref: str | None = None

    # Additive pause/resume signal: an agent sets `awaiting_input=True` (with
    # `pending_question` describing what it needs) instead of completing when
    # it has a genuine blocking uncertainty it wants a human to resolve before
    # continuing. `result` still carries whatever resumable state the agent
    # needs to pick up where it left off — see RunCoordinator._apply_agent_output
    # and RunCoordinator.resume_step. Every agent that never sets this behaves
    # exactly as before: default False/None, always the "completed" path.
    awaiting_input: bool = False
    pending_question: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Agent Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentManifest:
    """Static declaration of what an agent does and what it accepts.

    Registered once per agent in app/agents/<agent>/manifest.py and
    stored in the Registry at startup. The Selector uses `goals` to route
    an orchestrator run to the right agent.
    """

    agent_id: str
    purpose: str  # one sentence, shown in Agents UI
    goals: frozenset[str]  # e.g. frozenset({"review_pr"})
    accepted_subject_types: frozenset[str]  # e.g. frozenset({"pull_request"})
    cost_class: Literal["cheap", "standard", "expensive"]
    max_graph_hops: int = 2
    output_schema_name: str = "AgentOutput"

    # ADR 0011, OD-3 — the statically-determinable infrastructure this agent
    # depends on, e.g. frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J})
    # (see app.orchestrator.preflight for the closed set of valid values,
    # DEPENDENCY_LLM/DEPENDENCY_NEO4J/DEPENDENCY_GITHUB_WRITE). Not imported
    # here — this module must stay dependency-free (see the file docstring)
    # — so the value is a plain string, validated by
    # app.orchestrator.preflight.agent_requires and by the manifest-registry
    # test suite, not by this dataclass itself.
    #
    # Generalizes what `max_graph_hops > 0` and `default_stage_for_agent(...)
    # is not None` already imply for Neo4j and the LLM respectively: a single
    # declarative field a *future* pre-flight check's `applies_to` can test
    # membership against (`DEPENDENCY_GITHUB_WRITE in manifest.
    # required_dependencies`, exactly what `app.orchestrator.preflight.
    # WARNING_CHECKS` already does for the git_ops agents) instead of
    # hardcoding its own bespoke signal. Deliberately does not
    # replace `max_graph_hops` (which also drives the graph hop *budget*, a
    # different concern) or `default_stage_for_agent` (which resolves *which*
    # AI stage config to use, not just whether one is needed) — both of
    # those checks are already implemented and unaffected by this field; see
    # ADR 0011 OD-3 for the full reasoning. Default empty: an agent with no
    # statically-determinable dependency (none exist yet beyond LLM/Neo4j/
    # GitHub-write) declares nothing, which is also what every agent
    # declared, implicitly, before this field existed.
    required_dependencies: frozenset[str] = frozenset()

    # KAN-28 — declares that this agent takes an action visible outside
    # GraphForge (posting to GitHub/Jira/etc., as opposed to reading from
    # or reasoning about them) and therefore must only ever run with a
    # context that traces back to an explicit human authorization, not as
    # an implicit side effect of the agent simply being invoked (see
    # `ARCHITECTURE.md` § Security Considerations). Purely declarative —
    # this field does not itself enforce anything; it makes the property
    # enumerable (`[m for m in registry.all_manifests() if m.
    # requires_external_write_authorization]` is the "full inventory" this
    # was introduced for) and gives
    # `tests/unit/ai/test_manifest_dependency_integrity.py` something to
    # check every `DEPENDENCY_GITHUB_WRITE` manifest against, so a future
    # write-capable agent can't add the dependency without also declaring
    # the authorization property. Default False: every agent that reads
    # or reasons only (the overwhelming majority) declares nothing, same
    # as before this field existed.
    requires_external_write_authorization: bool = False

    def __post_init__(self) -> None:
        if not self.agent_id:
            raise ValueError("agent_id must be non-empty")
        if not self.goals:
            raise ValueError("goals must be non-empty")


# ---------------------------------------------------------------------------
# AgentContext — the input handed to an agent's run() method
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Everything an agent needs to start a run: the resolved subject,
    optional LLM model override, and a free-form extras dict for any
    agent-specific inputs that don't fit the typed fields.

    Phase 1: context assembly is minimal — agents query the graph
    themselves using the tools wired at construction time.
    """

    subject: Subject
    goal: str
    model: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocols — frozen interface signatures (no bodies)
# ---------------------------------------------------------------------------


@runtime_checkable
class IAgent(Protocol):
    """The one method every agent must implement."""

    async def run(self, context: AgentContext) -> AgentOutput:  # pragma: no cover
        ...


class IRegistry(Protocol):
    """Maps agent_id → (manifest, agent instance)."""

    def register(self, manifest: AgentManifest, agent: IAgent) -> None:  # pragma: no cover
        ...

    def get(self, agent_id: str) -> tuple[AgentManifest, IAgent] | None:  # pragma: no cover
        ...

    def all_manifests(self) -> list[AgentManifest]:  # pragma: no cover
        ...


class ISelector(Protocol):
    """Maps a goal string to an agent_id."""

    def select(self, goal: str) -> str:  # pragma: no cover
        ...


class IRunCoordinator(Protocol):
    """Executes a selected agent for a subject+goal and persists the run."""

    async def execute(
        self, subject: Subject, goal: str, model: str | None = None
    ) -> Any:  # pragma: no cover
        ...
