"""`WorkingContext` — Context Discovery's working memory.

Not an output object. This is the state the reasoning engine reads at the
top of every cycle and writes at the bottom of it: the ledger of facts and
evidence, the freshly-derived confidence assessments, the open knowledge
gaps, and the transcript narrating the investigation as it happens. It is
persisted mid-investigation when discovery pauses for a human answer, and
reconstructed to resume — so the same object serves as live memory and as
the pause/resume checkpoint.

Readiness is a *derived* property here, computed from assessments on demand
rather than stored and refreshed by hand. That removes the class of bug
where a stored verdict and the knowledge it was supposedly based on drift
apart:

    BLOCKED  — a required capability is unsatisfied. Planning cannot run.
    PARTIAL  — every required capability is satisfied, but a recommended one
               is not. Planning may run if the human accepts the gap.
    READY    — every applicable capability is satisfied.

Note what is absent: a confidence threshold. Readiness turns on whether the
things discovery actually needed are present, which is a different claim
from "the average score is above 0.7".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.context_pipeline.reasoning.capabilities import (
    CapabilityAssessment,
    ClarificationQuestion,
    assess,
    overall_confidence,
    unmet,
)
from app.context_pipeline.reasoning.ledger import Ledger

Readiness = Literal["READY", "PARTIAL", "BLOCKED"]

# Why the investigation actually stopped — an axis distinct from `Readiness`
# (readiness says "is there enough"; this says "why did looking stop"). Added
# after an audit found the engine's own narration collapsing "ran out of
# cycles" and "genuinely nothing left to try" into the same sentence — see
# `WorkingContext.completion_status` for the precedence that derives one from
# the other, and `_verdict_line` in engine.py for the narration this gates.
#
#   COMPLETED          — every applicable capability satisfied (READY).
#   BUDGET_EXHAUSTED    — hit `MAX_CYCLES` while investigators still had
#                          candidates or requirements were still unmet. The
#                          one case that must never be narrated as "I've
#                          gathered everything I can."
#   PROVIDERS_EXHAUSTED — every investigator genuinely declined further work
#                          and a human is now being asked (a pending
#                          clarification question exists).
#   BLOCKED             — a required capability is unsatisfied, no further
#                          automated avenue exists, and no question would
#                          help either — remediation outside the tool only.
#   PARTIAL             — every required capability is satisfied; only
#                          optional/recommended context is missing.
CompletionStatus = Literal[
    "COMPLETED", "BUDGET_EXHAUSTED", "PROVIDERS_EXHAUSTED", "BLOCKED", "PARTIAL"
]

GapStatus = Literal[
    # Nothing has closed this yet; the engine may still investigate or ask.
    "open",
    # A human answered, but the answer has not been corroborated yet.
    "claimed",
    # A human's answer was corroborated by a real subsequent investigation.
    "verified",
    # A human's answer was investigated and could NOT be corroborated.
    # Deliberately distinct from "open": we know the answer didn't hold, and
    # saying so is more useful than silently re-asking.
    "refuted",
    # No investigator can close this and no question would help — remediation
    # outside the tool is required (connect Jira, index the repository).
    "unresolvable",
]

TranscriptKind = Literal["intent", "observation", "question", "answer", "conclusion"]

# Re-exported so callers keep importing the clarification type from the module
# that owns discovery state, even though it is declared beside the `verify`
# function it is paired with (see capabilities.Capability).
__all__ = [
    "ClarificationQuestion",
    "CompletionStatus",
    "DiscoveryMetadata",
    "GapStatus",
    "KnowledgeGap",
    "Readiness",
    "Transcript",
    "TranscriptEntry",
    "WorkingContext",
]


class KnowledgeGap(BaseModel):
    """One thing discovery needs and does not have.

    Identity is stable across reasoning cycles (`gap_id` is derived from the
    capability) so a human answer attached in one cycle survives into the
    next. Re-deriving gaps every cycle is what keeps them honest; keeping
    their ids stable is what keeps them answerable.
    """

    gap_id: str
    capability: str
    summary: str
    why: str
    severity: Literal["blocking", "advisory"]
    status: GapStatus = "open"
    recommended_action: list[str] = Field(default_factory=list)
    question: ClarificationQuestion | None = None
    # What the human said, verbatim, if they were asked. Recorded here as a
    # claim; whether it held is `status`, and the fact it produced (if any)
    # lives in the ledger like any other fact.
    user_claim: str | None = None
    resolution_note: str = ""
    # Signal labels from the capability assessment that are unsatisfied —
    # the specific, actionable "what's missing" the UI renders under this gap.
    missing_signals: list[str] = Field(default_factory=list)

    @property
    def is_answerable(self) -> bool:
        return self.question is not None and self.status in ("open", "refuted")


class TranscriptEntry(BaseModel):
    """One line of the investigation as the user reads it.

    The transcript is the collaborative surface: the engine states intent
    before acting, reports what it observed after, and states conclusions.
    `evidence_ids` ties narration back to the ledger so no transcript line
    is a claim the evidence doesn't support.
    """

    kind: TranscriptKind
    text: str
    iteration: int = 0
    evidence_ids: list[str] = Field(default_factory=list)


class Transcript(BaseModel):
    entries: list[TranscriptEntry] = Field(default_factory=list)

    def say(
        self,
        kind: TranscriptKind,
        text: str,
        *,
        iteration: int = 0,
        evidence_ids: list[str] | None = None,
    ) -> None:
        # A verdict identical to the last one adds nothing. Across clarification
        # rounds the engine re-concludes each time, and when the verdict hasn't
        # changed the user just saw the same sentence twice — which reads as the
        # transcript glitching rather than as history.
        if kind == "conclusion" and self.entries:
            last_conclusion = next(
                (e for e in reversed(self.entries) if e.kind == "conclusion"), None
            )
            if last_conclusion is not None and last_conclusion.text == text:
                return
        self.entries.append(
            TranscriptEntry(
                kind=kind,
                text=text,
                iteration=iteration,
                evidence_ids=evidence_ids or [],
            )
        )

    def lines(self) -> list[str]:
        return [e.text for e in self.entries]


class DiscoveryMetadata(BaseModel):
    goal: str = ""
    iteration: int = 0
    clarification_rounds: int = 0
    # Set when the engine stops proposing actions because no investigator had
    # anything left to offer — the precondition for asking a human anything.
    providers_exhausted: bool = False
    # How many times `reasoning.understanding.synthesize_engineering_
    # understanding` has actually called the LLM (not counted: the
    # zero-evidence short-circuit, which never calls it at all) — bounds
    # mid-loop re-synthesis to a fixed budget (see engine.py's
    # `_MAX_MID_LOOP_SYNTHESIS_CALLS`) so "understanding drives
    # investigation" doesn't mean an unbounded LLM call per cycle.
    synthesis_calls: int = 0
    # Set by `engine.investigate()` when its own `while` loop exits because
    # `iteration` reached `max_cycles`, not because either internal `break`
    # fired (nothing left unmet, or no investigator proposed anything).
    # Reset to False at the top of every `investigate()` call, so a resumed
    # run that finishes inside its own fresh budget correctly clears a flag
    # set during an earlier, budget-cut pass. The sole input `completion_
    # status` needs beyond what `readiness`/`providers_exhausted`/
    # `next_question()` already track.
    cycle_budget_exhausted: bool = False


class WorkingContext(BaseModel):
    """The whole evolving state of one discovery run."""

    metadata: DiscoveryMetadata = Field(default_factory=DiscoveryMetadata)
    ledger: Ledger = Field(default_factory=Ledger)
    assessments: list[CapabilityAssessment] = Field(default_factory=list)
    gaps: list[KnowledgeGap] = Field(default_factory=list)
    transcript: Transcript = Field(default_factory=Transcript)
    # Derived text products that belong to memory rather than to the fact
    # ledger — chiefly the pre-formatted graph context blob and the enriched
    # prompt text Planning renders from. Facts are atomic and queryable;
    # these are rendered artifacts built from them.
    derived: dict[str, Any] = Field(default_factory=dict)

    # -- derived verdicts --------------------------------------------------

    def refresh_assessments(self) -> list[CapabilityAssessment]:
        """Re-read confidence from the ledger. Called at the top of every
        reasoning cycle so assessments are never stale relative to facts."""
        self.assessments = assess(self.ledger)
        return self.assessments

    @property
    def readiness(self) -> Readiness:
        pending = unmet(self.assessments)
        if any(a.necessity == "required" for a in pending):
            return "BLOCKED"
        if pending:
            return "PARTIAL"
        return "READY"

    @property
    def confidence(self) -> float:
        return overall_confidence(self.assessments)

    @property
    def completion_status(self) -> CompletionStatus:
        """Why the investigation actually stopped — derived, like
        `readiness`, never stored independently of the state it reads.

        Precedence (first match wins), each condition read off state this
        class already tracks:

        1. `readiness == "READY"` — every applicable capability satisfied.
           Takes priority over every other signal, including a budget flag
           that happened to also be set on the very cycle that finished the
           job (see engine.py's own note on that edge case): if the work is
           actually done, that is always the headline, regardless of how
           close to the cycle ceiling it finished.
        2. `metadata.cycle_budget_exhausted` — the loop hit `MAX_CYCLES`
           with real work still on the table. Distinct from every other
           unsatisfied state below because more budget, not more evidence,
           is what it needs.
        3. `next_question() is not None` — every investigator genuinely
           declined further work and a human is now being asked. This *is*
           "providers exhausted" in the most literal, load-bearing sense:
           it's the exact precondition `next_question()` itself requires.
        4. `readiness == "BLOCKED"` — a required capability remains
           unsatisfied with no further automated avenue and no question
           that would help either (see `_conclude`'s `unresolvable` path).
        5. Otherwise `"PARTIAL"` — every required capability is satisfied;
           only optional/recommended context is missing.
        """
        if self.readiness == "READY":
            return "COMPLETED"
        if self.metadata.cycle_budget_exhausted:
            return "BUDGET_EXHAUSTED"
        if self.next_question() is not None:
            return "PROVIDERS_EXHAUSTED"
        if self.readiness == "BLOCKED":
            return "BLOCKED"
        return "PARTIAL"

    def assessment_for(self, capability: str) -> CapabilityAssessment | None:
        return next((a for a in self.assessments if a.capability == capability), None)

    # -- gaps --------------------------------------------------------------

    def gap_for(self, capability: str) -> KnowledgeGap | None:
        return next((g for g in self.gaps if g.capability == capability), None)

    def gap_by_question(self, question_id: str) -> KnowledgeGap | None:
        return next(
            (g for g in self.gaps if g.question and g.question.question_id == question_id),
            None,
        )

    def open_blocking_gaps(self) -> list[KnowledgeGap]:
        return [
            g
            for g in self.gaps
            if g.severity == "blocking" and g.status in ("open", "claimed", "refuted")
        ]

    def next_question(self) -> ClarificationQuestion | None:
        """The single highest-value question to ask, or None.

        Only ever returns something once `providers_exhausted` is set: the
        human is the most expensive source of information, so asking before
        the cheap automated ones are spent is a UX regression, not a
        shortcut. Blocking gaps are ordered before advisory ones by
        `open_blocking_gaps`; the first answerable one wins, so exactly one
        question is outstanding at a time.
        """
        if not self.metadata.providers_exhausted:
            return None
        for gap in self.open_blocking_gaps():
            if gap.is_answerable:
                return gap.question
        return None
