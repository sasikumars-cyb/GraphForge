"""Documentation Planning Agent output schema — the T in AgentOutput[T].

Structured documentation plan produced by the Documentation Planning Agent.
Deliberately has no `graph_context_used` / `repositories_consulted` fields
like Planning/Development/Testing — this agent runs no graph tools of its
own (it synthesizes over the prior three stages' already-graph-grounded
outputs), so those fields would be misleading here. Same reasoning as
engineering_review/schemas.py, which this mirrors.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.agents._contract import ComponentWarning


def _none_to_empty(v: str | None) -> str:
    """Coerce ``null`` from LLM JSON into an empty string."""
    return v if v is not None else ""


class RequiredDocumentationUpdate(BaseModel):
    """One existing or new documentation artifact this change affects."""

    document: str
    category: str = ""  # "repository" | "api" | "configuration" | "database" |
    # "architecture" | "developer" | "operational" | "user" | "release_notes"
    current_status: str = ""
    action: str = ""  # "create" | "update" | "remove" | "no_change"
    reason: str = ""
    priority: str = ""  # "low" | "medium" | "high"
    owner: str = ""
    estimated_effort: str = ""  # "small" | "medium" | "large"
    dependencies: list[str] = Field(default_factory=list)

    @field_validator(
        "category", "current_status", "action", "reason", "priority", "owner",
        "estimated_effort",
        mode="before",
    )
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


class NewDocumentationItem(BaseModel):
    """A documentation artifact that does not exist today and should be created."""

    name: str
    category: str = ""  # same category vocabulary as RequiredDocumentationUpdate
    purpose: str = ""
    suggested_location: str = ""
    owner: str = ""
    priority: str = ""  # "low" | "medium" | "high"
    estimated_effort: str = ""  # "small" | "medium" | "large"

    @field_validator(
        "category", "purpose", "suggested_location", "owner", "priority",
        "estimated_effort",
        mode="before",
    )
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


class ExistingDocumentationUpdate(BaseModel):
    """Section-level detail for one existing document being updated."""

    file_path: str
    sections_affected: list[str] = Field(default_factory=list)
    summary_of_changes: str = ""

    @field_validator("summary_of_changes", mode="before")
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


class DocumentationRisk(BaseModel):
    """A risk caused by incomplete or missing documentation."""

    description: str
    severity: str = ""  # "low" | "medium" | "high" | "critical"

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_none(cls, v: str | None) -> str:
        return _none_to_empty(v)


class DocumentationChecklistItem(BaseModel):
    """One line of the final documentation checklist.

    `applicable=False` renders as N/A rather than an open checkbox — e.g.
    "Database documentation updated" when the change has no schema impact.
    Always unchecked: this is a planning-stage artifact, produced before
    any documentation has actually been written.
    """

    label: str
    applicable: bool = True


class DocumentationPlan(BaseModel):
    """Structured output from the Documentation Planning Agent.

    Plans documentation — it does not write it. Every field is designed
    for card-based rendering in the frontend, mirroring TestPlan/
    EngineeringReadinessReport's shape.
    """

    goal: str
    executive_summary: str

    documentation_impact: str = ""  # "none" | "low" | "medium" | "high"
    impact_explanation: str = ""

    required_updates: list[RequiredDocumentationUpdate] = Field(default_factory=list)
    new_documentation: list[NewDocumentationItem] = Field(default_factory=list)
    existing_updates: list[ExistingDocumentationUpdate] = Field(default_factory=list)

    risks: list[DocumentationRisk] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    release_notes_draft: list[str] = Field(default_factory=list)
    checklist: list[DocumentationChecklistItem] = Field(default_factory=list)

    # Deterministic verification_warnings carried forward from Planning/
    # Development/Testing (see app.agents.verification) — never generated
    # by this agent's own LLM call, only read from the stages that
    # produced them. A documentation plan citing a component or repository
    # those stages already flagged as unverified should not be trusted
    # blindly either.
    prior_verification_warnings: list[str] = Field(default_factory=list)

    # This agent's OWN independent check — distinct from
    # prior_verification_warnings above, which only carries forward what
    # earlier stages already found. This agent has no `affected_components`
    # field of its own (its output names documents, not components), so it
    # scans its own narrative text (reason/summary_of_changes/
    # release_notes_draft) for indexed component names and independently
    # re-checks each one via app.agents.component_grounding — not just
    # trusting that a name already flagged upstream is the only one that
    # could be wrong. See PlanningResult.component_warnings.
    component_warnings: list[ComponentWarning] = Field(default_factory=list)

    prompt_version: str = "1.0"

    @field_validator("documentation_impact", "impact_explanation", mode="before")
    @classmethod
    def _coerce_none_top(cls, v: str | None) -> str:
        return _none_to_empty(v)
