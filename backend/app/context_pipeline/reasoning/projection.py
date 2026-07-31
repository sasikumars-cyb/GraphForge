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

from typing import Any

from app.agents._contract import Evidence
from app.context_pipeline.providers import wrap_artifact_text
from app.context_pipeline.reasoning.capabilities import GRAPH_TRAVERSAL_ACTION
from app.context_pipeline.reasoning.ledger import EvidenceRecord, Ledger
from app.context_pipeline.reasoning.memory import WorkingContext

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
    """Whether the knowledge graph was queried without an observed failure."""
    graph_evidence = [e for e in ledger.evidence if e.provider == "graph"]
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
        "confidence": state.confidence,
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

    return {
        # --- what Planning reads -----------------------------------------
        "original_request": state.derived.get("original_request") or state.metadata.goal,
        "enriched_text": state.derived.get("enriched_text") or state.metadata.goal,
        "resolved_references": [dict(f.value) for f in ledger.facts_of("reference")],
        "indexed_repositories": [dict(f.value) for f in ledger.facts_of("repository")],
        "graph_components": [dict(f.value) for f in ledger.facts_of("component")],
        "graph_topics": [dict(f.value) for f in ledger.facts_of("topic")],
        # The full relevance ordering of every indexed repository, best first.
        # Planning treats this as a ranking — star ratings by list position and
        # `[0]` as the target repository for its component-ownership
        # verification — so it must stay complete. The narrower "which of these
        # is actually the implementation site" judgement is
        # `implementation_candidates` below.
        "ranked_repository_names": (
            state.derived.get("ranked_repositories")
            or [i.statement for i in ledger.live_inferences("repository_candidate")]
            or ledger.subjects_of("repository")
        ),
        # Discovery's own interpretation: the repositories it believes this work
        # could belong to, each citing the repository facts that support it.
        # More than one entry means the ambiguity is genuine and unresolved.
        "implementation_candidates": [
            i.statement for i in ledger.live_inferences("repository_candidate")
        ],
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
        "confidence": state.confidence,
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
        # thousands of duplicated rows in every persisted AgentStep.
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
