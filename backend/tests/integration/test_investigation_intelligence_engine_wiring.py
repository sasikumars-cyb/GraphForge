"""ADR 0021 — the three Phase 1 collection call sites actually fire.

Runs the real `discover()` loop (fake investigators standing in for real
providers, same pattern `test_context_reasoning_engine.py` uses) against a
real `InvestigationIntelligenceService`/Postgres transaction, and proves:

- a `ProviderOutcomeEvent` row is written for a documentation action that
  yields evidence, scoped to the `KnowledgeConnection` its provider name
  resolves to
- an `InvestigationOutcomeEvent` row is written at `investigate()`'s clean
  exit, scoped to the repository the ledger established
- `session.intelligence=None` (every call site not yet wired, and every
  pre-existing test) writes nothing and changes no other engine behavior
- a `_failure_scope`/`_record_failed_outcome` degrade to "skip" exactly
  when no repository fact and no explicit selection exist yet, and
  otherwise write `terminal_outcome="FAILED"`

Fake investigators, not the real providers — the loop's own decisions and
the recording call sites around them are what's under test here, not any
one provider's retrieval logic (that's `test_context_providers.py`'s job).

`synthesize_engineering_understanding` is replaced with a deterministic
fake throughout, exactly like `test_context_reasoning_engine.py` does —
the real LLM-backed version is exercised elsewhere, and this file is only
about the Investigation Intelligence wiring around it. Every query is also
scoped to a `uuid4`-unique repository/connection name generated per test:
`db_session`'s per-test rollback only isolates this test's own writes, not
pre-existing rows already committed in the shared dev database by real
traffic against this same running backend, so an unscoped `SELECT *`
would see those too.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context_discovery.agent import _failure_scope, _record_failed_outcome
from app.context_pipeline.reasoning.engine import discover
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    InvestigationOutcome,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.memory import WorkingContext
from app.investigation_intelligence.contracts import InvestigationScope
from app.investigation_intelligence.service import InvestigationIntelligenceService
from app.models.investigation_intelligence import (
    InvestigationOutcomeRecord,
    InvestigationProviderEventRecord,
)
from app.models.knowledge_connection import KnowledgeConnection

pytestmark = pytest.mark.asyncio

async def _fake_synthesize(state: WorkingContext, session: SessionContext) -> None:
    return None


def _no_llm_synthesis():  # noqa: ANN201 - returns an unittest.mock.patch context manager
    return patch(
        "app.context_pipeline.reasoning.understanding.synthesize_engineering_understanding",
        new=_fake_synthesize,
    )


class _RepoInvestigator:
    """Stands in for `GraphInvestigator` — establishes a `repository` fact
    once, then goes silent (so the loop can terminate)."""

    name = "graph"

    def __init__(self, repo_name: str) -> None:
        self._repo_name = repo_name

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        if state.ledger.attempted(self.name, "survey"):
            return []
        return [
            InvestigationAction(
                provider=self.name,
                key="survey",
                intent="Surveying indexed repositories",
                targets="repository",
                cost=0,
            )
        ]

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        evidence = recorder.evidence("success", "graph looked.")
        recorder.fact("repository", self._repo_name, value={"name": self._repo_name}, evidence=evidence)
        return InvestigationOutcome(observation="graph found 1 repo.", yielded=True)


class _DocsInvestigator:
    """Stands in for `ConfluenceProvider` — one paid documentation action,
    then silent."""

    def __init__(self, provider_name: str) -> None:
        self.name = provider_name

    def propose(self, state: WorkingContext) -> list[InvestigationAction]:
        if state.ledger.attempted(self.name, "search"):
            return []
        return [
            InvestigationAction(
                provider=self.name,
                key="search",
                intent="Searching Confluence",
                targets="documentation",
                cost=1,
            )
        ]

    async def run(
        self, action: InvestigationAction, session: SessionContext, recorder: Recorder
    ) -> InvestigationOutcome:
        evidence = recorder.evidence("success", "confluence_mcp found a page.")
        recorder.fact("document", "runbook", value={"title": "runbook"}, evidence=evidence)
        return InvestigationOutcome(observation="confluence_mcp found 1 doc.", yielded=True)


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _enabled_connection(db_session: AsyncSession, source_type: str) -> uuid.UUID:
    connection = KnowledgeConnection(
        source_type=source_type,
        name="Test Confluence",
        transport="mcp",
        auth_method="oauth",
        enabled=True,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection.id


class TestProviderOutcomeCollection:
    async def test_documentation_action_writes_a_scoped_provider_event(
        self, db_session: AsyncSession
    ) -> None:
        provider_name = _unique_name("confluence_mcp")
        repo_name = _unique_name("payment-service")
        connection_id = await _enabled_connection(db_session, provider_name)
        intelligence = InvestigationIntelligenceService(db_session)
        session = SessionContext(db=db_session, user_id=None, intelligence=intelligence)

        with _no_llm_synthesis():
            await discover(
                request="Investigate the payment-service runbook",
                session=session,
                investigators=[_RepoInvestigator(repo_name), _DocsInvestigator(provider_name)],
            )

        rows = (
            (
                await db_session.execute(
                    select(InvestigationProviderEventRecord).where(
                        InvestigationProviderEventRecord.provider == provider_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.capability == "documentation"
        assert row.outcome == "success"
        assert row.scope_type == "knowledge_connection"
        assert row.scope_id == str(connection_id)
        assert row.yielded_evidence is True

    async def test_no_intelligence_configured_writes_nothing(self, db_session: AsyncSession) -> None:
        provider_name = _unique_name("confluence_mcp")
        repo_name = _unique_name("payment-service")
        session = SessionContext(db=db_session, user_id=None)  # intelligence defaults to None

        with _no_llm_synthesis():
            await discover(
                request="Investigate the payment-service runbook",
                session=session,
                investigators=[_RepoInvestigator(repo_name), _DocsInvestigator(provider_name)],
            )

        rows = (
            (
                await db_session.execute(
                    select(InvestigationProviderEventRecord).where(
                        InvestigationProviderEventRecord.provider == provider_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
        outcomes = (
            (
                await db_session.execute(
                    select(InvestigationOutcomeRecord).where(
                        InvestigationOutcomeRecord.scope_id == repo_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert outcomes == []

    async def test_documentation_action_writes_nothing_without_an_enabled_connection(
        self, db_session: AsyncSession
    ) -> None:
        # No KnowledgeConnection row seeded for this provider name —
        # `_investigation_scope` can't resolve one and must skip, not
        # fabricate.
        provider_name = _unique_name("confluence_mcp")
        repo_name = _unique_name("payment-service")
        intelligence = InvestigationIntelligenceService(db_session)
        session = SessionContext(db=db_session, user_id=None, intelligence=intelligence)

        with _no_llm_synthesis():
            await discover(
                request="Investigate the payment-service runbook",
                session=session,
                investigators=[_RepoInvestigator(repo_name), _DocsInvestigator(provider_name)],
            )

        rows = (
            (
                await db_session.execute(
                    select(InvestigationProviderEventRecord).where(
                        InvestigationProviderEventRecord.provider == provider_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


class TestInvestigationOutcomeCollection:
    async def test_clean_exit_writes_an_outcome_scoped_to_the_repository(
        self, db_session: AsyncSession
    ) -> None:
        provider_name = _unique_name("confluence_mcp")
        repo_name = _unique_name("payment-service")
        intelligence = InvestigationIntelligenceService(db_session)
        session = SessionContext(db=db_session, user_id=None, intelligence=intelligence)

        with _no_llm_synthesis():
            state = await discover(
                request="Investigate the payment-service runbook",
                session=session,
                investigators=[_RepoInvestigator(repo_name), _DocsInvestigator(provider_name)],
            )

        rows = (
            (
                await db_session.execute(
                    select(InvestigationOutcomeRecord).where(
                        InvestigationOutcomeRecord.scope_id == repo_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.terminal_outcome == state.readiness
        assert row.scope_type == "repository"
        assert row.cycles_used == state.metadata.iteration


class TestFailedOutcomeFallback:
    async def test_failure_scope_none_when_nothing_resolvable(self) -> None:
        assert _failure_scope(None, None) is None
        assert _failure_scope(None, []) is None

    async def test_failure_scope_falls_back_to_explicit_repository_selection(self) -> None:
        scope = _failure_scope(None, ["payment-service"])
        assert scope == InvestigationScope(scope_type="repository", scope_id="payment-service")

    async def test_record_failed_outcome_skips_when_scope_unresolvable(
        self, db_session: AsyncSession
    ) -> None:
        investigation_id = _unique_name("inv-failed")
        intelligence = InvestigationIntelligenceService(db_session)
        # Never raises, never writes, when there is nothing to scope to.
        await _record_failed_outcome(
            intelligence=intelligence,
            investigation_id=investigation_id,
            request="some request",
            state=None,
            explicit_repositories=None,
        )
        rows = (
            (
                await db_session.execute(
                    select(InvestigationOutcomeRecord).where(
                        InvestigationOutcomeRecord.investigation_id == investigation_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    async def test_record_failed_outcome_writes_when_explicit_repository_known(
        self, db_session: AsyncSession
    ) -> None:
        investigation_id = _unique_name("inv-failed")
        repo_name = _unique_name("payment-service")
        intelligence = InvestigationIntelligenceService(db_session)
        await _record_failed_outcome(
            intelligence=intelligence,
            investigation_id=investigation_id,
            request="some request",
            state=None,
            explicit_repositories=[repo_name],
        )
        rows = (
            (
                await db_session.execute(
                    select(InvestigationOutcomeRecord).where(
                        InvestigationOutcomeRecord.investigation_id == investigation_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].terminal_outcome == "FAILED"
        assert rows[0].scope_id == repo_name
