"""ADR 0018 RFC-05 — persists cross-repository `KnowledgeRelationship`s
(`CALLS_SERVICE`, `SHARES_TOPIC`, `DEPENDS_ON_REPOSITORY`) into Engineering
Memory, reusing the existing, parity-tested Hypothesis -> Validator ->
ConfidenceEngine pipeline unchanged: `build_candidate_pack_and_hypotheses`
and `CROSS_REPO_VALIDATORS` (`app.knowledge_engine.validators.cross_repo`,
proven equivalent to `cross_repo_linker`'s own hand-assigned confidence in
`tests/unit/knowledge_engine/test_cross_repo_parity.py`) and
`to_knowledge_relationship` (`app.indexer.hypotheses.shadow_runner`, the
same validate-then-aggregate mechanics RFC-02B/RFC-04 already use for
single-repository hypotheses). No new reasoning logic lives here — only
wiring and persistence.

Runs on its own, independent `AsyncSession` — never the session
`relink_account` holds its `pg_advisory_xact_lock` on. That lock is
transaction-scoped and must stay held until the *caller* of
`relink_account` (`run_indexing_job`) reaches its own commit point (see
`tests/integration/test_finding3_concurrent_relink_repro.py`).
`EngineeringMemoryService`'s store methods commit internally (RFC-04
contract, `app.knowledge_engine.memory_service`'s own docstring); doing
that on the lock-holding session would end its transaction and release the
lock the moment persistence ran, silently reopening the exact race
Finding #3 closed. A separate session's commit has no effect on a lock
held by a different session — the same independent-session shape
`test_finding3_concurrent_relink_repro.py` itself already uses.

Only repository pairs that produce at least one hypothesis get anything
persisted — an evidence pack recording "no candidate relationship found"
for the large majority of non-matching pairs (this is `O(N^2)` pairs per
relink) would be pure write amplification with no reconstruction value.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.indexer.hypotheses.shadow_runner import to_knowledge_relationship
from app.knowledge_engine.confidence.default_engine import DefaultConfidenceEngine
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.knowledge_engine.validators.cross_repo import (
    CROSS_REPO_VALIDATORS,
    build_candidate_pack_and_hypotheses,
)

if TYPE_CHECKING:
    # Deferred: `cross_repo_linker.py` imports this module at call time (see
    # its own comment) and `validators/cross_repo.py` imports
    # `cross_repo_linker.py` at runtime, so importing `RepoNodes` here at
    # module load time would complete the cycle. Only used as a type hint.
    from app.indexer.graph.cross_repo_linker import RepoNodes

logger = logging.getLogger(__name__)


async def _persist_pair(
    memory_db: AsyncSession, repo_id: str, source: RepoNodes, other: RepoNodes
) -> int:
    pack, hypotheses = build_candidate_pack_and_hypotheses(source, other)
    if not hypotheses:
        return 0

    engine = DefaultConfidenceEngine()
    relationships = []
    explanations = []
    for hypothesis in hypotheses:
        relationship, _confidence, explanation, _confirms, _contradicts = (
            await to_knowledge_relationship(hypothesis, pack, engine, CROSS_REPO_VALIDATORS)
        )
        relationships.append(relationship)
        explanations.append(explanation)

    memory = EngineeringMemoryService(memory_db)
    await memory.store_evidence_pack(uuid.UUID(repo_id), pack)
    await memory.store_relationships(uuid.UUID(repo_id), relationships, explanations)
    return len(relationships)


async def persist_cross_repo_relationships(nodes_by_repo: dict[str, RepoNodes]) -> None:
    """Best-effort, like `run_shadow_hypothesis_generation`: never raises —
    a persistence failure must never affect `relink_account`, whose Neo4j
    edge write has already completed by the time this is called. Opens its
    own session (see module docstring for why) and closes it when done."""
    persisted_relationship_count = 0
    try:
        async with AsyncSessionLocal() as memory_db:
            for repo_id, source in nodes_by_repo.items():
                for other_id, other in nodes_by_repo.items():
                    if other_id == repo_id:
                        continue
                    try:
                        persisted_relationship_count += await _persist_pair(
                            memory_db, repo_id, source, other
                        )
                    except Exception:
                        logger.exception(
                            "cross_repo_relationship_persistence_pair_failed " "source=%s other=%s",
                            repo_id,
                            other_id,
                        )
    except Exception:
        logger.exception("cross_repo_relationship_persistence_failed")
        return

    logger.info(
        "cross_repo_relationship_persistence_completed persisted_relationship_count=%d",
        persisted_relationship_count,
    )
