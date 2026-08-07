"""Typed contracts for Investigation Intelligence — ADR 0021.

Everything here is a plain, frozen dataclass. Nothing in this module
touches SQLAlchemy or `AsyncSession` — that boundary belongs entirely to
`repository.py`, which is also the only place a `dict`/JSONB blob is ever
seen; every consumer outside this package (and every read/write through
`InvestigationIntelligenceService`) sees these typed shapes instead,
matching how every other typed contract in this codebase already behaves
(e.g. `app.knowledge_engine.contracts`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.context_pipeline.reasoning.investigation_planner import EngineeringStrategy

CURRENT_SNAPSHOT_VERSION = 1

ProviderOutcome = Literal["success", "not_found", "unavailable", "failed"]
Necessity = Literal["required", "recommended"]
PriorityBoostSource = Literal["live_llm", "memory_seeded", "both", "none"]
TerminalOutcome = Literal["READY", "PARTIAL", "BLOCKED", "FAILED"]


@dataclass(frozen=True)
class InvestigationScope:
    """Not always "repository" — see ADR 0021 §2. Confluence/Jira
    effectiveness is a property of the Atlassian site
    (`KnowledgeConnection.id`), not any one GitHub repository;
    architecture/source-control effectiveness genuinely is
    repository-scoped. Every write/read takes a scope built by whichever
    investigator/provider produced the event — this type is scope-axis
    agnostic on purpose."""

    scope_type: Literal["repository", "knowledge_connection"]
    scope_id: str


@dataclass(frozen=True)
class CandidateScore:
    """One action `_select()` considered but did not necessarily choose —
    what makes the winner's context reconstructable as a ranking rather
    than a single opaque outcome. See ADR 0021 §3a."""

    provider: str
    action_key: str
    capability: str
    necessity: Necessity
    score: float
    cost: int


@dataclass(frozen=True)
class StateSnapshot:
    """The whole decision context at one `_select()` call — not just the
    targeted capability's own state. `version` follows the exact
    precedent `EngineeringEvidencePackRecord.schema_version` already set
    in this codebase: the field that lets the shape *inside* the JSONB
    evolve without a column migration. A reader must tolerate an older
    `version` gracefully (see `StateSnapshot.empty` and
    `repository.py`'s deserialization) — never raise on one."""

    version: int
    candidates_considered: tuple[CandidateScore, ...]
    all_capability_scores: dict[str, float]
    open_contradictions: int

    @staticmethod
    def empty() -> StateSnapshot:
        """For an older/unrecognized `version`, or when nothing meaningful
        could be captured — never `None`, so a caller never needs a
        separate null-check path alongside the version tolerance one."""
        return StateSnapshot(
            version=CURRENT_SNAPSHOT_VERSION,
            candidates_considered=(),
            all_capability_scores={},
            open_contradictions=0,
        )


@dataclass(frozen=True)
class ProviderOutcomeEvent:
    """One provider action's outcome, with the planner-decision context
    that produced it folded into the same row — `_select()` picks exactly
    one action per cycle, which runs and produces (usually) exactly one
    new evidence entry, so a second table joined 1:1 against this one
    would only ever be queried alongside it. See ADR 0021 §3.

    In the rare case one action yields more than one new ledger evidence
    entry in a single cycle (the loop already accounts for this — see
    `engine.py`'s own `new_evidence` list), one `ProviderOutcomeEvent` is
    recorded per new entry, all sharing the same decision-context fields.
    """

    investigation_id: str
    cycle_number: int
    scope: InvestigationScope
    capability: str
    investigation_type: EngineeringStrategy
    provider: str
    action_key: str
    outcome: ProviderOutcome
    declared_cost: int
    latency_ms: int
    yielded_evidence: bool
    necessity_at_selection: Necessity
    base_score_at_selection: float
    priority_boost_applied: float
    priority_boost_source: PriorityBoostSource
    confidence_before: float
    confidence_after: float
    state_snapshot: StateSnapshot = field(default_factory=StateSnapshot.empty)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class InvestigationOutcomeEvent:
    """One completed (or crashed) investigation's terminal summary — the
    root record. Written from two different call sites, never one (see
    ADR 0021 §4): `investigate()`'s clean exit for READY/PARTIAL/BLOCKED,
    and the outer `try`/`except` around `discover()`/`resume()` in
    `context_discovery/agent.py` for FAILED, since a crash never reaches
    `investigate()`'s own exit at all.

    Not unique per `investigation_id`: a paused investigation that is
    later resumed reaches this same write path again once more cycles
    run, and — matching the append-only convention every other
    persisted-history table in this codebase already follows
    (`TimelineEntry`, `LearningEvent`, `EngineeringEvidencePackRecord`) —
    that second terminal state is a new row, never an update to the
    first. The most recent row for an `investigation_id` is "the"
    outcome; earlier ones are the trajectory that got there.
    """

    investigation_id: str
    scope: InvestigationScope
    investigation_type: EngineeringStrategy
    cycles_used: int
    terminal_outcome: TerminalOutcome
    confidence: float | None
    final_capability_scores: dict[str, float]
    contradictions_encountered: int
    contradictions_resolved: int
    priority_boost_source_used: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ProviderEffectiveness:
    """A read-time, decay-weighted aggregate over past
    `ProviderOutcomeEvent`s for one `(scope, capability, provider)` —
    never a raw row, never an LLM-asserted opinion. `usefulness` and the
    two averages are all derived from already-deterministic facts
    (`yielded_evidence`, `confidence_after - confidence_before`,
    `latency_ms`) — see ADR 0021 §3's "Usefulness" note."""

    provider: str
    capability: str
    weighted_success_rate: float
    weighted_usefulness: float
    average_latency_ms: float
    sample_count: int
    most_recent_at: datetime | None
