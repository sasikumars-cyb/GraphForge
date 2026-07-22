"""AI analysis orchestration service.

Coordinates the full AI analysis workflow for a pull request:
1. Ensure deterministic analysis exists (run if missing).
2. Build bounded context from deterministic results.
3. Call the LLM provider.
4. Persist and return the result.

Never duplicates deterministic logic — delegates to
:class:`~app.analysis.engine.impact_analysis_engine.ImpactAnalysisEngine`.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import ContextBuilder
from app.analysis.engine.impact_analysis_engine import ImpactAnalysisEngine
from app.core.exceptions import NotFoundError
from app.models.pull_request import PullRequest
from app.models.pull_request_ai_analysis import PullRequestAIAnalysis
from app.models.pull_request_analysis import PullRequestAnalysis
from app.models.repository import Repository

logger = logging.getLogger(__name__)


class AIAnalysisService:
    """Orchestrates AI-enriched impact analysis for a pull request.

    Depends on:
    - ``ImpactAnalysisEngine`` for deterministic analysis (reused, never duplicated).
    - ``ILLMProvider`` for the AI enrichment call.
    - ``AsyncSession`` for persistence.
    """

    def __init__(
        self,
        db: AsyncSession,
        llm_provider: ILLMProvider,
        impact_engine: ImpactAnalysisEngine,
    ) -> None:
        self._db = db
        self._llm_provider = llm_provider
        self._impact_engine = impact_engine

    async def analyze(self, pull_request_id: uuid.UUID) -> AIAnalysisResult:
        """Run AI analysis for a pull request.

        Ensures deterministic analysis exists first (runs it if missing),
        then builds context, calls the provider, persists, and returns.
        """
        pull_request = await self._db.get(PullRequest, pull_request_id)
        if pull_request is None:
            raise NotFoundError("Pull request not found.")

        repository = await self._db.get(Repository, pull_request.repository_id)
        if repository is None:
            raise NotFoundError("Repository not found.")

        # 1. Ensure deterministic analysis exists
        deterministic = await self._get_or_run_deterministic(pull_request_id)

        # 2. Build context
        impacted_repositories = await self._resolve_impacted_repositories(repository, deterministic)
        context = (
            ContextBuilder()
            .with_repository(
                name=repository.name,
                owner=repository.owner,
                default_branch=repository.default_branch,
            )
            .with_pull_request(
                title=pull_request.title,
                number=pull_request.number,
                head_ref=pull_request.head_ref,
                base_ref=pull_request.base_ref,
            )
            .with_analysis_from_persisted(deterministic)
            .with_repositories(impacted_repositories)
            .build()
        )

        # 3. Call provider
        logger.info(
            "Calling AI provider for PR %s (%s)",
            pull_request_id,
            pull_request.title,
        )
        result = await self._llm_provider.analyze(context)

        # 3b. Strip any repository the model invented, independent of
        # whether it followed the prompt's grounding instructions - a
        # deterministic backstop, not a hope.
        known_repository_names = {r["name"] for r in impacted_repositories}
        result.release_coordination_plan = result.release_coordination_plan.grounded_in(
            known_repository_names, repository.name
        )

        # 4. Persist
        await self._persist(pull_request_id, result)

        return result

    async def _get_or_run_deterministic(self, pull_request_id: uuid.UUID) -> PullRequestAnalysis:
        """Return existing deterministic analysis or run it now."""
        stmt = select(PullRequestAnalysis).where(
            PullRequestAnalysis.pull_request_id == pull_request_id
        )
        existing = await self._db.execute(stmt)
        analysis = existing.scalar_one_or_none()

        if analysis is not None:
            return analysis

        logger.info("No deterministic analysis found for PR %s, running now.", pull_request_id)
        return await self._impact_engine.analyze_pull_request(pull_request_id)

    async def _resolve_impacted_repositories(
        self, repository: Repository, deterministic: PullRequestAnalysis
    ) -> list[dict[str, str]]:
        """Resolve the current repository plus every repository id already
        present in the deterministic engine's cross-repository impact
        (``indirectly_impacted_services``) to human-readable metadata.

        A Postgres primary-key lookup on ids the deterministic engine
        already produced — never a new Neo4j traversal, never dependency
        discovery. Unresolvable ids (e.g. a repository since removed) are
        silently skipped rather than treated as an error.
        """
        downstream_ids: set[uuid.UUID] = set()
        for node in deterministic.indirectly_impacted_services:
            raw_id = node.get("repository_id")
            if not raw_id or raw_id == str(repository.id):
                continue
            try:
                downstream_ids.add(uuid.UUID(raw_id))
            except ValueError:
                continue

        resolved = [
            {
                "id": str(repository.id),
                "owner": repository.owner,
                "name": repository.name,
                "full_name": repository.full_name,
                "relation": "current",
            }
        ]

        if downstream_ids:
            stmt = select(Repository).where(Repository.id.in_(downstream_ids))
            result = await self._db.execute(stmt)
            for repo in result.scalars().all():
                resolved.append(
                    {
                        "id": str(repo.id),
                        "owner": repo.owner,
                        "name": repo.name,
                        "full_name": repo.full_name,
                        "relation": "downstream",
                    }
                )

        return resolved

    async def _persist(
        self, pull_request_id: uuid.UUID, result: AIAnalysisResult
    ) -> PullRequestAIAnalysis:
        """Persist or replace the AI analysis for a pull request."""
        stmt = select(PullRequestAIAnalysis).where(
            PullRequestAIAnalysis.pull_request_id == pull_request_id
        )
        existing = await self._db.execute(stmt)
        ai_analysis = existing.scalar_one_or_none()

        fields: dict[str, object] = {
            "executive_summary": result.executive_summary,
            "breaking_changes": [bc.model_dump() for bc in result.breaking_changes],
            "migration_advice": [ma.model_dump() for ma in result.migration_advice],
            "suggested_reviewers": [sr.model_dump() for sr in result.suggested_reviewers],
            "regression_tests": [rt.model_dump() for rt in result.regression_tests],
            "confidence_score": result.confidence.score,
            "confidence_reasoning": result.confidence.reasoning,
            "prompt_version": result.prompt_version,
        }

        if ai_analysis is None:
            ai_analysis = PullRequestAIAnalysis(pull_request_id=pull_request_id, **fields)
            self._db.add(ai_analysis)
        else:
            for field_name, value in fields.items():
                setattr(ai_analysis, field_name, value)

        await self._db.commit()
        await self._db.refresh(ai_analysis)
        return ai_analysis
