"""ADR 0018 — the first Frontier Hypothesis Generator: proposes engineering
capability hypotheses about a repository using an LLM, from evidence alone
(no source access, no cloning of its own — it only ever reads `pack.items`,
exactly like `DeterministicParserHypothesisGenerator`).

Repository/language/framework agnostic by construction: nothing here
branches on `model.language`/`model.framework`, a file extension, or a
build-tool name. The prompt describes the task in terms of evidence kinds
(`repository_readme`, `graph_node:*`, ...) that exist for every repository
this platform indexes, Java or Python or (once a parser exists) anything
else — the generator never needs to know which.

Recall over precision, deliberately (ADR 0018): this generator proposes,
it never decides truth. It assigns only `generator_confidence` — advisory,
per `Hypothesis`'s own contract, never read to gate anything. Whether a
proposed relationship actually holds is entirely the validators' and
confidence engine's job, unchanged by this generator's existence. Today,
no validator recognizes this generator's relationship vocabulary at all
(see the vocabulary's own comment below) — every hypothesis it produces
lands at `ConfidenceState.CANDIDATE` via `to_knowledge_relationship`'s
existing "no applicable validator" fallback (already built in RFC-02B/04
for exactly this future case), not because anything here decided that,
but because nothing yet exists to confirm or contradict it. Building that
validator coverage is explicitly future work, not this generator's job.

Scope, deliberately narrow for v1: single-repository capability claims
only (source is always the repository's own node, target is a synthetic
`{repository_id}:capability:{slug}` entity — see `_CAPABILITY_TYPES`).
Cross-repository claims ("Service A calls Service B", "depends on
repository X") are excluded here: this generator's only input is one
repository's own evidence pack, with no knowledge of what other
repositories exist or are named — the same reason the deterministic
cross-repo linking got its own separate pack/generator shape in RFC-05
rather than trying to fit inside the single-repository generator. A
cross-repository LLM generator, given two repositories' evidence the same
way `build_candidate_pack_and_hypotheses` is, is natural future work, not
this one.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from app.agents.prompt_utils import strip_markdown_fence
from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.factory import create_llm_provider
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.hypothesis import Hypothesis, HypothesisGenerator
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.evidence_curation import curate_for_prompt, render_curated_evidence_text

# The fixed relationship-type vocabulary this generator is scoped to
# propose, each mapped to its synthetic capability-entity slug. Fixed and
# small on purpose: an open-ended, LLM-invented relationship_type/entity-id
# vocabulary would break `relationship_key`-based version history (RFC-04)
# — the same repository re-analyzed on a later commit needs to converge on
# the SAME entity id for "this repo owns a database", not a slightly
# different phrase each run. The LLM chooses WHICH of these apply; it
# never invents the vocabulary itself.
_CAPABILITY_TYPES: dict[str, str] = {
    "OWNS_DATABASE": "database",
    "OWNS_MESSAGE_QUEUE": "message_queue",
    "OWNS_EVENT_SCHEMA": "event_schema",
    "OWNS_API": "api",
    "CONTAINS_INFRASTRUCTURE": "infrastructure",
    "CONTAINS_BATCH_JOB": "batch_job",
    "CONTAINS_SCHEDULED_JOB": "scheduled_job",
    "INTEGRATES_WITH_CLOUD_SERVICE": "cloud_integration",
    "CONTAINS_FEATURE_FLAG": "feature_flag",
    "CONTAINS_AUTHENTICATION": "authentication",
    "CONTAINS_AUTHORIZATION": "authorization",
    "CONTAINS_CACHING": "caching",
    "CONTAINS_EXTERNAL_API": "external_api",
}

_PROMPT_VERSION = "v1"

_SYSTEM_PROMPT = f"""You are a software-architecture analyst. You will be given evidence \
about ONE software repository: its README, manifests, configuration files, \
repository metadata, and a sample of its automatically discovered components. \
You are not told what language or framework it uses — infer everything from \
the evidence given, and never assume a specific one.

Propose hypotheses about engineering capabilities this repository likely has, \
using ONLY these relationship types: {", ".join(sorted(_CAPABILITY_TYPES))}.

Only propose a hypothesis when the evidence gives a real, specific reason to \
believe it. Do not propose a hypothesis the evidence is silent on. It is fine, \
and expected, for you to be wrong sometimes — a separate, independent \
validation step decides what is actually true from here. Your job is to \
maximize recall on real signals in the evidence, not to filter for certainty \
yourself.

Respond with a JSON array only — no prose, no markdown fences. Each element:
{{"relationship_type": one of the allowed types above,
"explanation": a short, specific reason grounded in the evidence given,
"confidence": a number from 0 to 1 for how directly the evidence supports it,
"evidence_item_ids": a non-empty array of the evidence item ids (given below, \
each line starting "id=...") that most directly support this claim}}

If the evidence supports no hypotheses at all, respond with an empty array: []."""


def _identity_for(model_name: str | None) -> GeneratorIdentity:
    return GeneratorIdentity(
        kind="llm", name=f"frontier_llm:{model_name or 'unresolved'}", version=_PROMPT_VERSION
    )


_ALLOWED_ID_CHARS = re.compile(r"^[A-Za-z0-9:_.\-]+$")


class FrontierHypothesisGenerator(HypothesisGenerator):
    """The one generic, repository-agnostic LLM `HypothesisGenerator`.
    Takes an already-constructed `ILLMProvider` (dependency-injected, not
    built internally) so tests can supply a fake provider without any
    network access or API key."""

    consumes = frozenset({"code", "documentation", "metadata"})

    def __init__(self, llm_provider: ILLMProvider, *, model_name: str | None = None) -> None:
        self._llm_provider = llm_provider
        self.identity = _identity_for(model_name)

    async def generate(self, pack: EngineeringEvidencePack) -> list[Hypothesis]:
        curated = curate_for_prompt(pack)
        evidence_ids = {item.id for item in pack.items}

        response = await self._llm_provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=render_curated_evidence_text(curated),
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
                repository_id=pack.repository_id,
                known_evidence_ids=evidence_ids,
                provenance=provenance,
            )
            if hypothesis is not None:
                hypotheses.append(hypothesis)
        return hypotheses


def build_frontier_hypothesis_generator() -> FrontierHypothesisGenerator:
    """The registry's factory — only called once
    `GeneratorPolicy.should_run` has already said yes (see
    `generator_registry.py`), so `create_llm_provider()` (which validates
    API-key configuration) is never invoked, and never fails, while the
    generator is disabled."""
    provider = create_llm_provider()
    model_name = getattr(provider, "model", None) or provider.__class__.__name__
    return FrontierHypothesisGenerator(provider, model_name=str(model_name))


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
    repository_id: str,
    known_evidence_ids: set[str],
    provenance: Provenance,
) -> Hypothesis | None:
    relationship_type = candidate.get("relationship_type")
    if relationship_type not in _CAPABILITY_TYPES:
        return None

    explanation = candidate.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        return None

    raw_evidence_refs = candidate.get("evidence_item_ids")
    if not isinstance(raw_evidence_refs, list):
        return None
    # Never trust an LLM-supplied id blindly: only ids that genuinely
    # exist in the pack this generator was given are kept, so
    # `Hypothesis.evidence_refs` can never cite evidence that isn't real
    # (a hallucinated id would otherwise silently satisfy the "non-empty"
    # contract check without meaning anything).
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

    slug = _CAPABILITY_TYPES[str(relationship_type)]
    source_entity = f"{repository_id}:repository"
    target_entity = f"{repository_id}:capability:{slug}"
    hypothesis_id = f"hyp:{provenance.generator.name}:{relationship_type}:{repository_id}"
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
