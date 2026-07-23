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

    kind: Literal["graph_traversal", "tool_call", "graph_fact", "llm_reasoning"]
    reference: str  # graph node id, tool name, or fact id
    summary: str


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
# Protocols — frozen interface signatures (no bodies, per TEAM_EXECUTION_PLAN.md)
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

    async def execute(self, subject: Subject, goal: str, model: str | None = None) -> Any:  # pragma: no cover
        ...
