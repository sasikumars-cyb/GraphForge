"""Refinement Planner's own structured turn shape — carried inside
`ConversationTurnPayload.refinement`. See
`app.services.refinement_grounding` for what's actually fetched/derived
vs. LLM-proposed; this module only defines the wire shape.

Work-item ids: a real Jira key ("PROT-5263") when the item already
exists, or a stable placeholder ("PROPOSED-01", ...) when GraphForge is
proposing it — never a fabricated Jira-shaped id. `status` on `WorkItem`
is the single source of truth for that distinction; never infer it from
the id's shape alone.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ask import ProvenanceKind

WorkItemType = Literal["epic", "story", "task", "spike"]
WorkItemStatus = Literal["existing", "proposed"]
EdgeRelationship = Literal["blocks", "depends_on", "enables", "related", "parent_child"]
QuestionCategory = Literal["known", "derived", "assumption", "unknown"]
ReadinessLevel = Literal["ready", "mostly_ready", "needs_clarification", "not_ready"]


class WorkItem(BaseModel):
    id: str
    type: WorkItemType
    status: WorkItemStatus
    title: str
    objective: str = ""
    context: str = ""
    scope: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    related_systems: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    # Why this item exists / how it was determined — shown in the ticket
    # intelligence panel's "Why" section.
    evidence_note: str = ""
    provenance: ProvenanceKind = "recommendation"


class WorkItemEdge(BaseModel):
    source_id: str
    target_id: str
    relationship: EdgeRelationship
    # "ai_insight" by default — a proposed sequencing relationship between
    # (at least one) proposed work item is GraphForge's *interpretation*,
    # not a graph computation, so it does not get "derived". Only an edge
    # this service independently verifies (today: none — real Jira issue
    # links aren't fetched yet, see module docstring) would earn "fact" or
    # "derived"; `source_system` records which is which so that gap stays
    # honest rather than silently defaulting to a more confident label.
    provenance: ProvenanceKind = "ai_insight"
    # Set when `relationship` came from Jira's own issue links (a real
    # "blocks"/"relates to" already recorded there) rather than
    # GraphForge's own analysis — see `app.services.refinement_grounding`.
    source_system: Literal["jira", "refinement_analysis"] = "refinement_analysis"


class OpenQuestion(BaseModel):
    question: str
    category: QuestionCategory
    note: str = ""


class Spike(BaseModel):
    """Redundant with a `WorkItem(type="spike")` by design — this is the
    richer shape (questions/exit criteria) the product brief's own spike
    format asks for; `work_items` carries the same spike as a graph node
    so it participates in dependency edges like anything else."""

    work_item_id: str
    why: str
    questions: list[str] = Field(default_factory=list)
    exit_criteria: str = ""


class RefinementReadiness(BaseModel):
    level: ReadinessLevel
    score: int = Field(ge=0, le=100)
    ready_signals: list[str] = Field(default_factory=list)
    needs_clarification: list[str] = Field(default_factory=list)
    investigation_required: list[str] = Field(default_factory=list)


class RefinementPlan(BaseModel):
    requirement_summary: str = ""
    objective: str = ""
    desired_outcome: str = ""
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_work_categories: list[str] = Field(default_factory=list)

    work_items: list[WorkItem] = Field(default_factory=list)
    edges: list[WorkItemEdge] = Field(default_factory=list)
    spikes: list[Spike] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)

    # Carried forward turn to turn (set once, on the turn that actually
    # resolved a repository) so `compute_readiness` doesn't need the
    # original grounding call replayed on every follow-up.
    engineering_context_grounded: bool = False
    readiness: RefinementReadiness | None = None
    # Longest chain(s) through `edges` (blocks/depends_on only) — see
    # `refinement_grounding.compute_critical_path`. Empty when no chain is
    # longer than any other (the brief's "say Key dependency paths, not a
    # single critical path" case) — `critical_paths` (plural) then holds
    # every maximal chain instead.
    critical_paths: list[list[str]] = Field(default_factory=list)
    parallelizable_ids: list[str] = Field(default_factory=list)

    # Set only on a requirement source GraphForge couldn't actually fetch
    # (e.g. a bare Confluence URL with no Jira anchor — see
    # `refinement_grounding`'s own docstring on why that's not resolvable
    # today) — the frontend/LLM must not pretend the plan below is
    # grounded in content that was never retrieved.
    unresolved_source_note: str = ""
    # Set when the requirement itself came from a real Jira issue — the
    # "View in Jira" deep link's target. Never set for a pasted/freetext
    # requirement.
    source_jira_key: str | None = None
    source_jira_url: str | None = None
