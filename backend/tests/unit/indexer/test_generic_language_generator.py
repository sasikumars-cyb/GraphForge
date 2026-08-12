"""RFC-07 — `GenericLanguageHypothesisGenerator`: pure unit tests against a
fake `ILLMProvider`, no network, no API key, no real model call. Mirrors
`test_llm_generator.py`'s pattern for `FrontierHypothesisGenerator` -
same fake-provider shape, applied to the generic-language fallback's
different (structural, not capability) vocabulary."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.base import LLMRequestOptions, LLMResponse
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.indexer.hypotheses.generic_language_generator import GenericLanguageHypothesisGenerator
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
        generator=GeneratorIdentity(kind="deterministic", name="generic_file_discovery", version="1.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:repo-1:abc:generic_file_discovery",
        pack_version="v1",
        run_id="pack:repo-1:abc:generic_file_discovery",
    )


def _source_file_item(evidence_id: str, node_id: str, path: str, content: str) -> EvidenceItem:
    return EvidenceItem(
        id=evidence_id,
        kind="source_file",
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="code", locator=path, key=node_id, commit_sha="abc"
        ),
        raw_value=content,
        provenance=_provenance(),
    )


def _pack(items: list[EvidenceItem]) -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack:repo-1:abc:generic_file_discovery",
        repository_id="repo-1",
        commit_sha="abc",
        schema_version="v1",
        items=tuple(items),
    )


_ITEM_A = _source_file_item("ev:source:a", "repo-1:source-file:a.go", "a.go", 'import "b"')
_ITEM_B = _source_file_item("ev:source:b", "repo-1:source-file:b.go", "b.go", "package b")


async def test_valid_response_produces_hypothesis() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:b",
                "explanation": "a.go literally imports package b",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a", "ev:source:b"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response), model_name="test-model")
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))

    assert len(hypotheses) == 1
    h = hypotheses[0]
    assert h.relationship_type == "IMPORTS"
    assert h.source_entity == "repo-1:source-file:a.go"
    assert h.target_entity == "repo-1:source-file:b.go"
    assert h.generator_confidence == 0.9
    assert h.provenance.generator.kind == "llm"
    assert "generic_language_llm" in h.provenance.generator.name


async def test_empty_response_produces_no_hypotheses() -> None:
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider("[]"))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert hypotheses == []


async def test_pack_with_no_source_file_items_never_calls_the_llm() -> None:
    provider = _FakeLLMProvider("[]")
    generator = GenericLanguageHypothesisGenerator(provider)
    hypotheses = await generator.generate(_pack([]))
    assert hypotheses == []
    assert provider.last_user_prompt is None


async def test_disallowed_relationship_type_is_rejected() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "OWNS_DATABASE",  # not in the allowed vocabulary
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:b",
                "explanation": "x",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert hypotheses == []


async def test_hallucinated_file_id_is_rejected() -> None:
    # The LLM naming a file id it was never given must not become a
    # hypothesis pointing at a node id that doesn't exist.
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:does-not-exist",
                "explanation": "x",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert hypotheses == []


async def test_self_referencing_relationship_is_rejected() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:a",
                "explanation": "x",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert hypotheses == []


async def test_hallucinated_evidence_ref_is_rejected() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:b",
                "explanation": "x",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:made-up-id"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert hypotheses == []


async def test_missing_explanation_is_rejected() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:b",
                "explanation": "",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert hypotheses == []


async def test_out_of_range_confidence_is_dropped_not_the_whole_hypothesis() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:b",
                "explanation": "valid explanation",
                "confidence": 5.0,  # out of [0,1]
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert len(hypotheses) == 1
    assert hypotheses[0].generator_confidence is None


async def test_malformed_json_response_produces_no_hypotheses() -> None:
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider("not json at all"))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert hypotheses == []


async def test_markdown_fenced_response_is_still_parsed() -> None:
    response = "```json\n" + json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:b",
                "explanation": "fenced but valid",
                "confidence": 0.7,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    ) + "\n```"
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response))
    hypotheses = await generator.generate(_pack([_ITEM_A, _ITEM_B]))
    assert len(hypotheses) == 1


async def test_hypothesis_ids_are_deterministic_and_content_addressed() -> None:
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:b",
                "explanation": "x",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    gen1 = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response), model_name="m")
    gen2 = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response), model_name="m")
    h1 = (await gen1.generate(_pack([_ITEM_A, _ITEM_B])))[0]
    h2 = (await gen2.generate(_pack([_ITEM_A, _ITEM_B])))[0]
    assert h1.id == h2.id


async def test_a_node_id_containing_a_subdirectory_path_is_not_rejected() -> None:
    """Regression: `_ALLOWED_ID_CHARS` must accept `/` - a repository-wide
    node id (`f"{repository_id}:source-file:{rel_path}"`) legitimately
    contains one whenever the file lives in a subdirectory (e.g.
    `orders/orders.go`), which is the common case, not the exception. A
    too-strict charset here silently dropped every such hypothesis - it
    never raised, it just vanished before validation ever saw it."""
    nested_item = _source_file_item(
        "ev:source:nested", "repo-1:source-file:orders/orders.go", "orders/orders.go", "package orders"
    )
    response = json.dumps(
        [
            {
                "relationship_type": "IMPORTS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:source:nested",
                "explanation": "a.go imports orders/orders.go",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response), model_name="m")
    hypotheses = await generator.generate(_pack([_ITEM_A, nested_item]))
    assert len(hypotheses) == 1
    assert hypotheses[0].target_entity == "repo-1:source-file:orders/orders.go"


async def test_calls_can_target_a_generic_symbol_node() -> None:
    """A `GenericSymbol` node (a heuristically-detected function/method
    declaration - see `generic_language_evidence._extract_symbols`) is a
    valid CALLS endpoint, not only whole `SourceFile` nodes - proves the
    generator actually uses the symbol-level ids it's given, not just
    file-level ones."""
    symbol_item = EvidenceItem(
        id="ev:node:repo-1:generic-symbol:a.go:Helper",
        kind="graph_node:Component:GenericSymbol",
        source_type="code",
        reliability_tier=1,
        reference=EvidenceReference(
            repository_id="repo-1",
            source_type="code",
            locator="Helper",
            key="repo-1:generic-symbol:a.go:Helper",
            commit_sha="abc",
        ),
        raw_value=json.dumps({"name": "Helper", "file_path": "a.go"}),
        provenance=_provenance(),
    )
    response = json.dumps(
        [
            {
                "relationship_type": "CALLS",
                "source_file_id": "ev:source:a",
                "target_file_id": "ev:node:repo-1:generic-symbol:a.go:Helper",
                "explanation": "a.go calls Helper",
                "confidence": 0.9,
                "evidence_item_ids": ["ev:source:a"],
            }
        ]
    )
    generator = GenericLanguageHypothesisGenerator(_FakeLLMProvider(response), model_name="m")
    hypotheses = await generator.generate(_pack([_ITEM_A, symbol_item]))
    assert len(hypotheses) == 1
    assert hypotheses[0].target_entity == "repo-1:generic-symbol:a.go:Helper"
