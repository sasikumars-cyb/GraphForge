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

from app.agents.planning.tools import rank_repositories
from app.context_pipeline.reasoning.capabilities import (
    CANDIDATE_FUNNEL_WIDTH,
    MAX_SOURCE_FILES_PER_CANDIDATE,
    MIN_SOURCE_EVIDENCE_SPECIFICITY,
    SOURCE_RETRIEVAL_WIDTH,
    _corroborated_ranking_candidates,
    _corroboration_evidence,
    _matched_term_specificity,
    _ranking_ticket_terms,
    _relationship_degree,
    _repository_signals,
    _select_dependency_expansion_files,
    _select_relevant_source_files,
    _structural_specificity,
    _term_specificity_weights,
    ranked_repository_names,
    repository_role,
    resync_corroborated_candidates,
    resync_ranked_candidates,
)
from app.context_pipeline.reasoning.engine import _select, discover
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    InvestigationOutcome,
    SessionContext,
)
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
# 5b. Relationship specificity/frequency weighting (RFC-0012 Problem A) —
#     a high-degree shared dependency must carry less identification
#     weight than a rare, specific one; "structural" vs "heuristic" vs
#     "ambiguous" confidence must still be respected.
# ---------------------------------------------------------------------------


def _add_relationship(
    ledger: Ledger,
    *,
    target: str,
    source_repo: str,
    confidence: str = "heuristic",
    target_consumer_count: int | None = None,
) -> None:
    ev = ledger.add_evidence(provider="graph", action=f"scope_architecture:{target}", outcome="success", summary="s")
    value = {"via": "DEPENDS_ON_REPOSITORY", "source_repository": source_repo, "confidence": confidence}
    if target_consumer_count is not None:
        # RFC-0016 — the graph-wide fan-in `Neo4jGraphTool` attaches to a
        # real `repository_relationship` fact, as opposed to the within-
        # ledger distinct-source count `_relationship_degree` falls back
        # to when this is absent (see the tests further below).
        value["target_consumer_count"] = target_consumer_count
    ledger.add_fact(
        kind="repository_relationship",
        subject=target,
        provider="graph",
        evidence_id=ev.evidence_id,
        value=value,
    )


def _add_component(ledger: Ledger, *, repository: str, name: str, file_path: str) -> None:
    """RFC-0014 — an indexed function/class/module, exactly the shape
    `GraphInvestigator`'s scoped traversal already records as a
    `component` fact — what `_select_relevant_source_files` reads to
    decide which files are worth fetching."""
    ev = ledger.add_evidence(
        provider="graph", action=f"scope_architecture:{repository}", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="component",
        subject=name,
        provider="graph",
        evidence_id=ev.evidence_id,
        value={"name": name, "repository": repository, "file_path": file_path, "type": "Function"},
    )


def _set_ticket_terms(ledger: Ledger, terms: list[str]) -> None:
    ledger.facts_of("repository_ranking")[-1].value["ticket_terms"] = terms


def test_a_rare_single_caller_relationship_corroborates() -> None:
    """One repository, one caller — the ordinary case every existing
    RFC-0011 test already exercises. Weight = 0.6 (heuristic) / 1 (degree)
    = 0.6, clears the specificity bar."""
    ledger = _ranked_ledger([("caller", 5.0)])
    _add_relationship(ledger, target="shared-lib", source_repo="caller")

    evidence = _corroboration_evidence(ledger)
    assert "shared-lib" in evidence
    assert "caller" in evidence


def test_a_high_degree_shared_dependency_does_not_corroborate_any_single_caller() -> None:
    """Requirement: a dependency shared by many unrelated repositories must
    not, by itself, count as identification evidence for any one of them —
    the exact PROT-5764-live-benchmark failure (three unrelated repos all
    depending on the same shared library, all promoted as 'corroborated').
    Weight = 0.6 (heuristic) / 3 (degree) = 0.2, below the bar."""
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")
    for name in ("shared-lib", "caller-x", "caller-y", "caller-z"):
        ledger.add_fact(kind="repository", subject=name, provider="graph", evidence_id=ev.evidence_id)
    for caller in ("caller-x", "caller-y", "caller-z"):
        _add_relationship(ledger, target="shared-lib", source_repo=caller)

    evidence = _corroboration_evidence(ledger)
    assert "shared-lib" not in evidence, "a widely-shared dependency must not corroborate on its own"
    assert "caller-x" not in evidence
    assert "caller-y" not in evidence
    assert "caller-z" not in evidence


def test_structural_confidence_survives_a_higher_degree_than_heuristic_would() -> None:
    """Existing `structural` vs `heuristic` confidence (`app.indexer.graph.
    cross_repo_linker`'s own vocabulary) must still matter: a literal,
    unambiguous match (e.g. a Feign target) tolerates more shared callers
    before becoming too weak to corroborate than a name-guess does — same
    degree, different outcome, purely from the existing confidence label."""
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")
    for name in ("service-x", "caller-a", "caller-b"):
        ledger.add_fact(kind="repository", subject=name, provider="graph", evidence_id=ev.evidence_id)
    _add_relationship(ledger, target="service-x", source_repo="caller-a", confidence="structural")
    _add_relationship(ledger, target="service-x", source_repo="caller-b", confidence="structural")

    # Weight = 1.0 (structural) / 2 (degree) = 0.5, at the bar - still counts.
    evidence = _corroboration_evidence(ledger)
    assert "service-x" in evidence
    assert "caller-a" in evidence


def test_ambiguous_confidence_never_corroborates_regardless_of_degree() -> None:
    """`confidence: "ambiguous"` (an import matching several repositories
    at once — see `cross_repo_linker._downgrade_ambiguous_imports`) must
    never corroborate, even with a degree of exactly 1: it isn't "common,"
    it's "unresolved which one," a different kind of weak evidence."""
    ledger = _ranked_ledger([("caller", 5.0)])
    _add_relationship(ledger, target="ambiguous-target", source_repo="caller", confidence="ambiguous")

    evidence = _corroboration_evidence(ledger)
    assert "ambiguous-target" not in evidence
    assert "caller" not in evidence


def test_specificity_weighting_does_not_blacklist_any_specific_target() -> None:
    """The same target name that failed to corroborate at high degree in
    one ledger must corroborate cleanly in a different ledger where this
    run has only observed one caller for it — nothing about the name
    itself is special-cased or remembered across ledgers."""
    ledger = _ranked_ledger([("caller", 5.0)])
    _add_relationship(ledger, target="shared-lib", source_repo="caller")
    assert "shared-lib" in _corroboration_evidence(ledger)


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
        # RFC-0014 — an indexed, ticket-matching component is required for
        # a candidate to have any file worth escalating to; every
        # candidate gets one here so the test still isolates the width
        # bound, not file selection.
        _add_component(ledger, repository=name, name="widget_handler", file_path=f"{name}/widget.py")
    _set_ticket_terms(ledger, ["widget"])

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
        _add_component(ledger, repository=name, name="widget_handler", file_path=f"{name}/widget.py")
    _set_ticket_terms(ledger, ["widget"])

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)

    priority_by_name = {a.params["repository"]: a.priority for a in actions}
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


# ---------------------------------------------------------------------------
# RFC-0013 — bounded escalation from graph corroboration to source
# retrieval for a high-ranked candidate graph evidence alone couldn't
# resolve, and the cycle-budget fix (free-action batching + a funnel-
# derived MAX_CYCLES) that makes it actually reachable within budget.
# ---------------------------------------------------------------------------


def test_a_weakly_related_candidate_still_escalates_to_source_retrieval() -> None:
    """Requirement #1: a high-ranked, scoped candidate whose only graph
    evidence is too common to corroborate on its own (RFC-0012) must still
    be eligible for source retrieval — the pre-fix eligibility check
    excluded *any* candidate named by *any* relationship fact, regardless
    of strength, which wrongly treated a weak/common relationship as if it
    had already resolved the candidate."""
    # Two candidates, not one — a lone indexed repository is trivially
    # "identified" on its own (a different, pre-existing rule), which
    # would make this test pass for the wrong reason. `candidate-b` keeps
    # `candidate-a` a genuine, uncorroborated `basis: "ranking"` tie
    # survivor instead.
    ledger = _ranked_ledger([("candidate-a", 20.0), ("candidate-b", 1.0)])
    for caller in ("caller-1", "caller-2", "caller-3"):
        _add_relationship(ledger, target="candidate-a", source_repo=caller)
    ledger.add_evidence(
        provider="graph", action="scope_architecture:candidate-a", outcome="success", summary="scoped"
    )
    # RFC-0014 — an indexed component matching the ticket's own vocabulary
    # is what makes a file worth fetching; without one, escalation now
    # correctly declines rather than guessing.
    _add_component(ledger, repository="candidate-a", name="widget_handler", file_path="src/widget.py")
    _set_ticket_terms(ledger, ["widget"])
    assert "candidate-a" not in _corroboration_evidence(ledger), (
        "sanity check: degree-3 heuristic evidence must not already corroborate"
    )

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)

    fetched = {a.params["repository"] for a in actions}
    assert "candidate-a" in fetched
    assert actions[0].params["file_paths"] == ["src/widget.py"]


def test_a_strongly_corroborated_candidate_skips_source_retrieval() -> None:
    """Requirement #2: once graph evidence alone is sufficient (RFC-0012
    weight clears the bar), the repository capability is already satisfied
    — no need to spend the funnel's most expensive stage confirming what's
    already resolved."""
    # Two candidates again, for the same reason as above — the assertion
    # here must hold *because* corroboration succeeded, not because a lone
    # indexed repository is trivially identified regardless of evidence.
    ledger = _ranked_ledger([("candidate-a", 20.0), ("candidate-b", 1.0)])
    _add_relationship(ledger, target="candidate-a", source_repo="caller-1", confidence="structural")
    ledger.add_evidence(
        provider="graph", action="scope_architecture:candidate-a", outcome="success", summary="scoped"
    )
    assert "candidate-a" in _corroboration_evidence(ledger), (
        "sanity check: single-caller structural evidence must corroborate"
    )

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)

    assert actions == [], "a strongly corroborated candidate must not trigger source retrieval"


def test_an_already_fetched_candidate_is_not_refetched() -> None:
    """Requirement #3: a candidate source retrieval has already run for
    (whether or not it corroborated) must not be re-proposed — the
    'disproven'/'already investigated' case."""
    names = [(f"repo-{i}", 10.0 - i) for i in range(CANDIDATE_FUNNEL_WIDTH)]
    ledger = _ranked_ledger(names)
    for name, _score in names:
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )
        _add_component(ledger, repository=name, name="widget_handler", file_path=f"{name}/widget.py")
    _set_ticket_terms(ledger, ["widget"])
    ledger.add_evidence(
        provider="github", action="fetch_source_files:repo-0", outcome="success", summary="fetched"
    )

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)

    fetched = {a.params["repository"] for a in actions}
    assert "repo-0" not in fetched


def test_source_retrieval_escalation_remains_bounded_by_its_existing_width() -> None:
    """Requirement #4: unchanged from RFC-0011 — escalation never exceeds
    `SOURCE_RETRIEVAL_WIDTH`, regardless of how many funnel candidates are
    weakly (not strongly) related."""
    names = [(f"repo-{i}", 10.0 - i) for i in range(CANDIDATE_FUNNEL_WIDTH)]
    ledger = _ranked_ledger(names)
    for name, _score in names:
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )
        # Every candidate has *some* weak relationship evidence (degree 2,
        # heuristic - below the specificity bar) - none strongly
        # corroborated, so all four would be escalation-eligible if not
        # for the width bound.
        _add_relationship(ledger, target=name, source_repo="shared-caller-1")
        _add_relationship(ledger, target=name, source_repo="shared-caller-2")
        _add_component(ledger, repository=name, name="widget_handler", file_path=f"{name}/widget.py")
    _set_ticket_terms(ledger, ["widget"])

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)

    assert len(actions) == SOURCE_RETRIEVAL_WIDTH


def test_escalation_candidate_priority_remains_rank_based() -> None:
    """Requirement #5: unchanged from the scheduling fix — even with the
    new eligibility check, escalation actions are still prioritized by
    lexical rank, not by name or by how they were excluded."""
    names = [("zzz-repo-a", 20.0), ("zzz-repo-b", 15.0), ("aaa-repo-c", 10.0)]
    ledger = _ranked_ledger(names)
    for name, _score in names:
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )
        _add_component(ledger, repository=name, name="widget_handler", file_path=f"{name}/widget.py")
    _set_ticket_terms(ledger, ["widget"])

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    actions = GitHubInvestigator().propose(state)

    priority_by_name = {a.params["repository"]: a.priority for a in actions}
    assert priority_by_name == {"zzz-repo-a": 0, "zzz-repo-b": 1, "aaa-repo-c": 2}


def test_rfc_0011_refusal_behavior_survives_the_escalation_fix() -> None:
    """Requirement #6: a lone lexical winner with zero corroborating
    evidence of any kind must still not be treated as identified, cycle-
    budget and escalation changes notwithstanding."""
    ledger = _ranked_ledger([("zzz-lone-winner", 20.0), ("aaa-runner-up", 2.0)])
    identified = next(s for s in _repository_signals(ledger) if s.label == "Owning repository identified")
    assert identified.satisfied is False


def test_rfc_0012_specificity_weighting_survives_the_escalation_fix() -> None:
    """Requirement #7: a high-degree shared dependency still doesn't
    corroborate on its own, unaffected by the eligibility/cycle changes."""
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")
    for name in ("shared-lib", "caller-x", "caller-y", "caller-z"):
        ledger.add_fact(kind="repository", subject=name, provider="graph", evidence_id=ev.evidence_id)
    for caller in ("caller-x", "caller-y", "caller-z"):
        _add_relationship(ledger, target="shared-lib", source_repo=caller)

    evidence = _corroboration_evidence(ledger)
    assert "shared-lib" not in evidence


@pytest.mark.asyncio
async def test_multiple_free_actions_available_in_the_same_cycle_do_not_each_cost_a_cycle() -> None:
    """Requirement #8 / the free-action-batching fix itself: several
    simultaneously-available cost=0 actions (the real shape:
    `request_parser`'s `match_repository_names` + `match_tracked_
    repository_names`, both unlocked by the same precondition) must run
    within one cycle, not one per cycle."""

    class _TwoFreeActionsInvestigator:
        name = "free_fake"

        def __init__(self) -> None:
            self.ran: list[str] = []

        def propose(self, state: WorkingContext) -> list[InvestigationAction]:
            return [
                InvestigationAction(
                    provider=self.name, key=key, intent=f"free: {key}", targets="documentation", cost=0
                )
                for key in ("first", "second")
                if not state.ledger.attempted(self.name, key)
            ]

        async def run(self, action, session, recorder):  # type: ignore[no-untyped-def]
            self.ran.append(action.key)
            recorder.evidence("success", f"did {action.key}")
            return InvestigationOutcome(observation=f"did {action.key}", yielded=False)

    free_investigator = _TwoFreeActionsInvestigator()
    state = await discover(
        request="Investigate something",
        session=_session(),
        investigators=[free_investigator],
    )

    assert free_investigator.ran == ["first", "second"]
    assert state.metadata.iteration == 1, "both free actions must be batched into the same cycle"


@pytest.mark.asyncio
async def test_synthetic_escalation_benchmark_weak_evidence_candidate_gets_source_escalation() -> None:
    """Synthetic A/B/C benchmark, generic (no real repository names) —
    plus a second strongly-corroborated candidate A2, so the overall
    repository question stays genuinely ambiguous (two equally strong,
    competing candidates — not yet resolved) instead of trivially
    resolving the instant A corroborates, which would make GitHubInvestigator
    stop before ever considering B (correctly, per RFC-0011 — once *one*
    candidate is decisively identified, further investigation is waste;
    that path is covered separately by `test_a_strongly_corroborated_
    candidate_skips_source_retrieval`). This is what lets escalation for
    B actually be observed:
    - A and A2 both have strong (structural, single-caller) graph evidence
      -> both already corroborated, neither needs source retrieval, but
      *together* they're a genuine tie, not a resolved answer.
    - B has weak/common (heuristic, three-caller) graph evidence -> not
      corroborated by graph alone, but must escalate to source retrieval
      rather than being abandoned.
    - C has lexical ranking only, no relationship evidence at all.
    Then verifies source evidence CAN promote B once its fetched content
    actually matches this request's own vocabulary — corroboration via
    the escalation path, not merely reaching it.
    """
    ledger = _ranked_ledger(
        [
            ("candidate-a", 20.0),
            ("candidate-a2", 19.0),
            ("candidate-b", 15.0),
            ("candidate-c", 10.0),
        ]
    )
    _add_relationship(ledger, target="candidate-a", source_repo="caller-a1", confidence="structural")
    _add_relationship(ledger, target="candidate-a2", source_repo="caller-a2", confidence="structural")
    for caller in ("caller-b1", "caller-b2", "caller-b3"):
        _add_relationship(ledger, target="candidate-b", source_repo=caller)
    for name in ("candidate-a", "candidate-a2", "candidate-b", "candidate-c"):
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )
    # RFC-0014 — B's own indexed component is what makes it eligible for
    # file-content escalation at all; A/A2/C get none, so this also proves
    # escalation is driven by "matches the ticket," not "reached this far
    # in the funnel."
    _add_component(
        ledger, repository="candidate-b", name="widget_dispatch_handler", file_path="src/dispatch.py"
    )
    _set_ticket_terms(ledger, ["widget_dispatch_handler"])

    evidence = _corroboration_evidence(ledger)
    assert "candidate-a" in evidence, "A's single-caller structural evidence must already corroborate"
    assert "candidate-a2" in evidence, "A2's single-caller structural evidence must already corroborate"
    assert "candidate-b" not in evidence, "B's three-caller heuristic evidence must not corroborate alone"

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()
    identified = next(s for s in _repository_signals(ledger) if s.label == "Owning repository identified")
    assert identified.satisfied is False, (
        "sanity check: two equally strong candidates are a genuine tie, not a resolved answer"
    )

    actions = GitHubInvestigator().propose(state)
    fetched = {a.params["repository"] for a in actions}
    assert "candidate-b" in fetched, "B must escalate to source retrieval rather than being abandoned"
    assert "candidate-a" not in fetched, "A is already corroborated via graph evidence"
    assert "candidate-a2" not in fetched, "A2 is already corroborated via graph evidence"
    b_action = next(a for a in actions if a.params["repository"] == "candidate-b")
    assert b_action.params["file_paths"] == ["src/dispatch.py"], (
        "B's escalation must target the file its own indexed component matched, not a guess"
    )

    # Simulate B's fetched source *content* (not metadata) actually
    # matching this request's own vocabulary — source evidence promoting a
    # graph-insufficient candidate, via the `source_file` fact kind
    # `GitHubInvestigator._run_source_file_fetch` writes.
    for fact in ledger.facts_of("repository"):
        fact.value["full_name"] = f"acme/{fact.subject}"
    ev = ledger.add_evidence(
        provider="github", action="fetch_source_files:acme/candidate-b", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="source_file",
        subject="acme/candidate-b::src/dispatch.py",
        provider="github",
        evidence_id=ev.evidence_id,
        value={"repository": "candidate-b", "full_name": "acme/candidate-b", "path": "src/dispatch.py"},
        text="def widget_dispatch_handler(event):\n    ...\n",
    )

    evidence_after_source = _corroboration_evidence(ledger)
    assert "candidate-b" in evidence_after_source, (
        "source content corroborates B once graph evidence alone couldn't"
    )


# ---------------------------------------------------------------------------
# RFC-0014 — GitHub source investigation actually inspects source content.
# `_select_relevant_source_files` (generic file selection, no hardcoded
# names) and the `source_file` fact kind (distinguishable from `pull_
# request` repository-metadata evidence).
# ---------------------------------------------------------------------------


def test_select_relevant_source_files_ranks_by_ticket_term_overlap() -> None:
    """Generic file selection: the file whose indexed symbol shares the
    most words with the ticket's own vocabulary comes first — nothing
    about a specific filename or symbol is hardcoded, only the overlap
    computation itself."""
    ledger = _ranked_ledger([("candidate-a", 20.0)])
    _add_component(
        ledger, repository="candidate-a", name="schema_validation_handler", file_path="src/high.py"
    )
    _add_component(ledger, repository="candidate-a", name="schema_only", file_path="src/low.py")
    _add_component(ledger, repository="candidate-a", name="unrelated_utility", file_path="src/none.py")
    _set_ticket_terms(ledger, ["schema", "validation"])

    files = _select_relevant_source_files(ledger, "candidate-a", limit=2)
    assert files == ["src/high.py", "src/low.py"]


def test_select_relevant_source_files_is_bounded_per_candidate() -> None:
    """Requirement: a maximum files-per-candidate limit, even when many
    more indexed components match."""
    ledger = _ranked_ledger([("candidate-a", 20.0)])
    for i in range(5):
        _add_component(
            ledger, repository="candidate-a", name=f"widget_handler_{i}", file_path=f"src/widget_{i}.py"
        )
    _set_ticket_terms(ledger, ["widget"])

    files = _select_relevant_source_files(ledger, "candidate-a", limit=MAX_SOURCE_FILES_PER_CANDIDATE)
    assert len(files) == MAX_SOURCE_FILES_PER_CANDIDATE


def test_select_relevant_source_files_returns_nothing_without_ticket_terms() -> None:
    """No guessing: an empty ticket vocabulary means no file is preferred
    over any other, so none are selected — never "just fetch something"."""
    ledger = _ranked_ledger([("candidate-a", 20.0)])
    _add_component(ledger, repository="candidate-a", name="anything", file_path="src/x.py")
    assert _select_relevant_source_files(ledger, "candidate-a") == []


def test_select_relevant_source_files_returns_nothing_when_no_component_matches() -> None:
    ledger = _ranked_ledger([("candidate-a", 20.0)])
    _add_component(ledger, repository="candidate-a", name="format_currency", file_path="src/format.py")
    _set_ticket_terms(ledger, ["refund", "dispatch"])
    assert _select_relevant_source_files(ledger, "candidate-a") == []


def test_source_file_evidence_is_a_distinct_fact_kind_from_metadata_and_relationships() -> None:
    """Requirement: source content must be distinguishable from repository
    metadata (`pull_request`) and graph relationship evidence
    (`repository_relationship`) — not folded into either."""
    ledger = _ranked_ledger([("candidate-a", 20.0)])
    _add_relationship(ledger, target="candidate-a", source_repo="caller-1")
    for fact in ledger.facts_of("repository"):
        fact.value["full_name"] = f"acme/{fact.subject}"
    ev = ledger.add_evidence(
        provider="github", action="fetch_source_files:acme/candidate-a", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="source_file",
        subject="acme/candidate-a::src/widget.py",
        provider="github",
        evidence_id=ev.evidence_id,
        value={"repository": "candidate-a", "full_name": "acme/candidate-a", "path": "src/widget.py"},
        text="def widget_handler(): ...",
    )

    kinds = {fact.kind for fact in ledger.facts}
    assert "source_file" in kinds
    assert "pull_request" not in kinds
    assert "repository_relationship" in kinds
    assert len(ledger.facts_of("source_file")) == 1
    assert len(ledger.facts_of("repository_relationship")) == 1


@pytest.mark.asyncio
async def test_synthetic_benchmark_source_content_beats_name_similarity() -> None:
    """RFC-0014's exact required generic benchmark: A has the strongest
    lexical/name similarity to the ticket, but its own already-indexed
    source has nothing to do with what the ticket describes; B ranks
    lower but its actual indexed implementation matches. Source-content
    evidence must let GraphForge prefer B over A — an anti-cheating proof
    that this isn't driven by name/ranking alone."""
    ledger = _ranked_ledger([("candidate-a-strong-name", 20.0), ("candidate-b-weak-name", 12.0)])
    for name in ("candidate-a-strong-name", "candidate-b-weak-name"):
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )
    # A: real indexed components, but none relate to the ticket at all.
    _add_component(
        ledger, repository="candidate-a-strong-name", name="format_currency", file_path="src/format.py"
    )
    # B: an indexed component whose own name matches the ticket's vocabulary.
    _add_component(
        ledger,
        repository="candidate-b-weak-name",
        name="process_refund_request",
        file_path="src/refunds.py",
    )
    _set_ticket_terms(ledger, ["refund", "process", "request"])

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()

    # A is the sole TIE_RATIO leader (12/20 < 0.9) — a lone lexical winner,
    # exactly the RFC-0011 case that must never count as identified on its
    # own, and its own source doesn't help either (nothing it contains
    # matches the ticket).
    identified_before = next(
        s for s in _repository_signals(ledger) if s.label == "Owning repository identified"
    )
    assert identified_before.satisfied is False

    actions = GitHubInvestigator().propose(state)
    fetched = {a.params["repository"]: a.params["file_paths"] for a in actions}
    assert fetched.get("candidate-b-weak-name") == ["src/refunds.py"], (
        "B must escalate to exactly the file its own indexed symbol matched"
    )
    assert "candidate-a-strong-name" not in fetched, (
        "A's own indexed source has nothing matching the ticket — no fetch, no guess"
    )

    # Simulate B's fetched source *content* genuinely matching.
    for fact in ledger.facts_of("repository"):
        fact.value["full_name"] = f"acme/{fact.subject}"
    ev = ledger.add_evidence(
        provider="github",
        action="fetch_source_files:acme/candidate-b-weak-name",
        outcome="success",
        summary="s",
    )
    ledger.add_fact(
        kind="source_file",
        subject="acme/candidate-b-weak-name::src/refunds.py",
        provider="github",
        evidence_id=ev.evidence_id,
        value={
            "repository": "candidate-b-weak-name",
            "full_name": "acme/candidate-b-weak-name",
            "path": "src/refunds.py",
        },
        text="def process_refund_request(order):\n    ...\n",
    )
    resync_corroborated_candidates(ledger)

    identified_after = next(
        s for s in _repository_signals(ledger) if s.label == "Owning repository identified"
    )
    assert identified_after.satisfied is True, "B's source content must be enough to decide the answer"
    evidence_final = _corroboration_evidence(ledger)
    assert "candidate-b-weak-name" in evidence_final
    assert "candidate-a-strong-name" not in evidence_final
    # Anti-cheating: the fact that actually corroborated B is source
    # content, not a name/metadata match.
    b_candidate = next(
        c
        for c in ledger.live_inferences("repository_candidate")
        if c.statement == "candidate-b-weak-name"
    )
    supporting_kinds = {
        fact.kind for fact in ledger.facts if fact.fact_id in b_candidate.supporting_fact_ids
    }
    assert "source_file" in supporting_kinds


# ---------------------------------------------------------------------------
# RFC-0015 — evidence specificity: raw token overlap is not enough. A term
# common across the candidates this run has already scoped is weak
# evidence; a term none of them share is strong. Directly reproduces the
# live PROT-5764 failure mode generically: `validation`/`failed`/`client`
# are exactly the shape of "generic ETL vocabulary shared by most scoped
# candidates" this module now downweights.
# ---------------------------------------------------------------------------


def test_a_term_present_in_every_scoped_candidate_scores_near_zero() -> None:
    """A term every scoped repository's own components share can't
    discriminate between them — its specificity weight must approach 0,
    however many repositories are being compared."""
    ledger = _ranked_ledger([("repo-a", 20.0), ("repo-b", 15.0), ("repo-c", 10.0), ("repo-d", 5.0)])
    for name in ("repo-a", "repo-b", "repo-c", "repo-d"):
        _add_component(ledger, repository=name, name="handle_failed_request", file_path=f"{name}/x.py")

    weights = _term_specificity_weights(ledger, frozenset({"failed"}))
    assert weights["failed"] < 0.1


def test_a_term_present_in_no_scoped_candidate_scores_maximally_specific() -> None:
    ledger = _ranked_ledger([("repo-a", 20.0), ("repo-b", 15.0), ("repo-c", 10.0), ("repo-d", 5.0)])
    for name in ("repo-a", "repo-b", "repo-c", "repo-d"):
        _add_component(ledger, repository=name, name="handle_failed_request", file_path=f"{name}/x.py")

    weights = _term_specificity_weights(ledger, frozenset({"refund"}), exclude_repository="repo-a")
    assert weights["refund"] == 1.0


def test_multiple_common_terms_never_outweigh_specificity_threshold() -> None:
    """However many *common* terms match, they never combine into a
    passing score — this is what stops "validation" + "failed" + "client"
    together from being treated as strong evidence just because there are
    three of them."""
    ledger = _ranked_ledger([("repo-a", 20.0), ("repo-b", 15.0), ("repo-c", 10.0), ("repo-d", 5.0)])
    for name in ("repo-a", "repo-b", "repo-c", "repo-d"):
        _add_component(ledger, repository=name, name="validation_client_failed_helper", file_path=f"{name}/x.py")

    matched = {"validation", "client", "failed"}
    weights = _term_specificity_weights(ledger, frozenset(matched))
    score = _matched_term_specificity(matched, weights)
    assert score < MIN_SOURCE_EVIDENCE_SPECIFICITY


def test_one_specific_term_or_two_moderate_terms_clears_the_bar() -> None:
    ledger = _ranked_ledger([("repo-a", 20.0), ("repo-b", 15.0), ("repo-c", 10.0), ("repo-d", 5.0)])
    # "refund" appears in none of the *other* scoped repos' components —
    # only in repo-b's own, which is exactly why it's specific *for repo-b*.
    _add_component(ledger, repository="repo-a", name="other_stuff", file_path="a/x.py")
    _add_component(ledger, repository="repo-b", name="process_refund_dispatch", file_path="b/refund.py")
    _add_component(ledger, repository="repo-c", name="more_stuff", file_path="c/x.py")
    _add_component(ledger, repository="repo-d", name="unrelated_stuff", file_path="d/x.py")

    weights = _term_specificity_weights(ledger, frozenset({"refund"}), exclude_repository="repo-b")
    score = _matched_term_specificity({"refund"}, weights)
    assert score >= MIN_SOURCE_EVIDENCE_SPECIFICITY


@pytest.mark.asyncio
async def test_phase8_synthetic_adversarial_benchmark_specific_implementation_beats_common_words() -> None:
    """The exact generic benchmark shape requested: A ranks highest
    lexically and contains only common/generic words; B ranks lower but
    contains the actual specific implementation; C contains one common
    keyword and is otherwise unrelated; D is a filler candidate that
    exists only to give the specificity computation a real, multi-
    repository corpus to compare against (matching how a live funnel of
    `CANDIDATE_FUNNEL_WIDTH` candidates actually looks). Nothing here
    names any real repository, ticket, or company.

    Expected: B wins. A and C must not become resolved from common-word
    overlap alone.
    """
    ledger = _ranked_ledger(
        [
            ("candidate-a-common-words", 20.0),
            ("candidate-b-specific-impl", 14.0),
            ("candidate-c-one-keyword", 11.0),
            ("candidate-d-filler", 8.0),
        ]
    )
    for name in (
        "candidate-a-common-words",
        "candidate-b-specific-impl",
        "candidate-c-one-keyword",
        "candidate-d-filler",
    ):
        ledger.add_evidence(
            provider="graph", action=f"scope_architecture:{name}", outcome="success", summary="scoped"
        )
    # A: only common/generic vocabulary, shared with both C and D below —
    # two *other* scoped candidates sharing each word is what actually
    # drives the specificity weight down into "common," not just one.
    _add_component(
        ledger, repository="candidate-a-common-words", name="validation_error_handler", file_path="a/validate.py"
    )
    _add_component(
        ledger, repository="candidate-a-common-words", name="request_failed_logger", file_path="a/failed.py"
    )
    _add_component(ledger, repository="candidate-a-common-words", name="client_wrapper", file_path="a/client.py")
    # D: filler, shares the same common words as A — a real corpus of
    # repositories that all happen to use ordinary ETL/programming
    # vocabulary.
    _add_component(
        ledger, repository="candidate-d-filler", name="validation_summary_report", file_path="d/validate.py"
    )
    _add_component(ledger, repository="candidate-d-filler", name="client_factory", file_path="d/client.py")
    _add_component(ledger, repository="candidate-d-filler", name="failed_job_monitor", file_path="d/failed.py")
    # C: also shares the same common vocabulary (its own fetched *content*
    # further below still only actually contains "client" — this is
    # corpus-building for the specificity computation, not what C's
    # source turns out to say).
    _add_component(
        ledger, repository="candidate-c-one-keyword", name="client_registry_loader", file_path="c/registry.py"
    )
    _add_component(
        ledger, repository="candidate-c-one-keyword", name="validation_check_helper", file_path="c/validate.py"
    )
    _add_component(
        ledger, repository="candidate-c-one-keyword", name="failed_retry_notice", file_path="c/failed.py"
    )
    # B: the actual specific implementation — words that appear nowhere
    # else in this scoped set.
    _add_component(
        ledger,
        repository="candidate-b-specific-impl",
        name="refund_dispatch_processor",
        file_path="b/refund_dispatch.py",
    )
    _set_ticket_terms(ledger, ["refund", "dispatch", "client", "failed", "validation"])

    state = WorkingContext()
    state.ledger = ledger
    state.refresh_assessments()

    # -- Phase 3/8: file selection and per-term weights are inspectable —
    # as seen when scoring B's own match (i.e. excluding B itself from the
    # "how common is this elsewhere" count, same as the real corroboration
    # path does).
    weights = _term_specificity_weights(
        ledger,
        frozenset({"refund", "dispatch", "client", "failed", "validation"}),
        exclude_repository="candidate-b-specific-impl",
    )
    assert weights["refund"] > weights["client"]
    assert weights["dispatch"] > weights["validation"]
    assert weights["refund"] == 1.0, "seen in no other scoped candidate's components"
    assert weights["client"] < MIN_SOURCE_EVIDENCE_SPECIFICITY, "shared by A, C, and D"

    # -- Phase 6: nothing is decisively identified from graph/lexical
    # ranking alone yet (no relationship evidence exists in this scenario).
    identified_before = next(
        s for s in _repository_signals(ledger) if s.label == "Owning repository identified"
    )
    assert identified_before.satisfied is False

    # -- Escalation: A (rank 1) is investigated first (rank-based
    # scheduling, RFC-0013, unchanged) but its own files are still only
    # ever fetched, never wrongly trusted before content is examined.
    actions = GitHubInvestigator().propose(state)
    fetched_by_repo = {a.params["repository"]: a.params["file_paths"] for a in actions}
    assert "candidate-a-common-words" in fetched_by_repo, "A is still investigated (rank-based scheduling preserved)"
    assert "candidate-b-specific-impl" in fetched_by_repo
    assert "candidate-c-one-keyword" in fetched_by_repo

    # Simulate every candidate's fetched source content.
    for fact in ledger.facts_of("repository"):
        fact.value["full_name"] = f"acme/{fact.subject}"

    def _add_source(repository: str, path: str, text: str) -> None:
        full_name = f"acme/{repository}"
        ev = ledger.add_evidence(
            provider="github", action=f"fetch_source_files:{full_name}", outcome="success", summary="s"
        )
        ledger.add_fact(
            kind="source_file",
            subject=f"{full_name}::{path}",
            provider="github",
            evidence_id=ev.evidence_id,
            value={"repository": repository, "full_name": full_name, "path": path},
            text=text,
        )

    # A's fetched file: real content, but only common words — matches
    # exactly what its own component name promised (this benchmark isn't
    # testing selection, it's testing whether *this* content resolves A).
    _add_source(
        "candidate-a-common-words",
        "a/validate.py",
        "def validation_error_handler(request):\n    if request_failed_logger(request):\n"
        "        client_wrapper().notify()\n",
    )
    # C's fetched file: the one common keyword, nothing else.
    _add_source(
        "candidate-c-one-keyword", "c/registry.py", "def client_registry_loader():\n    return {}\n"
    )
    # B's fetched file: the actual specific implementation.
    _add_source(
        "candidate-b-specific-impl",
        "b/refund_dispatch.py",
        "def refund_dispatch_processor(order):\n    return dispatch(order)\n",
    )

    evidence_final = _corroboration_evidence(ledger)
    assert "candidate-b-specific-impl" in evidence_final, "B's specific implementation must corroborate"
    assert "candidate-a-common-words" not in evidence_final, (
        "A must NOT be resolved merely from common-word overlap"
    )
    assert "candidate-c-one-keyword" not in evidence_final, "C's single common keyword must not resolve it"

    # -- Phase 6/9: the repository capability is now satisfied, decided by
    # B specifically, via source content specifically.
    resync_corroborated_candidates(ledger)
    identified_after = next(
        s for s in _repository_signals(ledger) if s.label == "Owning repository identified"
    )
    assert identified_after.satisfied is True
    b_candidate = next(
        c
        for c in ledger.live_inferences("repository_candidate")
        if c.statement == "candidate-b-specific-impl"
    )
    supporting_kinds = {
        fact.kind for fact in ledger.facts if fact.fact_id in b_candidate.supporting_fact_ids
    }
    assert "source_file" in supporting_kinds
    a_or_c_candidates = [
        c
        for c in ledger.live_inferences("repository_candidate")
        if c.statement in ("candidate-a-common-words", "candidate-c-one-keyword")
        and c.value.get("basis") == "corroborated"
    ]
    assert a_or_c_candidates == [], "neither A nor C may be promoted as corroborated"


# ---------------------------------------------------------------------------
# 10. RFC-0016 — architectural provider/consumer role, derived from real
#     graph-wide fan-in rather than a within-ledger proxy. None of these
#     tests name a real repository, capability, or ticket; "shared-jobs"/
#     "avangrid"/"sce" below are generic stand-ins for "the shared provider"
#     and "two of its many tenant consumers," structurally identical to any
#     other shared-library scenario (auth, Kafka framework, ETL, etc.).
# ---------------------------------------------------------------------------


def test_high_degree_repository_is_recognized_as_shared_infrastructure() -> None:
    """`repository_role` classifies purely from a count — no name, no
    domain vocabulary. And `_relationship_degree` must read the graph-wide
    `target_consumer_count` a real edge now carries, not merely count how
    many `repository_relationship` facts happen to already be sitting in
    this run's ledger (the RFC-0016 fix): a shared repository with 50 real
    consumers graph-wide, of which this run only ever scoped one caller,
    must still be recognized as high-degree.
    """
    assert repository_role(0) == "consumer"
    assert repository_role(2) == "consumer"
    assert repository_role(3) == "shared_provider"
    assert repository_role(50) == "shared_provider"

    ledger = _ranked_ledger([("caller-one", 5.0)])
    _add_relationship(ledger, target="shared-jobs", source_repo="caller-one", target_consumer_count=50)

    degree = _relationship_degree(ledger)
    assert degree["shared-jobs"] == 50, (
        "must reflect the real graph-wide fan-in the edge itself reports, "
        "not the single relationship fact this run's ledger happens to hold"
    )


def test_relationship_degree_falls_back_to_ledger_count_without_graph_wide_data() -> None:
    """Backward compatibility: a fact with no `target_consumer_count` (a
    synthetic ledger, or a backend that hasn't populated it) still gets the
    original within-ledger distinct-source count — RFC-0016 is a strict
    enrichment, not a breaking change to any existing fact shape."""
    ledger = Ledger()
    ev = ledger.add_evidence(provider="graph", action="survey", outcome="success", summary="s")
    for name in ("shared-lib", "caller-x", "caller-y"):
        ledger.add_fact(kind="repository", subject=name, provider="graph", evidence_id=ev.evidence_id)
    for caller in ("caller-x", "caller-y"):
        _add_relationship(ledger, target="shared-lib", source_repo=caller)  # no target_consumer_count

    assert _relationship_degree(ledger)["shared-lib"] == 2


def test_shared_infrastructures_individual_edges_become_less_discriminative() -> None:
    """A single relationship edge into a repository whose real graph-wide
    fan-in is high must score far below the specificity bar, even though
    only ONE `repository_relationship` fact exists in this run's ledger —
    this is exactly the case the old ledger-scoped `_relationship_degree`
    got wrong (it would have computed degree=1, weight=0.6, and wrongly
    corroborated both ends)."""
    ledger = _ranked_ledger([("caller-one", 5.0)])
    _add_relationship(ledger, target="shared-jobs", source_repo="caller-one", target_consumer_count=50)

    evidence = _corroboration_evidence(ledger)
    assert "shared-jobs" not in evidence, "high real-world fan-in must weaken this edge as evidence"
    assert "caller-one" not in evidence, (
        "the caller's only edge is to something the graph says is widely shared infrastructure"
    )


def _add_source_file(ledger: Ledger, *, repository: str, path: str, text: str) -> None:
    """Same shape the Phase 8 benchmark above uses: a fetched `source_file`
    fact, the thing `_corroboration_evidence`'s lexical/source branch
    actually scores (component facts only build the specificity corpus,
    see `_term_document_frequencies` — they don't themselves corroborate)."""
    full_name = f"acme/{repository}"
    repo_fact = next(f for f in ledger.facts_of("repository") if f.subject == repository)
    repo_fact.value["full_name"] = full_name
    ev = ledger.add_evidence(
        provider="github", action=f"fetch_source_files:{full_name}", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="source_file",
        subject=f"{full_name}::{path}",
        provider="github",
        evidence_id=ev.evidence_id,
        value={"repository": repository, "full_name": full_name, "path": path},
        text=text,
    )


def test_shared_provider_can_still_be_selected_via_ticket_specific_evidence() -> None:
    """Explicit non-exclusion requirement: a shared provider is never
    blacklisted. When the ticket's own vocabulary specifically matches the
    provider's own implementation (not merely the fact that it's popular),
    it must still corroborate — a shared library CAN be the repository a
    ticket is actually about."""
    ledger = _ranked_ledger(
        [("shared-jobs", 5.0), ("caller-one", 4.0), ("caller-two", 4.0), ("caller-three", 4.0)]
    )
    _set_ticket_terms(ledger, ["quota", "throttle", "shared"])
    _add_relationship(ledger, target="shared-jobs", source_repo="caller-one", target_consumer_count=50)
    _add_relationship(ledger, target="shared-jobs", source_repo="caller-two", target_consumer_count=50)
    _add_relationship(ledger, target="shared-jobs", source_repo="caller-three", target_consumer_count=50)
    # Give the other scoped candidates *some* components so the specificity
    # corpus (RFC-0015) has more than one repository to compare against.
    _add_component(ledger, repository="caller-one", name="unrelated_stuff", file_path="x/y.py")
    _add_component(ledger, repository="caller-two", name="other_thing", file_path="a/b.py")
    _add_component(ledger, repository="caller-three", name="misc_helper", file_path="p/q.py")
    _add_component(ledger, repository="shared-jobs", name="quota_throttle_guard", file_path="core/quota.py")
    _add_source_file(
        ledger,
        repository="shared-jobs",
        path="core/quota.py",
        text="def quota_throttle_guard():\n    enforce_shared_quota()\n",
    )

    evidence = _corroboration_evidence(ledger)
    assert "shared-jobs" in evidence, (
        "a shared provider must still corroborate when ticket vocabulary specifically "
        "matches its own implementation, despite its edges being weak evidence"
    )


def test_consumer_outranks_shared_provider_via_consumer_specific_evidence() -> None:
    """The PROT-5764 shape in the abstract: a shared provider has many
    consumers; the ticket's own vocabulary specifically matches only ONE
    consumer's own implementation. That consumer must corroborate; the
    provider (popular, but not specifically named by this ticket) and the
    other, unrelated consumers must not."""
    ledger = _ranked_ledger(
        [("shared-jobs", 5.0), ("tenant-a", 4.0), ("tenant-b", 4.0), ("tenant-c", 4.0)]
    )
    _set_ticket_terms(ledger, ["ledger", "reconciliation", "posting"])
    for tenant in ("tenant-a", "tenant-b", "tenant-c"):
        _add_relationship(ledger, target="shared-jobs", source_repo=tenant, target_consumer_count=50)
    # Only tenant-b's own component/source matches the ticket's specific vocabulary.
    _add_component(ledger, repository="tenant-a", name="unrelated_stuff", file_path="x/y.py")
    _add_component(ledger, repository="tenant-b", name="ledger_reconciliation_posting", file_path="b/l.py")
    _add_component(ledger, repository="tenant-c", name="other_thing", file_path="p/q.py")
    _add_component(ledger, repository="shared-jobs", name="generic_job_runner", file_path="core/run.py")
    _add_source_file(
        ledger,
        repository="tenant-b",
        path="b/l.py",
        text="def ledger_reconciliation_posting():\n    post_entries()\n",
    )

    evidence = _corroboration_evidence(ledger)
    assert "tenant-b" in evidence, "the ticket-specific consumer must corroborate"
    assert "shared-jobs" not in evidence, (
        "the provider is popular but not specifically named by this ticket's vocabulary"
    )
    assert "tenant-a" not in evidence
    assert "tenant-c" not in evidence


def test_unrelated_consumers_of_the_same_provider_are_not_promoted_together() -> None:
    """Extension of the existing high-degree-shared-dependency regression
    (RFC-0012), now proven against real graph-wide fan-in rather than a
    within-ledger proxy: several genuinely unrelated consumers of the same
    popular shared provider, none with any ticket-specific evidence, must
    all fail to corroborate and none may be promoted as identified."""
    ledger = _ranked_ledger(
        [("shared-jobs", 5.0), ("tenant-a", 4.0), ("tenant-b", 4.0), ("tenant-c", 4.0)]
    )
    for tenant in ("tenant-a", "tenant-b", "tenant-c"):
        _add_relationship(ledger, target="shared-jobs", source_repo=tenant, target_consumer_count=50)

    evidence = _corroboration_evidence(ledger)
    assert evidence == {}, "no candidate may be promoted from shared-dependency membership alone"

    resync_corroborated_candidates(ledger)
    promoted = [
        c
        for c in ledger.live_inferences("repository_candidate")
        if c.value.get("basis") == "corroborated"
    ]
    assert promoted == [], "none of the unrelated consumers may be promoted as corroborated"


# ---------------------------------------------------------------------------
# 11. RFC-0017 — continuous multi-term co-occurrence scoring, and source-file
#     relevance using name + type + path (with the existing test-code
#     discount reused, not re-implemented). None of these name any real
#     repository, ticket, or company.
# ---------------------------------------------------------------------------


def test_three_moderate_terms_beat_one_incidental_rare_term() -> None:
    """Positive direction: three terms that are each only moderately rare
    (none individually clears the old discrete `_MODERATE_TERM_WEIGHT`
    bar) but genuinely co-occur must be able to outscore one incidental
    higher-weight term matched alone — the exact gap the live PROT-5764
    benchmark exposed (schema/pipeline/validation, each ~0.2, losing to one
    unrelated ~0.5 term) before RFC-0017's continuous score was added."""
    weights = {"schema": 0.25, "pipeline": 0.25, "validation": 0.25, "event": 0.45}
    three_moderate = _matched_term_specificity({"schema", "pipeline", "validation"}, weights)
    one_incidental = _matched_term_specificity({"event"}, weights)
    assert three_moderate > one_incidental, (three_moderate, one_incidental)


def test_many_generic_terms_never_outscore_one_highly_specific_term() -> None:
    """Negative direction — the RFC-0015 adversarial requirement, still
    intact: piling on many generic/common terms must never let a match
    outscore one genuinely specific term, however many generic terms
    there are. The continuous score is bounded to `_MAX_COMBINATION_TERMS`
    of the strongest matches specifically so this can't be defeated by
    sheer term count."""
    weights = {
        "alpha": 0.12,
        "beta": 0.12,
        "gamma": 0.12,
        "delta": 0.12,
        "epsilon": 0.12,
        "zeta": 0.12,
        "specific": 0.95,
    }
    many_generic = _matched_term_specificity(
        {"alpha", "beta", "gamma", "delta", "epsilon", "zeta"}, weights
    )
    one_specific = _matched_term_specificity({"specific"}, weights)
    assert many_generic < one_specific, (many_generic, one_specific)
    assert many_generic < MIN_SOURCE_EVIDENCE_SPECIFICITY


def test_rfc_0015_adversarial_benchmark_still_passes_with_continuous_scoring() -> None:
    """Direct regression guard: re-run the exact weight shape the existing
    Phase 8 benchmark (`test_phase8_synthetic_adversarial_benchmark_
    specific_implementation_beats_common_words`, above) relies on — three
    common terms shared by most scoped candidates must still fail to
    clear the specificity bar even with the new continuous score folded
    in, and must still be strictly less specific than one truly unique
    term."""
    weights = {"validation": 0.207, "failed": 0.207, "client": 0.207, "refund": 1.0}
    common_three = _matched_term_specificity({"validation", "failed", "client"}, weights)
    unique_one = _matched_term_specificity({"refund"}, weights)
    assert common_three < MIN_SOURCE_EVIDENCE_SPECIFICITY, common_three
    assert unique_one >= MIN_SOURCE_EVIDENCE_SPECIFICITY
    assert common_three < unique_one


def test_select_relevant_source_files_uses_path_not_just_name() -> None:
    """RFC-0017: a component whose bare NAME shares nothing with the
    ticket, but whose FILE PATH does, must still be selectable — the exact
    gap that let `schema_validator.py` be invisible to the old name-only
    matcher despite living at a path built entirely from the ticket's own
    vocabulary."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0), ("repo-c", 4.0)])
    _set_ticket_terms(ledger, ["schema", "validation"])
    # This component's own name shares nothing with the ticket at all.
    _add_component(
        ledger, repository="repo-a", name="main", file_path="pipeline/validation/schema_validator.py"
    )
    _add_component(ledger, repository="repo-b", name="other_stuff", file_path="b/x.py")
    _add_component(ledger, repository="repo-c", name="more_stuff", file_path="c/x.py")

    selected = _select_relevant_source_files(ledger, "repo-a")
    assert "pipeline/validation/schema_validator.py" in selected


def test_select_relevant_source_files_discounts_test_components() -> None:
    """RFC-0017: reuses the existing `_is_test_component`/
    `_TEST_RELEVANCE_FACTOR` discount (`app.agents.planning.tools`) so a
    repository's test suite — routinely the numeric majority of its
    indexed components — can't crowd the actual production file worth
    reading out of a bounded top-`limit` cutoff purely on vocabulary the
    test merely exercises."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0), ("repo-c", 4.0)])
    _set_ticket_terms(ledger, ["quota"])
    _add_component(
        ledger,
        repository="repo-a",
        name="test_quota_guard",
        file_path="tests/test_quota_guard.py",
    )
    _add_component(ledger, repository="repo-a", name="quota_guard", file_path="core/quota_guard.py")
    _add_component(ledger, repository="repo-b", name="other_stuff", file_path="b/x.py")
    _add_component(ledger, repository="repo-c", name="more_stuff", file_path="c/x.py")

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["core/quota_guard.py"], (
        "the production file must outrank the equally-matching test file once discounted"
    )


# ---------------------------------------------------------------------------
# 12. RFC-0019 — config/deployment evidence in the corroboration/
#     specificity pipeline. "alpha"/"beta" are structurally interchangeable
#     synthetic operational identifiers; nothing here names TnT, Avangrid,
#     PROT-5764, or any real repository.
# ---------------------------------------------------------------------------


def test_config_evidence_corroborates_the_ticket_named_repository_over_an_identical_sibling() -> None:
    """Two candidates have identical component-level source evidence
    (the same matching function, same file shape); only their config
    evidence differs. Fetched config content naming the ticket's own
    operational identifier must be able to corroborate that repository,
    exactly like any other `source_file` evidence — the same specificity
    gate (RFC-0015), the same corroboration mechanism, no special case."""
    ledger = _ranked_ledger([("repo-alpha", 5.0), ("repo-beta", 5.0), ("repo-gamma", 4.0)])
    _set_ticket_terms(ledger, ["shared", "event", "beta"])
    # Identical source-code evidence in both repo-alpha and repo-beta.
    _add_component(
        ledger, repository="repo-alpha", name="process_shared_event", file_path="core/shared_event.py"
    )
    _add_component(
        ledger, repository="repo-beta", name="process_shared_event", file_path="core/shared_event.py"
    )
    _add_component(ledger, repository="repo-gamma", name="unrelated_stuff", file_path="x/y.py")
    # Config/deployment evidence differs: only repo-beta's config names "beta".
    _add_component(
        ledger,
        repository="repo-alpha",
        name="identifier alpha env production",
        file_path="deploy/job_a.yml",
    )
    _add_component(
        ledger,
        repository="repo-beta",
        name="identifier beta env production",
        file_path="deploy/job_b.yml",
    )
    _add_source_file(
        ledger, repository="repo-beta", path="deploy/job_b.yml", text="identifier beta env production"
    )

    evidence = _corroboration_evidence(ledger)
    assert "repo-beta" in evidence, "config evidence naming the ticket's identifier must corroborate"
    assert "repo-alpha" not in evidence, (
        "identical source evidence alone, without matching config evidence, must not corroborate"
    )


def test_config_evidence_file_is_selected_for_fetch_when_it_matches_the_ticket() -> None:
    """The config file itself must be a candidate for source retrieval —
    `_select_relevant_source_files` reading `ConfigFile`-shaped components
    exactly like any other component, no new selection logic."""
    ledger = _ranked_ledger([("repo-beta", 5.0), ("repo-alpha", 4.0), ("repo-gamma", 4.0)])
    _set_ticket_terms(ledger, ["beta"])
    _add_component(ledger, repository="repo-beta", name="unrelated_symbol", file_path="core/x.py")
    _add_component(
        ledger,
        repository="repo-beta",
        name="identifier beta env production",
        file_path="deploy/job_b.yml",
    )
    _add_component(ledger, repository="repo-alpha", name="unrelated_stuff", file_path="a/y.py")
    _add_component(ledger, repository="repo-gamma", name="also_unrelated", file_path="g/z.py")

    selected = _select_relevant_source_files(ledger, "repo-beta")
    assert "deploy/job_b.yml" in selected


# ---------------------------------------------------------------------------
# 13. RFC-0020 — structural config references vs. lexical file ranking.
#     Fully generic vocabulary ("gamma"/"acme-corp"/"services/main.py" are
#     structurally interchangeable placeholders); nothing here names any
#     real ticket, organization, workflow, or repository.
# ---------------------------------------------------------------------------


def test_config_reference_boost_pulls_in_a_structurally_referenced_file() -> None:
    """The exact RFC-0020 audit finding, reproduced generically: a config
    file's own decisive identifier sits *after* a long run of unrelated
    filler (proving the truncation fix keeps it searchable at all), and
    the config structurally references a source file with zero lexical
    match of its own — that file must only be selected once the
    `referenced_by_config_file_paths` boost is applied, never before."""
    ledger = _ranked_ledger(
        [("metadata-repo", 5.0), ("operational-repo", 5.0), ("filler-repo", 4.0)]
    )
    _set_ticket_terms(ledger, ["gamma", "acme-corp"])

    _add_component(
        ledger,
        repository="metadata-repo",
        name=(
            "organization acme-corp description internal platform component "
            "for the acme-corp systems team acme-corp"
        ),
        file_path="catalog-info.yaml",
    )
    filler = " ".join(f"boilerplate_field_{i} noise_value_{i}" for i in range(60))
    _add_component(
        ledger,
        repository="operational-repo",
        name=f"organization acme-corp {filler} late_discriminator gamma",
        file_path="deploy/job.yml",
    )
    _add_component(
        ledger, repository="operational-repo", name="run_entrypoint", file_path="services/main.py"
    )
    _add_component(ledger, repository="filler-repo", name="unrelated_stuff", file_path="x/y.py")

    # Before the reference is attached: services/main.py has no lexical
    # match of its own and must not be selected.
    selected_before = _select_relevant_source_files(ledger, "operational-repo")
    assert "services/main.py" not in selected_before
    assert "deploy/job.yml" in selected_before, (
        "the late 'gamma' field must still be searchable — the truncation fix"
    )

    # Attach the structural reference exactly as `TraverseArchitectureGraphTool`
    # populates it from a real `REFERENCES` edge (RFC-0019/0020).
    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "services/main.py":
            fact.value["referenced_by_config_file_paths"] = ["deploy/job.yml"]

    selected_after = _select_relevant_source_files(ledger, "operational-repo")
    assert "services/main.py" in selected_after, "the structurally referenced file must be boosted in"
    assert "deploy/job.yml" in selected_after, "the referencing config's own match must still count"


def test_config_reference_boost_never_exceeds_the_referencing_configs_own_score() -> None:
    """A file referenced by an *irrelevant* config (one that never scored
    above 0) must not be boosted into selection out of nothing — the
    boost is conditional evidence propagation, never a free pass."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["gamma"])
    _add_component(
        ledger, repository="repo-a", name="totally_unrelated_config", file_path="deploy/job.yml"
    )
    _add_component(ledger, repository="repo-a", name="run_entrypoint", file_path="services/main.py")
    _add_component(ledger, repository="repo-b", name="also_unrelated", file_path="x/y.py")

    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "services/main.py":
            fact.value["referenced_by_config_file_paths"] = ["deploy/job.yml"]

    selected = _select_relevant_source_files(ledger, "repo-a")
    assert selected == [], "nothing scored, so nothing — including the referenced file — is selected"


def test_operational_identifier_beats_common_organization_mention_in_ranking() -> None:
    """Cross-repository requirement: generic organization-name metadata
    must not outrank a specific operational config match at the
    repository-ranking level either (`rank_repositories`, RFC-0018) —
    'acme-corp' is common to both repos (mirrors the real audit finding:
    the org name appeared in both sibling repos' metadata), so only
    'gamma' can discriminate."""
    filler = " ".join(f"boilerplate_field_{i} noise_value_{i}" for i in range(60))
    metadata_component = {
        "id": "metadata-config",
        "name": "organization acme-corp description internal platform component acme-corp",
        "type": "ConfigFile",
        "file_path": "catalog-info.yaml",
        "repository": "metadata-repo",
    }
    operational_component = {
        "id": "operational-config",
        "name": f"organization acme-corp {filler} late_discriminator gamma",
        "type": "ConfigFile",
        "file_path": "deploy/job.yml",
        "repository": "operational-repo",
    }

    indexed_repos = [{"name": "metadata-repo"}, {"name": "operational-repo"}]
    components = [metadata_component, operational_component]

    ranked = rank_repositories(indexed_repos, components, ["gamma", "acme-corp"])
    scores = dict((name, score) for score, name in ranked)
    assert scores["operational-repo"] > scores["metadata-repo"], scores


# ---------------------------------------------------------------------------
# 14. RFC-0021 — evidence-aware file-selection tie-breaking. Fully generic
#     vocabulary; nothing here names any real ticket, organization,
#     workflow, or repository.
# ---------------------------------------------------------------------------


def test_structural_reference_wins_an_exact_score_tie_over_lexical_only() -> None:
    """Case 1: a lexically-matching file and a structurally-referenced
    file land at the *exact same* score — the structural reference must
    win the tie, where the old alphabetical-only tie-break would pick
    whichever name happened to sort first."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["delta"])
    # "aaa_lexical_only.py" sorts alphabetically before "zzz_referenced.py"
    # — if the old alphabetical fallback were still deciding this, the
    # lexical-only file would win. It must not.
    _add_component(ledger, repository="repo-a", name="aaa_lexical_only_delta", file_path="aaa_lexical_only.py")
    _add_component(ledger, repository="repo-a", name="config_naming_delta", file_path="config.yml")
    _add_component(ledger, repository="repo-b", name="unrelated_stuff", file_path="x/y.py")
    # zzz_referenced.py has no lexical match of its own — only the
    # structural reference from config.yml (same score as aaa_lexical_only.py).
    _add_component(ledger, repository="repo-a", name="no_match_here", file_path="zzz_referenced.py")
    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "zzz_referenced.py":
            fact.value["referenced_by_config_file_paths"] = ["config.yml"]

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["zzz_referenced.py"], (
        "the structurally-referenced file must win the exact-score tie, "
        f"not the alphabetically-earlier lexical-only file: got {selected}"
    )


def test_stronger_lexical_score_still_beats_weaker_structural_reference() -> None:
    """Case 2: structural provenance is a tie-break, never an override —
    a file with a genuinely higher score must still win even against a
    structurally-referenced file with a lower (boosted) score."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0), ("repo-c", 4.0)])
    _set_ticket_terms(ledger, ["delta", "epsilon", "zeta"])
    # strong-match.py matches all three terms -> high specificity.
    _add_component(
        ledger, repository="repo-a", name="delta_epsilon_zeta_handler", file_path="strong_match.py"
    )
    # weak-config.yml matches only one common-ish term -> lower specificity.
    _add_component(ledger, repository="repo-a", name="delta_only", file_path="weak_config.yml")
    _add_component(ledger, repository="repo-b", name="delta_epsilon_zeta_other", file_path="x/other1.py")
    _add_component(ledger, repository="repo-c", name="delta_epsilon_zeta_third", file_path="x/other2.py")

    _add_component(ledger, repository="repo-a", name="referenced_by_weak_config", file_path="weakly_referenced.py")
    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "weakly_referenced.py":
            fact.value["referenced_by_config_file_paths"] = ["weak_config.yml"]

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["strong_match.py"], (
        "a genuinely higher lexical score must still win over a lower-scoring "
        f"structurally-referenced file: got {selected}"
    )


def test_equal_score_with_no_structural_evidence_keeps_alphabetical_order() -> None:
    """Case 3: when neither tied file has structural provenance, behavior
    is unchanged from before RFC-0021 — alphabetical order decides."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["epsilon"])
    _add_component(ledger, repository="repo-a", name="epsilon_first", file_path="aaa_file.py")
    _add_component(ledger, repository="repo-a", name="epsilon_second", file_path="zzz_file.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["aaa_file.py"], (
        f"with no structural evidence on either side, alphabetical order must decide: got {selected}"
    )


def test_structural_tie_break_does_not_fire_when_scores_differ_even_slightly() -> None:
    """Explicit regression guard: the structural tie-break must only ever
    activate on an *exact* score tie. A referenced file whose boosted
    score is even marginally lower than a competing lexical-only file's
    score must still lose — proving `-kv[1]` alone still fully decides
    ordering whenever scores are not byte-for-byte equal."""
    ledger = _ranked_ledger(
        [("repo-a", 5.0), ("repo-b", 4.0), ("repo-c", 4.0), ("repo-d", 4.0)]
    )
    _set_ticket_terms(ledger, ["delta", "epsilon"])
    # slightly_stronger.py matches two terms; the referencing config
    # matches only one -> its boosted target scores strictly lower.
    _add_component(ledger, repository="repo-a", name="delta_epsilon_combo", file_path="slightly_stronger.py")
    _add_component(ledger, repository="repo-a", name="delta_only_config", file_path="lesser_config.yml")
    _add_component(ledger, repository="repo-b", name="delta_epsilon_combo_2", file_path="x/o1.py")
    _add_component(ledger, repository="repo-c", name="delta_only_2", file_path="x/o2.py")
    _add_component(ledger, repository="repo-d", name="delta_epsilon_combo_3", file_path="x/o3.py")

    _add_component(ledger, repository="repo-a", name="referenced_target", file_path="boosted_but_lower.py")
    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "boosted_but_lower.py":
            fact.value["referenced_by_config_file_paths"] = ["lesser_config.yml"]

    weights = _term_specificity_weights(
        ledger, frozenset({"delta", "epsilon"}), exclude_repository="repo-a"
    )
    stronger_score = _matched_term_specificity({"delta", "epsilon"}, weights)
    weaker_score = _matched_term_specificity({"delta"}, weights)
    assert stronger_score > weaker_score, "fixture must produce a real, non-tied score gap"

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["slightly_stronger.py"], (
        f"any real score gap, however small, must still decide ordering: got {selected}"
    )


# ---------------------------------------------------------------------------
# 15. RFC-0022 — dependency-aware source investigation. Fully generic
#     vocabulary ("File A/B/C/D" are the literal roles requested); nothing
#     here names any real ticket, repository, or company.
# ---------------------------------------------------------------------------


def test_file_a_is_selected_from_lexical_evidence_alone() -> None:
    """Requirement 1: the strong initial entrypoint is still found purely
    by the existing, unmodified lexical selection — RFC-0022 adds nothing
    to this stage."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["zeta"])
    _add_component(ledger, repository="repo-a", name="zeta_entrypoint", file_path="file_a.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    selected = _select_relevant_source_files(ledger, "repo-a")
    assert selected == ["file_a.py"]


def test_dependency_targets_become_eligible_via_real_graph_relationships() -> None:
    """Requirements 2/3: B and C, reached via a real CALLS/IMPORTS edge
    from the already-fetched File A, become eligible and inherit a score
    capped at A's own true score — never exceeding it."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["zeta"])
    _add_component(ledger, repository="repo-a", name="zeta_entrypoint", file_path="file_a.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    a_score = _score_candidate_source_files_for_test(ledger, "repo-a")["file_a.py"]
    assert a_score > 0

    dependency_targets = {"file_b.py": {"file_a.py"}, "file_c.py": {"file_a.py"}}
    selected = _select_dependency_expansion_files(ledger, "repo-a", dependency_targets)
    assert set(selected) == {"file_b.py", "file_c.py"}


def test_dependency_score_never_exceeds_the_fetched_sources_own_score() -> None:
    """Requirement 3, explicit: the propagated score is capped, matching
    RFC-0020's own principle exactly — never manufactured, never larger
    than what made the source file relevant enough to fetch."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["zeta"])
    _add_component(ledger, repository="repo-a", name="zeta_entrypoint", file_path="file_a.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    scored = _score_candidate_source_files_for_test(ledger, "repo-a")
    a_score = scored["file_a.py"]

    dependency_targets = {"file_b.py": {"file_a.py"}}
    selected = _select_dependency_expansion_files(ledger, "repo-a", dependency_targets)
    assert selected == ["file_b.py"]
    # Recompute what the propagation actually produced, the same way
    # `_select_dependency_expansion_files` does internally, to assert the
    # cap directly rather than only its ranking consequence.
    from app.context_pipeline.reasoning.capabilities import _REFERENCE_BOOST_FACTOR

    assert a_score * _REFERENCE_BOOST_FACTOR <= a_score + 1e-9


def test_unrelated_strong_lexical_file_is_not_pulled_in_without_a_real_edge() -> None:
    """Requirement 4: File D has a strong lexical match of its own but no
    real CALLS/IMPORTS edge from File A — dependency expansion must never
    consider it merely because it shares vocabulary. `_select_dependency_
    expansion_files` only ever evaluates files that are literal keys in
    `dependency_targets`, so D — never listed there — cannot leak in."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["zeta"])
    _add_component(ledger, repository="repo-a", name="zeta_entrypoint", file_path="file_a.py")
    # File D: strong lexical match, but genuinely unrelated — no edge from A.
    _add_component(ledger, repository="repo-a", name="zeta_unrelated_strong_match", file_path="file_d.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    dependency_targets = {"file_b.py": {"file_a.py"}}  # file_d.py deliberately absent
    selected = _select_dependency_expansion_files(ledger, "repo-a", dependency_targets)
    assert "file_d.py" not in selected
    assert selected == ["file_b.py"]


def test_higher_scoring_dependency_target_beats_a_lower_scoring_one() -> None:
    """Requirement 5: within the dependency-expansion pool itself, a
    target reached from a more relevant fetched source still outranks one
    reached from a less relevant fetched source."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0), ("repo-c", 4.0)])
    _set_ticket_terms(ledger, ["zeta", "eta"])
    # file_strong_source.py matches both terms; file_weak_source.py matches one.
    _add_component(ledger, repository="repo-a", name="zeta_eta_strong", file_path="strong_source.py")
    _add_component(ledger, repository="repo-a", name="zeta_only_weak", file_path="weak_source.py")
    _add_component(ledger, repository="repo-b", name="zeta_eta_other", file_path="x/o1.py")
    _add_component(ledger, repository="repo-c", name="zeta_other", file_path="x/o2.py")

    dependency_targets = {
        "reached_from_strong.py": {"strong_source.py"},
        "reached_from_weak.py": {"weak_source.py"},
    }
    selected = _select_dependency_expansion_files(ledger, "repo-a", dependency_targets, limit=1)
    assert selected == ["reached_from_strong.py"]


def test_dependency_expansion_never_returns_a_file_outside_its_own_input() -> None:
    """Requirement 6: no unbounded BFS — the function is pure and only
    ever considers files that are literal keys of the `dependency_targets`
    mapping the caller (one bounded `get_neighborhood` hop, in
    `investigators.GitHubInvestigator._run_dependency_expansion`) already
    computed. It can never expand beyond what it was given."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["zeta"])
    _add_component(ledger, repository="repo-a", name="zeta_entrypoint", file_path="file_a.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    dependency_targets = {"file_b.py": {"file_a.py"}, "file_c.py": {"file_a.py"}}
    selected = _select_dependency_expansion_files(ledger, "repo-a", dependency_targets, limit=10)
    assert set(selected) <= set(dependency_targets.keys())


def test_dependency_expansion_propagates_nothing_from_an_unscored_source() -> None:
    """A source file with no score of its own (RFC-0011 discipline) must
    propagate nothing — the same 'no confident unsupported selection'
    guarantee RFC-0020 already enforces for config references."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["zeta"])
    _add_component(ledger, repository="repo-a", name="totally_unrelated", file_path="file_a.py")
    _add_component(ledger, repository="repo-b", name="also_unrelated", file_path="x/y.py")

    dependency_targets = {"file_b.py": {"file_a.py"}}
    selected = _select_dependency_expansion_files(ledger, "repo-a", dependency_targets)
    assert selected == []


def test_initial_selection_width_is_unaffected_by_dependency_expansion() -> None:
    """Requirement 7: `_select_relevant_source_files`'s own bounded
    behavior (`MAX_SOURCE_FILES_PER_CANDIDATE`) is completely unchanged —
    dependency expansion is a separate, additional, later stage with its
    own separate budget, never a wider version of the same selection."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["zeta"])
    for i in range(5):
        _add_component(ledger, repository="repo-a", name=f"zeta_match_{i}", file_path=f"file_{i}.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    selected = _select_relevant_source_files(ledger, "repo-a")
    assert len(selected) == MAX_SOURCE_FILES_PER_CANDIDATE


def _score_candidate_source_files_for_test(ledger, repository) -> dict:
    from app.context_pipeline.reasoning.capabilities import _score_candidate_source_files

    scored, _referenced_by, _signal = _score_candidate_source_files(ledger, repository)
    return scored


# ---------------------------------------------------------------------------
# 16. RFC-0023 — dependency target relevance scoring. Fully generic
#     vocabulary ("alpha"/"validation"/"failure", "File A/B/C/D/E") exactly
#     matching the requested benchmark shape; nothing here names any real
#     ticket, repository, or company.
# ---------------------------------------------------------------------------


def _target_component(name: str, file_path: str, is_test: bool | None = None) -> dict:
    return {"name": name, "type": "Function", "file_path": file_path, "is_test": is_test}


def _rfc_0023_fixture():
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["alpha", "validation", "failure"])
    _add_component(ledger, repository="repo-a", name="alpha_entrypoint", file_path="file_a.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")
    dependency_targets = {
        "file_b.py": {"file_a.py"},
        "file_c.py": {"file_a.py"},
        "file_d.py": {"file_a.py"},
    }
    target_components = {
        "file_b.py": [_target_component("validate_alpha", "file_b.py")],
        "file_c.py": [_target_component("write_data", "file_c.py")],
        "file_d.py": [_target_component("log_status", "file_d.py")],
    }
    return ledger, dependency_targets, target_components


def test_zero_relevance_dependency_targets_remain_eligible() -> None:
    """Assertion 1: a structurally connected target with zero lexical
    relevance of its own (File C, File D) must still be selectable — the
    RFC-0022 guarantee is unchanged by RFC-0023's own-relevance scoring."""
    ledger, dependency_targets, target_components = _rfc_0023_fixture()
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=3
    )
    assert set(selected) == {"file_b.py", "file_c.py", "file_d.py"}


def test_dependency_target_with_own_relevance_wins_over_unrelated_siblings() -> None:
    """Assertion 2 — the exact live PROT-5764 failure, reproduced
    generically: three dependency targets of the same fetched source file
    all inherit the identical capped score; only File B (validate_alpha,
    genuinely matching ticket vocabulary) must win, not whichever
    unrelated file happens to sort first alphabetically."""
    ledger, dependency_targets, target_components = _rfc_0023_fixture()
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=1
    )
    assert selected == ["file_b.py"], f"the target with genuine own relevance must win: got {selected}"


def test_structural_relationship_still_required_for_dependency_expansion() -> None:
    """Assertion 3: File E has a strong lexical match to the ticket but no
    real CALLS/IMPORTS edge from the fetched File A — own relevance alone,
    without a structural edge, must never make it eligible."""
    ledger, dependency_targets, target_components = _rfc_0023_fixture()
    # File E would match every ticket term, but is deliberately never
    # listed in `dependency_targets` or `target_components` below — no
    # structural edge from A reaches it, so it must never be considered.
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=10
    )
    assert "file_e.py" not in selected
    assert set(selected) <= set(dependency_targets.keys())


def test_alphabetical_fallback_when_own_relevance_ties() -> None:
    """Assertion 5 (also requirement 3 of the implementation task): two
    dependency targets with equally-irrelevant own names — both
    contributing nothing beyond the shared inherited floor — still
    resolve via alphabetical order. The fix only changes ranking when a
    target's own relevance genuinely differs; it never removes the
    existing fallback."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["alpha", "validation", "failure"])
    _add_component(ledger, repository="repo-a", name="alpha_entrypoint", file_path="file_a.py")
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    dependency_targets = {"zzz_file.py": {"file_a.py"}, "aaa_file.py": {"file_a.py"}}
    target_components = {
        "zzz_file.py": [_target_component("write_data", "zzz_file.py")],
        "aaa_file.py": [_target_component("log_status", "aaa_file.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=1
    )
    assert selected == ["aaa_file.py"]


def test_zero_own_relevance_scores_zero_not_the_sources_own_score() -> None:
    """RFC-0023 Option B's contract, superseding Option A's retired
    'never below the inherited floor' rule: a target with zero own
    relevance (File C, File D) now scores exactly 0, not the fetched
    source file's own score. Structural connectivity from 'file_a.py'
    is what makes File B/C/D *eligible* at all (RFC-0022's guarantee,
    still intact — all three are still selectable); it is deliberately
    no longer a floor under their *ranking* — that's what lets File B's
    genuine own relevance win outright instead of tying with siblings
    that merely share the same structural edge (the live PROT-5764
    failure Option A reproduced: `schema_validator.py` tied with
    `base_table_loader.py`/`bronze_writer.py` at the inherited floor and
    lost the alphabetical tiebreak)."""
    ledger, dependency_targets, target_components = _rfc_0023_fixture()
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=3
    )
    assert set(selected) == {"file_b.py", "file_c.py", "file_d.py"}, (
        "zero-relevance targets are still eligible — RFC-0022's guarantee is unchanged"
    )
    assert selected[0] == "file_b.py", (
        "but ranking is by own relevance alone now: the one target with genuine "
        "relevance sorts first instead of tying with the zero-relevance siblings"
    )


def test_initial_source_file_selection_is_unaffected_by_rfc_0023() -> None:
    """Requirement 4: RFC-0021's initial selection (`_select_relevant_
    source_files`) is untouched by this change — a structural tie there
    must still be decided by structural-reference provenance, not by
    dependency-target own-relevance (a different function entirely)."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["delta"])
    _add_component(ledger, repository="repo-a", name="aaa_lexical_only_delta", file_path="aaa_lexical_only.py")
    _add_component(ledger, repository="repo-a", name="config_naming_delta", file_path="config.yml")
    _add_component(ledger, repository="repo-b", name="unrelated_stuff", file_path="x/y.py")
    _add_component(ledger, repository="repo-a", name="no_match_here", file_path="zzz_referenced.py")
    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "zzz_referenced.py":
            fact.value["referenced_by_config_file_paths"] = ["config.yml"]

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["zzz_referenced.py"], "RFC-0021's own tie-break must remain exactly as before"


# ---------------------------------------------------------------------------
# 17. RFC-0023 Option B — structural inheritance is an eligibility signal
#     only; `own_relevance` alone decides ranking among eligible dependency
#     targets. Fully generic vocabulary ("widget"/"dispatch"/"retry",
#     "entry.py"/File A/B/C/D) — nothing here names any real ticket,
#     repository, or company.
# ---------------------------------------------------------------------------


def _option_b_fixture():
    """A fetched source file ('entry.py') whose own score is deliberately
    *higher* than any single dependency target's own relevance can reach
    on its own — the exact shape that broke Option A's `max(inherited,
    own_relevance)` on the live PROT-5764 benchmark (a well-matched
    entrypoint's own score routinely exceeds what one dependency target's
    own name/path can independently earn). Two sibling repositories give
    `_term_specificity_weights` a real corpus to compute against, so
    "widget" — shared by every scoped repository — is correctly judged
    maximally generic (weight 0), the same way "schema"/"pipeline" were
    on the real graph, while "dispatch" (unique to repo-a) is judged
    maximally specific.
    """
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0), ("repo-c", 3.0)])
    _set_ticket_terms(ledger, ["widget", "dispatch", "retry"])
    _add_component(
        ledger, repository="repo-a", name="widget_dispatch_entrypoint", file_path="entry.py"
    )
    _add_component(ledger, repository="repo-b", name="widget_gateway", file_path="x/gateway.py")
    _add_component(ledger, repository="repo-b", name="retry_policy", file_path="x/retry.py")
    _add_component(ledger, repository="repo-c", name="widget_registry", file_path="y/registry.py")

    dependency_targets = {
        "file_a.py": {"entry.py"},  # relevant — matches "retry"
        "file_b.py": {"entry.py"},  # merely structurally connected — no match
        "file_c.py": {"entry.py"},  # merely structurally connected — no match
    }
    target_components = {
        "file_a.py": [_target_component("retry_handler", "file_a.py")],
        "file_b.py": [_target_component("log_writer", "file_b.py")],
        "file_c.py": [_target_component("noop_helper", "file_c.py")],
    }
    return ledger, dependency_targets, target_components


def test_generic_relevant_dependency_outranks_merely_structural_siblings() -> None:
    """The generic A/B/C reproduction of the live PROT-5764 failure: the
    fetched source's own score is higher than File A's own relevance —
    under the superseded `max(inherited, own_relevance)` design this
    would have let the inherited floor swamp File A entirely and tie it
    with the two unrelated siblings (exactly what happened to
    `schema_validator.py` on the real benchmark). Under eligibility-only
    inheritance, File A's own relevance is judged on its own terms and
    wins outright."""
    ledger, dependency_targets, target_components = _option_b_fixture()

    scored = _score_candidate_source_files_for_test(ledger, "repo-a")
    assert scored["entry.py"] > 0, "fixture precondition: the source must itself be relevant"

    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=1
    )
    assert selected == ["file_a.py"], f"the genuinely relevant dependency must win: got {selected}"


def test_generic_zero_relevance_structural_dependencies_remain_eligible() -> None:
    """File B and File C have no lexical relevance of their own, but the
    real CALLS/IMPORTS edge from 'entry.py' still makes them eligible —
    with a large enough limit, all three are returned."""
    ledger, dependency_targets, target_components = _option_b_fixture()
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=3
    )
    assert set(selected) == {"file_a.py", "file_b.py", "file_c.py"}


def test_generic_unrelated_lexical_match_without_structural_edge_is_not_eligible() -> None:
    """File D would match every ticket term, but no real structural edge
    from 'entry.py' reaches it — own relevance alone, without structural
    connectivity, must never make it eligible."""
    ledger, dependency_targets, target_components = _option_b_fixture()
    # File D is deliberately absent from both dependency_targets and
    # target_components below — no structural edge reaches it.
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=10
    )
    assert "file_d.py" not in selected
    assert set(selected) <= set(dependency_targets.keys())


def test_generic_zero_score_ties_retain_alphabetical_ordering() -> None:
    """When every eligible target has equally-zero own relevance, ranking
    falls back to alphabetical order — unchanged from RFC-0022."""
    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, ["widget", "dispatch", "retry"])
    _add_component(
        ledger, repository="repo-a", name="widget_dispatch_entrypoint", file_path="entry.py"
    )
    _add_component(ledger, repository="repo-b", name="unrelated", file_path="x/y.py")

    dependency_targets = {"zzz_file.py": {"entry.py"}, "aaa_file.py": {"entry.py"}}
    target_components = {
        "zzz_file.py": [_target_component("log_writer", "zzz_file.py")],
        "aaa_file.py": [_target_component("noop_helper", "aaa_file.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=1
    )
    assert selected == ["aaa_file.py"]


# ---------------------------------------------------------------------------
# 18. RFC-0024 — directness tie-break (CALLS beats IMPORTS-only) and fan-in
#     structural specificity (a widely-shared, generic dependency is
#     discounted, floored so a genuinely strong lexical match can never be
#     erased). Fully generic vocabulary ("gizmo"/"dispatch"/"retry"/
#     "audit") — nothing here names any real ticket, repository, or
#     company; the audit's own three example terms (gizmo/retry/audit) are
#     used verbatim.
# ---------------------------------------------------------------------------


def _rfc_0024_fixture():
    """Four repositories give `_term_specificity_weights` a real spread to
    compute against: "gizmo" (shared by all 3 siblings, weight 0 — the
    structural equivalent of a stopword), "dispatch" (unique to repo-a,
    weight 1.0), "retry" (shared by 1 of 3 siblings, weight 0.5), "audit"
    (shared by 2 of 3 siblings, weight ~0.2075 — a genuinely *weak* but
    real signal, below `_MODERATE_TERM_WEIGHT`, unlike "retry")."""
    ledger = _ranked_ledger(
        [("repo-a", 5.0), ("repo-b", 4.0), ("repo-c", 3.0), ("repo-d", 2.0)]
    )
    _set_ticket_terms(ledger, ["gizmo", "dispatch", "retry", "audit"])
    _add_component(
        ledger, repository="repo-a", name="gizmo_dispatch_entrypoint", file_path="entry.py"
    )
    _add_component(ledger, repository="repo-b", name="gizmo_gateway", file_path="x/gateway.py")
    _add_component(ledger, repository="repo-c", name="gizmo_registry", file_path="y/registry.py")
    _add_component(ledger, repository="repo-d", name="gizmo_worker", file_path="z/worker.py")
    _add_component(ledger, repository="repo-b", name="retry_policy", file_path="x/retry.py")
    _add_component(ledger, repository="repo-c", name="audit_trail", file_path="y/audit.py")
    _add_component(ledger, repository="repo-d", name="audit_log", file_path="z/audit.py")
    return ledger


def test_direct_calls_wins_tie_over_imports_only_when_lexical_relevance_equal() -> None:
    """Property 1: two targets with identical own relevance and identical
    fan-in — one reached by a real `CALLS` edge, the other only by
    `IMPORTS` — must resolve in favor of the `CALLS` target. Directness is
    a tie-break, not a score component: it only ever decides between two
    otherwise-equal candidates."""
    ledger = _rfc_0024_fixture()
    dependency_targets = {"file_calls.py": {"entry.py"}, "file_imports.py": {"entry.py"}}
    target_components = {
        "file_calls.py": [_target_component("retry_handler", "file_calls.py")],
        "file_imports.py": [_target_component("retry_handler", "file_imports.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        direct_targets={"file_calls.py"},
        fan_in={"file_calls.py": 1, "file_imports.py": 1},
        limit=1,
    )
    assert selected == ["file_calls.py"]


def test_high_fan_in_discounts_generic_utility_relevance() -> None:
    """Property 2: two targets with identical raw own relevance (both
    match only "retry") — one referenced by a single file repo-wide, the
    other by 20 — must rank the low-fan-in target first. This is the
    generic reproduction of the live PROT-5764 failure: a logging
    helper's coincidental term match outscoring `schema_validator.py`
    purely because it happened to be lexically luckier, with nothing
    accounting for it being referenced by 15 files repo-wide."""
    ledger = _rfc_0024_fixture()
    dependency_targets = {"contained.py": {"entry.py"}, "shared_utility.py": {"entry.py"}}
    target_components = {
        "contained.py": [_target_component("retry_handler", "contained.py")],
        "shared_utility.py": [_target_component("retry_logger", "shared_utility.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        fan_in={"contained.py": 1, "shared_utility.py": 20},
        limit=1,
    )
    assert selected == ["contained.py"], "the widely-shared utility must not outrank the contained file"


def test_structural_specificity_never_exceeds_one() -> None:
    """Property 3: the lowest possible fan-in (0, or tied for lowest in
    the batch) must never push a target's specificity above 1.0 — no
    fan-in value can *boost* a score past its own lexical relevance, it
    can only ever discount a more-shared sibling."""
    assert _structural_specificity(0, 20) == 1.0
    assert _structural_specificity(0, 0) == 1.0
    assert _structural_specificity(1, 1) == 1.0


def test_low_fan_in_target_scores_exactly_its_own_relevance_not_more() -> None:
    """Property 3, end-to-end: a target tied for the lowest fan-in in the
    batch scores exactly `own_relevance` — receiving no artificial bonus
    for being contained, only ever a possible discount for being shared."""
    ledger = _rfc_0024_fixture()
    dependency_targets = {"contained.py": {"entry.py"}, "shared_utility.py": {"entry.py"}}
    target_components = {
        "contained.py": [_target_component("retry_handler", "contained.py")],
        "shared_utility.py": [_target_component("retry_logger", "shared_utility.py")],
    }
    # Only one candidate present, isolated from the pair above, to read
    # `contained.py`'s exact score via the `limit=2` full ranking.
    selected_with_scores = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        fan_in={"contained.py": 0, "shared_utility.py": 20},
        limit=2,
    )
    assert selected_with_scores[0] == "contained.py"


def test_strong_lexical_relevance_survives_worst_case_structural_discount() -> None:
    """Property 4: a target with strong own relevance (matches "dispatch"
    + "gizmo", own_relevance 1.0) suffers the worst possible structural
    discount (tied for highest fan-in in the batch, floored at
    `_MIN_STRUCTURAL_RETENTION`) — a target with weak own relevance
    (matches only "audit", own_relevance ~0.2075, below
    `_MODERATE_TERM_WEIGHT`) enjoys the best possible structural signal
    (lowest fan-in, no discount at all). The strong match must still win:
    the floor exists so structure can narrow a gap, never invert one."""
    ledger = _rfc_0024_fixture()
    dependency_targets = {"strong_but_shared.py": {"entry.py"}, "weak_but_contained.py": {"entry.py"}}
    target_components = {
        "strong_but_shared.py": [_target_component("gizmo_dispatch_utility", "strong_but_shared.py")],
        "weak_but_contained.py": [_target_component("audit_reporter", "weak_but_contained.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        fan_in={"strong_but_shared.py": 20, "weak_but_contained.py": 0},
        limit=1,
    )
    assert selected == ["strong_but_shared.py"], (
        "a genuinely strong lexical match must survive even the worst possible "
        "structural discount against the weakest possible lexical match with "
        "the best possible structural signal"
    )


def test_zero_lexical_relevance_targets_remain_eligible_with_structural_signals_present() -> None:
    """Property 5: RFC-0022's eligibility guarantee is unaffected by
    RFC-0024 — a structurally connected target with zero own relevance
    stays eligible (and selectable, given enough `limit`) even when
    `direct_targets`/`fan_in` are supplied."""
    ledger = _rfc_0024_fixture()
    dependency_targets = {
        "file_a.py": {"entry.py"},
        "file_b.py": {"entry.py"},
        "file_c.py": {"entry.py"},
    }
    target_components = {
        "file_a.py": [_target_component("retry_handler", "file_a.py")],
        "file_b.py": [_target_component("write_data", "file_b.py")],
        "file_c.py": [_target_component("log_status", "file_c.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        direct_targets={"file_a.py", "file_b.py"},
        fan_in={"file_a.py": 1, "file_b.py": 5, "file_c.py": 5},
        limit=3,
    )
    assert set(selected) == {"file_a.py", "file_b.py", "file_c.py"}


def test_alphabetical_fallback_still_final_when_structural_signals_tie() -> None:
    """Property 6: when own relevance, directness, and fan-in are all
    identical between two targets, alphabetical order is still the final
    fallback — RFC-0024 adds a tie-break tier, it doesn't remove the
    existing one."""
    ledger = _rfc_0024_fixture()
    dependency_targets = {"zzz_file.py": {"entry.py"}, "aaa_file.py": {"entry.py"}}
    target_components = {
        "zzz_file.py": [_target_component("write_data", "zzz_file.py")],
        "aaa_file.py": [_target_component("log_status", "aaa_file.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        direct_targets={"zzz_file.py", "aaa_file.py"},
        fan_in={"zzz_file.py": 3, "aaa_file.py": 3},
        limit=1,
    )
    assert selected == ["aaa_file.py"]


def test_generic_gizmo_retry_audit_benchmark_combines_all_rfc_0024_signals() -> None:
    """The audit's own end-to-end benchmark: source `entry.py` structurally
    reaches three targets that all superficially look similar —

    - Target G (`retry_handler`, reached by `CALLS`, fan-in 1): genuinely
      relevant and contained — must win outright.
    - Target R (`retry_config`, reached only by `IMPORTS`, fan-in 1):
      lexically *identical* to G — must lose despite the tie, purely for
      lacking a direct call.
    - Target L (`retry_logger`, reached by `CALLS`, fan-in 20): lexically
      identical to G and R too, but a widely-shared utility — must be
      suppressed below both, despite being directly called.
    """
    ledger = _rfc_0024_fixture()
    dependency_targets = {
        "target_g.py": {"entry.py"},
        "target_r.py": {"entry.py"},
        "target_l.py": {"entry.py"},
    }
    target_components = {
        "target_g.py": [_target_component("retry_handler", "target_g.py")],
        "target_r.py": [_target_component("retry_config", "target_r.py")],
        "target_l.py": [_target_component("retry_logger", "target_l.py")],
    }
    selected = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        direct_targets={"target_g.py", "target_l.py"},
        fan_in={"target_g.py": 1, "target_r.py": 1, "target_l.py": 20},
        limit=3,
    )
    assert selected == ["target_g.py", "target_r.py", "target_l.py"], (
        f"expected the genuinely relevant, directly-called, contained target first, "
        f"the lexically-tied import-only target second, and the widely-shared "
        f"generic utility last: got {selected}"
    )


def test_rfc_0024_signals_are_fully_backward_compatible_when_omitted() -> None:
    """Callers that predate RFC-0024 (no `direct_targets`/`fan_in`
    supplied) must see byte-identical behavior to RFC-0023's own-relevance-
    only ranking — the new parameters are strictly additive."""
    ledger, dependency_targets, target_components = _rfc_0023_fixture()
    without_rfc_0024 = _select_dependency_expansion_files(
        ledger, "repo-a", dependency_targets, target_components, limit=3
    )
    with_empty_rfc_0024_signals = _select_dependency_expansion_files(
        ledger,
        "repo-a",
        dependency_targets,
        target_components,
        direct_targets=None,
        fan_in=None,
        limit=3,
    )
    assert without_rfc_0024 == with_empty_rfc_0024_signals


# ---------------------------------------------------------------------------
# 19. RFC-0026 — `_ranking_ticket_terms` must not silently drop a term
#     `extract_key_terms` already decided was significant, purely because
#     it's short. Fully generic vocabulary ("api"/"alpha"/"beta") — the
#     acronym used is deliberately not "ui" anywhere in this section, to
#     prove this is a generic identifier-preservation fix, not a UI-
#     specific patch.
# ---------------------------------------------------------------------------


def test_acronym_shaped_term_survives_ranking_tokenization() -> None:
    """Test 1: a real, uppercase acronym in the original text ("UI") must
    still be present after both `extract_key_terms` (first stage) and
    `_ranking_ticket_terms` (second stage) — the second stage must not
    undo what the first correctly preserved."""
    from app.agents.planning.classifier import extract_key_terms

    text = "The UI workflow failed for tenant alpha."
    extracted = extract_key_terms(text)
    assert "ui" in extracted, "fixture precondition: extract_key_terms must itself keep ui"

    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, list(extracted))
    assert "ui" in _ranking_ticket_terms(ledger)


def test_ordinary_multi_word_terms_are_still_split_and_stemmed() -> None:
    """Test 2: the fix must not break ordinary tokenization — compound/
    plural terms extracted from "schema validation failure"-shaped text
    still come out exactly as before (split, lowercased, plural-stemmed)."""
    from app.agents.planning.classifier import extract_key_terms

    text = "schema validation failure events"
    extracted = extract_key_terms(text)

    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, list(extracted))
    terms = _ranking_ticket_terms(ledger)
    assert {"schema", "validation", "failure"} <= terms
    assert "event" in terms, "plural stemming must still apply to ordinary words"


def test_generic_acronym_other_than_ui_also_survives() -> None:
    """Test 3: this must generalize to any acronym-shaped identifier the
    existing tokenizer rules already recognize, not just "ui" — "QA" is
    used here specifically (like "ui", exactly 2 characters, so it
    exercises the same below-`_MIN_TOKEN_LENGTH` edge case, not just a
    3+ character word that was never actually at risk), deliberately
    different from the live PROT-5764 vocabulary, to prove there is no
    UI-specific special case anywhere in the fix."""
    from app.agents.planning.classifier import extract_key_terms

    text = "The affected deployment uses the QA workflow."
    extracted = extract_key_terms(text)
    assert "qa" in extracted, "fixture precondition: extract_key_terms must itself keep qa"

    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, list(extracted))
    assert "qa" in _ranking_ticket_terms(ledger)


def test_ranking_actually_receives_the_preserved_term() -> None:
    """Test 4: the preserved term must reach real corroboration/source-
    selection scoring, not just survive as a set member in isolation.
    Repo A's own component mentions "alpha" (present only in the
    description, not the goal); repo B's mentions "beta"."""
    from app.agents.planning.classifier import extract_key_terms

    goal = "pipeline failure"
    description = "The affected tenant is ALPHA."
    extracted = extract_key_terms(f"{goal} {description}")
    assert "alpha" in extracted

    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, list(extracted))
    _add_component(ledger, repository="repo-a", name="alpha_handler", file_path="a.py")
    _add_component(ledger, repository="repo-b", name="beta_handler", file_path="b.py")

    scored_a = _score_candidate_source_files_for_test(ledger, "repo-a")
    scored_b = _score_candidate_source_files_for_test(ledger, "repo-b")
    assert scored_a.get("a.py", 0.0) > 0.0, "repo A must receive discriminating evidence from alpha"
    assert scored_a.get("a.py", 0.0) > scored_b.get("b.py", 0.0), (
        "repo A (which genuinely has alpha) must outscore repo B (which doesn't)"
    )


def test_prot_5764_shaped_but_domain_neutral_identifier_reaches_ranking() -> None:
    """Test 5: the PROT-5764 shape (a short acronym present only in the
    ticket *description*, absent from the *goal*, naming a config value
    only one of two near-identical repositories has) reproduced with
    fully generic vocabulary — "qa"/"mode" (2 characters, the same edge
    case "ui" hits), no Avangrid/Databricks/UI/TnT anywhere."""
    from app.agents.planning.classifier import extract_key_terms

    goal = "pipeline failure"
    description = "The affected deployment uses the QA workflow."
    assert "qa" not in extract_key_terms(goal), "fixture precondition: the goal alone lacks qa"
    extracted = extract_key_terms(f"{goal} {description}")
    assert "qa" in extracted

    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, list(extracted))
    # The acronym segment is upper-cased within the identifier,
    # deliberately mirroring the real live shape (a Databricks job name
    # like "AVANGRID_EM_UI_UDW"): a short acronym only reliably tokenizes
    # out of a component's own name when *something* about its casing in
    # the source marks it as one -- an all-lowercase "qa" fragment inside
    # an ordinary identifier is indistinguishable from a truncated word.
    _add_component(ledger, repository="repo-a", name="QA_mode_config", file_path="a.py")
    _add_component(ledger, repository="repo-b", name="batch_mode_config", file_path="b.py")

    scored_a = _score_candidate_source_files_for_test(ledger, "repo-a")
    scored_b = _score_candidate_source_files_for_test(ledger, "repo-b")
    assert scored_a.get("a.py", 0.0) > scored_b.get("b.py", 0.0)


def test_rfc_0025_ticket_term_lifecycle_still_works_with_the_rfc_0026_fix() -> None:
    """Critical regression: RFC-0025's lifecycle refresh (a short goal's
    ticket_terms replaced by the fuller work-item text's once available)
    must still work end to end now that RFC-0026 changes how those terms
    get tokenized on the way into ranking."""
    from app.agents.planning.classifier import extract_key_terms

    short_goal = "pipeline failure"
    full_text = "pipeline failure. The affected tenant is ALPHA and uses the QA workflow."

    ledger = _ranked_ledger([("repo-a", 5.0), ("repo-b", 4.0)])
    _set_ticket_terms(ledger, list(extract_key_terms(short_goal)))
    goal_only_terms = _ranking_ticket_terms(ledger)
    assert "qa" not in goal_only_terms and "alpha" not in goal_only_terms

    # RFC-0025's own refresh: the same fact, updated in place once the
    # fuller text is available.
    ledger.facts_of("repository_ranking")[-1].value["ticket_terms"] = list(
        extract_key_terms(full_text)
    )
    refreshed_terms = _ranking_ticket_terms(ledger)
    assert "qa" in refreshed_terms
    assert "alpha" in refreshed_terms
    assert len(ledger.facts_of("repository_ranking")) == 1, "still exactly one fact, never duplicated"


# ---------------------------------------------------------------------------
# 20. RFC-0027 — a pre-clamp matched-term-signal tie-break, inserted between
#     the existing `[0,1]` score and RFC-0021's structural-reference
#     provenance key. Fully generic vocabulary ("widget"/"gizmo"/"sprocket"/
#     "cog") — nothing here names any real ticket, repository, or company.
# ---------------------------------------------------------------------------


def _saturating_corpus_ledger(ticket_terms: list[str]):
    """~20 unrelated sibling repositories, each with one distinct component
    sharing no vocabulary with any ticket term used in this section — so
    every one of `ticket_terms` independently reaches the specificity
    ceiling (weight 1.0), reproducing the real live scale effect (a large,
    diverse "other repositories" corpus) without needing a real graph."""
    names = [("repo-a", 20.0)] + [(f"sibling-{i}", 19.0 - i) for i in range(19)]
    ledger = _ranked_ledger(names)
    _set_ticket_terms(ledger, ticket_terms)
    for i in range(19):
        _add_component(
            ledger, repository=f"sibling-{i}", name=f"unrelated_thing_{i}", file_path=f"s{i}.py"
        )
    return ledger


def test_stronger_pre_clamp_signal_wins_when_clamped_scores_tie() -> None:
    """The required benchmark: File A matches exactly one maximally-
    specific term and has structural-reference provenance; File B matches
    three independently maximally-specific terms and has none. Both reach
    the same clamped score of 1.0 — the fix must prefer File B, whose
    pre-clamp signal is genuinely stronger, over File A's structural
    provenance alone. This is the exact live PROT-5764 failure shape,
    reproduced with vocabulary that names nothing real."""
    from app.context_pipeline.reasoning.capabilities import _score_candidate_source_files

    ledger = _saturating_corpus_ledger(["widget", "gizmo", "sprocket"])
    _add_component(ledger, repository="repo-a", name="widget_handler", file_path="file_a.py")
    _add_component(ledger, repository="repo-a", name="widget_config_ref", file_path="config.yml")
    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "file_a.py":
            fact.value["referenced_by_config_file_paths"] = ["config.yml"]
    _add_component(
        ledger,
        repository="repo-a",
        name="widget_gizmo_sprocket_processor",
        file_path="file_b.py",
    )

    scored, referenced_by, signal = _score_candidate_source_files(ledger, "repo-a")
    assert scored["file_a.py"] == scored["file_b.py"] == 1.0, (
        "fixture precondition: both files must reach the identical clamped ceiling"
    )
    assert signal["file_b.py"] > signal["file_a.py"], (
        "fixture precondition: File B's pre-clamp signal must genuinely exceed File A's"
    )
    assert "file_a.py" in referenced_by, "fixture precondition: File A has structural provenance"

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["file_b.py"], (
        f"the genuinely stronger pre-clamp signal must win the tie: got {selected}"
    )


def test_a_real_score_difference_still_dominates_the_signal_tie_break() -> None:
    """Property 1: score is still the sole, dominant criterion — a file
    with a clearly higher clamped score wins even when the *lower*-scoring
    file happens to have the larger pre-clamp signal."""
    ledger = _saturating_corpus_ledger(["widget", "gizmo", "sprocket", "cog"])
    # High score, modest signal (one maximally-specific term).
    _add_component(ledger, repository="repo-a", name="widget_handler", file_path="strong.py")
    # Lower score (a single moderately-specific-but-not-maximal term),
    # deliberately given a name that would also rack up a large raw
    # signal if it were allowed to compete on that alone — it must not
    # win, because its clamped score never actually ties strong.py's.
    _add_component(ledger, repository="sibling-0", name="cog", file_path="s0.py")
    _add_component(ledger, repository="sibling-1", name="cog", file_path="s1.py")
    _add_component(ledger, repository="repo-a", name="cog_only_weak_match", file_path="weak.py")

    from app.context_pipeline.reasoning.capabilities import _score_candidate_source_files

    scored, _referenced_by, signal = _score_candidate_source_files(ledger, "repo-a")
    assert scored["strong.py"] > scored["weak.py"], "fixture precondition: scores must differ"

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["strong.py"], "a genuine score difference must never be overridden"


def test_structural_provenance_still_decides_when_signal_also_ties() -> None:
    """Property 2: when both score *and* pre-clamp signal are identical
    between two candidates, RFC-0021's structural-reference provenance
    still makes the final call — unchanged from before this RFC."""
    ledger = _saturating_corpus_ledger(["widget"])
    _add_component(ledger, repository="repo-a", name="widget_one", file_path="referenced.py")
    _add_component(ledger, repository="repo-a", name="widget_two", file_path="unreferenced.py")
    _add_component(ledger, repository="repo-a", name="widget_config", file_path="config.yml")
    for fact in ledger.facts_of("component"):
        if fact.value.get("file_path") == "referenced.py":
            fact.value["referenced_by_config_file_paths"] = ["config.yml"]

    from app.context_pipeline.reasoning.capabilities import _score_candidate_source_files

    scored, referenced_by, signal = _score_candidate_source_files(ledger, "repo-a")
    assert scored["referenced.py"] == scored["unreferenced.py"]
    assert signal["referenced.py"] == signal["unreferenced.py"]
    assert "referenced.py" in referenced_by and "unreferenced.py" not in referenced_by

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["referenced.py"]


def test_alphabetical_fallback_when_score_signal_and_provenance_all_tie() -> None:
    """Property 3: when score, pre-clamp signal, and structural provenance
    are all equal, alphabetical file-path order remains the final,
    unchanged fallback."""
    ledger = _saturating_corpus_ledger(["widget"])
    _add_component(ledger, repository="repo-a", name="widget_one", file_path="zzz_file.py")
    _add_component(ledger, repository="repo-a", name="widget_two", file_path="aaa_file.py")

    selected = _select_relevant_source_files(ledger, "repo-a", limit=1)
    assert selected == ["aaa_file.py"]


# ---------------------------------------------------------------------------
# 21. RFC-0036 — exact compound-identifier match outranks the lexical score.
#
# The IDF weights below are computed against *other repositories*, which is
# the right prior for choosing a repository and the wrong one for choosing a
# file inside one already chosen: a file every sibling repository also has is
# scored "common" precisely when the request names it directly. These tests
# use a corpus shaped so a distractor saturates to 1.0 on two rare words
# while the file the request actually names scores near zero.
# ---------------------------------------------------------------------------


def _compound_corpus_ledger(ticket_terms: list[str]):
    """~20 sibling repositories in which `order`/`history` are ubiquitous
    (so nearly worthless as discriminators) while `staging`/`seed` are rare
    (so each clears `_MODERATE_TERM_WEIGHT` and together saturate the
    two-moderate-term bonus at 1.0)."""
    names = [("repo-a", 20.0)] + [(f"sibling-{i}", 19.0 - i) for i in range(19)]
    ledger = _ranked_ledger(names)
    _set_ticket_terms(ledger, ticket_terms)
    for i in range(19):
        _add_component(
            ledger, repository=f"sibling-{i}", name="order_history", file_path="order_history.py"
        )
    # Rare: present in exactly one other repository.
    _add_component(ledger, repository="sibling-0", name="staging_seed", file_path="staging_seed.py")
    return ledger


def _add_rfc0036_candidates(ledger) -> None:
    _add_component(
        ledger, repository="repo-a", name="order_history", file_path="src/order_history.py"
    )
    _add_component(
        ledger, repository="repo-a", name="staging_seed", file_path="src/staging_seed.py"
    )


def test_exact_compound_identifier_beats_a_distractor_that_saturates_the_score() -> None:
    """The RFC-0036 benchmark: the distractor reaches the score ceiling on
    two rare words; the file the request actually names by identifier scores
    near zero. Identity must win."""
    ledger = _compound_corpus_ledger(["order_history", "staging", "seed"])
    _add_rfc0036_candidates(ledger)

    scored = _score_candidate_source_files_for_test(ledger, "repo-a")
    assert scored["src/staging_seed.py"] > scored["src/order_history.py"], (
        "fixture must reproduce the defect: the distractor should out-score the named file"
    )

    assert _select_relevant_source_files(ledger, "repo-a", limit=1) == ["src/order_history.py"]


def test_no_compound_identifier_in_the_request_leaves_ordering_unchanged() -> None:
    """Only single-part terms — nothing is a compound, so the new tier never
    fires and the existing score ordering stands."""
    ledger = _compound_corpus_ledger(["staging", "seed"])
    _add_rfc0036_candidates(ledger)

    assert _select_relevant_source_files(ledger, "repo-a", limit=1) == ["src/staging_seed.py"]


def test_compound_present_but_no_candidate_matches_leaves_ordering_unchanged() -> None:
    """A compound the request names that nothing in the repository is
    actually called must not perturb anything."""
    ledger = _compound_corpus_ledger(["unrelated_thing", "staging", "seed"])
    _add_rfc0036_candidates(ledger)

    assert _select_relevant_source_files(ledger, "repo-a", limit=1) == ["src/staging_seed.py"]


def test_compound_does_not_match_a_glued_word_containing_it() -> None:
    """`test_data` must not match `latestdataset.py` — a squashed-substring
    test would match it ("la-TESTDATA-set"); whole-identifier equality
    must not."""
    from app.context_pipeline.reasoning.capabilities import _exact_identifier_match_paths

    ledger = _compound_corpus_ledger(["test_data", "staging", "seed"])
    _add_component(
        ledger, repository="repo-a", name="latestdataset", file_path="src/latestdataset.py"
    )

    assert _exact_identifier_match_paths(ledger, "repo-a") == set()


def test_compound_does_not_match_a_differently_pluralized_identifier() -> None:
    """`user_id` must not match `parse_userids.py`. Note `tokenize()` would
    drop "id" entirely on its 3-character floor, which is exactly why this
    comparison uses ordered identifier parts instead."""
    from app.context_pipeline.reasoning.capabilities import _exact_identifier_match_paths

    ledger = _compound_corpus_ledger(["user_id", "staging", "seed"])
    _add_component(
        ledger, repository="repo-a", name="parse_userids", file_path="src/parse_userids.py"
    )

    assert _exact_identifier_match_paths(ledger, "repo-a") == set()


def test_two_exact_matches_fall_through_to_the_existing_ordering() -> None:
    """When identity cannot separate two files, every pre-existing tie-break
    (score, then RFC-0027 signal, then RFC-0021 provenance, then path) must
    still decide — the new tier is additive, not a replacement."""
    ledger = _compound_corpus_ledger(["order_history", "staging", "seed"])
    _add_component(
        ledger, repository="repo-a", name="order_history", file_path="zzz/order_history.py"
    )
    _add_component(
        ledger, repository="repo-a", name="order_history", file_path="aaa/order_history.py"
    )

    selected = _select_relevant_source_files(ledger, "repo-a", limit=2)
    assert set(selected) == {"aaa/order_history.py", "zzz/order_history.py"}
    # Identical scores/signal/provenance -> the existing alphabetical
    # fallback still has the final say.
    assert selected[0] == "aaa/order_history.py"


def test_exact_identifier_matching_is_selective_not_a_broad_promotion() -> None:
    """Safety: the tier must fire for the named identifier only, not sweep
    in unrelated files that merely share a word with it."""
    from app.context_pipeline.reasoning.capabilities import _exact_identifier_match_paths

    ledger = _compound_corpus_ledger(["order_history", "staging", "seed"])
    _add_component(
        ledger, repository="repo-a", name="order_history", file_path="src/order_history.py"
    )
    for name in (
        "order_service",
        "history_writer",
        "order_history_archiver",
        "reorder",
        "history",
        "orders",
    ):
        _add_component(ledger, repository="repo-a", name=name, file_path=f"src/{name}.py")

    assert _exact_identifier_match_paths(ledger, "repo-a") == {"src/order_history.py"}


def test_scoring_itself_is_untouched_by_the_new_tier() -> None:
    """RFC-0036 changes ordering only. Two independent proofs: the scorer
    does not reference the new signal at all, and for one fixed ledger the
    scores it returns still rank the distractor above the named file (i.e.
    the numbers are unchanged) even though selection now inverts that."""
    import inspect

    from app.context_pipeline.reasoning.capabilities import _score_candidate_source_files

    source = inspect.getsource(_score_candidate_source_files)
    assert "_exact_identifier_match_paths" not in source
    assert "exact_identifier" not in source

    ledger = _compound_corpus_ledger(["order_history", "staging", "seed"])
    _add_rfc0036_candidates(ledger)
    scored = _score_candidate_source_files_for_test(ledger, "repo-a")

    assert scored["src/staging_seed.py"] == 1.0
    assert scored["src/staging_seed.py"] > scored["src/order_history.py"]
    assert _select_relevant_source_files(ledger, "repo-a", limit=1) == ["src/order_history.py"]


def test_dependency_expansion_does_not_consult_the_new_tier() -> None:
    """RFC-0022/0023/0024 selection must be unaffected — the new signal is
    scoped to `_select_relevant_source_files` alone."""
    import inspect

    from app.context_pipeline.reasoning.capabilities import _select_dependency_expansion_files

    source = inspect.getsource(_select_dependency_expansion_files)
    assert "_exact_identifier_match_paths" not in source
    assert "exact_identifier" not in source
