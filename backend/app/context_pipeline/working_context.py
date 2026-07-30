"""`WorkingContext` — the mutable state the Context Discovery reasoning loop
builds up progressively, instead of retrieving everything and dumping it
downstream (see `reasoning_loop.run_discovery_loop`).

Organized into four sections so each has one job:

- `metadata`: what is this discovery run *about* (goal, work item, which
  iteration/round it's on).
- `knowledge`: what has been *gathered* — entities, repositories,
  architecture, the graph's own availability/data state.
- `reasoning`: what the loop has *concluded* — capability-specific
  confidence, assumptions, blocking issues, readiness.
- `compatibility`: the exact field names Planning already reads via
  `get_stage_result()` (`original_request`, `enriched_text`, ...) — kept
  separate and untouched so persisting this object never changes what
  downstream stages see.

This is what gets persisted into `AgentStep.result` both while discovery is
still in progress (paused, `status="awaiting_input"`) and once it finishes
(`status="completed"`). See `app.agents.context_discovery.agent` for the
flatten/reconstruct functions that translate between this nested shape and
the flat `ContextDiscoveryResult` schema actually written to the database
(kept flat for backward compatibility with existing consumers).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Readiness = Literal["READY", "PARTIAL", "BLOCKED"]
IssueSeverity = Literal["blocking", "warning"]


class ClarificationQuestion(BaseModel):
    """One question the reasoning loop wants a human to answer before it can
    proceed. `why` is mandatory — the loop must always explain why it's
    asking, not just what. `options` is empty for a free-text answer."""

    question_id: str
    question: str
    why: str
    options: list[str] = Field(default_factory=list)


class BlockingIssue(BaseModel):
    """A single uncertainty the reasoning loop found — the one generic shape
    every kind of gap is represented as (repository ambiguity, a missing
    Jira reference, an unavailable Confluence, a graph that never came up,
    a future "missing permissions" check, ...). No special-casing per
    issue type anywhere else in the codebase: readiness evaluation and the
    Discovery Summary both just iterate this list.

    `severity="blocking"` means this issue alone can hold readiness at
    BLOCKED and produces a `clarification_question` worth pausing for.
    `severity="warning"` is informational — shown to the user, factored
    into confidence, but never pauses the loop (e.g. "Confluence isn't
    connected" is worth surfacing, not worth stopping for).
    """

    issue_id: str
    type: str
    severity: IssueSeverity
    message: str
    reason: str
    recommended_action: list[str] = Field(default_factory=list)
    clarification_question: ClarificationQuestion | None = None
    resolved: bool = False


class CapabilityConfidence(BaseModel):
    """Confidence broken out per capability rather than one opaque number —
    a 0.42 on `implementation_candidates` alongside a 1.0 on `work_item`
    says something an averaged 0.71 never could. `overall()` is the one
    place a single number gets derived from these, for the UI/telemetry
    surfaces that still want one (AgentOutput.confidence.score, the legacy
    persisted `confidence` field) — never the other way around."""

    work_item: float = 0.0
    repository: float = 0.0
    architecture: float = 0.0
    implementation_candidates: float = 0.0
    documentation: float = 0.0

    def overall(self) -> float:
        values = [
            self.work_item,
            self.repository,
            self.architecture,
            self.implementation_candidates,
            self.documentation,
        ]
        return sum(values) / len(values)


class CapabilityCheck(BaseModel):
    """One policy check readiness evaluation runs — see
    `reasoning_loop.evaluate_readiness`. `severity="required"` checks must
    all pass for READY; `severity="recommended"` checks are shown as
    warnings but never by themselves prevent READY (matches the Discovery
    Summary example: "14 implementation candidates" plus "Confluence
    unavailable" can still be Readiness: READY)."""

    capability: str
    label: str
    satisfied: bool
    severity: Literal["required", "recommended"]
    detail: str = ""


class ContextMetadata(BaseModel):
    goal: str = ""
    work_item: str | None = None
    workflow_type: str = "context_discovery"
    iteration: int = 0
    clarification_rounds: int = 0


class GraphKnowledge(BaseModel):
    """The Knowledge Graph's own availability/data state — kept apart from
    `architecture`'s components/topics because "did the graph even
    respond" and "what did it contain" answer two different questions the
    readiness/confidence checks each ask separately."""

    available: bool = False
    has_data: bool = False
    context_text: str = ""


class Knowledge(BaseModel):
    """Everything gathered from the existing Jira/Confluence/GitHub/Graph
    providers — unchanged retrieval, just organized here rather than
    flattened into the top-level object."""

    entities: list[dict] = Field(default_factory=list)
    repositories: list[dict] = Field(default_factory=list)
    architecture: dict = Field(default_factory=dict)  # {"components": [...], "topics": [...]}
    implementation_candidates: list[str] = Field(default_factory=list)
    resolved_sources: list[dict] = Field(default_factory=list)
    graph: GraphKnowledge = Field(default_factory=GraphKnowledge)


class Reasoning(BaseModel):
    """What the loop has concluded from `Knowledge` — confidence, blocking
    issues, and the readiness verdict they produce."""

    assumptions: list[str] = Field(default_factory=list)
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)
    user_answers: dict[str, str] = Field(default_factory=dict)
    confidence: CapabilityConfidence = Field(default_factory=CapabilityConfidence)
    readiness: Readiness = "PARTIAL"
    checks: list[CapabilityCheck] = Field(default_factory=list)
    # Set once MAX_CLARIFICATION_ROUNDS is reached with a blocking issue
    # still unresolved (see reasoning_loop.resume_discovery) — readiness
    # stays BLOCKED (accurate: something is still unresolved) but the loop
    # stops asking further questions, so a paused run must check this
    # alongside readiness rather than readiness alone (see
    # DiscoveryLoopResult.paused/pending_question).
    exhausted: bool = False

    def next_blocking_issue(self) -> BlockingIssue | None:
        """The single highest-value unresolved blocking issue to ask about
        next — the first one still unresolved, in the order it was raised
        (detection appends in priority order)."""
        for issue in self.blocking_issues:
            if issue.severity == "blocking" and not issue.resolved:
                return issue
        return None

    def resolve_issue(self, question_id: str, answer: str) -> BlockingIssue | None:
        """Record the user's answer against whichever issue raised that
        question, mark it resolved (never asked again), and return it so
        the caller can react to *which* issue was just resolved (e.g. to
        decide whether more evidence should be gathered)."""
        self.user_answers[question_id] = answer
        for issue in self.blocking_issues:
            q = issue.clarification_question
            if q is not None and q.question_id == question_id:
                issue.resolved = True
                return issue
        return None


class Compatibility(BaseModel):
    """The exact field names/shapes Planning already reads via
    `get_stage_result()` — never renamed, never restructured, so nesting
    the rest of `WorkingContext` changes nothing for existing consumers."""

    original_request: str = ""
    enriched_text: str = ""
    planning_metadata: dict = Field(default_factory=dict)


class DiscoverySummaryItem(BaseModel):
    """One human-facing line in a `DiscoverySummary` — "✓ Repository
    resolved" / "⚠ Confluence unavailable" rendered from real state, never
    a paraphrase invented separately from what `WorkingContext` actually
    contains."""

    label: str
    status: Literal["ok", "warning", "error"]
    detail: str = ""


class DiscoverySummary(BaseModel):
    """The human-facing report generated *from* a `WorkingContext` — see
    `reasoning_loop.build_discovery_summary`. Downstream agents consume the
    structured `WorkingContext`/`ContextDiscoveryResult`; humans (the
    Workflow UI) consume this instead of trying to read the raw structure
    themselves."""

    items: list[DiscoverySummaryItem] = Field(default_factory=list)
    readiness: Readiness = "PARTIAL"
    headline: str = ""


class WorkingContext(BaseModel):
    """The authoritative, progressively-built understanding of one Context
    Discovery run. Replaces "retrieve everything, pass everything" with a
    single object that is read, updated, and re-assessed each iteration."""

    metadata: ContextMetadata = Field(default_factory=ContextMetadata)
    knowledge: Knowledge = Field(default_factory=Knowledge)
    reasoning: Reasoning = Field(default_factory=Reasoning)
    compatibility: Compatibility = Field(default_factory=Compatibility)

    def next_blocking_question(self) -> ClarificationQuestion | None:
        issue = self.reasoning.next_blocking_issue()
        return issue.clarification_question if issue else None

    def resolve_question(self, question_id: str, answer: str) -> None:
        self.reasoning.resolve_issue(question_id, answer)
