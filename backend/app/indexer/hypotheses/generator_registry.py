"""ADR 0018 — the plugin registry for hypothesis generators whose
`GeneratorIdentity` does not depend on per-run data.

The deterministic parser generator is deliberately NOT in this registry:
its identity depends on the parsed `ArchitectureModel`'s language/
framework, known only inside `run_shadow_hypothesis_generation` itself, so
it stays directly instantiated there, completely unchanged (per this RFC's
explicit "do not modify existing generators" instruction — this registry
adds a second, parallel path for OTHER generators, it does not replace the
first). This registry is for every generator whose identity is fixed
ahead of time: the Frontier LLM generator today, and future Runtime/
Documentation/Infrastructure/API/Security/Git History/Human generators
(see ADR 0018) — each one more entry here, never a change to
`shadow_runner.py`'s loop.

`factory` is called only after `policy.should_run()` returns `True` —
this is what keeps `build_frontier_hypothesis_generator()` (which calls
`create_llm_provider()`, and can raise if the LLM feature is enabled but
misconfigured) from ever running while the feature is off, which is the
default (see `Settings.enable_frontier_llm_generator`'s own docstring).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import get_settings
from app.indexer.hypotheses.llm_generator import build_frontier_hypothesis_generator
from app.knowledge_engine.contracts.generator_policy import GeneratorPolicy, StaticGeneratorPolicy
from app.knowledge_engine.contracts.hypothesis import HypothesisGenerator
from app.knowledge_engine.contracts.provenance import GeneratorIdentity

# Used only for the pre-construction policy context (see
# `GeneratorExecutionContext`'s own docstring on why this doesn't need to
# match the constructed generator's own, possibly more specific, identity
# exactly) — `StaticGeneratorPolicy` ignores it entirely today, but a
# future budget-aware or premium-only policy could read `kind`/`name` from
# it without needing the provider constructed first.
_FRONTIER_LLM_POLICY_IDENTITY = GeneratorIdentity(
    kind="llm", name="frontier_llm_generator", version="registry"
)


@dataclass(frozen=True)
class RegisteredGenerator:
    identity: GeneratorIdentity
    factory: Callable[[], HypothesisGenerator]
    policy: GeneratorPolicy


def build_generator_registry() -> tuple[RegisteredGenerator, ...]:
    settings = get_settings()
    return (
        RegisteredGenerator(
            identity=_FRONTIER_LLM_POLICY_IDENTITY,
            factory=build_frontier_hypothesis_generator,
            policy=StaticGeneratorPolicy(settings.enable_frontier_llm_generator),
        ),
    )
