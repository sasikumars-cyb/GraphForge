"""Response/request shapes for `POST /ask` — the Home page's "ask
GraphForge anything" entry point.

`AskResponse` is the layered Answer/Why/Evidence/Impact/Provenance
contract the Home page renders — every field here is a reshaping of an
existing, already-computed result (`BlastRadius`, `QueryResult`), never a
new computation. See `app.api.v1.routers.ask` for what actually produces
each field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The five kinds `ProvenanceTag` (frontend/src/components/intelligence/
# ProvenanceTag.tsx) already renders. Kept as the same closed vocabulary
# here so the API can never emit a provenance kind the UI doesn't know
# how to label — see that component's own docstring for why misusing
# "ai_insight" for a deterministic graph computation is the specific
# failure mode this whole contract exists to prevent. "recommendation" —
# an AI-suggested *action* (a migration phase, a test to run) — is kept
# distinct from "ai_insight" (an AI-suggested *interpretation*, e.g. a
# risk ranking): one says "here's what I think is true," the other says
# "here's what I think you should do," and conflating them would blur
# exactly the distinction Migration Assistant's risk/recommendation
# vocabulary depends on.
ProvenanceKind = Literal["fact", "derived", "ai_insight", "human_decision", "recommendation"]


# H-2 — a server-side ceiling on anything that reaches a paid model. The
# browser textarea is not a control: `POST /ask` and `POST /conversations`
# accepted a 400 KB question and forwarded every byte of it to the
# provider. 4,000 characters is roughly 1,000 tokens — comfortably longer
# than any real engineering question, short enough that a scripted caller
# cannot turn one request into a large bill. Enforced by pydantic, so the
# request is rejected during validation, before any handler, any graph
# query and any LLM call.
MAX_QUESTION_LENGTH = 4_000


class AskRequest(BaseModel):
    question: str = Field(max_length=MAX_QUESTION_LENGTH)


class AskAction(BaseModel):
    """One gateway out of the chat answer and into the existing product —
    the "chat response becomes a gateway" requirement. `href` is always a
    path into this same frontend, never an external URL."""

    label: str
    kind: Literal[
        "explore_impact",
        "view_repository",
        "view_dependency_graph",
        "investigate",
        "create_migration_plan",
        "validate_migration",
        "view_work_graph",
        "create_planning_workflow",
        "generate_testing_strategy",
        "view_jira_issue",
    ]
    href: str


class AskEvidenceItem(BaseModel):
    # e.g. "GitHub" | "Jira" | "Confluence" | "Dependency Graph" — the
    # source system name shown as the evidence chip's label, matching the
    # example in the product brief ("GitHub · Jira · Dependency Graph").
    source: str
    label: str
    provenance: ProvenanceKind


class AskImpact(BaseModel):
    severity: Literal["low", "medium", "high"]
    summary: str
    affected_repositories: list[str] = Field(default_factory=list)
    affected_apis: list[str] = Field(default_factory=list)
    affected_databases: list[str] = Field(default_factory=list)
    affected_queues: list[str] = Field(default_factory=list)
    # True when the blast radius was larger than the reporting limit and
    # the lists above are a bounded sample (see `ask_grounding.
    # build_impact_facts`). A consumer must never present a truncated
    # result as an exhaustive impact analysis.
    truncated: bool = False


class AskRepositoryCandidate(BaseModel):
    """One repository GraphForge considered but could not confidently
    choose between. Shown to the user so an ambiguous question becomes a
    one-click clarification instead of a dead end."""

    name: str
    full_name: str
    repository_id: str
    score: float


class AskResponse(BaseModel):
    # "answered" — resolved deterministically here, no further round trip
    # needed. "route_to_investigation" — this endpoint deliberately
    # doesn't attempt an answer (no confident repository match, or a
    # question shape none of the deterministic paths cover); the frontend
    # is expected to fall back to the existing free-text investigation
    # flow (`POST /agent-runs` with goal="discover_context") rather than
    # this endpoint re-implementing that orchestration.
    # "needs_clarification" — the question was understood (see `intent`)
    # but GraphForge could not confidently identify WHICH system it means.
    # `candidates` carries what it did find so the caller can ask. No
    # answer, no evidence and no impact are produced in this state: a
    # guess presented with evidence badges is worse than a question.
    status: Literal["answered", "needs_clarification", "route_to_investigation"]
    question: str
    # The classification of the question itself, always preserved (M-1).
    # An impact question whose repository could not be resolved is still
    # an impact question — reporting it as "general" lost the one signal
    # that explains why no answer came back, and corrupted any analytics
    # counting how often each intent succeeds.
    intent: Literal["impact", "dependency", "general"]
    # Why resolution ended where it did — "strong_match",
    # "exact_name_match", "candidates_too_close",
    # "only_generic_terms_matched", "below_minimum_confidence", ... See
    # `ask_grounding.RepositoryResolution`.
    resolution_reason: str = ""
    candidates: list[AskRepositoryCandidate] = Field(default_factory=list)
    resolved_repository_id: str | None = None
    resolved_repository_name: str | None = None
    answer: str = ""
    why: str = ""
    evidence: list[AskEvidenceItem] = Field(default_factory=list)
    impact: AskImpact | None = None
    actions: list[AskAction] = Field(default_factory=list)
