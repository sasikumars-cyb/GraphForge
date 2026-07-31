"""`ContextDiscoveryResult` — the JSON-serializable shape persisted into
`AgentStep.result` for the context_discovery stage.

Three groups of fields, with different owners and different purposes:

1. **The Planning-facing view** (`enriched_text`, `indexed_repositories`,
   `graph_components`, ...). Flat and prompt-ready because that's what
   Planning's `get_stage_result()` read path wants. These are *derived views*
   over the reasoning engine's fact ledger — see
   `reasoning.projection.build_result` — never a separate copy of the truth.

2. **The readiness verdict** (`readiness`, `confidence`,
   `capability_confidence`, `blocking_reasons`, `remediation_steps`,
   `unresolved_questions`). Read by the readiness gate in
   `api/v1/routers/workflows.py` and by the workflow UI.

3. **The human-facing report and the resumable state**
   (`discovery_report`, `working_memory`). The report is what a person reads:
   the investigation transcript, the per-capability confidence decomposition
   with every signal cited to evidence, the findings with their provenance,
   and the open gaps. `working_memory` is the engine's own `WorkingContext`,
   persisted verbatim so a paused run resumes from exactly the state it
   paused in rather than from a lossy re-parse of the flat fields above.

Two things deliberately absent: the classifier `PlanningProfile` (a pure
function of `enriched_text`, so consumers re-derive it rather than us shipping
a bespoke serializer for a dataclass of shared singletons) and the raw
provider artifacts (their prose is already in `enriched_text` and their
retrieval is already in the evidence trail — a third copy would be storage
for its own sake).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextDiscoveryResult(BaseModel):
    """What Context Discovery produces — persisted verbatim into
    `AgentStep.result`."""

    # --- 1. The Planning-facing view ------------------------------------

    # The literal text the user typed/pasted — never modified, so the UI's
    # "Task Description" keeps showing exactly what was asked for.
    original_request: str

    # `original_request` plus every piece of retrieved prose, each wrapped as
    # untrusted content. Recomputed from the fact ledger on every cycle, so it
    # can never contain a paragraph the current facts don't support.
    enriched_text: str

    resolved_references: list[dict[str, Any]] = Field(default_factory=list)
    indexed_repositories: list[dict[str, Any]] = Field(default_factory=list)
    # The primary work item's structured fields — status/issue_type/
    # priority/labels plus any Problem/Business Goal/Acceptance Criteria/
    # Constraints/Dependencies sections detected in its description (see
    # investigators._extract_ticket_sections). `{}` when no work item
    # was resolved.
    ticket_summary: dict[str, Any] = Field(default_factory=dict)
    # Complete and uncurated — every `component` fact, unranked, kept for
    # debugging/the JSON tab (see `projection.build_result`'s comment on
    # this field). No agent's own prompt construction should read this
    # directly; see `evidence_package` below.
    graph_components: list[dict[str, Any]] = Field(default_factory=list)
    graph_topics: list[dict[str, Any]] = Field(default_factory=list)
    # The curated, bounded, tiered replacement for `graph_components` —
    # `reasoning.curation.EvidencePackage.model_dump()`. Planning,
    # Development, Testing, and Documentation Planning all read this for
    # their own component selection now (see each agent's own
    # `_resolve_context`/equivalent) rather than the raw list above.
    evidence_package: dict[str, Any] = Field(default_factory=dict)
    # Every indexed repository in relevance order, best first. A *ranking*, not
    # a claim of ownership — Planning reads it positionally (star ratings, and
    # `[0]` as the target for its component-ownership verification), so it stays
    # complete even when discovery cannot pick a winner.
    ranked_repository_names: list[str] = Field(default_factory=list)

    # Discovery's own interpretation of where the work belongs: the engine's
    # live `repository_candidate` inferences, each citing the repository facts
    # supporting it. More than one entry means the ambiguity is genuine — which
    # is what holds the `repository` capability unsatisfied and, once providers
    # are exhausted, produces the clarification question.
    implementation_candidates: list[str] = Field(default_factory=list)

    # --- Multi-repository selection (ADR 0010 §2 — the canonical model) --
    # `repositories` is the ONLY field anything ever populates directly —
    # each entry is `{name, source, selected, reason, relationship, rank,
    # graph_version}` (see `reasoning.projection.RepositoryCandidate`).
    # Every field below it is a READ-ONLY compatibility projection, derived
    # from `repositories` by `reasoning.projection.project_repositories`
    # and nothing else (invariant I6) — new code must read `repositories`
    # directly, never these. They're kept only because
    # `explicit_repositories`/`suggested_repositories`/`selected_
    # repositories` are pre-existing callers' contract, and `implementation_
    # candidates`/`ranked_repository_names` (above) predate this model
    # entirely. Absent from a result persisted before this field existed;
    # every reader falls back to `implementation_candidates`/
    # `ranked_repository_names` in that case.
    repositories: list[dict[str, Any]] = Field(default_factory=list)
    explicit_repositories: list[dict[str, Any]] = Field(default_factory=list)
    suggested_repositories: list[dict[str, Any]] = Field(default_factory=list)
    selected_repositories: list[dict[str, Any]] = Field(default_factory=list)

    graph_context_text: str = ""
    graph_available: bool = False
    graph_has_data: bool = False

    # Diagnostics: which reference types were detected, how many reasoning
    # cycles ran, which providers were actually consulted.
    planning_metadata: dict[str, Any] = Field(default_factory=dict)

    prompt_version: str = "1.0"

    # --- 2. Readiness verdict -------------------------------------------

    goal: str = ""
    # READY / PARTIAL / BLOCKED — derived from whether required capabilities
    # are satisfied, never from a confidence threshold. See
    # `reasoning.memory.WorkingContext.readiness`.
    readiness: str = "BLOCKED"
    # Necessity-weighted mean of the per-capability scores below, excluding
    # capabilities that don't apply to this request. Every input is
    # evidence-derived; no LLM self-report contributes to it.
    confidence: float = 0.0
    capability_confidence: dict[str, float] = Field(default_factory=dict)
    clarification_rounds: int = 0
    blocking_reasons: list[str] = Field(default_factory=list)
    remediation_steps: list[str] = Field(default_factory=list)
    # Interpretations discovery is proceeding on, each derived from cited
    # facts (an inference with no supporting facts cannot be recorded).
    assumptions: list[str] = Field(default_factory=list)
    user_answers: dict[str, str] = Field(default_factory=dict)
    # At most one entry: the single question discovery is waiting on. Empty
    # unless the run is paused.
    unresolved_questions: list[dict[str, Any]] = Field(default_factory=list)

    # --- 3. Report + resumable state ------------------------------------

    # The human-facing report: transcript, confidence decomposition with
    # per-signal evidence, findings with provenance, gaps with remediation.
    # See `reasoning.projection.build_discovery_report`.
    discovery_report: dict[str, Any] = Field(default_factory=dict)

    # The engine's `WorkingContext`, dumped verbatim. The resume path
    # (`reasoning.projection.restore`) rebuilds from this and nothing else.
    working_memory: dict[str, Any] = Field(default_factory=dict)
