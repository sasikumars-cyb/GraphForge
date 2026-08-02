"""ADR 0018 — the Frontier Hypothesis Generator: pure unit tests against a
fake `ILLMProvider`, no network, no API key, no real model call."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.base import LLMRequestOptions, LLMResponse
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.indexer.hypotheses.llm_generator import FrontierHypothesisGenerator
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance

pytestmark = pytest.mark.asyncio


class _FakeLLMProvider(ILLMProvider):
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.last_user_prompt: str | None = None

    async def complete(
        self, *, system_prompt: str, user_prompt: str, options: LLMRequestOptions | None = None
    ) -> LLMResponse:
        self.last_user_prompt = user_prompt
        return LLMResponse(text=self._response_text)

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        raise NotImplementedError


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:repo-1:abc:test",
        pack_version="v1",
        run_id="pack:repo-1:abc:test",
    )


def _readme_item() -> EvidenceItem:
    return EvidenceItem(
        id="evidence:repo-1:repo:repository_readme:README.md",
        kind="repository_readme",
        source_type="documentation",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="documentation", locator="README.md"
        ),
        raw_value="This service owns its own Postgres database for orders.",
        provenance=_provenance(),
    )


def _pack(items: list[EvidenceItem]) -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack:repo-1:abc:test",
        repository_id="repo-1",
        commit_sha="abc",
        schema_version="v1",
        items=tuple(items),
    )


async def test_generates_hypothesis_from_valid_response() -> None:
    readme = _readme_item()
    response = json.dumps(
        [
            {
                "relationship_type": "OWNS_DATABASE",
                "explanation": "README explicitly says it owns a Postgres database.",
                "confidence": 0.8,
                "evidence_item_ids": [readme.id],
            }
        ]
    )
    generator = FrontierHypothesisGenerator(_FakeLLMProvider(response))

    hypotheses = await generator.generate(_pack([readme]))

    assert len(hypotheses) == 1
    hyp = hypotheses[0]
    assert hyp.relationship_type == "OWNS_DATABASE"
    assert hyp.source_entity == "repo-1:repository"
    assert hyp.target_entity == "repo-1:capability:database"
    assert hyp.evidence_refs == (readme.id,)
    assert hyp.generator_confidence == 0.8


async def test_strips_markdown_fence() -> None:
    readme = _readme_item()
    response = (
        "```json\n"
        + json.dumps(
            [
                {
                    "relationship_type": "OWNS_DATABASE",
                    "explanation": "explained",
                    "confidence": 0.5,
                    "evidence_item_ids": [readme.id],
                }
            ]
        )
        + "\n```"
    )
    generator = FrontierHypothesisGenerator(_FakeLLMProvider(response))

    hypotheses = await generator.generate(_pack([readme]))

    assert len(hypotheses) == 1


async def test_rejects_unknown_relationship_type() -> None:
    readme = _readme_item()
    response = json.dumps(
        [
            {
                "relationship_type": "TOTALLY_MADE_UP_TYPE",
                "explanation": "explained",
                "confidence": 0.5,
                "evidence_item_ids": [readme.id],
            }
        ]
    )
    generator = FrontierHypothesisGenerator(_FakeLLMProvider(response))

    hypotheses = await generator.generate(_pack([readme]))

    assert hypotheses == []


async def test_rejects_hallucinated_evidence_id() -> None:
    readme = _readme_item()
    response = json.dumps(
        [
            {
                "relationship_type": "OWNS_DATABASE",
                "explanation": "explained",
                "confidence": 0.5,
                "evidence_item_ids": ["evidence:does-not-exist"],
            }
        ]
    )
    generator = FrontierHypothesisGenerator(_FakeLLMProvider(response))

    hypotheses = await generator.generate(_pack([readme]))

    assert hypotheses == []


async def test_rejects_hypothesis_with_no_evidence_ids() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "OWNS_DATABASE",
                "explanation": "explained",
                "confidence": 0.5,
                "evidence_item_ids": [],
            }
        ]
    )
    generator = FrontierHypothesisGenerator(_FakeLLMProvider(response))

    hypotheses = await generator.generate(_pack([_readme_item()]))

    assert hypotheses == []


async def test_empty_array_response_yields_no_hypotheses() -> None:
    generator = FrontierHypothesisGenerator(_FakeLLMProvider("[]"))

    hypotheses = await generator.generate(_pack([_readme_item()]))

    assert hypotheses == []


async def test_malformed_json_response_yields_no_hypotheses_not_a_crash() -> None:
    generator = FrontierHypothesisGenerator(_FakeLLMProvider("not json at all"))

    hypotheses = await generator.generate(_pack([_readme_item()]))

    assert hypotheses == []


async def test_out_of_range_confidence_is_dropped_not_the_whole_hypothesis() -> None:
    readme = _readme_item()
    response = json.dumps(
        [
            {
                "relationship_type": "OWNS_DATABASE",
                "explanation": "explained",
                "confidence": 5.0,
                "evidence_item_ids": [readme.id],
            }
        ]
    )
    generator = FrontierHypothesisGenerator(_FakeLLMProvider(response))

    hypotheses = await generator.generate(_pack([readme]))

    assert len(hypotheses) == 1
    assert hypotheses[0].generator_confidence is None


async def test_curated_prompt_cites_evidence_ids() -> None:
    readme = _readme_item()
    provider = _FakeLLMProvider("[]")
    generator = FrontierHypothesisGenerator(provider)

    await generator.generate(_pack([readme]))

    assert provider.last_user_prompt is not None
    assert readme.id in provider.last_user_prompt
