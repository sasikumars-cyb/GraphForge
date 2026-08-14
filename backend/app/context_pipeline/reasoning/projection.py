"""Rendering working memory into the shapes other things consume.

Working memory is normalized (atomic facts, evidence, inferences). The two
consumers want something else:

- **Planning** wants the flat `ContextDiscoveryResult` it already reads via
  `get_stage_result()` — `enriched_text`, `indexed_repositories`,
  `graph_components`, and so on. Those are *derived views* over the fact
  ledger, computed here, not a second copy of the truth.
- **A human** wants a report that says what was searched, what was found,
  what's missing, how confident discovery is in each area and why, and what
  to do next — with every line traceable to the evidence behind it.

Everything in this module is a pure function of `WorkingContext`. Nothing
here decides anything; if a number or a status looks wrong, the cause is in
the ledger or the assessments, never in this file.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from app.agents._contract import Evidence
from app.context_pipeline.providers import wrap_artifact_text
from app.context_pipeline.reasoning.capabilities import (
    GRAPH_TRAVERSAL_ACTION,
    _latest_graph_evidence,
)
from app.context_pipeline.reasoning.ledger import EvidenceRecord, Ledger
from app.context_pipeline.reasoning.memory import WorkingContext
from app.context_pipeline.reasoning.understanding import InvestigationWorkspace

# Fact kinds whose retrieved prose belongs in the planning prompt, in the
# order they should appear.
_PROSE_KINDS: tuple[str, ...] = ("work_item", "document", "pull_request")


def render_enriched_text(state: WorkingContext) -> str:
    """The request plus every piece of retrieved prose, each wrapped as
    untrusted content by the same helper the old pipeline used.

    Recomputed after every investigation cycle rather than accumulated by
    string concatenation, so it always reflects exactly the facts currently in
    the ledger — a withdrawn or absent fact can't leave a stale paragraph
    behind in the prompt Planning renders from.
    """
    parts = [state.derived.get("original_request") or state.metadata.goal]
    for kind in _PROSE_KINDS:
        for fact in state.ledger.facts_of(kind):  # type: ignore[arg-type]
            if fact.text:
                parts.append(wrap_artifact_text(fact.provider, fact.text))
    return "".join(parts)


def _graph_reachable(ledger: Ledger) -> bool:
    """Whether the knowledge graph was queried without an observed failure.

    RFC-0028: reads the *latest* evidence per graph-provider action (see
    `_latest_graph_evidence`), not every attempt ever made in this run — a
    since-resolved early failure (e.g. a hop-budget-limited pass before a
    clarification pause, retried successfully after the answer) must not
    keep reporting the graph as unreachable for the rest of the run.
    """
    graph_evidence = _latest_graph_evidence(ledger)
    return bool(graph_evidence) and not any(e.outcome == "failed" for e in graph_evidence)


def _contract_kind(record: EvidenceRecord) -> Any:
    """Which contract evidence kind honestly describes this record.

    Only the graph *traversal* is a `graph_traversal`. The repository lookup
    also comes from the graph provider but reads Postgres, so labelling it a
    traversal would assert a Neo4j query that never ran — and the agent
    contract's "at least one graph/tool entry" rule would then be satisfiable
    by an unreachable graph.

    A human answer is `human_input`, not a tool call: the whole point of the
    evidence trail is saying where a claim came from.
    """
    if record.provider == "user":
        return "human_input"
    if (
        record.provider == "graph"
        and record.action == GRAPH_TRAVERSAL_ACTION
        # Only a traversal that actually read the graph earns this kind. The
        # agent contract treats `graph_traversal` as proof of grounding ("at
        # least one graph/tool entry per output"), so a failed or skipped
        # attempt carrying it would let an unreachable graph satisfy that
        # check. The attempt still appears — as a `tool_call` with
        # status="failed", so nothing is hidden, only correctly labelled.
        and record.outcome in ("success", "not_found")
    ):
        return "graph_traversal"
    return "tool_call"


def to_contract_evidence(state: WorkingContext) -> list[Evidence]:
    """Project the ledger into the agent contract's `Evidence` list.

    `status` carries the ledger's outcome straight through — the two
    vocabularies were deliberately kept identical — and `kind` is chosen by
    `_contract_kind` so no entry claims a category of work that didn't happen.
    """
    projected: list[Evidence] = []
    for record in state.ledger.evidence:
        projected.append(
            Evidence(
                kind=_contract_kind(record),
                reference=f"{record.provider}:{record.action}",
                # The "FAILED: " prefix is this codebase's cross-agent
                # convention for a failed observation (see
                # planning.tools.to_evidence) and the Workflow activity feed
                # keys off it. `status` carries the same fact structurally;
                # both are populated so a consumer can use either without a
                # failed call ever reading as a successful one.
                summary=(
                    f"FAILED: {record.summary}" if record.outcome == "failed" else record.summary
                ),
                status=record.outcome,
            )
        )
    return projected


# ---------------------------------------------------------------------------
# The human-facing report
# ---------------------------------------------------------------------------


def build_discovery_report(state: WorkingContext) -> dict[str, Any]:
    """What the Context Explorer renders.

    Three sections, deliberately distinct:

    - `confidence`: per-capability score with its full signal decomposition,
      so "Architecture 65%" is followed by the ✓/✗ list that produces 65%.
    - `findings`: the facts, grouped by kind, each carrying the evidence that
      established it — this is the "Repository: soco-payment / Evidence: ✓ …"
      view. Interpretations are listed separately, citing their facts.
    - `gaps`: what's missing, why it matters, what was tried, what to do.

    Plus `transcript`: the narration, which is the part that makes discovery
    read as an investigation rather than a report generator.
    """
    return {
        "readiness": state.readiness,
        "completion_status": state.completion_status,
        "confidence": state.confidence,
        "context_completeness": state.confidence,
        "headline": _headline(state),
        "transcript": [e.model_dump() for e in state.transcript.entries],
        "confidence_breakdown": [
            {
                "capability": a.capability,
                "label": a.label,
                "necessity": a.necessity,
                "score": a.score,
                "satisfied": a.satisfied,
                "explanation": a.explanation(),
                "signals": [
                    {
                        "label": s.label,
                        "satisfied": s.satisfied,
                        "detail": s.detail,
                        "evidence_ids": s.evidence_ids,
                    }
                    for s in a.signals
                ],
            }
            for a in state.assessments
        ],
        "findings": _findings(state),
        "interpretations": [
            {
                "statement": i.statement,
                "kind": i.kind,
                "supporting_fact_ids": i.supporting_fact_ids,
                "withdrawn": i.withdrawn,
            }
            for i in state.ledger.inferences
        ],
        "gaps": [
            {
                "gap_id": g.gap_id,
                "capability": g.capability,
                "summary": g.summary,
                "why": g.why,
                "severity": g.severity,
                "status": g.status,
                "missing": g.missing_signals,
                "recommended_action": g.recommended_action,
                "resolution_note": g.resolution_note,
                "user_claim": g.user_claim,
            }
            for g in state.gaps
        ],
        "investigation": [
            {
                "evidence_id": e.evidence_id,
                "provider": e.provider,
                "action": e.action,
                "outcome": e.outcome,
                "summary": e.summary,
                "intent": e.intent,
                "iteration": e.iteration,
            }
            for e in state.ledger.evidence
        ],
    }


# How many facts of one kind the human-facing report lists individually. A
# monorepo can legitimately produce four figures of components; nobody reads
# them, and embedding them all makes the report both unusable and large. The
# true total rides along as `total`, so the report never implies it found less
# than it did. Only display is bounded — the ledger itself keeps every fact,
# because Planning's component-ownership verification needs the complete set.
_MAX_REPORTED_FACTS_PER_KIND = 50


def _findings(state: WorkingContext) -> list[dict[str, Any]]:
    """Facts grouped by kind, each with its evidence trail — the "why do you
    believe this" view, answerable without another reasoning pass."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    totals: dict[str, int] = {}
    for fact in state.ledger.facts:
        totals[fact.kind] = totals.get(fact.kind, 0) + 1
        items = grouped.setdefault(fact.kind, [])
        if len(items) >= _MAX_REPORTED_FACTS_PER_KIND:
            continue
        record = state.ledger.evidence_by_id(fact.evidence_id)
        items.append(
            {
                "fact_id": fact.fact_id,
                "subject": fact.subject,
                "provider": fact.provider,
                "verified": fact.verified,
                "evidence": (
                    {
                        "evidence_id": record.evidence_id,
                        "summary": record.summary,
                        "outcome": record.outcome,
                    }
                    if record
                    else None
                ),
            }
        )
    return [
        {"kind": kind, "items": items, "total": totals[kind]} for kind, items in grouped.items()
    ]


def _headline(state: WorkingContext) -> str:
    """One line stating the verdict — taken from the transcript's own final
    conclusion so the headline and the narration can never disagree."""
    conclusions = [e for e in state.transcript.entries if e.kind == "conclusion"]
    if conclusions:
        return conclusions[-1].text
    return f"Readiness: {state.readiness}."


# ---------------------------------------------------------------------------
# The flat result Planning reads
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The canonical repository model (ADR 0010 §2 / invariant I5)
# ---------------------------------------------------------------------------
#
# `repositories` is the ONLY repository-shaped field anything ever populates
# directly. `ranked_repository_names`, `implementation_candidates`,
# `explicit_repositories`, `suggested_repositories`, and
# `selected_repositories` are read-only compatibility projections, computed
# exclusively by `project_repositories` below — nothing else in this
# codebase may independently write any of those five keys (invariant I6).
# The first two predate this model and are pre-existing production fields
# (Planning's star-rating and `[0]`-fallback code, in particular, reads
# `ranked_repository_names` positionally) — their *values* are preserved
# exactly by this projection, only their provenance changes from
# independently-computed to derived.


class RepositoryCandidate(BaseModel):
    """One repository Context Discovery has identified. `source` is where it
    came from; `selected` is whether it's actually in scope for this work —
    two different questions (a repository can be a live candidate without
    being selected, but never selected without first being a candidate)."""

    name: str
    source: Literal["explicit", "suggested"]
    selected: bool
    reason: str = ""
    relationship: str = ""
    # Position in the relevance ranking, when one exists (see
    # `ranked_repository_names`) — `None` for a repository the ranking never
    # scored at all (e.g. explicit-only with no ranking ever computed).
    rank: int | None = None
    # ADR 0010 §4 — populated only where the ledger genuinely carries a
    # graph_version signal today (a `repository_relationship` fact's
    # `target_graph_version`, for a suggested-via-relationship candidate).
    # Honestly `None` elsewhere rather than a fabricated value — no fact
    # currently threads a plain ranked/explicit candidate's own graph
    # version into the reasoning ledger.
    graph_version: str | None = None
    # ADR 0010 (Theme E) — "structural" or "heuristic" for a suggested-via-
    # relationship candidate (see `cross_repo_linker.py`'s rules); empty for
    # every other source, since explicit/ranked candidates have no edge
    # confidence to report.
    confidence: str = ""


def project_repositories(repositories: list[RepositoryCandidate]) -> dict[str, Any]:
    """The single pure function that derives every legacy/compatibility
    repository field from the canonical `repositories` list (ADR 0010 §2).
    Called once by `build_result` to populate the stored `AgentStep.result`,
    and again by any reader (Planning, `RepositorySelector.tsx`'s
    TypeScript equivalent) over whatever `repositories` a human override
    resolves to — never trusting the stored projection keys directly
    (invariant I6).
    """
    ranked = sorted((r for r in repositories if r.rank is not None), key=lambda r: r.rank)
    return {
        "ranked_repository_names": [r.name for r in ranked] or [r.name for r in repositories],
        "implementation_candidates": [r.name for r in repositories],
        "explicit_repositories": [r.model_dump() for r in repositories if r.source == "explicit"],
        "suggested_repositories": [r.model_dump() for r in repositories if r.source == "suggested"],
        "selected_repositories": [r.model_dump() for r in repositories if r.selected],
    }


def _build_repositories(state: WorkingContext) -> list[RepositoryCandidate]:
    """The canonical `repositories` list — every live `repository_candidate`
    inference, with `rank` from the graph investigator's own relevance
    ordering and `selected` from the default rule: every explicit
    repository, plus — only when nothing explicit was found at all — the
    single surviving suggested candidate (today's pre-multi-repository
    behavior: one ranked/only-indexed repository, auto-used). Once any
    explicit repository exists, suggested ones stay opt-in — the whole point
    of `source: "suggested"` is that a human decides whether to include it,
    via the Repositories panel's checkboxes.
    """
    ledger = state.ledger
    ranked_names = state.derived.get("ranked_repositories") or []
    rank_by_name = {name: index for index, name in enumerate(ranked_names)}

    relationship_versions = {
        f.subject: f.value.get("target_graph_version")
        for f in ledger.facts_of("repository_relationship")
    }

    candidates = [
        RepositoryCandidate(
            name=i.statement,
            source=i.value.get("source", "suggested"),
            selected=False,
            reason=i.value.get("reason", ""),
            relationship=i.value.get("relationship", ""),
            rank=rank_by_name.get(i.statement),
            graph_version=relationship_versions.get(i.statement),
            confidence=i.value.get("confidence", ""),
        )
        for i in ledger.live_inferences("repository_candidate")
    ]

    explicit = [c for c in candidates if c.source == "explicit"]
    suggested = [c for c in candidates if c.source == "suggested"]
    selected_names: set[str]
    if explicit:
        selected_names = {c.name for c in explicit}
    elif len(suggested) == 1:
        selected_names = {suggested[0].name}
    else:
        selected_names = set()

    return [c.model_copy(update={"selected": c.name in selected_names}) for c in candidates]


def build_reasoning_summary(state: WorkingContext) -> dict[str, Any]:
    """The minimal, report-safe projection of one investigation's
    `InvestigationWorkspace` — hypotheses and contradictions only, the two
    things a downstream report needs to honestly show "what does
    GraphForge believe, and why" (Report V2's reasoning-visualization
    requirement).

    Deliberately NOT the full workspace: `open_questions`, `dead_ends`,
    `candidate_repositories`/`candidate_architecture`, `reasoning_notes`,
    `next_investigation_candidates`, `information_gain_estimates`,
    `investigation_history`, and `investigation_graph` all stay exactly
    as private as `engineering_understanding`'s own docstring already
    requires the whole workspace to be for Planning — this function is
    the one place that decides what of the workspace a report consumer
    is allowed to see, and it draws that line at hypotheses/
    contradictions only.

    Reads `state.derived.get("investigation_workspace")` — already a
    plain dict (`InvestigationWorkspace.model_dump()`) produced by the
    *existing* synthesis call (see `synthesize_engineering_
    understanding`). This function performs no LLM call and adds no new
    one; it is a pure projection over data the synthesis pass already
    computed this run, at every call site `build_result` already runs
    from (not gated on whether the run paused for a clarification
    question, unlike `working_memory` below — hypotheses/contradictions
    are two small lists, not the full ledger, so the storage-size
    argument that gates `working_memory` doesn't apply here).

    Re-validates through the real `Hypothesis`/`Contradiction` models
    (never hand-reshapes the dict) so a caller downstream can trust every
    entry has exactly the shape those models guarantee, and so a future
    field added to either model is projected automatically without this
    function needing to change.

    Also carries `synthesis_state` — one of `"not_run"`/`"failed"`/
    `"completed_empty"`/`"completed"` (see `_resolve_synthesis_run_state`
    below and ADR 0024 §11). This is the one deliberately small addition
    over the original version of this function: a report consumer must be
    able to tell "the reasoning engine investigated and found nothing" (a
    real, positive result) apart from "reasoning synthesis never ran or
    failed" (an availability problem) — collapsing both to an empty
    projection, as this function used to, made that distinction
    impossible downstream. No new LLM call: `synthesize_engineering_
    understanding` already knows which of the three raw cases it hit
    (zero-evidence short-circuit, caught synthesis exception, or a clean
    completion) at the exact point it stashes `investigation_workspace` —
    this function only has to read the one extra string it now also
    stashes (`state.derived["investigation_workspace_run_state"]`) rather
    than re-deriving anything.

    `{}` only in the one case that truly precedes this addition: a
    persisted result from before this field existed (`derived` has no
    `investigation_workspace` key at all). Report V2's data plumbing
    treats that bare `{}` identically to `synthesis_state == "not_run"`
    — never a confident-looking empty list either way.
    """
    workspace_dump = state.derived.get("investigation_workspace")
    if not workspace_dump:
        return {}
    try:
        workspace = InvestigationWorkspace.model_validate(workspace_dump)
    except Exception:
        return {}
    raw_run_state = state.derived.get("investigation_workspace_run_state")
    synthesis_state = _resolve_synthesis_run_state(raw_run_state, workspace)
    return {
        "synthesis_state": synthesis_state,
        "hypotheses": [h.model_dump() for h in workspace.hypotheses],
        "contradictions": [c.model_dump() for c in workspace.contradictions],
        "iteration": state.metadata.iteration,
    }


_SynthesisRunState = Literal["not_run", "failed", "completed_empty", "completed"]


def _resolve_synthesis_run_state(
    raw: str | None, workspace: InvestigationWorkspace
) -> _SynthesisRunState:
    """Turns the raw signal `synthesize_engineering_understanding` stashes
    (`"not_run"`/`"failed"`/`"completed"`, or `None` for a call site that
    predates this addition) plus the workspace's own list lengths into the
    exhaustive four-value state ADR 0024 §11 defines.

    A plain string, not `app.agents.report_generation.contracts.
    SynthesisRunState` — this module is upstream of report_generation and
    must not import it; the two share the same four literal values by
    convention, bridged explicitly at read time (`data_plumbing.
    map_synthesis_run_state`), the same pattern this codebase already
    uses for `Hypothesis.status` (a bare `Literal` here) versus
    `contracts.SynthesisStatus` (the report-facing enum) — see
    `data_plumbing.map_synthesis_status`.

    `raw is None`/`"not_run"` both resolve to `"not_run"`: the honest
    default when the signal isn't there is "we don't know reasoning ran,"
    never a guess at `"completed"`.
    """
    if raw == "failed":
        return "failed"
    if raw is None or raw == "not_run":
        return "not_run"
    if not workspace.hypotheses and not workspace.contradictions:
        return "completed_empty"
    return "completed"


def build_result(state: WorkingContext) -> dict[str, Any]:
    """Project working memory into the persisted `ContextDiscoveryResult`.

    The Planning-facing fields (`enriched_text`, `indexed_repositories`,
    `graph_components`, `graph_topics`, `ranked_repository_names`,
    `graph_context_text`) are derived views over the fact ledger — they exist
    because Planning needs a flat prompt-ready shape, not because discovery
    keeps a second copy of its knowledge in that form.
    """
    ledger = state.ledger
    question = state.next_question()
    blocking = [g for g in state.gaps if g.severity == "blocking" and g.status not in ("verified",)]

    # --- the canonical repository model (ADR 0010 §2) --------------------
    # `repositories` is the only field populated directly; every other
    # repository-shaped key below comes from `project_repositories` and
    # nothing else ever writes them (invariant I6). A human override always
    # targets `repositories` itself (see `_OVERRIDABLE_FIELDS` in
    # `workflow_service.py`) — `get_stage_result()`'s merge then makes every
    # projection below automatically consistent with whatever override is
    # in effect for any reader that re-derives them the same way Planning
    # does, rather than trusting the stored, potentially-stale projection
    # keys directly.
    repositories = _build_repositories(state)
    projected_repositories = project_repositories(repositories)
    # `project_repositories` is a pure function of `repositories` alone (ADR
    # 0010 §2), so its own `ranked_repository_names` only ever covers
    # repositories that are *candidates* — it cannot know about an indexed
    # repository the ranking scored but that never became a candidate (e.g.
    # an unrelated third repository once explicit repositories exist —
    # see `capabilities.resync_ranked_candidates`'s suppression rule).
    # `ranked_repository_names` must stay the *complete* ordering regardless
    # (Planning's star-rating and its `[0]`-fallback verification read every
    # indexed repository positionally) — this is the one place with access
    # to both `repositories` and the ledger/derived ranking, so it applies
    # the same three-tier fallback the pre-Theme-D formula used: the full
    # relevance ordering when the graph investigator computed one, else the
    # (possibly narrower) candidate-derived projection, else every indexed
    # repository's own name.
    full_ranking = state.derived.get("ranked_repositories")
    if full_ranking:
        projected_repositories["ranked_repository_names"] = full_ranking
    elif not projected_repositories["ranked_repository_names"]:
        projected_repositories["ranked_repository_names"] = ledger.subjects_of("repository")

    # Scope graph_components/graph_topics to candidate repositories when
    # candidates exist — prevents the initial unscoped survey's components
    # from other repos bleeding into the output after a repository has been
    # selected (via clarification answer or explicit re-run).
    candidate_repo_names = {r.name for r in repositories}
    all_components = [dict(f.value) for f in ledger.facts_of("component")]
    all_topics = [dict(f.value) for f in ledger.facts_of("topic")]
    if candidate_repo_names:
        scoped_components = [
            c for c in all_components if c.get("repository") in candidate_repo_names
        ]
        scoped_topics = [t for t in all_topics if t.get("repository") in candidate_repo_names]
    else:
        scoped_components = all_components
        scoped_topics = all_topics

    return {
        # --- what Planning reads -----------------------------------------
        "original_request": state.derived.get("original_request") or state.metadata.goal,
        "enriched_text": state.derived.get("enriched_text") or state.metadata.goal,
        "resolved_references": [dict(f.value) for f in ledger.facts_of("reference")],
        "indexed_repositories": [dict(f.value) for f in ledger.facts_of("repository")],
        # The primary work item's structured fields (see
        # investigators.JiraInvestigator/`_extract_ticket_sections`) —
        # status/issue_type/priority/labels plus whatever Problem/
        # Business Goal/Acceptance Criteria/Constraints/Dependencies
        # sections the ticket's own description actually contains, by
        # real heading detection, never LLM summarization. `{}` when no
        # work item was ever resolved (a freeform request) or the run
        # predates this field.
        "ticket_summary": (
            dict(ledger.facts_of("work_item")[0].value) if ledger.facts_of("work_item") else {}
        ),
        # Complete and uncurated, deliberately — the debugging/JSON-tab
        # view, kept exactly as before (see this function's own
        # docstring). No agent's prompt construction should read this
        # directly anymore; see `evidence_package` below for the
        # bounded, tiered, ranked replacement every agent now uses.
        # Scoped to candidate repositories so a global survey's components
        # from non-selected repos don't leak into the output.
        "graph_components": scoped_components,
        # The curated Evidence Package (see reasoning.curation) — computed
        # once, after the investigation loop exits
        # (investigators.curate_evidence, called from engine.investigate),
        # not re-derived here. Empty ({}) only for a run that predates
        # this field or produced no components at all.
        "evidence_package": state.derived.get("evidence_package") or {},
        # The synthesized, hypothesis-tested, self-challenged engineering
        # conclusion (see reasoning.understanding) — computed once,
        # immediately after evidence_package, in engine.investigate. This,
        # not evidence_package/graph_context_text, is Planning's primary
        # prompt input under the Frontier-Class Investigation Agent
        # redesign (see app.agents.planning.agent._graph_context_text_from).
        # `{}` for a run that predates this field or produced no evidence
        # to synthesize over.
        "engineering_understanding": state.derived.get("engineering_understanding") or {},
        # The report-safe hypotheses/contradictions projection — see
        # build_reasoning_summary's own docstring for exactly what this
        # does and does not carry, and why it's unconditional (unlike
        # working_memory below, which only survives a paused run).
        "reasoning_summary": build_reasoning_summary(state),
        "graph_topics": scoped_topics,
        "repositories": [r.model_dump() for r in repositories],
        **projected_repositories,
        "graph_context_text": state.derived.get("graph_context_text", ""),
        # Existing TestRail/uploaded test cases relevant to this request —
        # same derived-view pattern as graph_components/graph_context_text
        # above, over `test_case` facts instead. See TestCoverageInvestigator
        # (investigators.py) and app.agents.testing.agent's Step 4, which
        # prefers this over its own live lookup when a workflow ran discovery.
        "existing_test_coverage": [dict(f.value) for f in ledger.facts_of("test_case")],
        "test_coverage_text": state.derived.get("test_coverage_text", ""),
        # The same rule the `architecture` capability uses for "Knowledge graph
        # reachable": we queried it and nothing failed. Planning branches its
        # confidence reasoning on this to distinguish an infrastructure problem
        # ("retry when the graph service is restored") from an empty index
        # ("index the repository"), so conflating the two sends users to fix the
        # wrong thing.
        "graph_available": _graph_reachable(ledger),
        "graph_has_data": ledger.has_fact("component", "topic"),
        "planning_metadata": {
            "detected_reference_types": [f.value.get("type") for f in ledger.facts_of("reference")],
            "reasoning_cycles": state.metadata.iteration,
            "providers_consulted": sorted({e.provider for e in ledger.evidence}),
        },
        # --- readiness / confidence --------------------------------------
        "goal": state.metadata.goal,
        "readiness": state.readiness,
        "completion_status": state.completion_status,
        "confidence": state.confidence,
        # Same value as `confidence` above, honestly named — see
        # ContextDiscoveryResult.context_completeness's own docstring.
        # Additive, not a recomputation.
        "context_completeness": state.confidence,
        "capability_confidence": {a.capability: a.score for a in state.assessments},
        "clarification_rounds": state.metadata.clarification_rounds,
        "blocking_reasons": [g.summary for g in blocking],
        "remediation_steps": _dedupe([step for g in blocking for step in g.recommended_action]),
        "assumptions": [
            i.statement for i in ledger.inferences if i.kind == "assumption" and not i.withdrawn
        ],
        "user_answers": {
            g.question.question_id: g.user_claim
            for g in state.gaps
            if g.question is not None and g.user_claim is not None
        },
        "unresolved_questions": (
            [{**question.model_dump(), "blocking": True}] if question is not None else []
        ),
        # --- the human-facing report -------------------------------------
        "discovery_report": build_discovery_report(state),
        # The synthesis LLM's own scratch reasoning (hypotheses,
        # contradictions, and — critically — `investigation_history`, the
        # code-authored log `_map_reasoning` greps for the literal string
        # "synthesis degraded" to detect a failed synthesis pass). Projected
        # as its own top-level key, unconditionally, same as
        # `engineering_understanding` above — *not* gated on `question is
        # not None` the way `working_memory` below is. It used to be
        # readable only via `working_memory.derived.investigation_workspace`,
        # which is deliberately omitted once discovery finishes (see that
        # field's own comment) — meaning a completed run's degraded-
        # synthesis signal was silently discarded before the mapper ever
        # saw it, and every completed investigation reported `degraded=
        # False` regardless of what synthesis actually did. See the P1
        # regression this fixes: a real LLM timeout was shown to the user
        # as "no competing hypotheses were needed."
        "investigation_workspace": state.derived.get("investigation_workspace") or {},
        "investigation_priority": state.derived.get("investigation_priority") or {},
        # --- the full working memory, for resume -------------------------
        # Only carried while the run is actually paused. It exists so a resumed
        # run continues from real state rather than a lossy reconstruction of
        # this flat projection — an earlier design rebuilt a WorkingContext by
        # re-parsing its own output, so the resumed run reasoned over strictly
        # less than the paused one knew.
        #
        # Omitted once discovery has finished, because nothing reads it then and
        # it is a second full copy of the ledger already projected into
        # `graph_components`/`discovery_report`. On a large monorepo that is
        # thousands of duplicated rows in every persisted AgentStep. (Reasoning
        # state specifically no longer depends on this — see
        # `investigation_workspace` above.)
        "working_memory": state.model_dump() if question is not None else {},
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def restore(result: dict[str, Any]) -> WorkingContext:
    """Rebuild working memory from a persisted paused result — the exact
    object that was saved, not an approximation of it (see `working_memory`
    above)."""
    memory = result.get("working_memory")
    if not memory:
        raise ValueError(
            "Cannot resume: this run has no persisted working memory. Working memory is "
            "only kept while a run is paused awaiting an answer, so this run either already "
            "finished or predates reasoning-driven discovery."
        )
    return WorkingContext.model_validate(memory)
