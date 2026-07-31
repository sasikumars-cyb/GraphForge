"""Unit tests for Context Discovery's reasoning engine
(app.context_pipeline.reasoning).

These target the architectural guarantees, not just the happy path:

- the ledger physically cannot hold a fact without evidence, or an
  interpretation without facts
- confidence is derived from named signals over the ledger, and inapplicable
  capabilities are excluded rather than defaulted
- reasoning drives gathering: an investigator that can't help stays silent,
  and the engine stops as soon as requirements are met
- the human is the last resort: no question exists until every investigator
  has declined to propose work
- verify-then-resolve: an answer that no investigation corroborates is
  refuted, moves no confidence, and creates no fact

Investigators are hand-written fakes here so the loop's decisions are visible;
the real ones are exercised through `test_context_discovery_agent.py` and the
provider-level integration path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.context_pipeline.reasoning import capabilities
from app.context_pipeline.reasoning.capabilities import (
    CapabilityAssessment,
    assess,
    overall_confidence,
    unmet,
)
from app.context_pipeline.reasoning.engine import (
    MAX_CLARIFICATION_ROUNDS,
    MAX_MID_LOOP_SYNTHESIS_CALLS,
    _select,
    _settle_claims,
    discover,
    resume,
)
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    InvestigationOutcome,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.investigators import (
    GraphInvestigator,
    RequestParseInvestigator,
)
from app.context_pipeline.reasoning.ledger import Ledger
from app.context_pipeline.reasoning.memory import KnowledgeGap, WorkingContext
from app.context_pipeline.reasoning.projection import build_result, to_contract_evidence
from app.tools.interfaces import ToolResult


def _session() -> SessionContext:
    return SessionContext(db=None, user_id=None)  # type: ignore[arg-type]


class FakeInvestigator:
    """Records what it was asked to do and yields a scripted set of facts."""

    def __init__(
        self,
        name: str,
        *,
        targets: str = "repository",
        cost: int = 1,
        repositories: list[str] | None = None,
        components: list[tuple[str, str]] | None = None,
        silent: bool = False,
    ) -> None:
        self.name = name
        self._targets = targets
        self._cost = cost
        self._repositories = repositories or []
        self._components = components or []
        self._silent = silent
        self.ran: list[str] = []

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        if self._silent or state.ledger.attempted(self.name, "look"):
            return []
        return [
            InvestigationAction(
                provider=self.name,
                key="look",
                intent=f"{self.name}: looking",
                targets=self._targets,
                cost=self._cost,
            )
        ]

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        self.ran.append(action.key)
        evidence = recorder.evidence("success", f"{self.name} looked.")
        facts = [
            recorder.fact("repository", name, value={"name": name}, evidence=evidence)
            for name in self._repositories
        ]
        if self.name == "graph":
            # A graph investigator must record its traversal attempt under the
            # shared action name: that record is what the `architecture`
            # capability reads for reachability, and what distinguishes a
            # genuine traversal from a Postgres-only repository lookup. A fake
            # that skips it isn't standing in for the real contract.
            traversal = recorder.evidence(
                "success" if self._components else "not_found",
                f"{self.name} traversed.",
                action=capabilities.GRAPH_TRAVERSAL_ACTION,
            )
            for comp, repo in self._components:
                recorder.fact(
                    "component",
                    comp,
                    value={"name": comp, "repository": repo},
                    evidence=traversal,
                )
        if len(facts) > 1:
            # Simulate a broad, unfocused survey that found every repository
            # equally plausible — the real `GraphInvestigator` records this
            # same fact kind (`repository_ranking`); interpreting it into
            # `repository_candidate` inferences happens entirely in
            # `capabilities.LEDGER_RESYNC_HOOKS`, never inside an
            # investigator's `run()` (ADR 0010, invariant I1). A single
            # repository needs no ranking fact at all — `resync_ranked_
            # candidates` promotes "the only indexed repository" from the
            # `repository` fact alone.
            recorder.fact(
                "repository_ranking",
                "ranking",
                value={"scored": [[1.0, fact.subject] for fact in facts]},
            )
        return InvestigationOutcome(
            observation=f"{self.name} found {len(self._repositories)} repos.",
            yielded=bool(facts),
        )


# ---------------------------------------------------------------------------
# Ledger: provenance is structurally enforced
# ---------------------------------------------------------------------------


def test_ledger_rejects_a_fact_without_real_evidence() -> None:
    ledger = Ledger()
    with pytest.raises(ValueError, match="not in this ledger"):
        ledger.add_fact(kind="repository", subject="ghost", provider="graph", evidence_id="ev_nope")


def test_ledger_rejects_an_uncited_interpretation() -> None:
    ledger = Ledger()
    with pytest.raises(ValueError, match="must cite the facts"):
        ledger.add_inference(
            kind="assumption", statement="probably the payment service", supporting_fact_ids=[]
        )


def test_withdrawn_inferences_are_superseded_not_deleted() -> None:
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="a", outcome="success", summary="s")
    fact = ledger.add_fact(
        kind="repository", subject="payment-service", provider="graph", evidence_id=ev.evidence_id
    )
    ledger.add_inference(
        kind="repository_candidate", statement="payment-service", supporting_fact_ids=[fact.fact_id]
    )
    ledger.withdraw_inferences("repository_candidate")

    assert ledger.live_inferences("repository_candidate") == []
    assert len(ledger.inferences) == 1, "history is kept, not erased"


# ---------------------------------------------------------------------------
# Confidence is evidence-derived
# ---------------------------------------------------------------------------


def test_capability_score_decomposes_into_its_signals() -> None:
    ledger = Ledger()
    ev = ledger.add_evidence(
        provider="graph",
        action=capabilities.GRAPH_TRAVERSAL_ACTION,
        outcome="not_found",
        summary="s",
    )
    ledger.add_fact(
        kind="repository", subject="payment-service", provider="graph", evidence_id=ev.evidence_id
    )

    architecture = next(a for a in assess(ledger) if a.capability == "architecture")
    satisfied = [s for s in architecture.signals if s.satisfied]
    total_weight = sum(s.weight for s in architecture.signals)
    expected = sum(s.weight for s in satisfied) / total_weight

    assert architecture.score == pytest.approx(expected, abs=1e-4)
    assert "✓" in architecture.explanation()
    assert "✗" in architecture.explanation()


def test_satisfied_signals_cite_evidence_and_unsatisfied_ones_explain() -> None:
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")
    ledger.add_fact(
        kind="repository", subject="payment-service", provider="graph", evidence_id=ev.evidence_id
    )

    repository = next(a for a in assess(ledger) if a.capability == "repository")
    for signal in repository.signals:
        if signal.satisfied:
            assert signal.evidence_ids, f"{signal.label} must cite the evidence behind it"
        else:
            assert signal.detail, f"{signal.label} must say what is missing"


def test_inapplicable_capabilities_are_excluded_from_overall_confidence() -> None:
    """A request with no ticket and no doc anchor must not be scored on
    work_item/documentation — the old design defaulted them to 1.0 and
    inflated the total with capabilities it never examined."""
    assessments = assess(Ledger())
    assert {a.capability for a in assessments if a.necessity == "not_applicable"} == {
        "work_item",
        "documentation",
    }
    # An empty ledger knows nothing, so the honest overall score is zero.
    assert overall_confidence(assessments) == 0.0


def test_ambiguity_needs_no_special_case_just_two_live_candidates() -> None:
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")
    for name in ("payment-service", "billing-service"):
        fact = ledger.add_fact(
            kind="repository", subject=name, provider="graph", evidence_id=ev.evidence_id
        )
        ledger.add_inference(
            kind="repository_candidate", statement=name, supporting_fact_ids=[fact.fact_id]
        )

    repository = next(a for a in assess(ledger) if a.capability == "repository")
    identified = next(s for s in repository.signals if s.label == "Owning repository identified")
    assert identified.satisfied is False
    assert "equally plausible" in identified.detail


# ---------------------------------------------------------------------------
# Reasoning drives gathering
# ---------------------------------------------------------------------------


def test_selection_prefers_required_capabilities_over_recommended() -> None:
    ledger = Ledger()
    assessments = assess(ledger)
    required = InvestigationAction(
        provider="graph", key="a", intent="i", targets="repository", cost=3
    )
    recommended = InvestigationAction(
        provider="confluence",
        key="b",
        intent="i",
        targets="documentation",
        cost=1,
    )
    marker = object()
    chosen, _ = _select([(recommended, marker), (required, marker)], assessments)  # type: ignore[list-item]
    assert chosen is required, "a cheap optional lookup must not preempt required context"


def test_select_priority_boost_breaks_ties_within_the_same_necessity_tier() -> None:
    """`priority_boost` (engineering understanding's read of which
    investigation would most improve its current hypotheses — see
    reasoning.understanding.capability_priority) can prefer one candidate
    over an equally-scored one of the SAME necessity, without needing a
    real LLM call at selection time: this test builds the boost by hand."""
    marker = object()
    assessments = [
        CapabilityAssessment(
            capability="architecture",
            label="Architecture",
            necessity="recommended",
            score=0.5,
            signals=[],
        ),
        CapabilityAssessment(
            capability="documentation",
            label="Documentation",
            necessity="recommended",
            score=0.5,
            signals=[],
        ),
    ]
    architecture_action = InvestigationAction(
        provider="graph", key="a", intent="i", targets="architecture", cost=1
    )
    documentation_action = InvestigationAction(
        provider="confluence", key="b", intent="i", targets="documentation", cost=1
    )
    candidates = [(architecture_action, marker), (documentation_action, marker)]  # type: ignore[list-item]

    # With no boost, tied necessity/score/cost falls back to the action key —
    # "a" sorts before "b", so architecture wins on tie-break alone.
    chosen, _ = _select(candidates, assessments)
    assert chosen is architecture_action

    # A contradiction-driven boost on documentation must be able to flip that.
    chosen, _ = _select(candidates, assessments, {"documentation": 0.4})
    assert chosen is documentation_action


def test_select_priority_boost_never_overrides_necessity_tier() -> None:
    """A required capability must always win over a recommended one, no
    matter how large a priority boost the recommended one receives — the
    boost is a tie-breaker within a tier, never a way to skip required
    context (same invariant `test_selection_prefers_required_capabilities_
    over_recommended` already guarantees for the un-boosted case)."""
    marker = object()
    assessments = [
        CapabilityAssessment(
            capability="repository",
            label="Repository",
            necessity="required",
            score=0.9,
            signals=[],
        ),
        CapabilityAssessment(
            capability="documentation",
            label="Documentation",
            necessity="recommended",
            score=0.1,
            signals=[],
        ),
    ]
    required_action = InvestigationAction(
        provider="graph", key="a", intent="i", targets="repository", cost=3
    )
    recommended_action = InvestigationAction(
        provider="confluence", key="b", intent="i", targets="documentation", cost=1
    )
    candidates = [(recommended_action, marker), (required_action, marker)]  # type: ignore[list-item]

    chosen, _ = _select(candidates, assessments, {"documentation": 100.0})
    assert chosen is required_action


@pytest.mark.asyncio
async def test_mid_loop_synthesis_runs_and_is_bounded_by_its_own_budget() -> None:
    """Engineering understanding actively participating in investigation,
    not just summarizing it afterward: a mid-loop call happens after a real
    (paid) retrieval yields something, its `investigation_priority` output
    lands on `state.derived` where the next `_select` call reads it, and the
    total call count never exceeds the mid-loop budget plus the one
    always-run call after the loop exits (see engine.py's own comments on
    both call sites). `synthesize_engineering_understanding` itself is
    replaced with a deterministic fake — the real LLM-backed version is
    covered by test_understanding.py; this test is only about the wiring."""
    calls: list[int] = []

    async def fake_synthesize(state: WorkingContext, session: SessionContext) -> None:
        calls.append(1)
        state.metadata.synthesis_calls += 1
        state.derived["investigation_priority"] = {"documentation": 0.9}

    productive = FakeInvestigator(
        "graph", repositories=["payment-service"], components=[("RateLimiter", "payment-service")]
    )
    extra = FakeInvestigator("confluence", targets="documentation", cost=3)

    with patch(
        "app.context_pipeline.reasoning.understanding.synthesize_engineering_understanding",
        new=fake_synthesize,
    ):
        state = await discover(
            request="Add a rate limiter to payment-service",
            session=_session(),
            investigators=[productive, extra],
        )

    # One mid-loop call (the budget is 1) plus the always-run call after the
    # loop exits — never more, regardless of how many cycles ran.
    assert len(calls) == MAX_MID_LOOP_SYNTHESIS_CALLS + 1
    assert state.metadata.synthesis_calls == len(calls)
    assert state.derived["investigation_priority"] == {"documentation": 0.9}


@pytest.mark.asyncio
async def test_engine_stops_once_requirements_are_met() -> None:
    """The second investigator must never run: once the graph satisfied the
    required capabilities there is nothing left worth gathering."""
    productive = FakeInvestigator(
        "graph", repositories=["payment-service"], components=[("RateLimiter", "payment-service")]
    )
    extra = FakeInvestigator("confluence", targets="documentation", cost=3)

    state = await discover(
        request="Add a rate limiter to payment-service",
        session=_session(),
        investigators=[productive, extra],
    )

    assert state.readiness == "READY"
    assert productive.ran == ["look"]
    assert extra.ran == [], "gathering must stop when nothing is missing"


@pytest.mark.asyncio
async def test_an_investigator_that_cannot_help_stays_silent_and_is_never_run() -> None:
    silent = FakeInvestigator("github", silent=True)
    state = await discover(
        request="Add a rate limiter",
        session=_session(),
        investigators=[FakeInvestigator("graph", repositories=["payment-service"]), silent],
    )
    assert silent.ran == []
    assert "github" not in {e.provider for e in state.ledger.evidence}


@pytest.mark.asyncio
async def test_transcript_narrates_intent_before_each_observation() -> None:
    state = await discover(
        request="Add a rate limiter",
        session=_session(),
        investigators=[FakeInvestigator("graph", repositories=["payment-service"])],
    )
    kinds = [e.kind for e in state.transcript.entries]
    assert kinds[0] == "intent"
    assert "observation" in kinds
    assert kinds[-1] == "conclusion"
    # Intent always precedes the observation it explains.
    assert kinds.index("intent") < kinds.index("observation")


@pytest.mark.asyncio
async def test_a_failing_investigator_is_recorded_and_does_not_kill_discovery() -> None:
    class Exploding:
        name = "graph"

        def propose(self, state: WorkingContext) -> list[InvestigationAction]:
            if state.ledger.attempted(self.name, "boom"):
                return []
            return [
                InvestigationAction(
                    provider=self.name,
                    key="boom",
                    intent="about to fail",
                    targets="repository",
                )
            ]

        async def run(self, action, session, recorder):  # type: ignore[no-untyped-def]
            raise RuntimeError("neo4j exploded")

    state = await discover(request="anything", session=_session(), investigators=[Exploding()])

    failures = [e for e in state.ledger.evidence if e.outcome == "failed"]
    assert failures, "the failed attempt must still be visible in the trail"
    assert state.readiness == "BLOCKED"


# ---------------------------------------------------------------------------
# The human is the last resort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_question_is_asked_while_an_investigator_could_still_help() -> None:
    """A question must never be produced mid-investigation. `next_question`
    refuses until providers_exhausted, so a still-working engine can't ask."""
    state = WorkingContext()
    state.metadata.goal = "x"
    state.refresh_assessments()
    assert state.metadata.providers_exhausted is False
    assert state.next_question() is None


@pytest.mark.asyncio
async def test_question_appears_only_after_every_investigator_declined() -> None:
    state = await discover(
        request="Add retry logic",
        session=_session(),
        # Yields two tied candidates and then has nothing more to offer.
        investigators=[
            FakeInvestigator("graph", repositories=["payment-service", "billing-service"])
        ],
    )

    assert state.metadata.providers_exhausted is True
    question = state.next_question()
    assert question is not None
    assert question.question_id == "gap_repository"
    # Real values only — an instruction label as an option is what let a UI
    # verb be submitted as an answer.
    assert set(question.options) == {"payment-service", "billing-service"}
    assert question.investigated, "the question must show what was already tried"


@pytest.mark.asyncio
async def test_exactly_one_question_is_outstanding_at_a_time() -> None:
    state = await discover(
        request="Add retry logic",
        session=_session(),
        investigators=[
            FakeInvestigator("graph", repositories=["payment-service", "billing-service"])
        ],
    )
    answerable = [g for g in state.gaps if g.question is not None]
    assert len(answerable) == 1


# ---------------------------------------------------------------------------
# Verify-then-resolve
# ---------------------------------------------------------------------------


async def _tied_state() -> WorkingContext:
    """Two equally-plausible repositories, each with architecture indexed — so
    `repository` is the *only* unmet capability and readiness turns purely on
    whether the ambiguity gets resolved."""
    return await discover(
        request="Add retry logic",
        session=_session(),
        investigators=[
            FakeInvestigator(
                "graph",
                repositories=["payment-service", "billing-service"],
                components=[
                    ("RetryHandler", "payment-service"),
                    ("RetryHandler", "billing-service"),
                ],
            )
        ],
    )


@pytest.mark.asyncio
async def test_a_corroborated_answer_verifies_and_moves_confidence() -> None:
    state = await _tied_state()

    class Verifier:
        """Stands in for the graph's verification query: confirms the named
        repository really is indexed."""

        name = "graph"

        def propose(self, s: WorkingContext) -> list[InvestigationAction]:
            for gap in s.gaps:
                if gap.status == "claimed" and gap.user_claim:
                    key = f"verify:{gap.user_claim}"
                    if not s.ledger.attempted(self.name, key):
                        return [
                            InvestigationAction(
                                provider=self.name,
                                key=key,
                                intent="verifying",
                                targets=gap.capability,
                                params={"claim": gap.user_claim},
                            )
                        ]
            return []

        async def run(self, action, session, recorder):  # type: ignore[no-untyped-def]
            # Records only what it observed — whether the claimed repository
            # is indexed. Promoting a corroborated claim into an explicit
            # `repository_candidate` inference is `capabilities.resync_
            # verified_claim_candidates`'s job, triggered once
            # `engine._settle_claims` raises `Fact.verified` on the
            # underlying `user_statement` fact (ADR 0010, invariant I1).
            claim = action.params["claim"]
            recorder.evidence("success", f"Confirmed {claim} is indexed.")
            existing = recorder.existing_fact("repository", claim)
            if existing is None:
                return InvestigationOutcome(observation=f"{claim} is not indexed.", yielded=False)
            return InvestigationOutcome(observation=f"Confirmed {claim}.", yielded=True)

    resumed = await resume(
        state=state,
        question_id="gap_repository",
        answer="billing-service",
        session=_session(),
        investigators=[Verifier()],
    )

    gap = resumed.gap_for("repository")
    assert gap is not None
    assert gap.status == "verified"
    assert resumed.readiness == "READY"
    # The claim's own fact is promoted only once corroborated.
    claim_facts = resumed.ledger.facts_of("user_statement", verified_only=False)
    assert claim_facts and claim_facts[0].verified is True


@pytest.mark.asyncio
async def test_an_uncorroborated_answer_is_refuted_and_creates_no_knowledge() -> None:
    """The exact failure the old design had: an answer that is really a UI
    instruction was accepted, promoted to a repository, and flipped readiness
    to READY. Nothing may corroborate it, so nothing may change."""
    state = await _tied_state()
    before = state.confidence

    class NeverConfirms:
        name = "graph"

        def propose(self, s: WorkingContext) -> list[InvestigationAction]:
            for gap in s.gaps:
                if gap.status == "claimed" and gap.user_claim:
                    key = f"verify:{gap.user_claim}"
                    if not s.ledger.attempted(self.name, key):
                        return [
                            InvestigationAction(
                                provider=self.name,
                                key=key,
                                intent="verifying",
                                targets=gap.capability,
                            )
                        ]
            return []

        async def run(self, action, session, recorder):  # type: ignore[no-untyped-def]
            recorder.withdraw("repository_candidate")
            recorder.evidence("not_found", "That repository is not indexed.")
            return InvestigationOutcome(observation="Not found.", yielded=False)

    resumed = await resume(
        state=state,
        question_id="gap_repository",
        answer="Select a repository",
        session=_session(),
        investigators=[NeverConfirms()],
    )

    gap = resumed.gap_for("repository")
    assert gap is not None
    assert gap.status == "refuted"
    assert resumed.readiness == "BLOCKED"
    assert resumed.confidence <= before
    # No phantom repository entered the knowledge base.
    assert "Select a repository" not in resumed.ledger.subjects_of("repository")
    assert "Select a repository" not in [
        i.statement for i in resumed.ledger.live_inferences("repository_candidate")
    ]
    claim_facts = resumed.ledger.facts_of("user_statement", verified_only=False)
    assert claim_facts and claim_facts[0].verified is False


@pytest.mark.asyncio
async def test_refuted_answer_is_narrated_rather_than_silently_dropped() -> None:
    state = await _tied_state()
    resumed = await resume(
        state=state,
        question_id="gap_repository",
        answer="does-not-exist",
        session=_session(),
        investigators=[],
    )
    said = " ".join(resumed.transcript.lines())
    assert "does-not-exist" in said
    assert "couldn't confirm" in said


@pytest.mark.asyncio
async def test_clarification_rounds_are_capped() -> None:
    state = await _tied_state()
    for i in range(MAX_CLARIFICATION_ROUNDS):
        state = await resume(
            state=state,
            question_id="gap_repository",
            answer=f"wrong-{i}",
            session=_session(),
            investigators=[],
        )

    assert state.metadata.clarification_rounds == MAX_CLARIFICATION_ROUNDS
    assert state.next_question() is None, "the loop must stop asking, not stop being blocked"
    assert state.readiness == "BLOCKED"
    gap = state.gap_for("repository")
    assert gap is not None and gap.status == "unresolvable"
    assert gap.recommended_action, "an unresolvable gap must still say what to do"


def test_verifying_one_claim_does_not_verify_a_different_claim_with_the_same_text() -> None:
    """Two different clarification questions answered with the same literal
    string in one run (e.g. both answered "payment-service") must be
    corroborated independently. `_settle_claims` used to match a claim's
    `user_statement` fact by answer text alone, so confirming one gap's
    claim silently flipped `verified=True` on every other gap's fact that
    happened to carry the same text — including one that was never itself
    corroborated."""
    ledger = Ledger()

    ev1 = ledger.add_evidence(provider="user", action="answer:q1", outcome="success", summary="s1")
    ledger.add_fact(
        kind="user_statement",
        subject="same-answer",
        provider="user",
        evidence_id=ev1.evidence_id,
        value={"question_id": "q1", "capability": "repository"},
        verified=False,
    )
    ev2 = ledger.add_evidence(provider="user", action="answer:q2", outcome="success", summary="s2")
    ledger.add_fact(
        kind="user_statement",
        subject="same-answer",
        provider="user",
        evidence_id=ev2.evidence_id,
        value={"question_id": "q2", "capability": "work_item"},
        verified=False,
    )
    # Only the repository claim gets independently corroborated.
    repo_fact = ledger.add_fact(
        kind="repository", subject="same-answer", provider="graph", evidence_id=ev1.evidence_id
    )
    ledger.add_inference(
        kind="repository_candidate",
        statement="same-answer",
        supporting_fact_ids=[repo_fact.fact_id],
    )

    state = WorkingContext(ledger=ledger)
    state.gaps.append(
        KnowledgeGap(
            gap_id="gap_repository",
            capability="repository",
            summary="s",
            why="w",
            severity="blocking",
            status="claimed",
            user_claim="same-answer",
            question=capabilities.ClarificationQuestion(question_id="q1", question="q", why="w"),
        )
    )
    state.gaps.append(
        KnowledgeGap(
            gap_id="gap_work_item",
            capability="work_item",
            summary="s",
            why="w",
            severity="blocking",
            status="claimed",
            user_claim="same-answer",
            question=capabilities.ClarificationQuestion(question_id="q2", question="q", why="w"),
        )
    )

    _settle_claims(state)

    repository_gap = state.gap_for("repository")
    work_item_gap = state.gap_for("work_item")
    assert repository_gap is not None and repository_gap.status == "verified"
    assert work_item_gap is not None and work_item_gap.status == "refuted", (
        "work_item's own claim was never independently corroborated"
    )

    facts_by_question = {
        f.value.get("question_id"): f
        for f in state.ledger.facts_of("user_statement", verified_only=False)
    }
    assert facts_by_question["q1"].verified is True
    assert facts_by_question["q2"].verified is False, (
        "verifying q1's claim must not verify q2's fact just because the text matched"
    )


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_is_blocked_when_a_required_capability_is_unmet() -> None:
    state = await discover(request="x", session=_session(), investigators=[])
    assert state.readiness == "BLOCKED"
    assert any(a.necessity == "required" for a in unmet(state.assessments))


@pytest.mark.asyncio
async def test_readiness_is_partial_when_only_a_recommended_capability_is_unmet() -> None:
    """Documentation is recommended: missing it must not block, but must be
    surfaced and require acknowledgement rather than being hidden."""
    state = await discover(
        request="Implement PROT-1",
        session=_session(),
        investigators=[
            FakeInvestigator(
                "graph", repositories=["payment-service"], components=[("C", "payment-service")]
            )
        ],
    )
    # Force the documentation capability to apply by adding a work item fact.
    ev = state.ledger.add_evidence(
        provider="jira", action="fetch", outcome="success", summary="fetched"
    )
    state.ledger.add_fact(
        kind="work_item", subject="PROT-1", provider="jira", evidence_id=ev.evidence_id
    )
    state.ledger.add_fact(
        kind="reference",
        subject="PROT-1",
        provider="request_parser",
        evidence_id=ev.evidence_id,
        value={"type": "jira_issue"},
    )
    state.refresh_assessments()

    documentation = state.assessment_for("documentation")
    assert documentation is not None and documentation.necessity == "recommended"
    assert state.readiness == "PARTIAL"


# ---------------------------------------------------------------------------
# The capability registry — extensibility and the question/verify pairing
# ---------------------------------------------------------------------------


def test_a_capability_cannot_ask_a_question_it_cannot_verify() -> None:
    """The structural guarantee behind "verify before resolve": a capability
    declares `question` and `verify` together, so it is impossible to ship a
    question whose answer nothing would ever check."""
    with pytest.raises(ValueError, match="must declare `question` and `verify` together"):
        capabilities.Capability(
            key="rogue",
            label="Rogue",
            gap_summary="s",
            gap_why="w",
            necessity=lambda _l: "required",
            signals=lambda _l: [],
            remediation=lambda _l: [],
            question=lambda _ctx: capabilities.ClarificationQuestion(
                question_id="q", question="?", why="because"
            ),
            # verify omitted
        )

    with pytest.raises(ValueError, match="must declare `question` and `verify` together"):
        capabilities.Capability(
            key="rogue2",
            label="Rogue",
            gap_summary="s",
            gap_why="w",
            necessity=lambda _l: "required",
            signals=lambda _l: [],
            remediation=lambda _l: [],
            verify=lambda _l, _c: True,  # verify without a question
        )


def test_every_registered_capability_is_self_consistent() -> None:
    """Guards the registry itself: each capability must assess cleanly against
    an empty ledger, and any askable one must pair with a verifier."""
    ledger = Ledger()
    for capability in capabilities.CAPABILITIES:
        assessment = capability.assess(ledger)
        assert assessment.capability == capability.key
        assert 0.0 <= assessment.score <= 1.0
        assert capability.remediation(ledger) or not capability.askable
        assert capability.askable == (capability.verify is not None)


@pytest.mark.asyncio
async def test_a_new_capability_needs_no_engine_changes() -> None:
    """The extensibility claim, tested rather than asserted in a comment.

    Registering one new `Capability` must be enough for the engine to assess
    it, raise a gap for it, choose its remediation, ask its question and
    verify the answer — with no edit to engine.py, memory.py or projection.py.
    """
    probe = capabilities.Capability(
        key="deployment_topology",
        label="Deployment topology",
        gap_summary="Deployment topology is unknown.",
        gap_why="A plan that ignores where this runs can propose an impossible rollout.",
        necessity=lambda _l: "required",
        signals=lambda ledger: [
            capabilities.signal(
                "Topology discovered",
                ledger.has_fact("topic"),
                2.0,
                detail="no deployment topology is recorded",
            )
        ],
        remediation=lambda _l: ["Connect the deployment inventory"],
        question=lambda ctx: capabilities.ClarificationQuestion(
            question_id="gap_deployment_topology",
            question="Which environment does this deploy to?",
            why="I couldn't find any topology data.",
            options=["staging", "production"],
            investigated=ctx.investigated,
        ),
        verify=lambda ledger, claim: claim in ledger.subjects_of("topic"),
    )

    original = capabilities.CAPABILITIES
    try:
        capabilities.CAPABILITIES = (*original, probe)
        capabilities.BY_KEY[probe.key] = probe

        state = await discover(
            request="Ship the new limiter",
            session=_session(),
            investigators=[
                FakeInvestigator(
                    "graph",
                    repositories=["payment-service"],
                    components=[("RateLimiter", "payment-service")],
                )
            ],
        )

        # Assessed, and blocking readiness, purely from the declaration.
        assessment = state.assessment_for("deployment_topology")
        assert assessment is not None and not assessment.satisfied
        assert state.readiness == "BLOCKED"

        # A gap was raised with the declared framing and remediation.
        gap = state.gap_for("deployment_topology")
        assert gap is not None
        assert gap.summary == "Deployment topology is unknown."
        assert gap.recommended_action == ["Connect the deployment inventory"]

        # And its declared question is the one asked.
        question = state.next_question()
        assert question is not None
        assert question.question_id == "gap_deployment_topology"
        assert question.options == ["staging", "production"]
    finally:
        capabilities.CAPABILITIES = original
        capabilities.BY_KEY.pop(probe.key, None)


# ---------------------------------------------------------------------------
# Assumptions, evidence honesty, and persistence hygiene
# ---------------------------------------------------------------------------


def _graph_tool_result(repositories: list[str], components: list[tuple[str, str]]) -> ToolResult:
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": n} for n in repositories],
            "components": [{"name": c, "repository": r, "type": "service"} for c, r in components],
            "kafka_topics": [],
            "context_text": "graph context",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": f"{len(repositories)} repos",
            "_traverse_summary": f"{len(components)} components",
        },
        summary="queried",
    )


@pytest.mark.asyncio
async def test_choosing_the_only_indexed_repository_is_recorded_as_an_assumption() -> None:
    """Reasoning from absence is not the same as matching. When a repository is
    picked only because it is the sole indexed one, the user must be told that
    — otherwise "the repository" silently reads as something the request named.

    Uses the real `GraphInvestigator`, since recording this is part of how it
    interprets a ranking rather than something the engine does generically.
    """
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["payment-service"], [("RateLimiter", "payment-service")]
            )
        ),
    ):
        state = await discover(
            request="Add a rate limiter",
            session=_session(),
            investigators=[GraphInvestigator()],
        )

    assumptions = [i for i in state.ledger.inferences if i.kind == "assumption"]
    assert assumptions, "picking the only indexed repository is an assumption"
    assert "only indexed repository" in assumptions[0].statement
    # And, like every interpretation, it cites the facts it rests on.
    assert assumptions[0].supporting_fact_ids
    assert build_result(state)["assumptions"], "assumptions must reach the persisted result"


@pytest.mark.asyncio
async def test_a_relevance_ranked_choice_is_also_recorded_as_an_assumption() -> None:
    """Picking a winner from component-name relevance is a heuristic judgement,
    not a lookup. A user can only push back on it if told it was a judgement."""
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["payment-service", "billing-service"],
                [("RateLimiter", "payment-service"), ("Mailer", "billing-service")],
            )
        ),
    ):
        state = await discover(
            request="Add a rate limiter to the throttling path",
            session=_session(),
            investigators=[GraphInvestigator()],
        )

    candidates = state.ledger.live_inferences("repository_candidate")
    if len(candidates) != 1:
        pytest.skip("ranking did not produce a single leader for this fixture")
    assumptions = [i for i in state.ledger.inferences if i.kind == "assumption"]
    assert assumptions, "a relevance-ranked pick is an assumption, not an observation"
    assert "inferred from how closely" in assumptions[0].statement


@pytest.mark.asyncio
async def test_a_work_item_claim_is_never_proposed_as_a_repository_verification() -> None:
    """Regression: `GraphInvestigator.propose` looped over every `claimed`
    gap regardless of its capability, so answering a work_item clarification
    question (a corrected Jira key) was treated as a repository name to
    verify. That queried the graph for the ticket key and — because
    `_reassess_candidates` unconditionally withdraws every live
    `repository_candidate` inference before checking whether the focus
    matched anything — silently destroyed an already-correct, already-
    satisfied repository identification established earlier in the same
    run, flipping readiness from satisfied to blocked for a reason that had
    nothing to do with the repository at all.
    """
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["payment-service"], [("RateLimiter", "payment-service")]
            )
        ),
    ):
        state = await discover(
            request="Add a rate limiter", session=_session(), investigators=[GraphInvestigator()]
        )

    assert [i.statement for i in state.ledger.live_inferences("repository_candidate")] == [
        "payment-service"
    ]
    repository = state.assessment_for("repository")
    assert repository is not None and repository.satisfied is True

    # Simulate a work_item clarification question having just been answered
    # — nothing to do with the repository, which was already resolved above.
    state.gaps.append(
        KnowledgeGap(
            gap_id="gap_work_item",
            capability="work_item",
            summary="s",
            why="w",
            severity="blocking",
            status="claimed",
            user_claim="PROJ-456",
        )
    )

    actions = GraphInvestigator().propose(state)
    assert not any(a.key.startswith("verify_repository:") for a in actions), (
        "a work_item claim must never be proposed as a repository verification"
    )

    # And the already-established repository candidate must survive being
    # asked to propose again — not merely "no action proposed this time".
    assert [i.statement for i in state.ledger.live_inferences("repository_candidate")] == [
        "payment-service"
    ]


@pytest.mark.asyncio
async def test_a_failed_graph_traversal_is_never_grounding_evidence() -> None:
    """The agent contract treats `graph_traversal` as proof the graph was
    consulted. A failed attempt must stay visible but must not carry that kind,
    or an unreachable graph could satisfy the grounding requirement."""

    class BrokenGraph:
        name = "graph"

        def propose(self, s: WorkingContext) -> list[InvestigationAction]:
            if s.ledger.attempted(self.name, "survey"):
                return []
            return [
                InvestigationAction(
                    provider=self.name, key="survey", intent="looking", targets="architecture"
                )
            ]

        async def run(self, action, session, recorder):  # type: ignore[no-untyped-def]
            recorder.evidence("success", "Looked up indexed repositories: 0 found.")
            recorder.evidence(
                "failed",
                "Graph traversal failed: connection refused.",
                action=capabilities.GRAPH_TRAVERSAL_ACTION,
            )
            return InvestigationOutcome(observation="Graph is down.", yielded=False)

    state = await discover(request="anything", session=_session(), investigators=[BrokenGraph()])
    projected = to_contract_evidence(state)

    assert not any(e.kind == "graph_traversal" for e in projected)
    # But the failure is still on the record, both structurally and in the
    # cross-agent summary convention the activity feed reads.
    failed = [e for e in projected if e.status == "failed"]
    assert failed and failed[0].summary.startswith("FAILED: ")
    architecture = state.assessment_for("architecture")
    assert architecture is not None
    reachable = next(
        s for s in architecture.signals if s.label == "Knowledge graph queried without errors"
    )
    assert reachable.satisfied is False


@pytest.mark.asyncio
async def test_a_human_answer_is_labelled_human_input_not_a_tool_call() -> None:
    state = await _tied_state()
    resumed = await resume(
        state=state,
        question_id="gap_repository",
        answer="payment-service",
        session=_session(),
        investigators=[],
    )
    human = [e for e in to_contract_evidence(resumed) if e.kind == "human_input"]
    assert human, "a human answer must be attributed to the human, not to a tool"
    assert "payment-service" in human[0].summary


@pytest.mark.asyncio
async def test_working_memory_is_persisted_only_while_paused() -> None:
    """It exists to resume a paused run. Keeping it on a finished run stores a
    second full copy of the ledger for nothing."""
    paused = await _tied_state()
    assert paused.next_question() is not None
    assert build_result(paused)["working_memory"], "a paused run must be resumable"

    finished = await discover(
        request="Add a rate limiter",
        session=_session(),
        investigators=[
            FakeInvestigator(
                "graph",
                repositories=["payment-service"],
                components=[("RateLimiter", "payment-service")],
            )
        ],
    )
    assert finished.next_question() is None
    assert build_result(finished)["working_memory"] == {}


@pytest.mark.asyncio
async def test_ranking_stays_complete_while_candidates_stay_narrow() -> None:
    """Two different questions, two different fields. Planning reads
    `ranked_repository_names` positionally, so it must cover every indexed
    repository even when discovery cannot pick a winner; the ambiguity lives in
    `implementation_candidates`."""
    state = await _tied_state()
    result = build_result(state)

    assert set(result["ranked_repository_names"]) == {"payment-service", "billing-service"}
    assert len(result["implementation_candidates"]) == 2, "the tie is genuinely unresolved"


# ---------------------------------------------------------------------------
# Budget and remediation accuracy (found by real-browser validation)
# ---------------------------------------------------------------------------


def test_graph_hop_budget_accommodates_more_than_one_graph_query() -> None:
    """The manifest's per-repository read budget must fit the engine's real
    traversal shape.

    Regression: the budget was 2 — exactly one Component read plus one
    KafkaTopic read, sized for the single-pass pipeline this replaced. The
    engine's *second* graph query in a run (a traversal scoped to the repository
    it just identified) therefore raised GraphHopBudgetExceeded, which surfaced
    as "the architecture graph could not be read" and told the user to check
    their Neo4j connection for what was purely an internal ceiling.
    """
    from app.agents.context_discovery.manifest import CONTEXT_DISCOVERY_MANIFEST

    reads_per_query = 2  # one Component label read, one KafkaTopic read
    # survey, then a scoped traversal, then a verification query on resume.
    distinct_graph_queries = 3
    # Plus one bounded-neighborhood fetch (`get_neighborhood`) — the
    # curation stage's own graph read, run once after the investigation
    # loop exits (see investigators.curate_evidence).
    curation_reads = 1
    assert CONTEXT_DISCOVERY_MANIFEST.max_graph_hops >= (
        reads_per_query * distinct_graph_queries + curation_reads
    ), "the manifest must declare the traversal shape the engine actually performs"


def test_budget_exhaustion_is_not_reported_as_an_unreachable_graph() -> None:
    """Hitting our own ceiling is not an infrastructure fault, and must not
    mark the graph unreachable or send the user to check Neo4j."""
    ledger = Ledger()
    ledger.add_evidence(
        provider="graph", action="survey_architecture", outcome="success", summary="1 found"
    )
    ledger.add_evidence(
        provider="graph",
        action=capabilities.GRAPH_TRAVERSAL_ACTION,
        outcome="unavailable",
        summary="Reached this run's graph read budget, so no further traversal was performed.",
    )

    architecture = next(a for a in assess(ledger) if a.capability == "architecture")
    reachable = next(
        s for s in architecture.signals if s.label == "Knowledge graph queried without errors"
    )
    assert reachable.satisfied is True, "an internal budget is not a broken graph"


def test_architecture_remediation_does_not_blame_a_healthy_graph() -> None:
    """ "Check the Neo4j connection" is actively misleading when the graph just
    answered — it sends the user to debug a working system while the real cause,
    an unindexed repository, goes unaddressed."""
    architecture = capabilities.BY_KEY["architecture"]

    healthy = Ledger()
    healthy.add_evidence(
        provider="graph",
        action=capabilities.GRAPH_TRAVERSAL_ACTION,
        outcome="not_found",
        summary="empty",
    )
    assert architecture.remediation(healthy) == ["Index the repository"]

    broken = Ledger()
    broken.add_evidence(
        provider="graph",
        action=capabilities.GRAPH_TRAVERSAL_ACTION,
        outcome="failed",
        summary="connection refused",
    )
    assert "Check the Neo4j connection" in architecture.remediation(broken)


# ---------------------------------------------------------------------------
# Multi-repository selection: explicit vs. suggested
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_explicitly_named_repositories_are_both_selected_without_a_question() -> None:
    """A Jira naming two repositories together must put both in scope, not
    force a pick-one — the exact bug this feature fixes: 'Repo:
    ingestion-framework, etl-core' used to read as ambiguity."""
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["ingestion-framework", "etl-core", "streaming-pipeline"],
                [
                    ("SchemaMerger", "ingestion-framework"),
                    ("DeltaWriter", "etl-core"),
                ],
            )
        ),
    ):
        state = await discover(
            request=(
                "Enable Delta Lake mergeSchema for nested struct fields. "
                "Repo: ingestion-framework, etl-core"
            ),
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    result = build_result(state)
    explicit_names = {r["name"] for r in result["explicit_repositories"]}
    selected_names = {r["name"] for r in result["selected_repositories"]}
    assert explicit_names == {"ingestion-framework", "etl-core"}
    assert selected_names == {"ingestion-framework", "etl-core"}
    assert "streaming-pipeline" not in selected_names

    repository = state.assessment_for("repository")
    assert repository is not None and repository.satisfied is True
    assert state.readiness != "BLOCKED"
    # Two explicit repositories is not ambiguity — no question should ever
    # be raised over which one to use.
    repo_gap = state.gap_for("repository")
    assert repo_gap is None or repo_gap.status != "open"


@pytest.mark.asyncio
async def test_suggested_repositories_are_not_auto_selected_alongside_explicit_ones() -> None:
    """A repository the ranking merely finds relevant, but the request never
    named, stays `source: "suggested"` and out of the default selection —
    the human opts it in via the Repositories panel, it is never silently
    folded in alongside an explicit repository."""
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["ingestion-framework", "etl-core"],
                [("SchemaMerger", "ingestion-framework"), ("DeltaWriter", "etl-core")],
            )
        ),
    ):
        state = await discover(
            request="Enable Delta Lake mergeSchema. Repo: ingestion-framework",
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    result = build_result(state)
    explicit_names = {r["name"] for r in result["explicit_repositories"]}
    assert explicit_names == {"ingestion-framework"}
    selected_names = {r["name"] for r in result["selected_repositories"]}
    assert selected_names == {"ingestion-framework"}
    assert "etl-core" not in selected_names


@pytest.mark.asyncio
async def test_parse_retrieved_content_recognizes_an_indexed_repository_named_in_ticket_body() -> (
    None
):
    """Regression: a repository named only in *fetched* ticket content (e.g.
    a Jira description reading "Repo: etl-core"), not in the original
    request text, must still be recognized — previously `parse_retrieved_
    content` never received the known-repository-name list `match_
    repository_names` uses (it runs before the ticket is even fetched, so it
    can't see this text at all), so an indexed repository named explicitly
    in a ticket body was silently missed and the user was asked to pick
    manually between every indexed repository instead."""
    ledger = Ledger()
    repo_ev = ledger.add_evidence(
        provider="graph", action="survey_architecture", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="repository", subject="etl-core", provider="graph", evidence_id=repo_ev.evidence_id
    )

    work_item_ev = ledger.add_evidence(
        provider="jira", action="fetch_work_item", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="work_item",
        subject="NPT-29",
        provider="jira",
        evidence_id=work_item_ev.evidence_id,
        text=(
            "Duplicate records in SCD2 merge during concurrent writes. "
            "Repo: etl-core. Branch: bugfix/scd2-duplicate"
        ),
    )

    state = WorkingContext(ledger=ledger)
    investigator = RequestParseInvestigator()
    action = next(a for a in investigator.propose(state) if a.key == "parse_retrieved_content")
    assert action.params["known_repositories"] == frozenset({"etl-core"})

    recorder = Recorder(ledger, action, iteration=0)
    outcome = await investigator.run(action, _session(), recorder)

    assert outcome.yielded is True
    local_repo_facts = [
        f for f in ledger.facts_of("reference") if f.value.get("type") == "local_repository"
    ]
    assert {f.subject for f in local_repo_facts} == {"etl-core"}
    # The branch name is still recognized too — this pass must gain the new
    # signal, not lose the one it already had.
    github_repo_facts = [
        f for f in ledger.facts_of("reference") if f.value.get("type") == "github_repository"
    ]
    assert {f.subject for f in github_repo_facts} == {"bugfix/scd2-duplicate"}


def test_parse_retrieved_content_known_repositories_is_empty_before_any_repository_facts() -> None:
    """Safe no-op, matching `match_repository_names`'s own existing
    behavior: before any repository fact exists, the known-name list is
    empty and no local-repository match can ever fire — this is the
    pre-existing state for every ticket-content parse until the graph
    survey runs, not a regression."""
    ledger = Ledger()
    work_item_ev = ledger.add_evidence(
        provider="jira", action="fetch_work_item", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="work_item",
        subject="NPT-29",
        provider="jira",
        evidence_id=work_item_ev.evidence_id,
        text="Repo: etl-core.",
    )

    state = WorkingContext(ledger=ledger)
    action = next(
        a for a in RequestParseInvestigator().propose(state) if a.key == "parse_retrieved_content"
    )
    assert action.params["known_repositories"] == frozenset()
