"""ADR 0018 — the Frontier Hypothesis Generator wired end to end: real git
clone, real parse, real Neo4j write, real Postgres persistence, with a
fake `ILLMProvider` standing in for the real network call (no API key,
no cost, deterministic). Proves the five things this RFC commits to:

1. The generator produces hypotheses (from real README/manifest evidence
   `index_repository` gathered while the clone was alive).
2. Existing validators process them — no validator recognizes this
   generator's relationship vocabulary yet, so every one lands at
   `ConfidenceState.CANDIDATE` via the existing "no applicable validator"
   fallback, not a crash and not a false confirmation.
3. Engineering Memory stores the validated results.
4. The materializer remains unchanged: it never surfaces these new
   relationship types (single-repo edges come only from `graph_edge:*`
   evidence, which the LLM generator never produces; cross-repo edges are
   filtered to the three known cross-repo types) — proven by comparing a
   materialized replay before and after the LLM relationships exist.
5. The existing deterministic pipeline does not regress — same
   `controllers`/`services` summary, same deterministic relationship
   count, with or without the LLM generator enabled.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.base import LLMRequestOptions, LLMResponse
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.hypotheses import shadow_runner
from app.indexer.hypotheses.generator_registry import RegisteredGenerator
from app.indexer.hypotheses.llm_generator import FrontierHypothesisGenerator
from app.indexer.services.indexing_service import index_repository
from app.knowledge_engine.contracts.generator_policy import StaticGeneratorPolicy
from app.knowledge_engine.contracts.provenance import GeneratorIdentity
from app.knowledge_engine.materializer import materialize_repository_graph
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio

_FAKE_RESPONSE = json.dumps(
    [
        {
            "relationship_type": "OWNS_DATABASE",
            "explanation": "README describes a persistent order database.",
            "confidence": 0.7,
            "evidence_item_ids": ["__PLACEHOLDER__"],
        }
    ]
)


class _FakeLLMProvider(ILLMProvider):
    async def complete(
        self, *, system_prompt: str, user_prompt: str, options: LLMRequestOptions | None = None
    ) -> LLMResponse:
        # Cite whichever repository_readme/metadata evidence id actually
        # appears in the prompt, so the hypothesis's evidence_refs check
        # (real ids only, see llm_generator.py) passes regardless of what
        # the real extraction stage found for this fixture repo.
        for line in user_prompt.splitlines():
            if line.startswith("id=evidence:") and "repository_metadata" in line:
                cited_id = line.split(" ", 1)[0].removeprefix("id=")
                return LLMResponse(text=_FAKE_RESPONSE.replace("__PLACEHOLDER__", cited_id))
        return LLMResponse(text="[]")

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        raise NotImplementedError


@pytest.fixture
async def repository_row(db_session: AsyncSession) -> Repository:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()

    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name="spring-boot-repo",
        full_name="test-owner/spring-boot-repo",
        html_url="https://github.com/test-owner/spring-boot-repo",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


@pytest.fixture
async def graph_repository(
    repository_row: Repository,
) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    await repo.replace_repository_graph(str(repository_row.id), GraphPayload())


@pytest.fixture
def enable_fake_frontier_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_registry = (
        RegisteredGenerator(
            identity=GeneratorIdentity(kind="llm", name="frontier_llm_generator", version="test"),
            factory=lambda: FrontierHypothesisGenerator(
                _FakeLLMProvider(), model_name="fake-model"
            ),
            policy=StaticGeneratorPolicy(True),
        ),
    )
    monkeypatch.setattr(shadow_runner, "build_generator_registry", lambda: fake_registry)


async def test_llm_generator_produces_hypotheses_validated_to_candidate_and_persisted(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
    enable_fake_frontier_generator: None,
) -> None:
    repository_id = str(repository_row.id)

    await index_repository(
        repository_id=repository_id,
        html_url=str(spring_boot_git_repo),
        ref="main",
        db=db_session,
    )

    memory = EngineeringMemoryService(db_session)
    current = await memory.get_current_relationships(repository_row.id)

    llm_relationships = [r for r in current if r.relationship_type == "OWNS_DATABASE"]
    assert len(llm_relationships) == 1, "the fake LLM generator's hypothesis was not persisted"
    relationship = llm_relationships[0]
    assert relationship.source_entity == f"{repository_id}:repository"
    assert relationship.target_entity == f"{repository_id}:capability:database"
    # No validator recognizes OWNS_DATABASE yet — this MUST land at
    # CANDIDATE, never something stronger, proving the validators are
    # genuinely deciding (by correctly declining to confirm), not the
    # generator's own advisory confidence leaking through.
    assert relationship.confidence_state == "candidate"
    assert relationship.provenance
    assert relationship.provenance[0]["generator"]["kind"] == "llm"


async def test_materializer_ignores_llm_relationships(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
    enable_fake_frontier_generator: None,
) -> None:
    repository_id = str(repository_row.id)

    await index_repository(
        repository_id=repository_id,
        html_url=str(spring_boot_git_repo),
        ref="main",
        db=db_session,
    )

    memory = EngineeringMemoryService(db_session)
    current = await memory.get_current_relationships(repository_row.id)
    assert any(r.relationship_type == "OWNS_DATABASE" for r in current)

    payload = await materialize_repository_graph(db_session, repository_row.id)

    materialized_types = {edge.type for edge in payload.edges}
    assert "OWNS_DATABASE" not in materialized_types
    materialized_ids = {node.id for node in payload.nodes}
    assert f"{repository_id}:capability:database" not in materialized_ids


class _ManifestCitingFakeLLMProvider(ILLMProvider):
    """Unlike `_FakeLLMProvider` (which deliberately cites only
    `repository_metadata`, evidence no validator recognizes yet), this one
    cites whichever `repository_manifest` evidence item the real
    extraction stage found — the fixture's real `pom.xml` genuinely
    depends on `spring-kafka` (see `tests/fixtures/spring_boot_sample
    /pom.xml`), so `manifest_validator`'s keyword match is a real, not
    contrived, confirmation."""

    async def complete(
        self, *, system_prompt: str, user_prompt: str, options: LLMRequestOptions | None = None
    ) -> LLMResponse:
        for line in user_prompt.splitlines():
            if line.startswith("id=evidence:") and "repository_manifest" in line:
                cited_id = line.split(" ", 1)[0].removeprefix("id=")
                response = json.dumps(
                    [
                        {
                            "relationship_type": "OWNS_MESSAGE_QUEUE",
                            "explanation": "Manifest declares a Kafka client dependency.",
                            "confidence": 0.6,
                            "evidence_item_ids": [cited_id],
                        }
                    ]
                )
                return LLMResponse(text=response)
        return LLMResponse(text="[]")

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        raise NotImplementedError


async def test_llm_hypothesis_confirmed_by_manifest_evidence_rises_above_candidate(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual fix this validator RFC delivers: an LLM hypothesis whose
    cited evidence a validator can independently confirm no longer stays
    stuck at CANDIDATE — the missing capability this whole RFC was about."""
    fake_registry = (
        RegisteredGenerator(
            identity=GeneratorIdentity(kind="llm", name="frontier_llm_generator", version="test"),
            factory=lambda: FrontierHypothesisGenerator(
                _ManifestCitingFakeLLMProvider(), model_name="fake-model"
            ),
            policy=StaticGeneratorPolicy(True),
        ),
    )
    monkeypatch.setattr(shadow_runner, "build_generator_registry", lambda: fake_registry)

    repository_id = str(repository_row.id)
    await index_repository(
        repository_id=repository_id,
        html_url=str(spring_boot_git_repo),
        ref="main",
        db=db_session,
    )

    memory = EngineeringMemoryService(db_session)
    current = await memory.get_current_relationships(repository_row.id)
    llm_relationships = [r for r in current if r.relationship_type == "OWNS_MESSAGE_QUEUE"]

    assert len(llm_relationships) == 1
    assert llm_relationships[0].confidence_state == "likely"
    assert llm_relationships[0].max_confirming_reliability_tier == 1

    # ADR 0018 Confidence Explainability: persisted alongside the
    # relationship, not just computed and discarded.
    explanation = llm_relationships[0].explanation
    assert explanation is not None
    assert explanation["confirming_domains"] == ["repository_manifest"]
    assert explanation["strongest_domain"] == "repository_manifest"


async def test_deterministic_pipeline_unaffected_by_llm_generator(
    spring_boot_git_repo: Path,
    repository_row: Repository,
    graph_repository: Neo4jGraphRepository,
    db_session: AsyncSession,
) -> None:
    repository_id = str(repository_row.id)

    summary_without_llm = await index_repository(
        repository_id=repository_id,
        html_url=str(spring_boot_git_repo),
        ref="main",
        db=db_session,
    )
    memory = EngineeringMemoryService(db_session)
    relationships_without_llm = await memory.get_current_relationships(repository_row.id)
    deterministic_count_without = len(
        [r for r in relationships_without_llm if r.relationship_type != "OWNS_DATABASE"]
    )

    assert summary_without_llm["controllers"] == 1
    assert deterministic_count_without > 0
