"""The cognitive reasoning layer above retrieval — turns already-curated
evidence into engineering UNDERSTANDING, not another retrieval pass.

Everything upstream of this module (the investigation loop, the ledger, the
capability/confidence system, `curation.py`'s tiered `EvidencePackage`) is
unchanged and untouched by this redesign — it is production-ready
infrastructure that already does deterministic, evidence-first retrieval
correctly. What was missing is the layer a Principal Engineer's own head
would add on top of a pile of retrieved facts: competing hypotheses about
*why* the current behavior happens, cross-source synthesis ("this ticket's
requirement is implemented by X and tested by Y"), engineering insights
("this duplicates an existing abstraction"), and an honest accounting of
what remains unknown — all of it self-critiqued before being handed to
Planning.

Two models, two audiences:

- `InvestigationWorkspace` is scratch reasoning. Hypotheses, open questions,
  dead ends, candidate architecture not yet confirmed. It is stored for
  traceability/debugging only — **Planning must never read it** (see
  `synthesize_engineering_understanding`'s call sites: only
  `engineering_understanding` is threaded into any prompt).
- `EngineeringUnderstanding` is the validated conclusion — the only thing
  Planning consumes. Every field must be traceable to the evidence this
  module was given; it is not a place for the model to speculate.

This is a single, targeted LLM call (unlike everything upstream, which is
deterministic by design) because "why does this behave this way" and
"which existing abstraction does this duplicate" are judgment calls a
token-overlap/graph-proximity score cannot make. The call is strictly
grounded — it receives only what investigation already gathered and
curated, is instructed never to invent a repository/file/class not named
in that evidence, and degrades to a deterministic, evidence-only summary
(never raises, never blocks discovery) if the call fails or returns
something invalid. Facts remain the ledger's alone; this module's output
is always, structurally, a conclusion — never written back as a `Fact`.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.llm import invoke_llm_json
from app.agents.prompt_utils import parse_json_response
from app.context_pipeline.reasoning.curation import EvidencePackage, render_evidence_package_text
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigation_planner import (
    InvestigationTask,
    classify_engineering_strategy,
    plan_priority_boost,
    refresh_task_graph,
    seed_tasks,
    select_next_task,
)
from app.context_pipeline.reasoning.memory import WorkingContext
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

# Bounds the grounding text handed to the synthesis call — the same
# discipline `_MAX_GRAPH_CONTEXT_CHARS` applies to Planning's own prompt.
# Generous relative to that limit because this call's whole job is reading
# everything investigation gathered, not a per-agent slice of it.
_MAX_GROUNDING_CHARS = 16_000


class ContextDiscoverySynthesisError(AppError):
    status_code = 502
    error_code = "context_discovery_synthesis_error"


HypothesisStatus = Literal["supported", "rejected", "unknown"]

HypothesisSubjectKind = Literal["repository", "file", "component"]


class HypothesisSubjectEntity(BaseModel):
    """The structured, exact-match-only identity of a hypothesis's claim —
    ADR 0025 §7/§8. Set ONLY when the hypothesis's own `description` is
    itself an existence/location/attribution assertion about one specific
    named repository, file, or component (e.g. "the handler is defined in
    app/api/routes.py") — never for a causal, behavioral, predictive, or
    "why does this happen" hypothesis (e.g. "concurrent ingestion runs may
    race"), regardless of whether such a hypothesis names or discusses a
    repository/file/component in its prose. `kind` and `name` are always
    both present or the whole field is `None` — there is no partial state.
    This is the one piece of data that lets a hypothesis ever become
    `VERIFIED`/`UNVERIFIED` downstream instead of permanently
    `NOT_CHECKED` (see `app.agents.report_generation.data_plumbing.
    map_verification_status_for_subject_entity`); it is deliberately
    narrow so a claim-type mistake here can only ever produce a missed
    correlation, never a false one."""

    kind: HypothesisSubjectKind
    name: str


class Hypothesis(BaseModel):
    """One competing explanation for the behavior in question. A real
    investigation produces several of these and eliminates most — a
    workspace where every hypothesis is "supported" is a sign the model
    never tried to prove itself wrong."""

    description: str = ""
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    status: HypothesisStatus = "unknown"
    # ADR 0025 — optional, structured, exact-match-only. See
    # HypothesisSubjectEntity's own docstring for exactly when this may be
    # set. `None` is the correct, common case for most real hypotheses.
    subject_entity: HypothesisSubjectEntity | None = None


class Contradiction(BaseModel):
    """Evidence that conflicts with itself or with a hypothesis — the thing
    a real investigation must never quietly average away. `resolved`
    becomes true only once later evidence explains the conflict; until
    then it stays open and visible, and `capability_priority` below
    up-weights whichever capability might resolve it."""

    description: str = ""
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolution_note: str = ""


class InvestigationWorkspace(BaseModel):
    """The agent's working memory — and, as of this module's iterative
    redesign, the actual engine state driving investigation, not passive
    scratch space. Never consumed by Planning — see this module's
    docstring. Kept for the discovery report / debugging so a human can
    see the reasoning that produced `EngineeringUnderstanding`, not just
    the conclusion.

    `investigation_history` is the one field this module writes itself
    rather than trusting the LLM's account of it — a factual log of which
    synthesis round ran when and why, carried forward and appended to on
    every call (see `synthesize_engineering_understanding`), never
    regenerated from scratch. Everything else here is the LLM's own
    account of its current reasoning, entirely rebuilt each call from the
    complete current evidence (see this module's top-level docstring on
    why re-deriving rather than accumulating is the deliberate choice).
    """

    hypotheses: list[Hypothesis] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    dead_ends: list[str] = Field(default_factory=list)
    candidate_repositories: list[str] = Field(default_factory=list)
    candidate_architecture: list[str] = Field(default_factory=list)
    reasoning_notes: list[str] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    # What the LLM itself nominates as the highest-value next investigation
    # — free-text labels (e.g. "graph", "confluence", "github tests"), not
    # constrained to real capability keys at this layer. `capability_priority`
    # is what deterministically translates these into a boost `engine._select`
    # can actually act on; a label that matches nothing is simply ignored,
    # never guessed at.
    next_investigation_candidates: list[str] = Field(default_factory=list)
    information_gain_estimates: dict[str, float] = Field(default_factory=dict)
    investigation_history: list[str] = Field(default_factory=list)
    # The Investigation Planner's own state (see reasoning.investigation_
    # planner) — the explicit, deterministic task graph the LLM's hypotheses/
    # contradictions/gain estimates above feed into, and `engineering_
    # strategy`, the classification (bug/feature/migration/...) that decided
    # this run's graph shape in the first place. Both are code-authored, not
    # LLM-authored, for the same reason `investigation_history` is: a plan's
    # own structure should not depend on the model remembering to preserve it
    # correctly across rounds.
    investigation_graph: list[InvestigationTask] = Field(default_factory=list)
    engineering_strategy: str = ""


class EngineeringUnderstanding(BaseModel):
    """The validated output — the only object Planning is allowed to read.
    Every field here is expected to be a conclusion, not speculation: if
    the evidence didn't support an answer, the honest value is "" / [] /
    a `remaining_unknowns` entry, never a fabricated one."""

    business_objective: str = ""
    current_behavior: str = ""
    desired_behavior: str = ""
    primary_repository: str = ""
    supporting_repositories: list[str] = Field(default_factory=list)
    implementation_ownership: list[str] = Field(default_factory=list)
    architecture_relationships: list[str] = Field(default_factory=list)
    reusable_components: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    validated_assumptions: list[str] = Field(default_factory=list)
    rejected_assumptions: list[str] = Field(default_factory=list)
    remaining_unknowns: list[str] = Field(default_factory=list)
    confidence: dict[str, float] = Field(default_factory=dict)
    engineering_insights: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT = """You are a Principal Engineer performing an engineering investigation \
inside GraphForge, a multi-agent SDLC system. Retrieval is already finished — everything \
you need has already been gathered and curated below. You are not a search engine and you \
must not behave like one: your job is to convert already-gathered evidence into engineering \
UNDERSTANDING, not to ask for more evidence or restate what was retrieved.

Ground rules, all mandatory:

1. Use ONLY the evidence given below. Never invent a repository, file, class, test, or fact \
that isn't named in it. An unknown stays in `remaining_unknowns` — never guessed at.
2. Generate multiple COMPETING hypotheses about why the current behavior happens before \
settling on one. For each, actively look for evidence that would prove it wrong, not only \
evidence that confirms it. Mark each "supported", "rejected", or "unknown" — a workspace \
where every hypothesis ends up "supported" means you never tried to eliminate any, which is \
not a real investigation.
3. Synthesize across sources instead of listing them independently: relate the ticket to \
specific evidence items ("the ticket's requirement X is implemented by Y and tested by Z"), \
not "here is the ticket, here is Y, here is Z" as separate paragraphs.
4. Keep facts, conclusions, assumptions, and unknowns strictly separate. A conclusion must \
be traceable to specific evidence cited below it — never a bare assertion.
5. Confidence values are per-category, in [0, 1], and must reflect how complete the evidence \
for that specific category actually is. Do not repeat one flat number across every category.
6. If the evidence is too thin to conclude something (no tests were found at all, no \
architecture graph reached this repository), say so honestly in `remaining_unknowns` rather \
than fabricating a plausible-sounding answer.
7. Before finalizing, challenge your own conclusions: could another repository or component \
own this behavior instead? Is there evidence that contradicts your leading hypothesis? Would \
an experienced Principal Engineer investigate anything else first? If genuine uncertainty \
remains, it belongs in `remaining_unknowns`, not smoothed over.
8. If two pieces of evidence conflict, or evidence conflicts with a hypothesis you'd otherwise \
favor, record it as a `contradiction` — do not silently average it away or pick a side without \
saying so. An unresolved contradiction should also lower that hypothesis's confidence.
9. If a previous round's hypotheses/contradictions are given below, do not just repeat them — \
confirm, refute, or refine each one using the NEW evidence, and say plainly when new evidence \
has changed your mind (a hypothesis moving from "supported" to "rejected" is exactly the kind \
of update this investigation exists to make).
10. Nominate, in `next_investigation_candidates`/`information_gain_estimates`, which of the \
following would most improve understanding if investigated next — use ONLY these labels, \
scored 0 (useless) to 1 (highly valuable): "work_item" (re-reading/clarifying the ticket), \
"repository" (confirming which repository owns this), "architecture" (graph/dependency \
traversal), "documentation" (Confluence/design docs). Do not invent other labels — an \
unlisted capability cannot be acted on.
11. For each hypothesis, set `subject_entity` ONLY when the hypothesis's `description` IS \
ITSELF an existence, location, or attribution claim about one specific named repository, \
file, or component — i.e. it asserts WHERE something is or WHICH named thing owns it, and \
nothing more. Example that qualifies: "The handler is defined in app/api/routes.py" -> \
{"kind": "file", "name": "app/api/routes.py"}. Examples that do NOT qualify, even though \
they name or discuss a repository/file/component: "Concurrent ingestion runs may race and \
both write an active record" (a behavior/causation claim); "PaymentGatewayV1 is only \
referenced by 3 legacy call sites, all already migrated" (a reasoning/assessment claim, not \
a location claim); any hypothesis about WHY something happens, WHETHER something is safe, or \
WHAT the impact of a change would be. When in doubt, leave `subject_entity` null — a missed \
opportunity to link a hypothesis to a verification check is far preferable to a wrong one, \
and most real hypotheses correctly have no `subject_entity` at all. `name` must be copied \
exactly as it appears in the evidence given below (a real file path, component name, or \
repository name already named in this investigation) — never a paraphrase or a guess at \
what it might be called.

Respond with a single JSON object and nothing else (no markdown fences), with exactly two \
top-level keys.

"workspace" — your internal working notes, never shown to Planning directly:
{
  "hypotheses": [{"description": str, "supporting_evidence": [str], "contradicting_evidence": \
[str], "confidence": float, "status": "supported" | "rejected" | "unknown", "subject_entity": \
{"kind": "repository" | "file" | "component", "name": str} | null — see rule 11, null for \
almost every hypothesis}],
  "open_questions": [str],
  "unknowns": [str],
  "dead_ends": [str — hypotheses you considered and eliminated, and why],
  "candidate_repositories": [str],
  "candidate_architecture": [str],
  "reasoning_notes": [str],
  "contradictions": [{"description": str, "evidence_for": [str], "evidence_against": [str], \
"resolved": bool, "resolution_note": str}],
  "next_investigation_candidates": [str — from the fixed label set in rule 10],
  "information_gain_estimates": {"<label from rule 10>": float}
}

"understanding" — the validated conclusion, the ONLY thing Planning will read; every field \
must be backed by the evidence given, never speculative:
{
  "business_objective": str,
  "current_behavior": str,
  "desired_behavior": str,
  "primary_repository": str,
  "supporting_repositories": [str],
  "implementation_ownership": [str — which component/file owns which behavior],
  "architecture_relationships": [str],
  "reusable_components": [str],
  "dependencies": [str],
  "risks": [str],
  "constraints": [str],
  "validated_assumptions": [str],
  "rejected_assumptions": [str],
  "remaining_unknowns": [str],
  "confidence": {"business_objective": float, "current_behavior": float, "desired_behavior": \
float, "implementation_ownership": float, "architecture": float, "dependencies": float, \
"reuse": float, "risks": float, "overall": float},
  "engineering_insights": [str — e.g. "existing implementation already satisfies most of the \
requested behavior", "this duplicates an existing abstraction", "this change crosses \
repository boundaries" — state one only if the evidence actually supports it]
}
"""


def _ticket_sections(state: WorkingContext) -> dict[str, str]:
    work_items = state.ledger.facts_of("work_item")
    if not work_items:
        return {}
    return {str(k): str(v) for k, v in (work_items[0].value.get("sections") or {}).items()}


def _repository_context_lines(state: WorkingContext) -> list[str]:
    lines: list[str] = []
    for inference in state.ledger.live_inferences("repository_candidate"):
        source = inference.value.get("source", "suggested")
        reason = inference.value.get("reason", "")
        relationship = inference.value.get("relationship", "")
        line = f"- {inference.statement} (source={source})"
        if reason:
            line += f": {reason}"
        if relationship:
            line += f" [{relationship}]"
        lines.append(line)
    return lines


def _gap_lines(state: WorkingContext) -> list[str]:
    return [f"- [{gap.severity}/{gap.status}] {gap.summary} — {gap.why}" for gap in state.gaps]


def _assumption_lines(state: WorkingContext) -> list[str]:
    return [
        f"- {inference.statement}"
        for inference in state.ledger.inferences
        if inference.kind == "assumption" and not inference.withdrawn
    ]


def _previous_round_lines(previous: InvestigationWorkspace | None) -> list[str]:
    if previous is None:
        return []
    lines: list[str] = []
    if previous.hypotheses:
        lines.append("Hypotheses from the previous round (confirm, refute, or refine each):")
        for hyp in previous.hypotheses:
            lines.append(f"- [{hyp.status}] {hyp.description} (confidence {hyp.confidence:.0%})")
    unresolved = [c for c in previous.contradictions if not c.resolved]
    if unresolved:
        lines.append("Unresolved contradictions from the previous round:")
        for contradiction in unresolved:
            lines.append(f"- {contradiction.description}")
    return lines


def _build_grounding_text(
    state: WorkingContext,
    package: EvidencePackage,
    ticket_sections: dict[str, str],
    previous_workspace: InvestigationWorkspace | None = None,
) -> str:
    """Everything the synthesis call is allowed to reason over — nothing
    more. Deliberately built from the same already-gathered material every
    other consumer reads (ledger facts, the curated evidence package, live
    repository/assumption inferences, open gaps) rather than performing any
    retrieval of its own."""
    sections: list[str] = []

    request = state.derived.get("original_request") or state.metadata.goal
    if request:
        sections.append(f"## Request\n{request}")

    if ticket_sections:
        rendered = "\n".join(f"**{key}**: {value}" for key, value in ticket_sections.items())
        sections.append(
            "## Ticket sections (deterministically extracted, not LLM-summarized)\n" + rendered
        )

    enriched = state.derived.get("enriched_text") or ""
    if enriched:
        sections.append(
            "## Retrieved source text (Jira/Confluence/GitHub, already fetched)\n" + enriched
        )

    repo_lines = _repository_context_lines(state)
    if repo_lines:
        sections.append(
            "## Repository candidates identified by investigation\n" + "\n".join(repo_lines)
        )

    sections.append(
        "## Curated architecture evidence (already ranked, scored, and tiered — do not "
        "re-rank or re-derive this)\n" + render_evidence_package_text(package)
    )

    gap_lines = _gap_lines(state)
    if gap_lines:
        sections.append(
            "## Known gaps in this investigation (already identified — do not "
            "re-discover these, incorporate them as unknowns/risks)\n" + "\n".join(gap_lines)
        )

    assumption_lines = _assumption_lines(state)
    if assumption_lines:
        sections.append(
            "## Assumptions investigation is proceeding on (unvalidated)\n"
            + "\n".join(assumption_lines)
        )

    previous_lines = _previous_round_lines(previous_workspace)
    if previous_lines:
        sections.append(
            "## Previous investigation round (see ground rule 9 — update, don't just repeat)\n"
            + "\n".join(previous_lines)
        )

    return "\n\n".join(sections)[:_MAX_GROUNDING_CHARS]


def _deterministic_understanding(
    state: WorkingContext, package: EvidencePackage, ticket_sections: dict[str, str]
) -> EngineeringUnderstanding:
    """The floor: what can be said about this run without any LLM
    reasoning at all, built purely from already-structured facts. Used
    when there is nothing to synthesize (no components, no ticket text)
    and as the fallback when the synthesis call itself fails — never
    raises, never leaves `engineering_understanding` empty on top of an
    error the caller would otherwise have to handle."""
    must_modify = package.by_tier("must_modify")
    dependencies = package.by_tier("architecture_dependency")
    reusable = package.by_tier("reusable_component")

    primary_repository = must_modify[0].repository if must_modify else ""
    other_repos = {item.repository for item in dependencies if item.repository}
    supporting = sorted(other_repos - {primary_repository})

    def _describe(item: Any) -> str:
        return f"{item.name} ({item.repository}, {item.path})"

    return EngineeringUnderstanding(
        business_objective=(
            ticket_sections.get("business_goal", "") or ticket_sections.get("problem", "")
        ),
        desired_behavior=ticket_sections.get("acceptance_criteria", ""),
        constraints=(
            [ticket_sections["constraints"]] if ticket_sections.get("constraints") else []
        ),
        primary_repository=primary_repository,
        supporting_repositories=supporting,
        implementation_ownership=[_describe(item) for item in must_modify],
        architecture_relationships=[_describe(item) for item in dependencies],
        reusable_components=[_describe(item) for item in reusable],
        remaining_unknowns=[
            *_gap_lines(state),
            "Engineering synthesis (hypothesis reasoning, cross-source insight) did not run "
            "for this investigation — the fields above are a deterministic summary of the "
            "curated evidence only.",
        ],
        confidence={"overall": state.confidence},
    )


def _previous_workspace(state: WorkingContext) -> InvestigationWorkspace | None:
    dump = state.derived.get("investigation_workspace")
    if not dump:
        return None
    try:
        return InvestigationWorkspace.model_validate(dump)
    except Exception:
        return None


def _hypothesis_status_changes(
    previous: InvestigationWorkspace | None, current: InvestigationWorkspace
) -> list[str]:
    """Which hypotheses flipped status between rounds, matched by exact
    description text (best-effort — a hypothesis the model re-describes
    slightly just reads as new, which is honest: this module has no way to
    know two descriptions mean the same thing without guessing)."""
    if previous is None:
        return []
    previous_status = {
        hyp.description: hyp.status for hyp in previous.hypotheses if hyp.description
    }
    changes: list[str] = []
    for hyp in current.hypotheses:
        prior = previous_status.get(hyp.description)
        if prior is not None and prior != hyp.status:
            changes.append(f'"{hyp.description}" moved {prior} -> {hyp.status}')
    return changes


def _history_entry(
    state: WorkingContext,
    previous: InvestigationWorkspace | None,
    current: InvestigationWorkspace,
    *,
    degraded: bool,
    selected_task: InvestigationTask | None = None,
) -> str:
    """One deterministic, code-authored line recording what this synthesis
    round actually did — appended to (never regenerated from) `investigation_
    history`, unlike every other workspace field (see `InvestigationWorkspace`'s
    own docstring on why this one field is different)."""
    evidence_count = len(state.ledger.evidence)
    if degraded:
        return (
            f"Cycle {state.metadata.iteration}: synthesis degraded to a deterministic summary "
            f"over {evidence_count} evidence record(s)."
        )
    changes = _hypothesis_status_changes(previous, current)
    unresolved = sum(1 for c in current.contradictions if not c.resolved)
    detail = f"{len(current.hypotheses)} hypothesis/es, {unresolved} unresolved contradiction(s)"
    if changes:
        detail += "; changed: " + "; ".join(changes)
    if selected_task is not None:
        detail += (
            f"; planner selected '{selected_task.purpose}' next "
            f"(capability={selected_task.required_capability or 'none'}, "
            f"expected gain={selected_task.expected_information_gain:.2f}) — "
            f"{selected_task.reason_for_creation}"
        )
    else:
        detail += "; planner has no actionable task ready"
    return (
        f"Cycle {state.metadata.iteration}: re-synthesized over {evidence_count} evidence "
        f"record(s) — {detail}."
    )


def _advance_investigation_graph(
    state: WorkingContext,
    previous: InvestigationWorkspace | None,
    workspace: InvestigationWorkspace,
) -> list[InvestigationTask]:
    """Classify the engineering problem once (persisting the classification
    across rounds, see `InvestigationWorkspace.engineering_strategy`), seed
    the strategy-specific graph once, and refresh it every call against the
    ledger's current capability assessments and this round's contradictions
    (see `investigation_planner.refresh_task_graph`). Called from every
    branch of `synthesize_engineering_understanding` — including the
    degraded and empty-ledger paths — because the graph itself is pure
    Python with no LLM cost, so there is no reason to skip planning just
    because synthesis degraded.
    """
    if previous is not None and previous.engineering_strategy:
        workspace.engineering_strategy = previous.engineering_strategy
    else:
        request_text = state.derived.get("original_request") or state.metadata.goal or ""
        workspace.engineering_strategy = classify_engineering_strategy(request_text)

    base_tasks = (
        previous.investigation_graph
        if previous is not None and previous.investigation_graph
        else seed_tasks(workspace.engineering_strategy)  # type: ignore[arg-type]
    )
    refreshed = refresh_task_graph(
        base_tasks, assessments=state.assessments, contradictions=workspace.contradictions
    )
    workspace.investigation_graph = refreshed
    return refreshed


async def synthesize_engineering_understanding(
    state: WorkingContext, session: SessionContext
) -> None:
    """Run after evidence curation and after each meaningful evidence-
    gathering step during investigation (see `engine.investigate`'s call
    sites) — not a one-shot post-hoc pass. Every call fully re-derives
    `workspace`/`understanding` from the *complete* current ledger (see
    this module's own docstring on recomputing vs. accumulating), except
    `investigation_history`, which this function explicitly carries
    forward and appends one deterministic entry to on every call.

    Writes `state.derived["investigation_workspace"]` (internal only —
    Planning must never read it) and `state.derived["engineering_
    understanding"]` (what Planning reads via `render_engineering_
    understanding_text`), and `state.derived["investigation_priority"]`
    (see `capability_priority` — what `engine._select` consults to prefer
    the investigation expected to most improve understanding). Never
    raises — a failed or invalid synthesis degrades to
    `_deterministic_understanding` rather than blocking discovery, exactly
    like `curate_evidence`'s own graceful degradation on a failed graph
    read.
    """
    package_dump = state.derived.get("evidence_package") or {}
    try:
        package = EvidencePackage.model_validate(package_dump)
    except Exception:
        package = EvidencePackage()

    ticket_sections = _ticket_sections(state)
    previous = _previous_workspace(state)
    grounding = _build_grounding_text(state, package, ticket_sections, previous)

    if not (state.ledger.facts or ticket_sections or package.items):
        # Nothing was ever gathered — e.g. a request with no resolvable
        # reference. Synthesizing over an empty ledger would be the model
        # inventing understanding from nothing, exactly what rule 1 forbids.
        workspace = InvestigationWorkspace()
        graph = _advance_investigation_graph(state, previous, workspace)
        workspace.investigation_history = [
            *(previous.investigation_history if previous else []),
            _history_entry(
                state, previous, workspace, degraded=True, selected_task=select_next_task(graph)
            ),
        ]
        state.derived["investigation_workspace"] = workspace.model_dump()
        # Nothing was ever gathered, so no synthesis call was even
        # attempted — this is "not applicable," not "failed." See
        # `reasoning.projection._resolve_synthesis_run_state` / ADR 0024
        # §11 for the full four-state model this feeds.
        state.derived["investigation_workspace_run_state"] = "not_run"
        state.derived["engineering_understanding"] = _deterministic_understanding(
            state, package, ticket_sections
        ).model_dump()
        state.derived["investigation_priority"] = plan_priority_boost({}, graph)
        return

    degraded = False
    state.metadata.synthesis_calls += 1
    try:
        raw = await invoke_llm_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=grounding,
            stage=session.stage,
            model=session.model,
            error_cls=ContextDiscoverySynthesisError,
            context=session.agent_context,
            purpose="synthesis",
            sequence=state.metadata.synthesis_calls,
        )
        data = parse_json_response(raw, ContextDiscoverySynthesisError)
        workspace = InvestigationWorkspace.model_validate(data.get("workspace") or {})
        understanding = EngineeringUnderstanding.model_validate(data.get("understanding") or {})
    except Exception:
        logger.exception("context_discovery_synthesis_failed")
        degraded = True
        workspace = InvestigationWorkspace(
            reasoning_notes=[
                "Synthesis call failed or returned an invalid response; falling "
                "back to a deterministic, evidence-only summary."
            ]
        )
        understanding = _deterministic_understanding(state, package, ticket_sections)
        understanding.remaining_unknowns = [
            *understanding.remaining_unknowns,
            "Engineering synthesis (LLM reasoning pass) failed — see logs for the underlying "
            "error.",
        ]

    graph = _advance_investigation_graph(state, previous, workspace)
    workspace.investigation_history = [
        *(previous.investigation_history if previous else []),
        _history_entry(
            state, previous, workspace, degraded=degraded, selected_task=select_next_task(graph)
        ),
    ]

    state.derived["investigation_workspace"] = workspace.model_dump()
    # See the zero-evidence branch above for "not_run" — this is the other
    # two of the three raw states `_resolve_synthesis_run_state` reads;
    # "completed_empty" vs "completed" is resolved downstream from the
    # workspace's own list lengths, not decided here.
    state.derived["investigation_workspace_run_state"] = "failed" if degraded else "completed"
    state.derived["engineering_understanding"] = understanding.model_dump()
    state.derived["investigation_priority"] = plan_priority_boost(
        capability_priority(workspace), graph
    )


# The only capability keys `engine._select` can actually act on (see
# `capabilities.py`'s registry) — anything the LLM nominates outside this
# set is deliberately ignored rather than guessed into the nearest match.
_KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {"work_item", "repository", "architecture", "documentation"}
)


def capability_priority(workspace: InvestigationWorkspace) -> dict[str, float]:
    """Deterministic translation of the workspace's own information-gain
    estimates into a priority boost per real capability key — the thing
    `engine._select` consults to prefer the investigation expected to most
    improve understanding, without making action *selection* itself an
    LLM call (see `engine._select`'s own docstring for why that stays
    deterministic and testable: which provider can answer a given question
    is read here from what the LLM already said in `information_gain_
    estimates`/`next_investigation_candidates`, not decided freshly by
    another model call at selection time).

    An unresolved contradiction always earns its capability a minimum
    boost even if the LLM didn't also score it in `information_gain_
    estimates` — a live contradiction is exactly the kind of thing worth
    investigating regardless of whether the model remembered to price it.
    """
    priority: dict[str, float] = {}
    for label, gain in workspace.information_gain_estimates.items():
        if label in _KNOWN_CAPABILITIES:
            priority[label] = max(priority.get(label, 0.0), max(0.0, min(1.0, float(gain))))
    for label in workspace.next_investigation_candidates:
        if label in _KNOWN_CAPABILITIES and label not in priority:
            priority[label] = 0.3
    if any(not c.resolved for c in workspace.contradictions):
        # Which capability the LLM most plausibly meant is unknowable from
        # a contradiction's free text alone without guessing — so an
        # unresolved contradiction boosts "architecture", the capability
        # whose own evidence (the graph) is what confirms or refutes a
        # behavioral claim more often than any other single source.
        priority["architecture"] = max(priority.get("architecture", 0.0), 0.4)
    return priority


_UNDERSTANDING_SECTIONS: tuple[tuple[str, str], ...] = (
    ("business_objective", "Business objective"),
    ("current_behavior", "Current behavior"),
    ("desired_behavior", "Desired behavior"),
    ("primary_repository", "Primary repository"),
)
_UNDERSTANDING_LIST_SECTIONS: tuple[tuple[str, str], ...] = (
    ("supporting_repositories", "Supporting repositories"),
    ("implementation_ownership", "Implementation ownership"),
    ("architecture_relationships", "Architecture relationships"),
    ("reusable_components", "Reusable components"),
    ("dependencies", "Dependencies"),
    ("constraints", "Constraints"),
    ("risks", "Risks"),
    ("validated_assumptions", "Validated assumptions"),
    ("rejected_assumptions", "Rejected assumptions"),
    ("remaining_unknowns", "Remaining unknowns"),
    ("engineering_insights", "Engineering insights"),
)


def render_engineering_understanding_text(understanding: EngineeringUnderstanding) -> str:
    """Planning's primary prompt input under this redesign — engineering
    understanding, not raw evidence (see this module's own docstring and
    `app.agents.planning.agent._graph_context_text_from`, which appends
    the curated evidence package after this text for traceability only).

    Empty when `understanding` has nothing set at all — the caller falls
    back to the pre-existing evidence-package rendering in that case.
    """
    lines: list[str] = []
    for field, heading in _UNDERSTANDING_SECTIONS:
        value = getattr(understanding, field)
        if value:
            lines.append(f"**{heading}**: {value}")
    for field, heading in _UNDERSTANDING_LIST_SECTIONS:
        values: list[str] = getattr(understanding, field)
        if values:
            lines.append(f"**{heading}**:\n" + "\n".join(f"- {v}" for v in values))
    return "\n\n".join(lines)
