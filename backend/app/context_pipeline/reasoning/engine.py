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
from app.context_pipeline.reasoning.investigators import default_investigators
from app.context_pipeline.reasoning.memory import KnowledgeGap, WorkingContext

logger = logging.getLogger(__name__)

# How many gather-and-reassess cycles one discovery run may perform. Reached
# only by a genuinely tangled request — the normal path terminates early
# because investigators stop proposing once requirements are met.
MAX_CYCLES = 8

# How many times the human may be asked across one discovery run. Past this,
# remaining blocking gaps are reported as unresolvable rather than becoming
# an endless interrogation.
MAX_CLARIFICATION_ROUNDS = 2


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
) -> tuple[InvestigationAction, Investigator]:
    """Pick the most valuable next action.

    Deterministic and explainable, in priority order:

    1. Actions targeting a *required* capability beat actions targeting a
       recommended one — never spend a turn on documentation while the
       repository is still unknown.
    2. Among those, target the weakest capability first (lowest score), since
       that's where a single retrieval buys the most.
    3. Cheaper actions win ties, so a local graph query is preferred over a
       multi-turn MCP conversation.

    Deliberately not an LLM call. Which provider can answer "which repository
    owns this" is a structural property of the providers, not a judgement
    call, and making it deterministic means the investigation is reproducible
    and testable.
    """
    necessity_rank = {"required": 0, "recommended": 1, "not_applicable": 2}
    by_capability = {a.capability: a for a in assessments}

    def sort_key(entry: tuple[InvestigationAction, Investigator]) -> tuple[int, float, int, str]:
        action, _ = entry
        assessment = by_capability.get(action.targets)
        necessity = assessment.necessity if assessment else "recommended"
        score = assessment.score if assessment else 1.0
        return (necessity_rank[necessity], score, action.cost, action.key)

    return sorted(candidates, key=sort_key)[0]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


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

    while state.metadata.iteration < max_cycles:
        state.metadata.iteration += 1
        iteration = state.metadata.iteration

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
                break
            if not candidates:
                break

        action, investigator = _select(free or candidates, state.assessments)
        state.transcript.say("intent", action.intent, iteration=iteration)

        recorder = Recorder(state.ledger, action, iteration)
        before = len(state.ledger.evidence)
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

        state.derived.update(outcome.derived)
        state.derived["enriched_text"] = render_enriched_text(state)

        new_evidence = [e.evidence_id for e in state.ledger.evidence[before:]]
        state.transcript.say(
            "observation", outcome.observation, iteration=iteration, evidence_ids=new_evidence
        )

    # Whether we ran out of proposals or out of budget, automated
    # investigation is over — this is the gate `next_question()` waits on.
    state.metadata.providers_exhausted = True
    state.refresh_assessments()
    _sync_gaps(state)
    state.derived["enriched_text"] = render_enriched_text(state)
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
            for fact in state.ledger.facts_of("user_statement", verified_only=False):
                if fact.subject == gap.user_claim and not fact.verified:
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


async def discover(
    *,
    request: str,
    session: SessionContext,
    investigators: list[Investigator] | None = None,
) -> WorkingContext:
    """Fresh discovery for `request`."""
    state = WorkingContext()
    state.metadata.goal = request
    state.derived["original_request"] = request
    state.derived["enriched_text"] = request
    state.transcript.say("intent", f"Working out what I need to know about: {request}")

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
