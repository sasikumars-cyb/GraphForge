"""The vocabulary of investigation — and the protocol that turns providers
into passive tools the reasoning engine chooses between.

The inversion this file encodes: an investigator does not know when it runs.
It answers one question — *"given what is known right now, what could I
contribute?"* — by returning zero or more `InvestigationAction`s. Returning
an empty list is how a provider says "nothing I can do here", and it is the
signal the engine uses to decide the providers are exhausted and a human
finally has to be asked.

Nothing here imports the concrete providers; `investigators.py` does that.
Nothing here decides ordering; `engine.py` does that.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext
from app.context_pipeline.reasoning.ledger import (
    EvidenceRecord,
    Fact,
    FactKind,
    Ledger,
    Outcome,
)

if TYPE_CHECKING:
    from app.context_pipeline.reasoning.memory import WorkingContext
    from app.investigation_intelligence.service import InvestigationIntelligenceService
    from app.orchestrator.live_progress import LiveProgress


@dataclass
class SessionContext:
    """Run-scoped handles an investigation needs. Constructed once per
    discovery run and passed to every investigator, so no investigator
    holds per-run state on itself (they are all stateless singletons, like
    every agent in this codebase)."""

    db: AsyncSession
    user_id: uuid.UUID | None
    graph_repo_override: Any = None
    # Same purpose as graph_repo_override, for TestCoverageInvestigator's
    # ITestCaseGraphRepository — lets a unit test substitute a fake without
    # touching a real Neo4j (this codebase's unit tests are I/O-free by
    # convention).
    test_case_graph_repo_override: Any = None
    model: str | None = None
    stage: str = "context_discovery"
    # The full agent contract context, when a real agent run built this
    # session (absent for e.g. ad-hoc test fixtures). Threaded through
    # purely so `reasoning.understanding.synthesize_engineering_
    # understanding` can pass it to `invoke_llm_json(context=...)` and get
    # ADR 0012 invocation persistence for free — `context.extras` already
    # carries `db`/`run_id`/`agent_step_id`, the same three values every
    # other agent's LLM call reads them from.
    agent_context: AgentContext | None = None
    # ADR 0021 — Investigation Intelligence. `None` for callers that never
    # construct one (ad-hoc test fixtures, older call sites not yet
    # migrated); every read/write site in `engine.py` must treat that as
    # "no signal available" and degrade silently, never require it.
    intelligence: InvestigationIntelligenceService | None = None
    # Best-effort live-progress checkpoint hook (see `app.orchestrator.
    # live_progress`) — `None` for every caller that never constructs one
    # (ad-hoc test fixtures, standalone/non-workflow runs with no `run_id`
    # to key a checkpoint on). `engine.py` must treat that as "nothing to
    # report to" and skip the call entirely, exactly like `intelligence`
    # above; when present, the callable itself already swallows its own
    # failures, so `engine.py` never needs a try/except around calling it.
    progress_sink: Callable[[LiveProgress], Awaitable[None]] | None = None


@dataclass(frozen=True)
class InvestigationAction:
    """One concrete thing the engine could do next.

    `intent` is the narration shown to the user *before* the action runs —
    "I'll search the indexed repositories to find which service owns this."
    It is mandatory because an investigation nobody can follow isn't
    collaborative, and it's stored onto the resulting `EvidenceRecord` so the
    trail explains what the engine was trying to learn, not just what it did.

    `key` is the dedupe identity: the engine never runs the same key twice
    in one discovery run, so it must encode the action's *target* as well as
    its verb (`traverse_graph:payment-service`, not just `traverse_graph`).

    `cost` orders equally-valuable candidates — cheap local lookups before
    expensive multi-turn MCP conversations. The human is deliberately not
    modelled here: asking a person is not an action the engine can choose,
    it's what happens when there are no actions left.

    `priority` breaks ties among same-cost actions that all target the same
    capability, lower first (0.0 is the default and sorts before anything
    positive). It exists specifically so an investigator proposing several
    actions *for the same capability in one cycle* — e.g. RFC-0011's
    candidate-corroboration funnel, one scoped graph traversal per ranked
    repository — can carry its own upstream ranking signal into `_select`'s
    tie-break, instead of `_select` falling through to `key` and sorting
    those actions alphabetically. Without this, `_select`'s deterministic
    ordering is *unintentionally* alphabetical by whatever string the
    action's target happens to be (a repository name, in this case) — see
    `reasoning.engine._select`'s sort key and RFC-0011's PROT-5764 live
    benchmark, which is exactly how this was found: the two lowest-ranked
    members of a 4-candidate funnel sorted first alphabetically and
    exhausted the cycle budget before the two highest-ranked ones — one of
    them the actual answer — were ever investigated.
    """

    provider: str
    key: str
    intent: str
    targets: str
    params: dict[str, Any] = field(default_factory=dict)
    cost: int = 1
    priority: float = 0.0
    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")


class Recorder:
    """Write handle an investigator uses to record what it found.

    Bound to one (ledger, action, iteration) triple so an investigator
    physically cannot record a fact without provenance, mislabel which
    provider produced it, or forget which cycle it belongs to. This is why
    `Ledger.add_fact`'s "evidence_id must already exist" rule is never
    awkward in practice: the recorder hands back the evidence record and the
    fact helper defaults to it.
    """

    def __init__(self, ledger: Ledger, action: InvestigationAction, iteration: int) -> None:
        self._ledger = ledger
        self._action = action
        self._iteration = iteration
        self._last_evidence: EvidenceRecord | None = None

    @property
    def ledger(self) -> Ledger:
        """RFC-0022 — read-only access to the underlying `Ledger`, for an
        investigator's `run()` that needs a pure, ledger-based derivation
        function (e.g. `capabilities.py`'s scoring helpers, which take a
        `Ledger` directly) rather than re-deriving the same thing from
        `facts_of` piecemeal. Same read-only spirit as `facts_of` above —
        writes still only ever happen through `.fact()`/`.evidence()`."""
        return self._ledger

    def evidence(
        self, outcome: Outcome, summary: str, *, action: str | None = None
    ) -> EvidenceRecord:
        record = self._ledger.add_evidence(
            provider=self._action.provider,
            action=action or self._action.key,
            outcome=outcome,
            summary=summary,
            iteration=self._iteration,
            intent=self._action.intent,
        )
        self._last_evidence = record
        return record

    def fact(
        self,
        kind: FactKind,
        subject: str,
        *,
        value: dict[str, Any] | None = None,
        text: str = "",
        verified: bool = True,
        evidence: EvidenceRecord | None = None,
    ) -> Fact:
        record = evidence or self._last_evidence
        if record is None:
            raise ValueError(
                f"Cannot record fact {subject!r} before recording the evidence that produced it."
            )
        return self._ledger.add_fact(
            kind=kind,
            subject=subject,
            provider=self._action.provider,
            evidence_id=record.evidence_id,
            value=value or {},
            text=text,
            iteration=self._iteration,
            verified=verified,
        )

    def facts_of(self, *kinds: FactKind) -> list[Fact]:
        """Read access to already-recorded facts, so an investigator can reason
        about what it (or an earlier cycle) has established — e.g. whether a
        repository was actually named in the request before claiming it was."""
        return self._ledger.facts_of(*kinds)

    def existing_fact(
        self,
        kind: FactKind,
        subject: str,
        *,
        value: dict[str, Any] | None = None,
        unique_on: tuple[str, ...] = (),
    ) -> Fact | None:
        """An already-recorded fact for this subject, if any.

        Investigations legitimately overlap — a scoped graph traversal
        re-reports the repository a broad survey already found. Re-recording it
        would double-count the same knowledge in the findings view and in
        signal evidence lists, so investigators reuse the existing fact
        instead. The original evidence trail is the right one to keep: it names
        the investigation that first established the fact.

        `unique_on` names extra `value` keys that participate in identity.
        Subject alone is not always unique: two services can each own a
        component called `RetryHandler`, and collapsing them loses the fact
        that the second repository has one at all — which then reads as
        "no components belong to the repository you chose".
        """
        for fact in self._ledger.facts:
            if fact.kind != kind or fact.subject != subject:
                continue
            if all((value or {}).get(key) == fact.value.get(key) for key in unique_on):
                return fact
        return None

    def fact_once(
        self,
        kind: FactKind,
        subject: str,
        *,
        value: dict[str, Any] | None = None,
        text: str = "",
        evidence: EvidenceRecord | None = None,
        unique_on: tuple[str, ...] = (),
    ) -> Fact:
        """`fact`, but idempotent per (kind, subject, *unique_on)."""
        found = self.existing_fact(kind, subject, value=value, unique_on=unique_on)
        if found is not None:
            return found
        return self.fact(kind, subject, value=value, text=text, evidence=evidence)

    # No `inference()`/`withdraw()` methods here, deliberately (ADR 0010,
    # invariant I1 — "Investigators observe. They never interpret."). An
    # investigator's `run()` receives only this `Recorder`, and this
    # `Recorder` has no method capable of writing or withdrawing an
    # `Inference` — interpretation of facts into inferences happens
    # exclusively in `capabilities.LEDGER_RESYNC_HOOKS`, which operate on a
    # raw `Ledger` (see `engine._resync`), not on a `Recorder`. This is
    # structural enforcement, not a convention: there is no API surface here
    # for an investigator to violate the invariant with even if it wanted to.


@dataclass
class InvestigationOutcome:
    """What the engine learns from having run one action.

    The facts themselves already went into the ledger via the `Recorder` —
    this carries only what the engine itself needs: how to narrate the
    result, whether anything was actually learned (`yielded`, which decides
    whether progress was made), and any derived text products that belong to
    working memory rather than to the fact ledger (`derived`, e.g. the
    pre-formatted graph context blob Planning's prompt is rendered from).
    """

    observation: str
    yielded: bool = False
    derived: dict[str, Any] = field(default_factory=dict)


class Investigator(Protocol):
    """A passive provider adapter. Two responsibilities, both reactive.

    `propose` must be honest about preconditions: only return an action when
    it could plausibly close one of the currently-open gaps, and never return
    one whose `key` is already in the ledger. The engine trusts an empty list
    to mean "exhausted", so a provider that proposes work it cannot do would
    keep the loop spinning, and one that stays silent when it could help
    would send the user a question that didn't need asking.
    """

    name: str

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:  # pragma: no cover
        ...

    async def run(
        self,
        action: InvestigationAction,
        session: SessionContext,
        recorder: Recorder,
    ) -> InvestigationOutcome:  # pragma: no cover
        ...
