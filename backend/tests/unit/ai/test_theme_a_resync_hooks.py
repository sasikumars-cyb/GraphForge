"""ADR 0010 §7 P0 (Theme A) regression tests — moving all `repository_
candidate` production into deterministic ledger resync hooks.

Covers:
- the original review's exact failing scenario (suggested repositories never
  populating once explicit candidates satisfied the capability)
- invariant I1 (`Recorder` cannot write/withdraw an `Inference`)
- invariant I3 (every resync hook is pure, idempotent, order-independent)
- the `resume()` sequencing fix (`_settle_claims` -> resync -> assessments)
"""

from __future__ import annotations

import inspect
from itertools import permutations
from unittest.mock import AsyncMock, patch

import pytest

from app.context_pipeline.reasoning.capabilities import LEDGER_RESYNC_HOOKS, _verify_repository
from app.context_pipeline.reasoning.engine import discover, resume
from app.context_pipeline.reasoning.investigation import Recorder, SessionContext
from app.context_pipeline.reasoning.investigators import (
    GraphInvestigator,
    RequestParseInvestigator,
)
from app.context_pipeline.reasoning.ledger import Ledger
from app.context_pipeline.reasoning.memory import WorkingContext
from app.context_pipeline.reasoning.projection import build_result
from app.tools.interfaces import ToolResult


def _session() -> SessionContext:
    return SessionContext(db=None, user_id=None)  # type: ignore[arg-type]


def _graph_tool_result(
    repositories: list[str],
    components: list[tuple[str, str]],
    cross_edges: list[dict] | None = None,
) -> ToolResult:
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": n} for n in repositories],
            "components": [{"name": c, "repository": r, "type": "service"} for c, r in components],
            "kafka_topics": [],
            "context_text": "x",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "x",
            "_traverse_summary": "x",
            "cross_repository_edges": cross_edges or [],
        },
        summary="q",
    )


# ---------------------------------------------------------------------------
# The original bug: suggested repositories never populated once explicit
# candidates satisfied the capability, because relationship-based promotion
# only ever ran inside `GraphInvestigator.run()`, which stopped being
# proposed once nothing was unmet.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggested_repositories_populate_for_the_original_review_scenario() -> None:
    """Exact reproduction of the original review's failing case: a Jira
    naming two repositories together, with a real cross-repository edge
    (SHARES_TOPIC) from one of them to a third, unnamed repository. Before
    this fix, `suggested_repositories` was always empty here."""
    cross_edges = [
        {
            "source_repository": "ingestion-framework",
            "target_repository": "streaming-pipeline",
            "type": "SHARES_TOPIC",
            "properties": {"topics": ["orders-created"]},
        }
    ]
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["ingestion-framework", "etl-core", "streaming-pipeline"],
                [("SchemaMerger", "ingestion-framework"), ("DeltaWriter", "etl-core")],
                cross_edges,
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
    suggested_names = {r["name"] for r in result["suggested_repositories"]}
    assert explicit_names == {"ingestion-framework", "etl-core"}
    assert suggested_names == {"streaming-pipeline"}, (
        "a real cross-repository relationship from an explicit repository must surface as "
        "a suggested candidate, regardless of investigator scheduling"
    )
    assert result["readiness"] == "READY"


# ---------------------------------------------------------------------------
# Invariant I1 — investigators observe, they never interpret
# ---------------------------------------------------------------------------


def test_recorder_has_no_inference_or_withdraw_methods() -> None:
    """Structural enforcement, not documentation: an investigator's `run()`
    receives only a `Recorder`, and `Recorder` must have no method capable of
    writing or withdrawing an `Inference` at all (ADR 0010, invariant I1)."""
    assert not hasattr(Recorder, "inference"), "Recorder must not expose inference()"
    assert not hasattr(Recorder, "withdraw"), "Recorder must not expose withdraw()"


# ---------------------------------------------------------------------------
# Invariant I3 — pure, idempotent, order-independent resync hooks
# ---------------------------------------------------------------------------


def test_every_resync_hook_has_a_ledger_only_signature() -> None:
    """Enforces the no-I/O requirement structurally: a hook whose signature
    accepts anything beyond a single `Ledger` couldn't be called by
    `engine._resync` at all, and couldn't plausibly reach a session/tool
    registry to perform I/O."""
    assert len(LEDGER_RESYNC_HOOKS) == 4
    for hook in LEDGER_RESYNC_HOOKS:
        params = list(inspect.signature(hook).parameters.values())
        assert len(params) == 1, f"{hook.__name__} must take exactly one argument"


def _ledger_with_two_explicit_and_one_relationship() -> Ledger:
    """A ledger holding, simultaneously: two repositories named directly in
    the request (explicit via reference), a corroborated human claim for a
    third (explicit via verified claim), a tied ranking fact naming a fourth
    repository the ranking alone would suggest, and a relationship fact from
    one explicit repository to a fifth. Built directly (no investigator run)
    so hook order can be permuted without re-running discovery each time.
    """
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")

    names = ["repo-a", "repo-b", "repo-c", "repo-d", "repo-e"]
    facts = {
        name: ledger.add_fact(
            kind="repository", subject=name, provider="graph", evidence_id=ev.evidence_id
        )
        for name in names
    }

    ref_ev = ledger.add_evidence(
        provider="request_parser", action="parse", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="reference",
        subject="repo-a",
        provider="request_parser",
        evidence_id=ref_ev.evidence_id,
        value={"type": "local_repository"},
    )

    claim_ev = ledger.add_evidence(provider="user", action="answer", outcome="success", summary="s")
    ledger.add_fact(
        kind="user_statement",
        subject="repo-b",
        provider="user",
        evidence_id=claim_ev.evidence_id,
        value={"question_id": "q1", "capability": "repository"},
        verified=True,
    )

    ledger.add_fact(
        kind="repository_ranking",
        subject="ranking",
        provider="graph",
        evidence_id=ev.evidence_id,
        value={"scored": [[1.0, "repo-c"], [1.0, "repo-d"]]},
    )

    rel_ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")
    ledger.add_fact(
        kind="repository_relationship",
        subject="repo-e",
        provider="graph",
        evidence_id=rel_ev.evidence_id,
        value={"via": "SHARES_TOPIC", "source_repository": "repo-a", "reason": "shares a topic"},
    )
    del facts
    return ledger


def test_resync_hooks_are_order_independent() -> None:
    """Running the four hooks in every possible order must produce the same
    final live-candidate set with the same source tags (ADR 0010, invariant
    I3) — because `resync_ranked_candidates`/`resync_relationship_
    candidates` determine "is this explicit" from facts
    (`_is_explicit_repository`), never from what an earlier hook in the same
    pass happened to have already written."""
    results = []
    for ordering in permutations(LEDGER_RESYNC_HOOKS):
        ledger = _ledger_with_two_explicit_and_one_relationship()
        for hook in ordering:
            hook(ledger)
        live = {
            i.statement: i.value.get("source")
            for i in ledger.live_inferences("repository_candidate")
        }
        results.append(live)

    first = results[0]
    for other in results[1:]:
        assert other == first, "resync hook order must never change the final candidate set"

    assert first["repo-a"] == "explicit"
    assert first["repo-b"] == "explicit"
    assert first["repo-e"] == "suggested"
    # repo-c/repo-d were only ever a ranking's *guess* — once repo-a/repo-b
    # are explicit, that guess about unrelated repositories is superseded,
    # not merely one candidate among several (`resync_ranked_candidates`
    # suppresses ranking-based suggestion entirely once any repository in
    # the request is explicit). Neither appears at all.
    assert "repo-c" not in first
    assert "repo-d" not in first


def test_resync_hooks_are_idempotent() -> None:
    """Running the full hook set twice (with a fresh withdraw between, as
    `engine._resync` always does) must not produce duplicate live inferences
    for the same repository."""
    ledger = _ledger_with_two_explicit_and_one_relationship()
    for _ in range(2):
        ledger.withdraw_inferences("repository_candidate")
        for hook in LEDGER_RESYNC_HOOKS:
            hook(ledger)

    live = ledger.live_inferences("repository_candidate")
    names = [i.statement for i in live]
    assert len(names) == len(set(names)), f"duplicate live candidates: {names}"


def test_resync_hooks_never_touch_the_ledger_when_no_repository_facts_exist() -> None:
    """Every hook must be a safe no-op on a ledger with nothing to interpret
    — this is what makes them safe to call unconditionally every cycle."""
    ledger = Ledger()
    for hook in LEDGER_RESYNC_HOOKS:
        hook(ledger)
    assert ledger.inferences == []


# ---------------------------------------------------------------------------
# `_verify_repository` checks the underlying fact, not a derived inference
# ---------------------------------------------------------------------------


def test_verify_repository_checks_the_fact_not_a_derived_inference() -> None:
    """A claim must corroborate the instant a `repository` fact exists for
    it, even with zero `repository_candidate` inferences live — otherwise
    `_settle_claims` (which calls this before any resync pass reflects the
    verification) could never mark anything verified at all (ADR 0010 §7,
    item 1)."""
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="verify", outcome="success", summary="s")
    ledger.add_fact(
        kind="repository", subject="billing-service", provider="graph", evidence_id=ev.evidence_id
    )

    assert ledger.live_inferences("repository_candidate") == []
    assert _verify_repository(ledger, "billing-service") is True
    assert _verify_repository(ledger, "nonexistent-service") is False


# ---------------------------------------------------------------------------
# `resume()` sequencing: resync must run again after `_settle_claims`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_reflects_a_verified_claim_as_an_explicit_candidate_immediately() -> None:
    """A claim verified inside `resume()` must be visible as a live,
    `source: "explicit"` candidate in the very `WorkingContext` `resume()`
    returns — not only on some later call — which requires resyncing again
    after `_settle_claims` sets `Fact.verified = True` (ADR 0010 §7, item 1:
    the gap round 1's design had)."""

    class Verifier:
        name = "graph"

        def propose(self, s: WorkingContext) -> list:
            for gap in s.gaps:
                if gap.status == "claimed" and gap.user_claim:
                    key = f"verify:{gap.user_claim}"
                    if not s.ledger.attempted(self.name, key):
                        from app.context_pipeline.reasoning.investigation import InvestigationAction

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
            from app.context_pipeline.reasoning.investigation import InvestigationOutcome

            claim = action.params["claim"]
            recorder.evidence("success", f"Confirmed {claim} is indexed.")
            return InvestigationOutcome(observation=f"Confirmed {claim}.", yielded=True)

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
            request="Add retry logic",
            session=_session(),
            investigators=[GraphInvestigator()],
        )

    resumed = await resume(
        state=state,
        question_id="gap_repository",
        answer="billing-service",
        session=_session(),
        investigators=[Verifier()],
    )

    live = {
        i.statement: i.value.get("source")
        for i in resumed.ledger.live_inferences("repository_candidate")
    }
    assert live.get("billing-service") == "explicit", (
        "a claim verified inside resume() must already be an explicit candidate in the "
        "WorkingContext resume() itself returns"
    )
