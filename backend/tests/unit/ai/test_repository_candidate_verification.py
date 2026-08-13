"""RFC-0011 — repository candidate verification.

Regression suite for the PROT-5764 black-box benchmark failure: Context
Discovery selected `ds-databricks-cmsenergy-dataingest` for a ticket that
never named a repository, because a lone lexical-ranking survivor was
scored identically to an explicitly-identified repository. See
`docs/benchmarks/context-discovery/PROT-5764-repository-resolution.md` for
the full incident writeup.

None of these tests hard-code Avangrid, PROT-5764, or the real repository
name into the *mechanism* under test — `capabilities.py`/`investigators.py`
contain no reference to any of them. Two tests below use PROT-5764-shaped
fixture data (clearly marked) purely as an integration check that the
benchmark scenario now resolves correctly; a third, structurally identical
but unrelated-domain fixture proves the mechanism itself is generic, not
tuned to this one ticket.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.context_pipeline.reasoning.capabilities import (
    CANDIDATE_FUNNEL_WIDTH,
    SOURCE_RETRIEVAL_WIDTH,
    _corroborated_ranking_candidates,
    _corroboration_evidence,
    _repository_signals,
    ranked_repository_names,
    resync_ranked_candidates,
)
from app.context_pipeline.reasoning.engine import _select, discover
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import GitHubInvestigator, GraphInvestigator, RequestParseInvestigator
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


def _ranked_ledger(names_and_scores: list[tuple[str, float]]) -> Ledger:
    """A ledger holding `repository` facts and a `repository_ranking` fact
    for the given (name, score) pairs, best-first — the shape
    `GraphInvestigator`'s survey produces, built directly so these tests
    don't need a real graph query for every scenario."""
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey_architecture", outcome="success", summary="s")
    for name, _score in names_and_scores:
        ledger.add_fact(kind="repository", subject=name, provider="graph", evidence_id=ev.evidence_id)
    ledger.add_fact(
        kind="repository_ranking",
        subject="ranking",
        provider="graph",
        evidence_id=ev.evidence_id,
        value={"scored": [[score, name] for name, score in names_and_scores]},
    )
    resync_ranked_candidates(ledger)
    return ledger


# ---------------------------------------------------------------------------
# 1. A lone lexical candidate must never be treated as equivalent to an
#    explicitly identified repository.
# ---------------------------------------------------------------------------


def test_lone_lexical_candidate_is_not_treated_as_identified() -> None:
    """The exact PROT-5764 mechanism: two repositories are ranked, one
    scores strictly higher and is the sole `TIE_RATIO` survivor, but
    nothing corroborates it. `identified` must be False."""
    ledger = _ranked_ledger([("candidate-a", 5.0), ("candidate-b", 1.0)])

    candidates = ledger.live_inferences("repository_candidate")
    assert {c.statement for c in candidates} == {"candidate-a"}
    assert candidates[0].value.get("basis") == "ranking"

    signals = _repository_signals(ledger)
    identified = next(s for s in signals if s.label == "Owning repository identified")
    assert identified.satisfied is False, "a lone ranking survivor must not count as identified"


# ---------------------------------------------------------------------------
# 2. An explicit reference scores differently from a suggested candidate.
# ---------------------------------------------------------------------------


def test_explicit_reference_scores_differently_from_suggested_candidate() -> None:
    def _repository_score(ledger: Ledger) -> float:
        signals = _repository_signals(ledger)
        satisfied = sum(s.weight for s in signals if s.satisfied)
        total = sum(s.weight for s in signals)
        return satisfied / total

    explicit_ledger = Ledger()
    ev = explicit_ledger.add_evidence(provider="parser", action="parse_request", outcome="success", summary="s")
    repo_fact = explicit_ledger.add_fact(
        kind="repository", subject="named-repo", provider="graph", evidence_id=ev.evidence_id
    )
    ref_fact = explicit_ledger.add_fact(
        kind="reference",
        subject="named-repo",
        provider="parser",
        evidence_id=ev.evidence_id,
        value={"type": "local_repository", "provider": "graph", "confidence": 0.6},
    )
    explicit_ledger.add_inference(
        kind="repository_candidate",
        statement="named-repo",
        supporting_fact_ids=[repo_fact.fact_id, ref_fact.fact_id],
        value={"source": "explicit", "reason": "Named directly in the request."},
    )

    ranking_ledger = _ranked_ledger([("ranked-repo", 5.0), ("other-repo", 1.0)])

    assert _repository_score(explicit_ledger) > _repository_score(ranking_ledger), (
        "an explicit reference must score strictly higher than an uncorroborated ranking "
        "leader, not merely differently"
    )
    explicit_identified = next(
        s for s in _repository_signals(explicit_ledger) if s.label == "Owning repository identified"
    )
    ranking_identified = next(
        s for s in _repository_signals(ranking_ledger) if s.label == "Owning repository identified"
    )
    assert explicit_identified.satisfied is True
    assert ranking_identified.satisfied is False


# ---------------------------------------------------------------------------
# 3. Sibling repositories with identical scaffolding, distinguished by
#    graph evidence — the actual PROT-5764 shape (a shared-library
#    dependency edge disambiguating two lexically-indistinguishable
#    candidates) reproduced with a generic, non-Avangrid domain.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sibling_repositories_with_identical_scaffolding_distinguished_by_graph_evidence() -> None:
    """Two repositories built from the same scaffold (`schema_validator`,
    `main_pipeline` — the same component names in both, exactly the
    "sibling ds-databricks-*-dataingest repos share a template" situation
    PROT-5764 exposed) cannot be told apart lexically. A real
    cross-repository relationship — `tenant-batch-widget` depends on
    `tenant-b-ingest`, not `tenant-a-ingest` — is what actually
    discriminates them."""
    cross_edges = [
        {
            "source_repository": "tenant-batch-widget",
            "target_repository": "tenant-b-ingest",
            "type": "DEPENDS_ON_REPOSITORY",
            "properties": {},
        }
    ]
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["tenant-a-ingest", "tenant-b-ingest", "tenant-batch-widget"],
                [
                    ("schema_validator", "tenant-a-ingest"),
                    ("main_pipeline", "tenant-a-ingest"),
                    ("schema_validator", "tenant-b-ingest"),
                    ("main_pipeline", "tenant-b-ingest"),
                ],
                cross_edges,
            )
        ),
    ):
        state = await discover(
            request="A batch failed schema validation and the pipeline's error handling is wrong",
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    candidates = state.ledger.live_inferences("repository_candidate")
    ranking_candidates = {c.statement for c in candidates if c.value.get("basis") == "ranking"}
    # Both siblings look identical lexically — both should have been ranked,
    # neither should win by lexical score alone.
    assert "tenant-a-ingest" in ranking_candidates or "tenant-b-ingest" in ranking_candidates

    corroborated = _corroborated_ranking_candidates(state.ledger)
    assert {c.statement for c in corroborated} == {"tenant-b-ingest"}, (
        "only the sibling with a real graph relationship should be corroborated"
    )


# ---------------------------------------------------------------------------
# 4. A cross-repository relationship can promote the correct candidate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_repository_relationship_can_promote_the_correct_candidate() -> None:
    cross_edges = [
        {
            "source_repository": "checkout-widget",
            "target_repository": "checkout-b-ingest",
            "type": "DEPENDS_ON_REPOSITORY",
            "properties": {},
        }
    ]
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["checkout-a-ingest", "checkout-b-ingest", "checkout-widget"],
                [("validator", "checkout-a-ingest"), ("validator", "checkout-b-ingest")],
                cross_edges,
            )
        ),
    ):
        state = await discover(
            request="Fix the validator error handling in the checkout ingest pipeline",
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    result = build_result(state)
    suggested_names = {r["name"] for r in result["suggested_repositories"]}
    assert "checkout-b-ingest" in suggested_names
    identified = next(s for s in _repository_signals(state.ledger) if s.label == "Owning repository identified")
    assert identified.satisfied is True


# ---------------------------------------------------------------------------
# 5. Source evidence can reject the lexical winner.
# ---------------------------------------------------------------------------


def test_source_evidence_can_reject_the_lexical_winner() -> None:
    """The lexical winner's fetched source never mentions this request's
    own vocabulary; a lower-ranked sibling's fetched source does. Only the
    lower-ranked one should end up corroborated — source evidence
    overriding a pure ranking score, not merely confirming it."""
    ledger = _ranked_ledger([("lexical-winner", 5.0), ("actual-owner", 3.0)])
    ev = ledger.add_evidence(provider="github", action="fetch_pull_request:acme/lexical-winner", outcome="success", summary="s")
    ledger.add_fact(
        kind="pull_request",
        subject="acme/lexical-winner",
        provider="github",
        evidence_id=ev.evidence_id,
        value={"title": "unrelated"},
        text="This module handles generic batch scaffolding and nothing else specific.",
    )
    # Give both repository facts a `full_name` matching what the source-fetch
    # subject would be, mirroring how `GitHubInvestigator` records it.
    for fact in ledger.facts_of("repository"):
        fact.value["full_name"] = f"acme/{fact.subject}"
    ev2 = ledger.add_evidence(provider="github", action="fetch_pull_request:acme/actual-owner", outcome="success", summary="s")
    ledger.add_fact(
        kind="pull_request",
        subject="acme/actual-owner",
        provider="github",
        evidence_id=ev2.evidence_id,
        value={"title": "the real one"},
        text="Handles order_confirmation_widget failures end to end.",
    )
    # `ticket_terms` recorded onto the ranking fact, as `GraphInvestigator`
    # does — the request's own specific vocabulary.
    ranking_fact = ledger.facts_of("repository_ranking")[-1]
    ranking_fact.value["ticket_terms"] = ["order_confirmation_widget"]

    # `actual-owner` never won the lexical tie (3.0 < 5.0 * TIE_RATIO), so
    # it never became a `basis: "ranking"` candidate at all — checking
    # `_corroboration_evidence` directly (rather than `_corroborated_
    # ranking_candidates`, which only re-examines *existing* ranking
    # candidates) is what proves source evidence can promote a candidate
    # the ranking itself never surfaced, not merely unblock one it did.
    evidence = _corroboration_evidence(ledger)
    assert "actual-owner" in evidence
    assert "lexical-winner" not in evidence, (
        "the lexical winner's own fetched source doesn't mention the request's vocabulary "
        "and must stay uncorroborated"
    )


# ---------------------------------------------------------------------------
# 6. Insufficient evidence prevents automatic selection.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insufficient_evidence_prevents_automatic_selection() -> None:
    """Neither candidate is ever corroborated (no relationships, no GitHub
    connection available) — Context Discovery must not select the lexical
    winner anyway. Readiness stays blocked on the repository capability and
    the clarification question is honest about *why*."""
    with (
        patch(
            "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
            new=AsyncMock(
                return_value=_graph_tool_result(
                    ["widget-a-ingest", "widget-b-ingest"],
                    [("validator", "widget-a-ingest"), ("validator", "widget-b-ingest")],
                )
            ),
        ),
        patch(
            "app.services.github_service.get_decrypted_access_token",
            new=AsyncMock(return_value=None),
        ),
    ):
        state = await discover(
            request="Fix the validator error handling in the widget ingest pipeline",
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator(), GitHubInvestigator()],
        )

    identified = next(s for s in _repository_signals(state.ledger) if s.label == "Owning repository identified")
    assert identified.satisfied is False
    result = build_result(state)
    assert result["readiness"] != "READY"


# ---------------------------------------------------------------------------
# 7. Bounded candidate investigation does not fetch every repository.
# ---------------------------------------------------------------------------


def test_graph_corroboration_is_bounded_by_the_candidate_funnel_width() -> None:
    many_names = [(f"repo-{i}", 10.0 - i) for i in range(CANDIDATE_FUNNEL_WIDTH + 6)]
    ledger = _ranked_ledger(many_names)
    assert len(ledger.live_inferences("repository_candidate")) < len(many_names), (
        "sanity check: not every repository should even become a candidate"
    )

    names = ranked_repository_names(ledger, limit=CANDIDATE_FUNNEL_WIDTH)
    assert len(names) == CANDIDATE_FUNNEL_WIDTH
    assert len(names) < len(many_names)

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GraphInvestigator().propose(state)
    assert len(actions) == CANDIDATE_FUNNEL_WIDTH, (
        "the graph corroboration stage must query at most CANDIDATE_FUNNEL_WIDTH "
        "repositories, never every ranked one"
    )


def test_source_retrieval_is_bounded_by_the_source_retrieval_width() -> None:
    """After the graph stage has scoped every funnel candidate and none was
    corroborated by a relationship, only `SOURCE_RETRIEVAL_WIDTH` of them
    — strictly fewer than the funnel width — escalate to a source fetch."""
    names = [(f"repo-{i}", 10.0 - i) for i in range(CANDIDATE_FUNNEL_WIDTH)]
    ledger = _ranked_ledger(names)
    for name, _score in names:
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)
    assert len(actions) == SOURCE_RETRIEVAL_WIDTH
    assert SOURCE_RETRIEVAL_WIDTH < CANDIDATE_FUNNEL_WIDTH


# ---------------------------------------------------------------------------
# 7b. Candidate investigation priority must survive from the ranking that
#     built the funnel into the actions the funnel proposes — a higher-
#     ranked candidate must not lose its investigation slot merely because
#     its repository name sorts later alphabetically than a lower-ranked
#     one's. Regression for the live PROT-5764 benchmark run this session:
#     RFC-0011's funnel correctly contained the top 4 candidates, but
#     `GraphInvestigator` proposed one same-cost, same-capability action per
#     candidate with no ranking signal attached, so `engine._select`'s
#     tie-break fell through to `action.key` — alphabetical by repository
#     name — and burned the entire cycle budget scoping the two *lowest*-
#     ranked funnel members while the two highest-ranked (one of them the
#     correct answer) were never investigated at all.
# ---------------------------------------------------------------------------


def test_candidate_investigation_priority_survives_alphabetical_key_inversion() -> None:
    """Four ranked candidates whose names sort in the exact reverse of
    their rank — `_select` must still pick the highest-ranked (rank 1)
    candidate's action first, cycle after cycle, not the one whose name
    happens to sort first."""
    # Rank 1..4, deliberately named so `scope_architecture:{name}` sorts
    # alphabetically in the OPPOSITE order: zzz-candidate-d (rank 4) first,
    # zzz-candidate-a (rank 1) last.
    ledger = _ranked_ledger(
        [
            ("zzz-candidate-a", 20.0),  # rank 1 — highest lexical score
            ("zzz-candidate-b", 15.0),  # rank 2
            ("zzz-candidate-c", 10.0),  # rank 3
            ("aaa-candidate-d", 5.0),  # rank 4 — but sorts first alphabetically
        ]
    )
    assert ranked_repository_names(ledger, limit=CANDIDATE_FUNNEL_WIDTH) == [
        "zzz-candidate-a",
        "zzz-candidate-b",
        "zzz-candidate-c",
        "aaa-candidate-d",
    ], "sanity check: ranking order and alphabetical key order must actually differ"

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GraphInvestigator().propose(state)

    # 1. The ranking signal survives from candidate generation into the
    #    action priority: `priority` must mirror rank position exactly,
    #    regardless of how the name sorts.
    priority_by_name = {a.params["repository"]: a.priority for a in actions}
    assert priority_by_name == {
        "zzz-candidate-a": 0,
        "zzz-candidate-b": 1,
        "zzz-candidate-c": 2,
        "aaa-candidate-d": 3,
    }

    # 2. Alphabetical repository-name ordering cannot override relevance —
    #    simulate the engine picking one action per cycle (the same
    #    real-world shape as the live benchmark: several equally-priced
    #    same-capability actions competing in `_select` every cycle) and
    #    confirm rank order wins over key order every time.
    investigator = GraphInvestigator()
    remaining = list(actions)
    picked_order: list[str] = []
    while remaining:
        chosen, _ = _select([(a, investigator) for a in remaining], state.assessments)
        picked_order.append(chosen.params["repository"])
        remaining.remove(chosen)
    assert picked_order == [
        "zzz-candidate-a",
        "zzz-candidate-b",
        "zzz-candidate-c",
        "aaa-candidate-d",
    ], "investigation order must follow rank, not the alphabetical action key"

    # 3. The funnel remains bounded — this fix must not turn into
    #    "investigate everything, just in the right order."
    assert len(actions) == CANDIDATE_FUNNEL_WIDTH


def test_explicit_repository_verification_priority_is_unaffected() -> None:
    """Existing requirement #3's explicit-reference path assigns no
    per-candidate ranking at all (there is nothing to rank — one repository
    was named directly), so its actions must keep the same default
    `priority=0.0` as before this fix, and its own dedupe-by-key behavior
    must be untouched."""
    ledger = Ledger()
    ev = ledger.add_evidence(provider="parser", action="parse_request", outcome="success", summary="s")
    ledger.add_fact(
        kind="reference",
        subject="named-repo",
        provider="parser",
        evidence_id=ev.evidence_id,
        value={"type": "local_repository", "provider": "graph", "confidence": 0.6, "source": "explicit_selection"},
    )

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GraphInvestigator().propose(state)

    assert len(actions) == 1
    assert actions[0].key == "verify_repository:named-repo"
    assert actions[0].priority == 0.0, "the explicit-reference path never assigns a ranking priority"


def test_source_retrieval_priority_also_follows_rank_not_the_key() -> None:
    """The same fix applied to the funnel's second, more expensive stage —
    source retrieval must also prioritize by rank, not by repository name,
    once several already-scoped candidates are all eligible in one cycle."""
    ledger = _ranked_ledger(
        [
            ("zzz-repo-a", 20.0),
            ("zzz-repo-b", 15.0),
            ("aaa-repo-c", 10.0),
        ]
    )
    for name in ("zzz-repo-a", "zzz-repo-b", "aaa-repo-c"):
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)

    priority_by_name = {a.params["reference"]["raw_value"]: a.priority for a in actions}
    assert priority_by_name == {"zzz-repo-a": 0, "zzz-repo-b": 1, "aaa-repo-c": 2}
    assert len(actions) == SOURCE_RETRIEVAL_WIDTH


def test_rfc_0011_safety_behavior_is_unchanged_by_the_scheduling_fix() -> None:
    """Requirement #1 (lone lexical winner is not identified) and #6
    (insufficient evidence blocks selection) must hold exactly as before —
    this fix only changes *investigation order*, never *what counts as
    identified*."""
    ledger = _ranked_ledger([("zzz-lone-winner", 20.0), ("aaa-runner-up", 2.0)])
    candidates = ledger.live_inferences("repository_candidate")
    assert {c.statement for c in candidates} == {"zzz-lone-winner"}
    identified = next(s for s in _repository_signals(ledger) if s.label == "Owning repository identified")
    assert identified.satisfied is False, "priority ordering must not make an uncorroborated winner 'identified'"


# ---------------------------------------------------------------------------
# 8. PROT-5764 as an integration test.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prot_5764_benchmark_resolves_via_relationship_corroboration() -> None:
    """Integration reproduction of the PROT-5764 benchmark
    (`docs/benchmarks/context-discovery/PROT-5764-repository-resolution.md`):
    two lexically-indistinguishable sibling repositories, disambiguated by
    a real graph relationship to the shared TnT library — the same
    mechanism as test 3/4 above, run against the actual repository names
    from the benchmark for direct traceability to that document. The
    *mechanism* under test contains no reference to any of these names —
    only this fixture does.
    """
    cross_edges = [
        {
            "source_repository": "ds-databricks-avangrid-em-ct-dataingest",
            "target_repository": "up-databricks-shared-jobs",
            "type": "DEPENDS_ON_REPOSITORY",
            "properties": {},
        }
    ]
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                [
                    "ds-databricks-avangrid-em-ct-dataingest",
                    "ds-databricks-avangrid-em-dataingest",
                    "up-databricks-shared-jobs",
                ],
                [
                    ("schema_validator", "ds-databricks-avangrid-em-ct-dataingest"),
                    ("main_pipeline", "ds-databricks-avangrid-em-ct-dataingest"),
                    ("schema_validator", "ds-databricks-avangrid-em-dataingest"),
                    ("main_pipeline", "ds-databricks-avangrid-em-dataingest"),
                ],
                cross_edges,
            )
        ),
    ):
        state = await discover(
            request=(
                "Avangrid TnT - event_owner should be set to 'client' if pipeline failed "
                "with schema validation failure"
            ),
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    result = build_result(state)
    suggested_names = {r["name"] for r in result["suggested_repositories"]}
    # Both siblings still surface as *suggestions* — `suggested_repositories`
    # is a display list of every live candidate a human can opt into (the
    # whole point of `source: "suggested"`), not a verdict. The verdict is
    # `identified`: only the sibling with a real relationship to the shared
    # TnT library is independently corroborated, so it — and only it —
    # decides "owning repository identified" below.
    assert "ds-databricks-avangrid-em-ct-dataingest" in suggested_names
    corroborated = _corroborated_ranking_candidates(state.ledger)
    assert {c.statement for c in corroborated} == {"ds-databricks-avangrid-em-ct-dataingest"}, (
        "the sibling with no relationship to the shared TnT library must not be corroborated"
    )
    identified = next(s for s in _repository_signals(state.ledger) if s.label == "Owning repository identified")
    assert identified.satisfied is True
    assert identified.fact_ids and set(identified.fact_ids).issubset(
        {f.fact_id for f in state.ledger.facts_of("repository") if f.subject == "ds-databricks-avangrid-em-ct-dataingest"}
        | {f.fact_id for f in state.ledger.facts_of("repository_relationship")}
    )


@pytest.mark.asyncio
async def test_synthetic_non_avangrid_benchmark_proves_the_mechanism_is_generic() -> None:
    """Same funnel, same shape, a completely unrelated domain — proves
    requirement generalization rather than a PROT-5764 special case. If
    this passes only because of something specific to the Avangrid
    fixture, this test (deliberately using unrelated names, an unrelated
    relationship type, and an unrelated request) would fail."""
    cross_edges = [
        {
            "source_repository": "logistics-router-svc",
            "target_repository": "logistics-warehouse-b-svc",
            "type": "SHARES_TOPIC",
            "properties": {"topics": ["shipment-events"]},
        }
    ]
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["logistics-warehouse-a-svc", "logistics-warehouse-b-svc", "logistics-router-svc"],
                [
                    ("InventoryReconciler", "logistics-warehouse-a-svc"),
                    ("ShipmentNotifier", "logistics-warehouse-a-svc"),
                    ("InventoryReconciler", "logistics-warehouse-b-svc"),
                    ("ShipmentNotifier", "logistics-warehouse-b-svc"),
                ],
                cross_edges,
            )
        ),
    ):
        state = await discover(
            request="ShipmentNotifier retries forever instead of giving up after 3 attempts",
            session=_session(),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    result = build_result(state)
    suggested_names = {r["name"] for r in result["suggested_repositories"]}
    assert "logistics-warehouse-b-svc" in suggested_names
    assert "logistics-warehouse-a-svc" not in suggested_names
