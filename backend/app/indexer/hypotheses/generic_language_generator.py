"""RFC-07 — the generic, language-agnostic LLM `HypothesisGenerator` for a
repository with no deterministic parser at all. Structurally the same
shape as `llm_generator.py`'s `FrontierHypothesisGenerator` (structured
JSON output, hallucinated-reference rejection, advisory-only
`generator_confidence`) - deliberately reused rather than re-invented,
since both are "propose from evidence, let validation decide truth"
generators differing only in what they propose about.

What makes this "the LLM fallback" rather than "the LLM replacing AST
parsing" (the distinction ADR 0018 and this RFC both require): this
generator never runs when a deterministic parser exists for the repository
- `indexing_service.py` only reaches it when `get_parser()` already
returned None. It also never claims a relationship type outside a small,
fixed, pre-approved vocabulary (`_ALLOWED_RELATIONSHIP_TYPES`), the same
"LLM picks from a menu, never invents the vocabulary" discipline
`FrontierHypothesisGenerator`'s `_CAPABILITY_TYPES` already established -
open-ended relationship-type invention would break `relationship_key`
version history (RFC-04) the same way it would there.

Trust is intentionally no higher than the rest of ADR 0018 already grants
an unvalidated hypothesis: `generator_confidence` stays advisory, never
authoritative - `knowledge_engine/validators/generic_structural.py`'s two
language-agnostic validators (entity grounding, cited-evidence mention)
are what actually let a well-evidenced hypothesis from this generator
reach `Verified`/`Highly Likely` and materialize; a poorly-evidenced one
correctly stays at `Candidate` and never does.

Operates over both file-level (`source_file`/`SourceFile`) and
symbol-level (`GenericSymbol`, a heuristic function/method-like
declaration - see `generic_language_evidence.py`) entities, so `CALLS`
can target an actual named function, not only a whole file - `IMPORTS`/
`DEPENDS_ON` still typically target files.

Batching (raised file caps, `_BATCH_SIZE`/content-hash caching) is the
*caller's* responsibility (`generic_language_runner.py`), not this
generator's - `generate(pack)` stays a pure, single-pack-in/hypotheses-out
function, called once per batch, matching `HypothesisGenerator`'s
existing contract exactly (no DB access inside a generator).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from app.agents.prompt_utils import strip_markdown_fence
from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.factory import create_llm_provider
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack, EvidenceItem
from app.knowledge_engine.contracts.hypothesis import Hypothesis, HypothesisGenerator
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance

_SOURCE_FILE_EVIDENCE_KIND = "source_file"
_SYMBOL_NODE_KIND = "graph_node:Component:GenericSymbol"
_ALLOWED_RELATIONSHIP_TYPES = frozenset({"IMPORTS", "CALLS", "DEPENDS_ON"})
_PROMPT_VERSION = "v2"

# One LLM call is given at most this many files' worth of context - the
# actual repository-wide file cap (`generic_language_evidence._MAX_FILES`)
# can exceed this; `generic_language_runner.py` splits into multiple
# batches of this size rather than growing a single prompt without bound.
_BATCH_SIZE = 20

_SYSTEM_PROMPT = f"""You are analyzing source files from a repository written in a \
programming language this tool has no dedicated parser for. You are given a set of \
files, each with its relative path, a content sample, and (where detected) a list of \
function/method-like symbols found in it - each labeled with an id.

Propose relationships using ONLY these relationship types: \
{", ".join(sorted(_ALLOWED_RELATIONSHIP_TYPES))}.
- IMPORTS and DEPENDS_ON: between two FILE ids (source_file_id/target_file_id below).
- CALLS: between two SYMBOL ids when both are given to you (a specific function/method \
calling another), or between two FILE ids if no symbol id is available for the specific \
function involved.

Only propose a relationship when a file's own content directly and specifically shows \
it - e.g. an import/include/require statement literally naming another file given to \
you, or a call expression naming a symbol clearly given to you. Do not guess at \
relationships the content doesn't actually show. It is fine, and expected, for you to \
find nothing in some or all files - a separate, independent validation step decides \
what is actually trusted from here, so under-claiming is always safer than \
over-claiming.

Respond with a JSON array only - no prose, no markdown fences. Each element:
{{"relationship_type": one of the allowed types above,
"source_file_id": the id (given below) of the file or symbol the relationship \
originates from,
"target_file_id": the id of the file or symbol it points to - must be a DIFFERENT id \
given to you, never invented,
"explanation": a short, specific reason grounded in the content given,
"confidence": a number from 0 to 1 for how directly the content supports it,
"evidence_item_ids": a non-empty array of the evidence item ids (given below, each \
line starting "id=...") that most directly support this claim}}

If nothing shows a supportable relationship, respond with an empty array: []."""


def _identity_for(model_name: str | None) -> GeneratorIdentity:
    return GeneratorIdentity(
        kind="llm", name=f"generic_language_llm:{model_name or 'unresolved'}", version=_PROMPT_VERSION
    )


_ALLOWED_ID_CHARS = re.compile(r"^[A-Za-z0-9:_./\-]+$")


def _render_prompt(source_items: list[EvidenceItem], symbols_by_file: dict[str, list[EvidenceItem]]) -> str:
    lines: list[str] = []
    for item in source_items:
        lines.append(f"id={item.id}")
        lines.append(f"path={item.reference.locator}")
        for symbol_item in symbols_by_file.get(item.reference.locator, []):
            lines.append(f"symbol: id={symbol_item.id} name={symbol_item.reference.locator}")
        lines.append(item.raw_value)
        lines.append("---")
    return "\n".join(lines)


class GenericLanguageHypothesisGenerator(HypothesisGenerator):
    """The one generic, language-agnostic LLM fallback `HypothesisGenerator`
    - takes an already-constructed `ILLMProvider` (dependency-injected,
    same pattern as `FrontierHypothesisGenerator`) so tests never need
    network access or a real API key."""

    consumes = frozenset({_SOURCE_FILE_EVIDENCE_KIND})

    def __init__(self, llm_provider: ILLMProvider, *, model_name: str | None = None) -> None:
        self._llm_provider = llm_provider
        self.identity = _identity_for(model_name)

    async def generate(self, pack: EngineeringEvidencePack) -> list[Hypothesis]:
        source_items = [item for item in pack.items if item.kind == _SOURCE_FILE_EVIDENCE_KIND]
        if not source_items:
            return []

        symbol_items = [item for item in pack.items if item.kind == _SYMBOL_NODE_KIND]
        symbols_by_file: dict[str, list[EvidenceItem]] = {}
        for item in symbol_items:
            file_path = json.loads(item.raw_value).get("file_path")
            if file_path:
                symbols_by_file.setdefault(file_path, []).append(item)

        evidence_ids = {item.id for item in pack.items}
        # Both file-level and symbol-level ids resolve to a usable graph
        # node id - IMPORTS/DEPENDS_ON typically target a file id, CALLS
        # typically targets a symbol id, but nothing here enforces which
        # relationship type uses which kind of id beyond what the prompt
        # asks for; `generic_structural.py`'s validators check groundedness
        # generically regardless of which kind of node either end is.
        node_id_by_evidence_id = {item.id: item.reference.key for item in source_items}
        node_id_by_evidence_id.update({item.id: item.reference.key for item in symbol_items})

        response = await self._llm_provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_render_prompt(source_items, symbols_by_file),
            options=LLMRequestOptions(response_format=ResponseFormat.JSON),
        )

        candidates = _parse_response(response.text)
        provenance = Provenance(
            generator=self.identity,
            produced_at=datetime.now(UTC),
            pack_id=pack.id,
            pack_version=pack.schema_version,
            run_id=pack.id,
        )

        hypotheses: list[Hypothesis] = []
        for candidate in candidates:
            hypothesis = _candidate_to_hypothesis(
                candidate,
                node_id_by_evidence_id=node_id_by_evidence_id,
                known_evidence_ids=evidence_ids,
                provenance=provenance,
            )
            if hypothesis is not None:
                hypotheses.append(hypothesis)
        return hypotheses


def build_generic_language_hypothesis_generator() -> GenericLanguageHypothesisGenerator:
    """Only called once the caller has already decided to run the
    fallback (see `generic_language_runner.py`), so `create_llm_provider()`
    - which validates API-key configuration - is never invoked, and never
    fails, for a repository that never reaches this path (every
    Python/Java repository, and any repository where the feature flag is
    off)."""
    provider = create_llm_provider()
    model_name = getattr(provider, "model", None) or provider.__class__.__name__
    return GenericLanguageHypothesisGenerator(provider, model_name=str(model_name))


def _parse_response(text: str) -> list[dict[str, object]]:
    cleaned = strip_markdown_fence(text).strip()
    if not cleaned:
        return []
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _candidate_to_hypothesis(
    candidate: dict[str, object],
    *,
    node_id_by_evidence_id: dict[str, str],
    known_evidence_ids: set[str],
    provenance: Provenance,
) -> Hypothesis | None:
    relationship_type = candidate.get("relationship_type")
    if relationship_type not in _ALLOWED_RELATIONSHIP_TYPES:
        return None

    explanation = candidate.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        return None

    source_file_id = candidate.get("source_file_id")
    target_file_id = candidate.get("target_file_id")
    if not isinstance(source_file_id, str) or not isinstance(target_file_id, str):
        return None
    # Never trust an LLM-supplied id blindly - only ids this generator
    # actually gave it (a file or symbol id), resolved to their real graph
    # node ids, are usable (a hallucinated reference is rejected here, not
    # merely unvalidated later).
    source_entity = node_id_by_evidence_id.get(source_file_id)
    target_entity = node_id_by_evidence_id.get(target_file_id)
    if source_entity is None or target_entity is None or source_entity == target_entity:
        return None

    raw_evidence_refs = candidate.get("evidence_item_ids")
    if not isinstance(raw_evidence_refs, list):
        return None
    evidence_refs = tuple(
        ref for ref in raw_evidence_refs if isinstance(ref, str) and ref in known_evidence_ids
    )
    if not evidence_refs:
        return None

    generator_confidence = candidate.get("confidence")
    if not isinstance(generator_confidence, (int, float)) or not (
        0.0 <= float(generator_confidence) <= 1.0
    ):
        generator_confidence = None
    else:
        generator_confidence = float(generator_confidence)

    hypothesis_id = (
        f"hyp:{provenance.generator.name}:{relationship_type}:{source_entity}:{target_entity}"
    )
    if not _ALLOWED_ID_CHARS.match(hypothesis_id):
        return None

    return Hypothesis(
        id=hypothesis_id,
        relationship_type=str(relationship_type),
        source_entity=source_entity,
        target_entity=target_entity,
        evidence_refs=evidence_refs,
        explanation=str(explanation),
        provenance=provenance,
        generator_confidence=generator_confidence,
    )
