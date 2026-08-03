"""`AgentMetrics` — timing/latency/token/cache/confidence collection for
one agent run. No business logic: every field here is either a duration
this module measured itself (`time.monotonic()`, never wall-clock, so
it's immune to clock adjustments during a run) or a number the caller
hands in verbatim (token usage, cache hits).

Deliberately not persisted anywhere by this module — `invoke_llm_json`
already persists LLM invocation metadata (ADR 0012) independently, so
`AgentMetrics` is the in-run, in-memory counterpart a `BaseFrontierAgent`
can fold into `AgentOutput.result["metrics"]` for the UI, not a second
persistence path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AgentMetrics:
    _run_started_at: float | None = field(default=None, repr=False)
    _service_started_at: float | None = field(default=None, repr=False)
    _llm_started_at: float | None = field(default=None, repr=False)

    total_duration_ms: float | None = None
    service_latency_ms: float | None = None
    llm_latency_ms: float | None = None
    token_usage: int | None = None
    cache_hits: int = 0
    cache_misses: int = 0
    confidence_distribution: dict[str, int] = field(default_factory=dict)

    def start_run(self) -> None:
        self._run_started_at = time.monotonic()

    def stop_run(self) -> None:
        if self._run_started_at is not None:
            self.total_duration_ms = (time.monotonic() - self._run_started_at) * 1000

    def start_service_call(self) -> None:
        self._service_started_at = time.monotonic()

    def stop_service_call(self) -> None:
        if self._service_started_at is not None:
            self.service_latency_ms = (time.monotonic() - self._service_started_at) * 1000

    def start_llm_call(self) -> None:
        self._llm_started_at = time.monotonic()

    def stop_llm_call(self) -> None:
        if self._llm_started_at is not None:
            self.llm_latency_ms = (time.monotonic() - self._llm_started_at) * 1000

    def record_confidence_states(self, states: list[str]) -> None:
        """`states` are `KnowledgeRelationshipRecord.confidence_state`
        values (`RelationshipInsight.confidence_state`) — tallied, not
        interpreted; this module has no notion of what a "good"
        distribution looks like."""
        for state in states:
            self.confidence_distribution[state] = self.confidence_distribution.get(state, 0) + 1

    def to_dict(self) -> dict[str, object]:
        return {
            "total_duration_ms": self.total_duration_ms,
            "service_latency_ms": self.service_latency_ms,
            "llm_latency_ms": self.llm_latency_ms,
            "token_usage": self.token_usage,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "confidence_distribution": dict(self.confidence_distribution),
        }
