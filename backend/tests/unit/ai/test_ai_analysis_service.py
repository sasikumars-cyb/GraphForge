"""Unit tests for AIAnalysisService."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.schemas.analysis_result import AIAnalysisResult, ConfidenceScore
from app.ai.services.ai_analysis_service import AIAnalysisService
from app.core.exceptions import NotFoundError

# -- Fake models for testing without DB -------------------------------------


@dataclass
class FakePullRequest:
    id: uuid.UUID
    repository_id: uuid.UUID
    title: str = "Fix payment flow"
    number: int = 42
    head_ref: str = "fix/pay"
    base_ref: str = "main"


@dataclass
class FakeRepository:
    id: uuid.UUID
    name: str = "order-svc"
    owner: str = "acme"
    default_branch: str = "main"
    full_name: str = "acme/order-svc"


@dataclass
class FakePullRequestAnalysis:
    id: uuid.UUID
    pull_request_id: uuid.UUID
    risk: str = "MEDIUM"
    directly_impacted_services: list[dict[str, str]] | None = None
    indirectly_impacted_services: list[dict[str, str]] | None = None
    impacted_apis: list[dict[str, str]] | None = None
    impacted_topics: list[dict[str, str]] | None = None
    impacted_libraries: list[dict[str, str]] | None = None
    dependency_paths: list[list[dict[str, str]]] | None = None

    def __post_init__(self) -> None:
        if self.directly_impacted_services is None:
            self.directly_impacted_services = []
        if self.indirectly_impacted_services is None:
            self.indirectly_impacted_services = []
        if self.impacted_apis is None:
            self.impacted_apis = []
        if self.impacted_topics is None:
            self.impacted_topics = []
        if self.impacted_libraries is None:
            self.impacted_libraries = []
        if self.dependency_paths is None:
            self.dependency_paths = []


def _ai_result() -> AIAnalysisResult:
    return AIAnalysisResult(
        executive_summary="No breaking changes.",
        confidence=ConfidenceScore(score=0.9, reasoning="Clear analysis"),
        prompt_version="1.0",
    )


@dataclass
class _Mocks:
    db: AsyncMock
    llm_provider: AsyncMock
    impact_engine: AsyncMock
    service: AIAnalysisService


def _make_service(
    *,
    pull_request: FakePullRequest | None = None,
    repository: FakeRepository | None = None,
    existing_analysis: FakePullRequestAnalysis | None = None,
    ai_result: AIAnalysisResult | None = None,
    cross_repo_repositories: list[FakeRepository] | None = None,
) -> _Mocks:
    """Build an AIAnalysisService with mocked dependencies.

    ``db.execute`` now has to distinguish three distinct query shapes:
    - ``select(PullRequestAnalysis)`` (existing deterministic-analysis check)
    - ``select(PullRequestAIAnalysis)`` (existing persist-upsert check)
    - ``select(Repository).where(id.in_(...))`` (new cross-repository name
      resolution) - the only one needing ``.scalars().all()`` rather than
      ``.scalar_one_or_none()``.
    """
    pr_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr = pull_request or FakePullRequest(id=pr_id, repository_id=repo_id)
    repo = repository or FakeRepository(id=repo_id)

    # Mock AsyncSession
    db = AsyncMock()

    async def mock_get(model_cls: type, obj_id: uuid.UUID) -> Any:
        if model_cls.__name__ == "PullRequest":
            return pr if obj_id == pr.id else None
        if model_cls.__name__ == "Repository":
            return repo if obj_id == repo.id else None
        return None

    db.get = AsyncMock(side_effect=mock_get)

    def mock_execute(stmt: Any) -> MagicMock:
        entity = stmt.column_descriptions[0]["entity"]
        result = MagicMock()
        if entity is not None and entity.__name__ == "Repository":
            result.scalars.return_value.all.return_value = cross_repo_repositories or []
        else:
            result.scalar_one_or_none.return_value = existing_analysis
        return result

    db.execute = AsyncMock(side_effect=mock_execute)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    # Mock LLM provider
    llm_provider = AsyncMock()
    llm_provider.analyze = AsyncMock(return_value=ai_result or _ai_result())

    # Mock impact engine
    impact_engine = AsyncMock()
    analysis = existing_analysis or FakePullRequestAnalysis(id=uuid.uuid4(), pull_request_id=pr.id)
    impact_engine.analyze_pull_request = AsyncMock(return_value=analysis)

    service = AIAnalysisService(
        db=db,
        llm_provider=llm_provider,
        impact_engine=impact_engine,
    )
    return _Mocks(db=db, llm_provider=llm_provider, impact_engine=impact_engine, service=service)


# -- Tests ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_success_with_existing_deterministic() -> None:
    """When deterministic analysis exists, skips re-running it."""
    pr_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr = FakePullRequest(id=pr_id, repository_id=repo_id)
    repo = FakeRepository(id=repo_id)
    analysis = FakePullRequestAnalysis(id=uuid.uuid4(), pull_request_id=pr_id)
    expected = _ai_result()

    mocks = _make_service(
        pull_request=pr,
        repository=repo,
        existing_analysis=analysis,
        ai_result=expected,
    )
    result = await mocks.service.analyze(pr_id)

    assert result.executive_summary == "No breaking changes."
    assert result.confidence.score == 0.9
    mocks.llm_provider.analyze.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_runs_deterministic_when_missing() -> None:
    """When no deterministic analysis exists, runs the impact engine."""
    pr_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr = FakePullRequest(id=pr_id, repository_id=repo_id)
    repo = FakeRepository(id=repo_id)

    mocks = _make_service(
        pull_request=pr,
        repository=repo,
        existing_analysis=None,
    )
    result = await mocks.service.analyze(pr_id)

    assert isinstance(result, AIAnalysisResult)
    mocks.impact_engine.analyze_pull_request.assert_awaited_once_with(pr_id)


@pytest.mark.asyncio
async def test_analyze_pull_request_not_found() -> None:
    """Raises NotFoundError when pull request doesn't exist."""
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    llm_provider = AsyncMock()
    impact_engine = AsyncMock()

    service = AIAnalysisService(db=db, llm_provider=llm_provider, impact_engine=impact_engine)

    with pytest.raises(NotFoundError, match="Pull request not found"):
        await service.analyze(uuid.uuid4())


@pytest.mark.asyncio
async def test_analyze_repository_not_found() -> None:
    """Raises NotFoundError when repository doesn't exist."""
    pr_id = uuid.uuid4()
    pr = FakePullRequest(id=pr_id, repository_id=uuid.uuid4())

    db = AsyncMock()

    async def mock_get(model_cls: type, obj_id: uuid.UUID) -> Any:
        if model_cls.__name__ == "PullRequest":
            return pr
        return None

    db.get = AsyncMock(side_effect=mock_get)
    llm_provider = AsyncMock()
    impact_engine = AsyncMock()

    service = AIAnalysisService(db=db, llm_provider=llm_provider, impact_engine=impact_engine)

    with pytest.raises(NotFoundError, match="Repository not found"):
        await service.analyze(pr_id)


@pytest.mark.asyncio
async def test_analyze_persists_result() -> None:
    """Service persists the AI analysis result."""
    pr_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr = FakePullRequest(id=pr_id, repository_id=repo_id)
    repo = FakeRepository(id=repo_id)
    analysis = FakePullRequestAnalysis(id=uuid.uuid4(), pull_request_id=pr_id)

    mocks = _make_service(
        pull_request=pr,
        repository=repo,
        existing_analysis=analysis,
    )
    await mocks.service.analyze(pr_id)

    mocks.db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_resolves_current_repository_with_no_cross_repo_impact() -> None:
    """With no indirectly-impacted services, the context only carries the
    current repository, and no Repository lookup is needed."""
    pr_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr = FakePullRequest(id=pr_id, repository_id=repo_id)
    repo = FakeRepository(id=repo_id, name="order-svc", owner="acme", full_name="acme/order-svc")
    analysis = FakePullRequestAnalysis(id=uuid.uuid4(), pull_request_id=pr_id)

    mocks = _make_service(pull_request=pr, repository=repo, existing_analysis=analysis)
    await mocks.service.analyze(pr_id)

    context = mocks.llm_provider.analyze.call_args.args[0]
    assert context.impacted_repositories == [
        {
            "id": str(repo_id),
            "owner": "acme",
            "name": "order-svc",
            "full_name": "acme/order-svc",
            "relation": "current",
        }
    ]


@pytest.mark.asyncio
async def test_resolves_cross_repository_names() -> None:
    """A cross-repository entry in indirectly_impacted_services is resolved
    to its owner/name via a Repository lookup and reaches the context
    alongside the current repository."""
    pr_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    downstream_repo_id = uuid.uuid4()
    pr = FakePullRequest(id=pr_id, repository_id=repo_id)
    repo = FakeRepository(id=repo_id, name="order-svc", owner="acme", full_name="acme/order-svc")
    downstream_repo = FakeRepository(
        id=downstream_repo_id,
        name="inventory-svc",
        owner="acme",
        full_name="acme/inventory-svc",
    )
    analysis = FakePullRequestAnalysis(
        id=uuid.uuid4(),
        pull_request_id=pr_id,
        indirectly_impacted_services=[
            {
                "id": f"{downstream_repo_id}:component:InventoryConsumer",
                "name": "InventoryConsumer",
                "node_type": "Component",
                "repository_id": str(downstream_repo_id),
            }
        ],
    )

    mocks = _make_service(
        pull_request=pr,
        repository=repo,
        existing_analysis=analysis,
        cross_repo_repositories=[downstream_repo],
    )
    await mocks.service.analyze(pr_id)

    context = mocks.llm_provider.analyze.call_args.args[0]
    relations = {r["name"]: r["relation"] for r in context.impacted_repositories}
    assert relations == {"order-svc": "current", "inventory-svc": "downstream"}


@pytest.mark.asyncio
async def test_unresolvable_cross_repository_id_is_skipped() -> None:
    """A repository_id that no longer resolves to a Repository row (e.g. a
    removed repository) is skipped, not treated as an error."""
    pr_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr = FakePullRequest(id=pr_id, repository_id=repo_id)
    repo = FakeRepository(id=repo_id, name="order-svc", owner="acme", full_name="acme/order-svc")
    analysis = FakePullRequestAnalysis(
        id=uuid.uuid4(),
        pull_request_id=pr_id,
        indirectly_impacted_services=[
            {
                "id": "gone:component:X",
                "name": "X",
                "node_type": "Component",
                "repository_id": str(uuid.uuid4()),
            }
        ],
    )

    mocks = _make_service(
        pull_request=pr,
        repository=repo,
        existing_analysis=analysis,
        cross_repo_repositories=[],
    )
    result = await mocks.service.analyze(pr_id)

    assert isinstance(result, AIAnalysisResult)
    context = mocks.llm_provider.analyze.call_args.args[0]
    assert len(context.impacted_repositories) == 1
    assert context.impacted_repositories[0]["relation"] == "current"
