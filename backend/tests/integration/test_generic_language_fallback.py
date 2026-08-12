"""RFC-07 end-to-end: a repository in a language GraphForge has no
`ILanguageParser` (and no `detect_language()` rule) for at all - proof
that a new language can enter the graph through registration/configuration
(the `enable_generic_language_fallback` flag) and the existing Evidence ->
Hypothesis -> Validation -> Confidence -> Knowledge -> Materializer
pipeline, without one line of new Neo4j or `graph/builder.py` code.

The LLM is mocked throughout (`_FakeLLMProvider`, patched onto
`create_llm_provider`) - no live network call, no API key, fully
deterministic CI. Real Postgres, real Neo4j, a real local-git clone.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.base import LLMRequestOptions, LLMResponse
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.core.config import get_settings
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services import indexing_service
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


class _FakeLLMProvider(ILLMProvider):
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    async def complete(
        self, *, system_prompt: str, user_prompt: str, options: LLMRequestOptions | None = None
    ) -> LLMResponse:
        return LLMResponse(text=self._response_text)

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        raise NotImplementedError


def _enable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_GENERIC_LANGUAGE_FALLBACK", "true")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    yield
    get_settings.cache_clear()


async def _make_user(db: AsyncSession) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com", full_name="Test User")
    db.add(user)
    await db.flush()
    return user


async def _make_repository(db: AsyncSession, user: User, html_url: str) -> Repository:
    repo = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="go-widgets",
        full_name="acme/go-widgets",
        default_branch="main",
        html_url=html_url,
    )
    db.add(repo)
    await db.flush()
    return repo


@pytest.fixture
async def graph_repository() -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo


async def test_disabled_by_default_still_returns_unsupported(
    db_session: AsyncSession, go_git_repo: Path
) -> None:
    """Zero behavior change when the flag is off (the default) - a
    language with no parser still fails exactly as it always has."""
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    with pytest.raises(indexing_service.UnsupportedRepositoryError):
        await indexing_service.run_indexing(db_session, repo)


async def test_new_language_flows_through_evidence_to_materialized_graph(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full RFC-07 claim, proven step by step against a real run:
    1. Language detection finds no parser for Go.
    2. The generic fallback is invoked (flag enabled).
    3. Structured evidence (Repository + SourceFile nodes) is produced.
    4. A hypothesis is proposed by the (mocked) LLM generator.
    5. It is validated - `generic_structural.py`'s two generic validators
       both confirm it (both endpoints are real, discovered entities; the
       citing evidence literally mentions the target), reaching VERIFIED.
    6. Confidence is computed.
    7. Knowledge (a `KnowledgeRelationship`) is persisted.
    8. The Materializer projects it into Neo4j - SourceFile nodes are
       present (deterministic evidence, always materializes), and the
       IMPORTS edge is now ALSO present - the exact limitation this RFC-07
       cycle set out to fix (previously every generic hypothesis was stuck
       at CANDIDATE for lack of any applicable validator, and this edge
       would have been absent). `test_hallucinated_file_reference_never_
       reaches_the_graph` (below) proves the opposite case - a claim about
       an entity that was never actually discovered - still correctly
       never reaches the graph, so promotion becoming real did not weaken
       the trust boundary against hallucination.
    """
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    llm_response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": f"ev:source:{repo.id}:source-file:main.go",
                "target_file_id": f"ev:source:{repo.id}:source-file:orders/orders.go",
                "explanation": "main.go imports example.com/widgets/orders",
                "confidence": 0.92,
                "evidence_item_ids": [f"ev:source:{repo.id}:source-file:main.go"],
            }
        ]
    )

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=_FakeLLMProvider(llm_response),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1
    assert summary["generic_language_files_discovered"] >= 2  # main.go + orders/orders.go
    assert summary["materialized_nodes"] >= 3  # Repository + 2 SourceFile nodes

    graph = await graph_repository.get_full_graph(str(repo.id))
    node_ids = {n.id for n in graph.nodes}
    assert f"{repo.id}:repository" in node_ids
    assert f"{repo.id}:source-file:main.go" in node_ids
    assert f"{repo.id}:source-file:orders/orders.go" in node_ids

    source_file_nodes = [n for n in graph.nodes if "SourceFile" in n.labels]
    assert all(n.properties.get("discovery_source") == "generic_fallback" for n in source_file_nodes)

    # RFC-07 this cycle: a well-evidenced generic hypothesis now clears
    # validation and is materialized as a real graph edge.
    imports_edges = [e for e in graph.edges if e.type == "IMPORTS"]
    assert len(imports_edges) == 1

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_well_evidenced_imports_hypothesis_is_verified_and_materializes(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RFC-07's central promotion claim, post-hardening: an IMPORTS
    hypothesis whose cited evidence contains an actual import-family
    keyword co-occurring with the target's name (confirmed by
    `GenericImportEvidenceValidator`, tier 3 - relationship-level
    evidence, not merely that both files exist) reaches a promotable
    confidence and appears as a real Neo4j edge, with zero
    language-specific graph code. Also asserts against Engineering
    Memory directly (not just edge presence) that the promoted
    relationship's confidence state is genuinely `verified`/
    `highly_likely` and that its `confirming_source_types` names the
    actual validator-level evidence that grounded it - the provenance a
    "why do you believe this?" answer would point to."""
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    main_source_id = f"ev:source:{repo.id}:source-file:main.go"
    llm_response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": main_source_id,
                "target_file_id": f"ev:source:{repo.id}:source-file:orders/orders.go",
                "explanation": "main.go imports example.com/widgets/orders",
                "confidence": 0.92,
                # Cites its own content - which literally mentions "orders" -
                # so GenericEvidenceMentionValidator can independently
                # re-derive the claim, not merely trust the citation.
                "evidence_item_ids": [main_source_id],
            }
        ]
    )

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=_FakeLLMProvider(llm_response),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1

    graph = await graph_repository.get_full_graph(str(repo.id))
    imports_edges = [e for e in graph.edges if e.type == "IMPORTS"]
    assert len(imports_edges) == 1
    assert imports_edges[0].source_id == f"{repo.id}:source-file:main.go"
    assert imports_edges[0].target_id == f"{repo.id}:source-file:orders/orders.go"

    memory = EngineeringMemoryService(db_session)
    relationships = await memory.get_current_relationships(repo.id)
    imports_relationship = next(r for r in relationships if r.relationship_type == "IMPORTS")
    assert imports_relationship.confidence_state in ("verified", "highly_likely")
    assert "import_evidence" in imports_relationship.confirming_source_types

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_well_evidenced_calls_hypothesis_between_symbols_materializes(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same promotion path, but for a function-level CALLS relationship
    between two heuristically-detected `GenericSymbol` nodes
    (`Summarize` -> `countOrders`, both declared in orders/orders.go) -
    proving CALLS is representable at the symbol level, not only between
    whole files. Critically, the cited evidence (`orders.go`'s own text)
    contains a REAL call site - `countOrders()` literally appears inside
    `Summarize`'s body - so `GenericCallEvidenceValidator` confirms at
    tier 3 on relationship-level evidence, not merely because both symbols
    exist (see the adversarial counterpart,
    `test_calls_hypothesis_without_a_call_site_is_not_verified_or_materialized`,
    directly below)."""
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    orders_source_id = f"ev:source:{repo.id}:source-file:orders/orders.go"
    summarize_symbol_id = f"ev:node:{repo.id}:generic-symbol:orders/orders.go:Summarize"
    count_orders_symbol_id = f"ev:node:{repo.id}:generic-symbol:orders/orders.go:countOrders"
    llm_response = json.dumps(
        [
            {
                "relationship_type": "CALLS",
                "source_file_id": summarize_symbol_id,
                "target_file_id": count_orders_symbol_id,
                "explanation": "Summarize calls countOrders directly in its body",
                "confidence": 0.88,
                "evidence_item_ids": [orders_source_id],
            }
        ]
    )

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=_FakeLLMProvider(llm_response),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1

    graph = await graph_repository.get_full_graph(str(repo.id))
    node_ids = {n.id for n in graph.nodes}
    assert f"{repo.id}:generic-symbol:orders/orders.go:Summarize" in node_ids
    assert f"{repo.id}:generic-symbol:orders/orders.go:countOrders" in node_ids

    calls_edges = [e for e in graph.edges if e.type == "CALLS"]
    assert len(calls_edges) == 1
    assert calls_edges[0].source_id == f"{repo.id}:generic-symbol:orders/orders.go:Summarize"
    assert calls_edges[0].target_id == f"{repo.id}:generic-symbol:orders/orders.go:countOrders"

    memory = EngineeringMemoryService(db_session)
    relationships = await memory.get_current_relationships(repo.id)
    calls_relationship = next(r for r in relationships if r.relationship_type == "CALLS")
    assert calls_relationship.confidence_state in ("verified", "highly_likely")
    assert "call_site_evidence" in calls_relationship.confirming_source_types

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_calls_hypothesis_without_a_call_site_is_not_verified_or_materialized(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single most important experimental proof in this hardening
    pass:

        A exists (Summarize)
        B exists (CalculateTotal)
        LLM says A CALLS B
        BUT the cited evidence contains no actual call site for B
            -> NOT VERIFIED, NOT MATERIALIZED

    `Summarize` genuinely exists and genuinely calls `CalculateTotal` in
    the real fixture - but this hypothesis deliberately cites `main.go`
    (which mentions neither symbol at all) as its evidence, so
    `GenericCallEvidenceValidator` has nothing to confirm on. Only
    `EndpointExistenceValidator` (tier 1, weak) can confirm - by design,
    that alone can never reach `HIGHLY_LIKELY`/`VERIFIED`, so the edge
    must never appear in Neo4j."""
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    main_source_id = f"ev:source:{repo.id}:source-file:main.go"
    summarize_symbol_id = f"ev:node:{repo.id}:generic-symbol:orders/orders.go:Summarize"
    calculate_total_symbol_id = f"ev:node:{repo.id}:generic-symbol:orders/pricing.go:CalculateTotal"
    llm_response = json.dumps(
        [
            {
                "relationship_type": "CALLS",
                "source_file_id": summarize_symbol_id,
                "target_file_id": calculate_total_symbol_id,
                "explanation": "Summarize calls CalculateTotal",
                "confidence": 0.95,  # high LLM-claimed confidence - deliberately irrelevant
                # main.go mentions neither symbol - no real relationship
                # evidence, only a citation.
                "evidence_item_ids": [main_source_id],
            }
        ]
    )

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=_FakeLLMProvider(llm_response),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1

    memory = EngineeringMemoryService(db_session)
    relationships = await memory.get_current_relationships(repo.id)
    calls_relationships = [r for r in relationships if r.relationship_type == "CALLS"]
    assert calls_relationships == [] or all(
        r.confidence_state not in ("verified", "highly_likely") for r in calls_relationships
    )

    graph = await graph_repository.get_full_graph(str(repo.id))
    assert [e for e in graph.edges if e.type == "CALLS"] == []

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_hallucinated_file_reference_never_reaches_the_graph(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: the LLM references a file id that was never given to
    it (a hallucinated evidence id, not merely a hallucinated *claim* about
    a real file). `generic_language_generator._candidate_to_hypothesis`
    rejects it outright - it never becomes a `Hypothesis`, never reaches
    validation, and certainly never reaches the graph. Proves rejection is
    safe even now that promotion is real (see the two tests above) - the
    earlier version of this test suite only proved rejection when nothing
    could ever be promoted at all."""
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    llm_response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": f"ev:source:{repo.id}:source-file:main.go",
                # Never discovered - main.go does not import this file.
                "target_file_id": f"ev:source:{repo.id}:source-file:does_not_exist.go",
                "explanation": "main.go imports a file that was never given as evidence",
                "confidence": 0.99,
                "evidence_item_ids": [f"ev:source:{repo.id}:source-file:main.go"],
            }
        ]
    )

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=_FakeLLMProvider(llm_response),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1

    graph = await graph_repository.get_full_graph(str(repo.id))
    assert graph.edges == []
    node_ids = {n.id for n in graph.nodes}
    assert f"{repo.id}:source-file:does_not_exist.go" not in node_ids

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_fabricated_evidence_citation_never_reaches_the_graph(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: both endpoints are real, but the cited evidence id is
    fabricated (never in the pack) - rejected at the same generator gate,
    for the same reason: a claim's endpoints being real does not excuse
    inventing what supports it."""
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    llm_response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": f"ev:source:{repo.id}:source-file:main.go",
                "target_file_id": f"ev:source:{repo.id}:source-file:orders/orders.go",
                "explanation": "fabricated citation",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:this-id-was-never-issued"],
            }
        ]
    )

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=_FakeLLMProvider(llm_response),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1
    graph = await graph_repository.get_full_graph(str(repo.id))
    assert graph.edges == []

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_unsupported_relationship_type_never_reaches_the_graph(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: a relationship type outside the pre-approved
    vocabulary (`IMPORTS`/`CALLS`/`DEPENDS_ON`) is dropped before it ever
    becomes a `Hypothesis` - the LLM does not get to invent new graph
    semantics, matching `FrontierHypothesisGenerator`'s own
    `_CAPABILITY_TYPES` discipline."""
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    llm_response = json.dumps(
        [
            {
                "relationship_type": "OWNS",
                "source_file_id": f"ev:source:{repo.id}:source-file:main.go",
                "target_file_id": f"ev:source:{repo.id}:source-file:orders/orders.go",
                "explanation": "not an allowed relationship type",
                "confidence": 0.9,
                "evidence_item_ids": [f"ev:source:{repo.id}:source-file:main.go"],
            }
        ]
    )

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=_FakeLLMProvider(llm_response),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1
    graph = await graph_repository.get_full_graph(str(repo.id))
    assert graph.edges == []

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_unchanged_repository_skips_the_llm_call_on_the_next_run(
    db_session: AsyncSession, go_git_repo: Path, graph_repository: Neo4jGraphRepository
) -> None:
    """RFC-07's content-hash caching claim: re-running the fallback for a
    repository whose files haven't changed since the prior run must not
    re-invoke the LLM at all - `_load_prior_fingerprint` finds the
    immediately preceding pack, `_batch_unchanged` finds every file's
    `content_hash` identical, and the batch (here, the repository's one
    and only batch - well under `_BATCH_SIZE`) is skipped entirely. This
    is explicitly NOT a claim of full incremental *revalidation* - see
    `run_generic_language_fallback`'s own docstring for that distinction.
    """
    from app.indexer.hypotheses.generic_language_runner import run_generic_language_fallback

    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    class _CountingFakeLLMProvider(ILLMProvider):
        def __init__(self) -> None:
            self.call_count = 0

        async def complete(
            self, *, system_prompt: str, user_prompt: str, options: LLMRequestOptions | None = None
        ) -> LLMResponse:
            self.call_count += 1
            return LLMResponse(text="[]")

        async def analyze(self, context: AIContext) -> AIAnalysisResult:
            raise NotImplementedError

    provider = _CountingFakeLLMProvider()
    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        return_value=provider,
    ):
        await run_generic_language_fallback(
            repository_id=str(repo.id),
            commit_sha="abc123",
            repo_root=go_git_repo,
            language_label="unsupported",
            db=db_session,
        )
        first_run_call_count = provider.call_count
        assert first_run_call_count >= 1

        await run_generic_language_fallback(
            repository_id=str(repo.id),
            commit_sha="abc123",
            repo_root=go_git_repo,
            language_label="unsupported",
            db=db_session,
        )

    assert provider.call_count == first_run_call_count  # zero additional calls

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())


async def test_llm_failure_still_materializes_deterministic_file_structure(
    db_session: AsyncSession,
    go_git_repo: Path,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic evidence (file discovery) must survive an LLM
    provider failure - the generic generator failing must not discard the
    file-level structure already gathered."""
    _enable_fallback(monkeypatch)
    user = await _make_user(db_session)
    repo = await _make_repository(db_session, user, str(go_git_repo))

    with patch(
        "app.indexer.hypotheses.generic_language_generator.create_llm_provider",
        side_effect=RuntimeError("provider unreachable"),
    ):
        summary = await indexing_service.run_indexing(db_session, repo)

    assert summary["generic_language_fallback"] == 1
    graph = await graph_repository.get_full_graph(str(repo.id))
    assert any(n.id == f"{repo.id}:source-file:main.go" for n in graph.nodes)

    await graph_repository.replace_repository_graph(str(repo.id), GraphPayload())
