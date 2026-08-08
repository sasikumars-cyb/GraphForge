"""The reasoning loop — Context Discovery's actual control flow.

    while budget remains:
        re-derive confidence from facts
        re-derive which knowledge gaps are still open
        if nothing is unmet:            -> done, READY
        ask every investigator what it could contribute right now
        if nobody proposes anything:    -> providers exhausted, stop
        pick the single most valuable action, narrate the intent,
        run it, fold facts/evidence into working memory, narrate the result

    then, and only then, consider asking the human one question

Three properties this file exists to guarantee:

**The human is the last resort.** `next_question()` refuses to return
anything until `providers_exhausted` is set, and that flag is only set when
every investigator has declined to propose work. A question is therefore
never the engine's first move, and the question itself carries the list of
what was tried before resorting to asking.

**Answers are verified, not trusted.** A human answer becomes a `claimed`
gap and an *unverified* `user_statement` fact. It closes nothing by itself.
Investigators then propose verification work (the graph re-queries the named
repository; Jira re-fetches the corrected key), and only a real corroborating
fact flips the gap to `verified`. If the corroboration doesn't come, the gap
is `refuted` and confidence does not move — because confidence reads facts,
and no fact was created. There is deliberately no code path that marks a gap
resolved because an answer arrived.

**Every conclusion is traceable.** The engine writes nothing to the ledger
itself except through a `Recorder`, so every fact keeps its evidence and
every interpretation keeps its facts.
"""

from __future__ import annotations

import logging
import time

from app.context_pipeline.reasoning import capabilities
from app.context_pipeline.reasoning.capabilities import (
    CapabilityAssessment,
    ClarificationQuestion,
    QuestionContext,
    unmet,
)
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    InvestigationOutcome,
    Investigator,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.investigation_planner import classify_engineering_strategy
from app.context_pipeline.reasoning.investigators import default_investigators
from app.context_pipeline.reasoning.memory import KnowledgeGap, WorkingContext
from app.investigation_intelligence.contracts import (
    CURRENT_SNAPSHOT_VERSION,
    CandidateScore,
    InvestigationOutcomeEvent,
    InvestigationScope,
    ProviderOutcomeEvent,
    StateSnapshot,
)

logger = logging.getLogger(__name__)

# How many gather-and-reassess cycles one discovery run may perform. Reached
# only by a genuinely tangled request — the normal path terminates early
# because investigators stop proposing once requirements are met.
MAX_CYCLES = 8

# How many times the human may be asked across one discovery run. Past this,
# remaining blocking gaps are reported as unresolvable rather than becoming
# an endless interrogation.
MAX_CLARIFICATION_ROUNDS = 2

# How many times `synthesize_engineering_understanding` may run *inside* the
# gather loop (not counting the always-run call once the loop exits). Bounds
# the cost of "understanding drives investigation": each mid-loop call is a
# real LLM invocation, so this stays a fixed, small budget rather than one
# call per cycle — re-synthesizing after every single retrieval on an
# 8-cycle run would be 8 additional LLM calls for a discovery run that used
# to make exactly one. Deliberately 1, not more: one mid-loop checkpoint is
# enough for a live hypothesis/contradiction to redirect the *rest* of the
# run (see the `priority_boost` it produces, consulted by every remaining
# `_select` call), and keeps worst-case cost at 2x a single-synthesis run
# (one mid-loop + the always-run final call) rather than growing with cycle
# count.
MAX_MID_LOOP_SYNTHESIS_CALLS = 1


# ---------------------------------------------------------------------------
# Gap derivation — one gap per unmet capability, stable identity across cycles
# ---------------------------------------------------------------------------


def _sync_gaps(state: WorkingContext) -> None:
    """Re-derive gaps from the current assessments.

    Called once investigation has stopped, not on every cycle: a capability
    that is unmet in cycle 1 purely because nothing has been gathered yet is
    not a "gap", it's just work not done. Materializing gaps mid-loop produced
    a report full of resolved complaints carrying the stale "missing" details
    they had before the evidence arrived.

    Gaps are keyed by capability so a human answer recorded against one
    survives across cycles; everything else about them is refreshed from the
    current assessment.
    """
    pending = {a.capability: a for a in unmet(state.assessments)}

    for gap in state.gaps:
        # A `claimed` gap is deliberately left alone: only `_settle_claims` may
        # decide whether a human's answer held, because that decision also
        # promotes the claim's fact and narrates the outcome. Auto-closing it
        # here because the capability now looks satisfied silently skipped both
        # — the user was never told their answer had been confirmed, and the
        # claim stayed marked unverified forever.
        if gap.capability not in pending and gap.status == "open":
            # Closed by investigation. Kept rather than deleted so the
            # transcript's account of the run stays whole, but the stale
            # "what's missing" list is cleared — nothing is.
            gap.status = "verified"
            gap.missing_signals = []
            gap.resolution_note = "Closed by evidence gathered during discovery."

    for key, assessment in pending.items():
        capability = capabilities.get(key)
        if capability is None:
            continue
        # Framing, remediation and severity all come from the capability's own
        # declaration, so this loop knows nothing about any specific capability
        # and a new one needs no change here.
        missing = [
            f"{sig.label} — {sig.detail}" if sig.detail else sig.label for sig in assessment.missing
        ]
        remediation = capability.remediation(state.ledger)
        severity = "blocking" if assessment.necessity == "required" else "advisory"

        existing = state.gap_for(key)
        if existing is None:
            state.gaps.append(
                KnowledgeGap(
                    gap_id=f"gap_{key}",
                    capability=key,
                    summary=capability.gap_summary,
                    why=capability.gap_why,
                    severity=severity,
                    recommended_action=remediation,
                    missing_signals=missing,
                )
            )
        else:
            existing.recommended_action = remediation
            existing.missing_signals = missing
            existing.severity = severity
            if existing.status == "verified":
                # Re-opened: something that looked closed no longer is.
                existing.status = "open"
                existing.resolution_note = ""


# ---------------------------------------------------------------------------
# Action selection
# ---------------------------------------------------------------------------


def _candidate_actions(
    state: WorkingContext, investigators: list[Investigator]
) -> list[tuple[InvestigationAction, Investigator]]:
    candidates: list[tuple[InvestigationAction, Investigator]] = []
    for investigator in investigators:
        try:
            proposed = investigator.propose(state)
        except Exception:
            # A misbehaving investigator must not end the whole investigation
            # — it just contributes nothing this cycle.
            logger.exception("context_discovery_propose_failed investigator=%s", investigator.name)
            continue
        for action in proposed:
            if state.ledger.attempted(action.provider, action.key):
                continue
            candidates.append((action, investigator))
    return candidates


def _select(
    candidates: list[tuple[InvestigationAction, Investigator]],
    assessments: list[CapabilityAssessment],
    priority_boost: dict[str, float] | None = None,
) -> tuple[InvestigationAction, Investigator]:
    """Pick the most valuable next action.

    Deterministic and explainable, in priority order:

    1. Actions targeting a *required* capability beat actions targeting a
       recommended one — never spend a turn on documentation while the
       repository is still unknown.
    2. Among those, target the weakest capability first (lowest score),
       adjusted downward by `priority_boost` (see
       `reasoning.understanding.capability_priority`) — engineering
       understanding's own read of which investigation would most improve
       its current hypotheses, so a capability the ledger scores as
       "adequate" can still be picked next when an unresolved contradiction
       or a live hypothesis specifically calls for more evidence there.
    3. Cheaper actions win ties, so a local graph query is preferred over a
       multi-turn MCP conversation.

    Still deliberately not an LLM call. `priority_boost` is itself a plain
    dict computed once, earlier, by a deterministic function reading the
    LLM's *already-produced* workspace — not a fresh judgement call made
    here. Which provider can structurally answer a given question stays a
    property this function reads off `assessments`/`priority_boost`, never
    something it asks a model to decide live, so action selection remains
    reproducible and unit-testable given a fixed `(assessments,
    priority_boost)` pair.
    """
    necessity_rank = {"required": 0, "recommended": 1, "not_applicable": 2}
    by_capability = {a.capability: a for a in assessments}
    boost = priority_boost or {}

    def sort_key(entry: tuple[InvestigationAction, Investigator]) -> tuple[int, float, int, str]:
        action, _ = entry
        assessment = by_capability.get(action.targets)
        necessity = assessment.necessity if assessment else "recommended"
        score = assessment.score if assessment else 1.0
        adjusted_score = score - boost.get(action.targets, 0.0)
        return (necessity_rank[necessity], adjusted_score, action.cost, action.key)

    return sorted(candidates, key=sort_key)[0]


# ---------------------------------------------------------------------------
# Investigation Intelligence (ADR 0021) — Phase 1 collection + one capped
# heuristic. Every function here degrades to "no signal" on any ambiguity
# (no repository fact yet, no enabled connection, no `intelligence` wired
# in at all) rather than guessing — this is a hint layered onto `_select()`,
# never a second decision path.
# ---------------------------------------------------------------------------


def _repository_scope(state: WorkingContext) -> InvestigationScope | None:
    """The repository the ledger has already established, if any —
    `None` before the first repository fact exists (e.g. cycle 1), or for
    an investigation that never pins one down at all (a broad survey that
    stays BLOCKED). The caller skips recording rather than inventing a
    scope, matching every other read/write in this section."""
    repo_facts = state.ledger.facts_of("repository")
    if not repo_facts:
        return None
    return InvestigationScope(scope_type="repository", scope_id=repo_facts[0].subject)


async def _investigation_scope(
    state: WorkingContext, session: SessionContext, action: InvestigationAction
) -> InvestigationScope | None:
    """`documentation` actions are scoped to the `KnowledgeConnection` the
    action's own provider name identifies (Confluence effectiveness is a
    property of the Atlassian site, not any one repository — ADR 0021 §2);
    every other capability is scoped to the repository the ledger has
    already established. `None` when neither resolves yet — the caller
    skips recording rather than inventing a scope."""
    if action.targets == "documentation":
        from sqlalchemy import select

        from app.models.knowledge_connection import KnowledgeConnection

        stmt = (
            select(KnowledgeConnection.id)
            .where(
                KnowledgeConnection.source_type == action.provider,
                KnowledgeConnection.enabled.is_(True),
            )
            .limit(1)
        )
        connection_id = (await session.db.execute(stmt)).scalar_one_or_none()
        if connection_id is None:
            return None
        return InvestigationScope(scope_type="knowledge_connection", scope_id=str(connection_id))

    return _repository_scope(state)


def _open_contradictions_count(state: WorkingContext) -> int:
    understanding = state.derived.get("engineering_understanding") or {}
    return sum(1 for c in understanding.get("contradictions", []) if not c.get("resolved"))


def _state_snapshot(
    state: WorkingContext,
    candidates: list[tuple[InvestigationAction, Investigator]],
    assessments: list[CapabilityAssessment],
) -> StateSnapshot:
    by_capability = {a.capability: a for a in assessments}

    def _necessity(target: str) -> str:
        # `CapabilityAssessment.necessity` has a third value,
        # "not_applicable", that `contracts.Necessity` deliberately doesn't
        # carry (ADR 0021's schema only distinguishes required/recommended
        # for planning purposes) — collapse it to "recommended" rather than
        # widen the contract for a value Phase 1 never needs to act on.
        assessment = by_capability.get(target)
        necessity = assessment.necessity if assessment else "recommended"
        return "recommended" if necessity == "not_applicable" else necessity

    considered = tuple(
        CandidateScore(
            provider=action.provider,
            action_key=action.key,
            capability=action.targets,
            necessity=_necessity(action.targets),
            score=(by_capability[action.targets].score if action.targets in by_capability else 1.0),
            cost=action.cost,
        )
        for action, _ in candidates
    )
    return StateSnapshot(
        version=CURRENT_SNAPSHOT_VERSION,
        candidates_considered=considered,
        all_capability_scores={a.capability: a.score for a in assessments},
        open_contradictions=_open_contradictions_count(state),
    )


async def _apply_memory_priority_boost(state: WorkingContext, session: SessionContext) -> None:
    """ADR 0021 §7 — the entire Phase 1 planner change. `state.derived[
    "investigation_priority"]` is fully *recomputed* (not merged) by
    `synthesize_engineering_understanding` on every synthesis pass, so
    calling this right after each pass — mid-loop or final — blends memory
    into a fresh live-only dict every time rather than compounding an
    already-blended one.

    Repository-scoped capabilities only: `documentation`'s effectiveness is
    per-`KnowledgeConnection`, and a capability-level boost (as opposed to
    the per-provider-action boost `_investigation_scope` resolves) has no
    single provider to look it up against, so Phase 1 leaves it unboosted
    rather than guessing one."""
    if session.intelligence is None:
        return
    live_boost = state.derived.get("investigation_priority") or {}
    if not live_boost:
        return
    scope = _repository_scope(state)
    if scope is None:
        return
    memory_keys: set[str] = set()
    blended: dict[str, float] = {}
    for capability, live_value in live_boost.items():
        memory_value = 0.0
        if capability != "documentation":
            try:
                memory_value = await session.intelligence.repository_provider_preference(
                    scope=scope, capability=capability
                )
            except Exception:
                logger.exception(
                    "investigation_intelligence_priority_boost_failed capability=%s", capability
                )
                memory_value = 0.0
        if memory_value:
            memory_keys.add(capability)
        blended[capability] = min(1.0, live_value + memory_value * 0.15)
    state.derived["investigation_priority"] = blended
    state.derived["_intelligence_boost_keys"] = memory_keys


def _priority_boost_source(state: WorkingContext, capability: str, boost_applied: float) -> str:
    if not boost_applied:
        return "none"
    memory_keys: set[str] = state.derived.get("_intelligence_boost_keys") or set()
    return "both" if capability in memory_keys else "live_llm"


def _investigation_id(state: WorkingContext, session: SessionContext) -> str:
    """A stable identity for one discovery run — `run_id` from the agent
    contract's extras when a real agent run built this session (every
    production call site), falling back to the run's own goal text for
    ad-hoc callers (test fixtures) that never pass one. Never `None`: every
    recorded event needs some grouping key, and a run without a `run_id`
    still has a goal."""
    extras = session.agent_context.extras if session.agent_context is not None else {}
    run_id = extras.get("run_id")
    return str(run_id) if run_id is not None else state.metadata.goal


async def _record_provider_outcomes(
    *,
    state: WorkingContext,
    session: SessionContext,
    action: InvestigationAction,
    iteration: int,
    candidates: list[tuple[InvestigationAction, Investigator]],
    assessments_before: list[CapabilityAssessment],
    assessment_before: CapabilityAssessment | None,
    boost_applied: float,
    confidence_before: float,
    latency_ms: int,
    new_evidence_ids: list[str],
    yielded: bool,
) -> None:
    """One `ProviderOutcomeEvent` per new ledger evidence entry from this
    cycle's action — usually exactly one (see `contracts.
    ProviderOutcomeEvent`'s own docstring on why it's not always
    guaranteed to be). Never raises: `session.intelligence` swallows write
    failures itself, and every lookup here (`scope`) degrades to skipping
    the write entirely rather than fabricating a value."""
    intelligence = session.intelligence
    if intelligence is None or not new_evidence_ids:
        return
    scope = await _investigation_scope(state, session, action)
    if scope is None:
        return

    investigation_id = _investigation_id(state, session)
    investigation_type = classify_engineering_strategy(state.metadata.goal)
    necessity = assessment_before.necessity if assessment_before else "recommended"
    if necessity == "not_applicable":
        necessity = "recommended"
    base_score = assessment_before.score if assessment_before else 1.0
    priority_boost_source = _priority_boost_source(state, action.targets, boost_applied)
    snapshot = _state_snapshot(state, candidates, assessments_before)

    # Best-effort: read *before* this cycle's own `_resync()` runs, so it
    # can lag the very latest inference withdrawal by one cycle. An
    # observability signal, never a gating value — the next cycle's own
    # resync+refresh remains the sole source of truth for what the engine
    # actually acts on.
    state.refresh_assessments()
    confidence_after = state.confidence

    for evidence_id in new_evidence_ids:
        record = state.ledger.evidence_by_id(evidence_id)
        if record is None:
            continue
        event = ProviderOutcomeEvent(
            investigation_id=investigation_id,
            cycle_number=iteration,
            scope=scope,
            capability=action.targets,
            investigation_type=investigation_type,
            provider=record.provider,
            action_key=action.key,
            outcome=record.outcome,
            declared_cost=action.cost,
            latency_ms=latency_ms,
            yielded_evidence=yielded,
            necessity_at_selection=necessity,
            base_score_at_selection=base_score,
            priority_boost_applied=boost_applied,
            priority_boost_source=priority_boost_source,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            state_snapshot=snapshot,
        )
        await intelligence.record_provider_outcome(event)


async def _record_clean_exit_outcome(state: WorkingContext, session: SessionContext) -> None:
    """`investigate()`'s own clean exit — READY/PARTIAL/BLOCKED, whichever
    `state.readiness` reads as right now. The `FAILED` terminal outcome is
    written from a different call site entirely (the outer `try`/`except`
    around `discover()`/`resume()` in `context_discovery/agent.py`), since
    a crash never reaches this return at all — see `contracts.
    InvestigationOutcomeEvent`'s own docstring for why two call sites,
    never one."""
    intelligence = session.intelligence
    if intelligence is None:
        return
    scope = _repository_scope(state)
    if scope is None:
        return

    understanding = state.derived.get("engineering_understanding") or {}
    contradictions = understanding.get("contradictions", [])

    event = InvestigationOutcomeEvent(
        investigation_id=_investigation_id(state, session),
        scope=scope,
        investigation_type=classify_engineering_strategy(state.metadata.goal),
        cycles_used=state.metadata.iteration,
        terminal_outcome=state.readiness,
        confidence=state.confidence,
        final_capability_scores={a.capability: a.score for a in state.assessments},
        contradictions_encountered=len(contradictions),
        contradictions_resolved=sum(1 for c in contradictions if c.get("resolved")),
        priority_boost_source_used=bool(state.derived.get("_intelligence_boost_keys")),
    )
    await intelligence.record_investigation_outcome(event)


# ---------------------------------------------------------------------------
# Live progress — best-effort, out-of-band checkpoints (see
# app.orchestrator.live_progress's own docstring for why this is a separate
# session/write path rather than a change to how `investigate()` itself
# commits). Purely additive: every line here is skipped outright when
# `session.progress_sink` is `None`, which is every call site except a real
# workflow-stage run with a `run_id` (see `context_discovery/agent.py`).
# ---------------------------------------------------------------------------

# Short, present-tense labels for the live checklist — deliberately not the
# full narrated `intent`/`observation` sentence (too long for a compact
# list) and not a per-action-key label either (a human doesn't need to see
# "fetch_work_item:PROJ-123" tick by, just "Checking Jira"). Every real
# `Investigator.name` this codebase registers has an entry; an unlisted one
# (a future investigator) still gets an honest generic fallback rather than
# blocking the checklist on this dict being kept in sync.
_STEP_LABELS: dict[str, str] = {
    "request_parser": "Parsing the request",
    "jira": "Checking Jira",
    "graph": "Investigating the architecture",
    "github": "Checking GitHub",
    "confluence": "Checking documentation",
    "google_drive": "Checking Google Drive",
    "test_coverage": "Checking test coverage",
    "user": "Recording your answer",
}


def _step_label(action: InvestigationAction) -> str:
    return _STEP_LABELS.get(action.provider, f"Checking {action.provider}")


async def _report_progress(
    session: SessionContext,
    *,
    iteration: int,
    max_iterations: int,
    completed_labels: list[str],
    active_label: str | None,
) -> None:
    if session.progress_sink is None:
        return
    from app.orchestrator.live_progress import LiveProgress, LiveProgressStep

    steps = [LiveProgressStep(label=label, status="done") for label in completed_labels]
    if active_label is not None:
        steps.append(LiveProgressStep(label=active_label, status="active"))
    # The sink itself (`write_live_progress`) already never raises — this
    # call is not wrapped in its own try/except because there is nothing
    # left here that could fail on top of what it already swallows.
    await session.progress_sink(
        LiveProgress(iteration=iteration, max_iterations=max_iterations, steps=steps)
    )


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def _resync(state: WorkingContext) -> None:
    """Re-establish every ledger interpretation invariant from the facts
    currently in `state.ledger` — the single place `repository_candidate`
    inferences are withdrawn and recomputed (ADR 0010, invariant I3).

    Withdrawal happens once, here, before any hook runs — never inside a
    hook itself. A hook that withdrew its own kind's inferences could erase
    what an earlier hook in the same pass already wrote; centralizing it
    here is what lets every hook in `capabilities.LEDGER_RESYNC_HOOKS` be
    purely additive and therefore safe to run in any order.

    Called on every reasoning cycle (`investigate`'s loop), once more after
    the loop exits, and once more after `_settle_claims` in `resume()` —
    that third call site matters: `_settle_claims` is the only place a
    `Fact.verified` flips, and it runs *after* `investigate()` returns, so
    without resyncing again afterward a claim verified in this call would
    never be reflected in the readiness/gaps `resume()`'s caller sees.
    """
    state.ledger.withdraw_inferences("repository_candidate")
    for hook in capabilities.LEDGER_RESYNC_HOOKS:
        hook(state.ledger)


async def investigate(
    state: WorkingContext,
    session: SessionContext,
    *,
    investigators: list[Investigator] | None = None,
    max_cycles: int = MAX_CYCLES,
) -> WorkingContext:
    """Run reasoning cycles until requirements are met or no investigator has
    anything left to offer. Mutates and returns `state` — this is working
    memory being updated in place, not a pure transform producing a report."""
    from app.context_pipeline.reasoning.projection import render_enriched_text

    pool = investigators if investigators is not None else default_investigators()

    # Reset here, not just at construction — a resumed run calls
    # `investigate()` a second time with a fresh `max_cycles` budget (see
    # `resume()` below), and a flag left over from the first, budget-cut pass
    # must not keep reporting BUDGET_EXHAUSTED once the second pass genuinely
    # finishes inside its own budget.
    state.metadata.cycle_budget_exhausted = False
    # True until a real exit condition (nothing unmet, or nothing left to
    # propose) actually fires — i.e. it stays True exactly when the `while`
    # condition itself is what ended the loop, which is precisely "the cycle
    # ceiling was the limiting factor," not a `bool` this loop has to
    # separately reason about.
    exhausted_by_cycle_budget = True
    # Live-progress checklist, carried across cycles (and across a resumed
    # run's second `investigate()` call — see `resume()`, which does not
    # reset this) — every completed action's short label, in order. Purely
    # additive reporting; see `_report_progress` above.
    completed_step_labels: list[str] = list(state.derived.get("_completed_step_labels") or [])

    while state.metadata.iteration < max_cycles:
        state.metadata.iteration += 1
        iteration = state.metadata.iteration

        _resync(state)
        state.refresh_assessments()

        candidates = _candidate_actions(state, pool)

        # Free actions (deterministic local parsing, no network, no tokens)
        # always run before anything is judged sufficient, and before any paid
        # retrieval. Two reasons, both learned the hard way:
        #
        # - Bootstrap. Until the request has been parsed, a work item looks
        #   "not applicable" because no reference has been recognized yet — so
        #   ranking parsing against retrieval by capability necessity puts it
        #   last and the graph query wins, after which requirements look met
        #   and the referenced ticket is never fetched at all.
        # - Honesty. A signal like "the request names a known repository" can
        #   only be satisfied by a parse pass that costs nothing to run, so
        #   skipping it understates confidence rather than saving work.
        free = [c for c in candidates if c[0].cost == 0]
        if not free:
            if not unmet(state.assessments):
                exhausted_by_cycle_budget = False
                break
            if not candidates:
                exhausted_by_cycle_budget = False
                break

        priority_boost = state.derived.get("investigation_priority") or {}
        candidate_pool = free or candidates
        action, investigator = _select(candidate_pool, state.assessments, priority_boost)
        state.transcript.say("intent", action.intent, iteration=iteration)
        await _report_progress(
            session,
            iteration=iteration,
            max_iterations=max_cycles,
            completed_labels=completed_step_labels,
            active_label=_step_label(action),
        )

        # Captured before the action runs — the decision context `_select()`
        # actually used, for Investigation Intelligence's own record of it
        # (ADR 0021 §3). `state.assessments` isn't touched again until the
        # next cycle's `_resync`+`refresh_assessments`, so these stay valid
        # right up to the recording call below.
        assessments_before = state.assessments
        assessment_before = state.assessment_for(action.targets)
        confidence_before = state.confidence
        boost_applied = priority_boost.get(action.targets, 0.0)

        recorder = Recorder(state.ledger, action, iteration)
        before = len(state.ledger.evidence)
        start = time.monotonic()
        try:
            outcome = await investigator.run(action, session, recorder)
        except Exception as exc:  # noqa: BLE001 - one provider must not kill discovery
            logger.exception(
                "context_discovery_investigation_failed provider=%s key=%s",
                action.provider,
                action.key,
            )
            if len(state.ledger.evidence) == before:
                # The investigator raised before recording anything. Record
                # the failure ourselves so the attempt is still visible and,
                # critically, so `attempted()` sees it and we don't retry it
                # forever.
                recorder.evidence("failed", f"{action.provider} raised an error: {exc}")
            outcome = InvestigationOutcome(
                observation=f"My attempt to use {action.provider} failed, so I moved on.",
                yielded=False,
            )
        latency_ms = int((time.monotonic() - start) * 1000)

        state.derived.update(outcome.derived)
        state.derived["enriched_text"] = render_enriched_text(state)

        new_evidence = [e.evidence_id for e in state.ledger.evidence[before:]]

        if session.intelligence is not None:
            await _record_provider_outcomes(
                state=state,
                session=session,
                action=action,
                iteration=iteration,
                candidates=candidate_pool,
                assessments_before=assessments_before,
                assessment_before=assessment_before,
                boost_applied=boost_applied,
                confidence_before=confidence_before,
                latency_ms=latency_ms,
                new_evidence_ids=new_evidence,
                yielded=outcome.yielded,
            )

        state.transcript.say(
            "observation", outcome.observation, iteration=iteration, evidence_ids=new_evidence
        )
        completed_step_labels.append(_step_label(action))
        await _report_progress(
            session,
            iteration=iteration,
            max_iterations=max_cycles,
            completed_labels=completed_step_labels,
            active_label=None,
        )

        # Mid-loop re-synthesis: engineering understanding actively driving
        # the *next* action, not just summarizing the last one. Gated three
        # ways so "understanding drives investigation" doesn't become "an
        # LLM call every cycle": only after a real (paid) retrieval that
        # actually yielded something new, only when the evidence count has
        # moved since the last synthesis at all (a `not_found`/`failed`
        # outcome that added evidence but taught nothing new doesn't
        # deserve a fresh reasoning pass), and only within
        # `MAX_MID_LOOP_SYNTHESIS_CALLS`. The always-run call after the loop
        # exits (below) is what guarantees Planning gets understanding
        # regardless of whether this budget was ever spent.
        if (
            action.cost > 0
            and outcome.yielded
            and state.metadata.synthesis_calls < MAX_MID_LOOP_SYNTHESIS_CALLS
            and len(state.ledger.evidence) != state.derived.get("_last_synthesis_evidence_count")
        ):
            from app.context_pipeline.reasoning.understanding import (
                synthesize_engineering_understanding,
            )

            await synthesize_engineering_understanding(state, session)
            state.derived["_last_synthesis_evidence_count"] = len(state.ledger.evidence)
            await _apply_memory_priority_boost(state, session)

    # Carried forward so a resumed run's second `investigate()` call
    # continues the same live checklist rather than restarting it empty —
    # see this loop's own initialization of `completed_step_labels` above.
    state.derived["_completed_step_labels"] = completed_step_labels

    # Whether we ran out of proposals or out of budget, automated
    # investigation is over — this is the gate `next_question()` waits on.
    # Deliberately unconditional and unchanged by the flag below: whether a
    # clarifying question may be offered is a separate question from why the
    # loop stopped, and narrowing it to "only when genuinely exhausted" would
    # be a real behavior change to what gets asked, not just to how the
    # outcome is reported — out of scope for the reporting fix this flag
    # exists for.
    state.metadata.providers_exhausted = True
    # Only true when the `while` condition itself ended the loop — i.e.
    # `iteration` reached `max_cycles` while real work (an unmet requirement,
    # or a candidate action) was still on the table. The `and` guards the one
    # edge case where the final in-loop action happens to satisfy everything
    # on the very last permitted cycle without ever hitting the `not unmet`
    # break (the loop simply isn't re-entered) — that is a genuine finish,
    # not a cutoff, and `completion_status` also independently prioritizes
    # `readiness == "READY"` above this flag for the same reason.
    state.metadata.cycle_budget_exhausted = (
        exhausted_by_cycle_budget and state.metadata.iteration >= max_cycles
    )
    _resync(state)
    state.refresh_assessments()
    _sync_gaps(state)
    state.derived["enriched_text"] = render_enriched_text(state)

    # One last checkpoint covering curation + final synthesis below — both
    # can take a real, human-noticeable amount of time (the synthesis call
    # especially, being an LLM round trip), and without this the checklist
    # would otherwise look finished-but-stuck for that whole stretch.
    await _report_progress(
        session,
        iteration=state.metadata.iteration,
        max_iterations=max_cycles,
        completed_labels=completed_step_labels,
        active_label="Synthesizing findings",
    )

    # Curation (see investigators.curate_evidence's own docstring): runs
    # once, here, over whatever component facts gathering already
    # produced — never as a competing proposed action, and never on a
    # partial/mid-cycle state. Both call sites that reach this point
    # (discover()'s first pass, resume()'s continuation after a human
    # answer) want the same thing: a final, bounded EvidencePackage
    # before _conclude() runs, so this belongs here rather than
    # duplicated in both callers.
    from app.context_pipeline.reasoning.investigators import curate_evidence

    await curate_evidence(state, session)

    # Synthesis (see reasoning.understanding's own docstring): the cognitive
    # reasoning layer above retrieval. Zero or more mid-loop calls already
    # ran above, each time engineering understanding itself had a chance to
    # redirect the *next* action (see `priority_boost` above); this final
    # call is unconditional regardless of that budget, because it is the
    # only one that runs after `curate_evidence` — the curated architecture
    # evidence package it just produced is new information no earlier
    # in-loop call could have reasoned over. Guarantees Planning always
    # gets an understanding built from the complete, final evidence.
    from app.context_pipeline.reasoning.understanding import synthesize_engineering_understanding

    await synthesize_engineering_understanding(state, session)
    await _record_clean_exit_outcome(state, session)

    return state


# ---------------------------------------------------------------------------
# Verification of human answers
# ---------------------------------------------------------------------------


def _verify_claim(state: WorkingContext, gap: KnowledgeGap) -> bool:
    """Did investigation actually corroborate what the human told us?

    Delegates to the capability's own `verify`, which always looks for a fact
    or inference *the investigation produced* — never at the answer string
    itself. That is the whole point: an answer that merely reads plausibly (a
    UI instruction label, a repository that doesn't exist, a mistyped ticket
    key) produces no corroborating evidence and therefore does not verify.

    A capability with no `verify` cannot have asked a question in the first
    place (see capabilities.Capability.__post_init__), so reaching here without
    one means a claim was recorded against something unaskable — treat as
    unverified rather than silently accepting it.
    """
    claim = (gap.user_claim or "").strip()
    if not claim:
        return False
    capability = capabilities.get(gap.capability)
    if capability is None or capability.verify is None:
        return False
    return capability.verify(state.ledger, claim)


def _settle_claims(state: WorkingContext) -> None:
    """Resolve every outstanding claim into verified or refuted, and narrate
    which — so the user learns whether their answer actually held rather than
    watching the state silently change."""
    for gap in state.gaps:
        if gap.status != "claimed":
            continue
        if _verify_claim(state, gap):
            gap.status = "verified"
            gap.resolution_note = f"Confirmed '{gap.user_claim}' against the knowledge graph."
            # The claim has now been independently corroborated, so the
            # user_statement fact stops being an outstanding claim. This is the
            # only place a fact's `verified` flag is ever raised, and it only
            # happens after `_verify_claim` found real supporting evidence.
            #
            # Matched by question_id, not by answer text: two different
            # clarification questions answered with the same literal string
            # in one run (e.g. both answered "payment-service") must not let
            # verifying one silently verify the other's fact too — each
            # claim is corroborated (or not) independently.
            question_id = gap.question.question_id if gap.question else None
            for fact in state.ledger.facts_of("user_statement", verified_only=False):
                if (
                    fact.subject == gap.user_claim
                    and fact.value.get("question_id") == question_id
                    and not fact.verified
                ):
                    fact.verified = True
            state.transcript.say(
                "conclusion",
                f"Confirmed: '{gap.user_claim}' checks out, and I've used it.",
                iteration=state.metadata.iteration,
            )
        else:
            gap.status = "refuted"
            gap.resolution_note = (
                f"Could not corroborate '{gap.user_claim}' — no matching evidence was found."
            )
            state.transcript.say(
                "conclusion",
                f"I couldn't confirm '{gap.user_claim}' — nothing I can reach corroborates it, "
                "so I haven't treated it as settled.",
                iteration=state.metadata.iteration,
            )


# ---------------------------------------------------------------------------
# Question generation — after exhaustion, exactly one, with real options
# ---------------------------------------------------------------------------


def _question_for(state: WorkingContext, gap: KnowledgeGap) -> ClarificationQuestion | None:
    """Build the question for a gap, or None when no answer could help.

    All phrasing and option selection belongs to the capability, declared
    beside the `verify` that will later check the answer — so a question and
    its verification can never drift apart, and a capability that cannot
    verify an answer structurally cannot ask for one.
    """
    capability = capabilities.get(gap.capability)
    if capability is None or capability.question is None:
        return None
    return capability.question(
        QuestionContext(
            ledger=state.ledger,
            investigated=_investigated_summary(state),
            # Only a refuted claim makes this a re-ask; a first ask has no
            # prior answer to acknowledge.
            previous_claim=gap.user_claim if gap.status == "refuted" else None,
        )
    )


def _investigated_summary(state: WorkingContext) -> list[str]:
    """What was actually tried, for display alongside the question. This is
    what makes the question read as a last resort rather than a first move —
    the user can see the automated avenues were spent."""
    lines: list[str] = []
    for record in state.ledger.evidence:
        marker = {"success": "✓", "not_found": "—", "unavailable": "—", "failed": "✗"}[
            record.outcome
        ]
        lines.append(f"{marker} {record.summary}")
    return lines


def _conclude(state: WorkingContext) -> None:
    """Attach a question to the highest-value answerable blocking gap, mark
    the rest unresolvable, and narrate the verdict."""
    rounds_left = state.metadata.clarification_rounds < MAX_CLARIFICATION_ROUNDS

    asked = False
    for gap in state.open_blocking_gaps():
        question = _question_for(state, gap) if rounds_left and not asked else None
        if question is not None:
            gap.question = question
            asked = True
        elif gap.status in ("open", "refuted"):
            gap.question = None
            gap.status = "unresolvable"
            gap.resolution_note = (
                "No further automated investigation is possible and no answer would resolve "
                "this — it needs the remediation listed above."
            )

    # Advisory gaps never pause anything, but should still be stated plainly
    # rather than silently downgrading confidence.
    for gap in state.gaps:
        if gap.severity == "advisory" and gap.status == "open":
            gap.status = "unresolvable"
            gap.resolution_note = "Optional context that could not be retrieved."

    state.transcript.say(
        "conclusion",
        _verdict_line(state),
        iteration=state.metadata.iteration,
    )


def _verdict_line(state: WorkingContext) -> str:
    readiness = state.readiness
    question = state.next_question()
    # Checked first, and before the question branch below, to match
    # `WorkingContext.completion_status`'s own precedence exactly (readiness
    # READY still wins over either — see that property's docstring). This is
    # the fix for the audit finding that a cycle-budget cutoff used to read
    # identically to genuine exhaustion ("I've gathered everything I can on
    # my own"): that sentence is only ever true when it wasn't the cycle
    # ceiling that stopped the loop.
    if state.metadata.cycle_budget_exhausted and readiness != "READY":
        line = (
            "I reached my investigation cycle limit before finishing — that's not the same as "
            "having exhausted every avenue, just what I could cover in the time I had. Here's "
            "what I found and what's still unknown below."
        )
        if question is not None:
            line += " One thing is also worth asking you about now, since I already have it queued."
        return line
    if question is not None:
        if state.metadata.clarification_rounds:
            # A re-ask must read as a continuation, not as the engine starting
            # the same conversation over.
            return (
                "That still leaves the same gap open, so I need one more piece of input "
                "before I can continue."
            )
        return (
            "I've gathered everything I can on my own, but one thing is genuinely ambiguous — "
            "I need your input before I continue."
        )
    if readiness == "READY":
        return "Context is sufficient — I understand what this change touches. Ready to plan."
    if readiness == "PARTIAL":
        missing = " ".join(
            g.summary for g in state.gaps if g.severity == "advisory" and g.status != "verified"
        )
        return (
            "I have everything Planning strictly requires, but some optional context is "
            f"missing. {missing or 'See the gaps listed above.'} You can continue if that's "
            "acceptable."
        )
    # Gap summaries are complete sentences, so joining them with "; " and then
    # appending "." produced a stray double period mid-sentence.
    blocked = " ".join(
        g.summary for g in state.gaps if g.severity == "blocking" and g.status != "verified"
    )
    return (
        f"I can't build enough context to plan. {blocked or 'Required context is missing.'} "
        "Planning would be guessing, so I've stopped here."
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _seed_explicit_repositories(state: WorkingContext, repo_names: list[str]) -> None:
    """Pre-seed the ledger with the user's repository selection as reference
    facts — the same kind `RequestParseInvestigator` would produce if it
    found a repository name in the request text.

    This does NOT create `repository` facts (that's the GraphInvestigator's
    job after validating the repo exists in the graph) or
    `repository_candidate` inferences (that's exclusively the resync hooks'
    job per ADR 0010, Invariant I1). It only records "the user pointed at
    these names" — the investigation pipeline validates and promotes them
    through the normal evidence → fact → inference chain:

      1. Reference fact seeded here (iteration 0)
      2. GraphInvestigator surveys indexed repos → creates `repository` facts
      3. `resync_repository_candidates` matches reference + repository fact
         → creates `repository_candidate` inference (source: "explicit")
      4. Architecture/documentation investigators target the explicit candidate

    If a seeded repository no longer exists in the graph (un-indexed between
    runs), no `repository` fact is created, no candidate is promoted, and
    readiness correctly reflects the gap — fail-safe by construction.
    """
    evidence = state.ledger.add_evidence(
        provider="user",
        action="explicit_repository_selection",
        outcome="success",
        summary=f"User explicitly selected {len(repo_names)} repositor{'y' if len(repo_names) == 1 else 'ies'}: {', '.join(repo_names)}.",
        iteration=0,
        intent="The user selected these repositories from the Context Explorer UI.",
    )
    for name in repo_names:
        state.ledger.add_fact(
            kind="reference",
            subject=name,
            provider="user",
            evidence_id=evidence.evidence_id,
            value={"type": "local_repository", "source": "explicit_selection"},
            iteration=0,
            verified=True,
        )
    state.transcript.say(
        "repository",
        f"User pre-selected repositor{'y' if len(repo_names) == 1 else 'ies'}: "
        f"{', '.join(repo_names)} — I'll validate and investigate these.",
    )


async def discover(
    *,
    request: str,
    session: SessionContext,
    investigators: list[Investigator] | None = None,
    explicit_repositories: list[str] | None = None,
) -> WorkingContext:
    """Fresh discovery for `request`.

    When `explicit_repositories` is provided (a user selected repositories
    from the UI), the ledger is pre-seeded with those repos as explicit
    candidates before investigation begins — so every investigator
    (architecture, documentation, graph) targets the correct repositories
    from cycle 1 rather than having to discover them first.
    """
    state = WorkingContext()
    state.metadata.goal = request
    state.derived["original_request"] = request
    state.derived["enriched_text"] = request
    state.transcript.say("intent", f"Working out what I need to know about: {request}")

    if explicit_repositories:
        _seed_explicit_repositories(state, explicit_repositories)

    await investigate(state, session, investigators=investigators)
    _conclude(state)

    logger.info(
        "context_discovery_finished readiness=%s confidence=%.2f cycles=%d "
        "facts=%d evidence=%d paused=%s",
        state.readiness,
        state.confidence,
        state.metadata.iteration,
        len(state.ledger.facts),
        len(state.ledger.evidence),
        state.next_question() is not None,
    )
    return state


async def resume(
    *,
    state: WorkingContext,
    question_id: str,
    answer: str,
    session: SessionContext,
    investigators: list[Investigator] | None = None,
) -> WorkingContext:
    """Fold a human answer into working memory and keep investigating.

    The answer is recorded as a claim, not a resolution. The loop then runs
    again — investigators propose verification work off the `claimed` gap —
    and `_settle_claims` decides afterwards whether the claim held. A refuted
    claim leaves the gap open and confidence unmoved, because nothing in the
    ledger changed.
    """
    gap = state.gap_by_question(question_id)
    if gap is None:
        raise ValueError(f"No pending question with id {question_id!r}.")

    state.metadata.clarification_rounds += 1
    gap.user_claim = answer.strip()
    gap.status = "claimed"
    state.transcript.say(
        "answer", f"You told me: {answer.strip()}", iteration=state.metadata.iteration
    )

    # Record the human as a source like any other — an unverified fact, so
    # nothing reads it as established knowledge.
    action = InvestigationAction(
        provider="user",
        key=f"answer:{question_id}",
        intent=(
            "Recording your answer to: " f"{gap.question.question if gap.question else question_id}"
        ),
        targets=gap.capability,
    )
    recorder = Recorder(state.ledger, action, state.metadata.iteration)
    recorder.evidence("success", f"You answered: {answer.strip()}")
    recorder.fact(
        "user_statement",
        answer.strip(),
        value={"question_id": question_id, "capability": gap.capability},
        verified=False,
    )

    state.transcript.say(
        "intent",
        f"Thanks. I'll verify that against the {gap.capability.replace('_', ' ')} evidence "
        "before I rely on it.",
        iteration=state.metadata.iteration,
    )

    # Re-open the investigation: providers get another chance to propose,
    # now that a claim exists for them to verify.
    state.metadata.providers_exhausted = False
    await investigate(
        state,
        session,
        investigators=investigators,
        max_cycles=state.metadata.iteration + MAX_CYCLES,
    )
    _settle_claims(state)
    # `_settle_claims` is the only place a `Fact.verified` flips (a claimed
    # `user_statement` becoming corroborated) — resync once more so a claim
    # verified in this very call is reflected as an explicit candidate
    # before readiness/gaps are computed below (ADR 0010 §7, item 1; see
    # `_resync`'s own docstring for why this call site exists).
    _resync(state)
    state.refresh_assessments()
    _sync_gaps(state)
    _conclude(state)

    logger.info(
        "context_discovery_resumed readiness=%s confidence=%.2f gap=%s status=%s",
        state.readiness,
        state.confidence,
        gap.capability,
        gap.status,
    )
    return state


def pending_question(state: WorkingContext) -> ClarificationQuestion | None:
    return state.next_question()
