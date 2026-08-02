"""GeneratorPolicy — decides *whether* a registered `HypothesisGenerator`
executes for a given run, kept deliberately separate from *what* the
generator does (ADR 0018's Frontier Hypothesis Generator entry).

The problem this closes: a plain `enabled: bool` flag on each registry
entry would work for "on/off" but would need the registry loop itself
rewritten the moment a real execution mode arrives — "only on a manual
trigger", "at most once per budget window", "only for premium accounts".
A policy is instead a single async decision point
(`should_run(context) -> bool`) that the loop calls identically regardless
of how a given policy makes its decision — adding a new mode is a new
`GeneratorPolicy` implementation, never a change to the loop or to
`HypothesisGenerator` itself. Same shape as `KnowledgeValidator`/
`CrossRepoLinkRule`: a small interface plus a registry of instances, not an
if-chain that grows a new branch per mode.

`GeneratorExecutionContext` carries only what's known and settled at
dispatch time (which repository, which commit, which generator, why this
run is happening at all) — no settings/DB/budget-tracker objects, so a
policy that needs one takes it as a constructor argument (see
`LLMEnabledGeneratorPolicy`) rather than this context growing an
ever-larger grab-bag of "things some future policy might want".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.knowledge_engine.contracts.provenance import GeneratorIdentity


@dataclass(frozen=True)
class GeneratorExecutionContext:
    """`trigger` is deliberately plain `str`, not a closed enum — same
    open-vocabulary reasoning as `EvidenceItem.kind` (evidence.py's module
    docstring): today only `"indexing_run"` exists; `"manual"`,
    `"scheduled"`, `"webhook"` arrive with whatever future caller first
    needs them, not as a schema change here."""

    repository_id: str
    commit_sha: str
    generator_identity: GeneratorIdentity
    trigger: str = "indexing_run"


class GeneratorPolicy(ABC):
    """Port every execution-gating rule implements. Implementations must
    never raise for an ordinary "not this time" decision — `False` is the
    correct result; raising is reserved for genuine policy failure (a
    budget tracker unreachable), which the caller is expected to log and
    treat as `should_run() == False` rather than let abort the whole run,
    the same isolation discipline `HypothesisGenerator.generate` already
    requires of itself."""

    @abstractmethod
    async def should_run(self, context: GeneratorExecutionContext) -> bool:
        raise NotImplementedError


class StaticGeneratorPolicy(GeneratorPolicy):
    """The only concrete policy this RFC needs: a fixed, constructor-time
    True/False, no per-call state. Every future mode (manual-trigger,
    scheduled, webhook-triggered, budget-limited, premium-only) is a new
    `GeneratorPolicy` subclass reading whatever state it needs (a job
    queue, a budget tracker, an account tier) — none of them change this
    class, `GeneratorPolicy`, or the loop that calls `should_run`.
    """

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    async def should_run(self, context: GeneratorExecutionContext) -> bool:
        return self._enabled
