"""ADR 0010 §7 P3 (Theme E) regression tests — a repository the user tracks
but hasn't indexed becomes an actionable gap instead of silence, and every
cross-repository edge carries a confidence marker.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.context_pipeline.reasoning import capabilities
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.investigators import RequestParseInvestigator
from app.context_pipeline.reasoning.ledger import Ledger


def _mock_session_with_tracked_repos(names: list[str]) -> SessionContext:
    result = MagicMock()
    result.all.return_value = [(name,) for name in names]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return SessionContext(db=db, user_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_match_tracked_repository_names_records_indexed_false() -> None:
    """The core Theme E behavior: a repository named in the request that the
    user tracks but has never indexed must be recorded, distinguishable
    from a repository that was never mentioned at all."""
    ledger = Ledger()
    action = InvestigationAction(
        provider="request_parser",
        key="match_tracked_repository_names",
        intent="checking tracked repos",
        targets="repository",
        params={"text": "Please also update streaming-pipeline", "existing": set()},
    )
    recorder = Recorder(ledger, action, iteration=1)
    session = _mock_session_with_tracked_repos(["streaming-pipeline", "unrelated-repo"])

    outcome = await RequestParseInvestigator._match_tracked_repository_names(
        action.params["text"], set(), session, recorder
    )

    assert outcome.yielded is True
    refs = ledger.facts_of("reference")
    assert len(refs) == 1
    assert refs[0].subject == "streaming-pipeline"
    assert refs[0].value["indexed"] is False


@pytest.mark.asyncio
async def test_match_tracked_repository_names_skips_names_already_recorded() -> None:
    """A name `match_repository_names` already recorded as indexed must not
    be re-recorded as unindexed by this pass — `existing` is the same dedup
    mechanism every other RequestParseInvestigator pass already relies on."""
    ledger = Ledger()
    action = InvestigationAction(
        provider="request_parser",
        key="match_tracked_repository_names",
        intent="checking tracked repos",
        targets="repository",
        params={"text": "Fix ingestion-framework", "existing": {"ingestion-framework"}},
    )
    recorder = Recorder(ledger, action, iteration=1)
    session = _mock_session_with_tracked_repos(["ingestion-framework"])

    outcome = await RequestParseInvestigator._match_tracked_repository_names(
        action.params["text"], {"ingestion-framework"}, session, recorder
    )

    assert outcome.yielded is False
    assert ledger.facts_of("reference") == []


@pytest.mark.asyncio
async def test_match_tracked_repository_names_is_a_no_op_when_nothing_matches() -> None:
    ledger = Ledger()
    session = _mock_session_with_tracked_repos(["completely-unrelated"])
    action = InvestigationAction(
        provider="request_parser",
        key="match_tracked_repository_names",
        intent="checking tracked repos",
        targets="repository",
        params={"text": "Add retry logic", "existing": set()},
    )
    recorder = Recorder(ledger, action, iteration=1)

    outcome = await RequestParseInvestigator._match_tracked_repository_names(
        action.params["text"], set(), session, recorder
    )

    assert outcome.yielded is False
    assert ledger.facts_of("reference") == []


# ---------------------------------------------------------------------------
# capabilities.py — the unindexed-mention detail text
# ---------------------------------------------------------------------------


_MATCHED_SIGNAL_LABEL = "Request names a repository that matched an indexed one"


def test_repository_signal_names_the_unindexed_repository_specifically() -> None:
    ledger = Ledger()
    ev = ledger.add_evidence(
        provider="request_parser", action="parse", outcome="success", summary="s"
    )
    ledger.add_fact(
        kind="reference",
        subject="streaming-pipeline",
        provider="request_parser",
        evidence_id=ev.evidence_id,
        value={"type": "local_repository", "indexed": False},
    )

    signals = capabilities.BY_KEY["repository"].signals(ledger)
    matched_signal = next(s for s in signals if s.label == _MATCHED_SIGNAL_LABEL)

    assert matched_signal.satisfied is False
    assert "streaming-pipeline" in matched_signal.detail
    assert "hasn't been indexed yet" in matched_signal.detail


def test_repository_signal_generic_detail_when_nothing_was_named_at_all() -> None:
    signals = capabilities.BY_KEY["repository"].signals(Ledger())
    matched_signal = next(s for s in signals if s.label == _MATCHED_SIGNAL_LABEL)

    assert matched_signal.satisfied is False
    assert matched_signal.detail == "the request does not name a known repository"
