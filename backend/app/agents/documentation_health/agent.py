"""Documentation Health Agent — goal=analyze_documentation_health.

Standalone, read-only AI Workspace capability (see manifest.py for why it
is not a Workflow stage, and how it differs from the neighbouring
`documentation_review` / `documentation_planning` agents).

Pipeline:

1. Clone the repository, reusing
   `app.indexer.scanner.repository_cloner.clone_repository` — the same
   cloning code the indexing pipeline uses, so repository scanning is not
   duplicated.
2. Discover Markdown via the shared
   `app.agents.documentation.discovery` layer.
3. Run every deterministic check and compute the score
   (`analysis.analyze_documentation` / `analysis.score_findings`).
4. Ask the LLM for narrative only — summary, strengths, areas for
   improvement, next actions — over facts already established.

The score and findings never depend on the model: a failed or malformed
LLM response degrades the report to deterministic-only (with the failure
recorded as evidence) rather than failing the run, because the numbers
are the part that has to be trustworthy.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, AgentOutput, Confidence, Evidence, Subject
from app.agents.documentation.discovery import discover_markdown_files
from app.agents.documentation_health.analysis import (
    analyze_documentation,
    build_stats,
    score_findings,
    summarize_files,
)
from app.agents.documentation_health.schemas import DocumentationHealthReport
from app.agents.llm import STAGE_DOCUMENTATION_HEALTH, StageAwareLLMProvider, stage_for
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.core.exceptions import AppError, NotFoundError
from app.indexer.scanner.repository_cloner import RepositoryCloneError, clone_repository
from app.models.repository import Repository
from app.services.github_service import get_decrypted_access_token

logger = logging.getLogger(__name__)

# Bounds the prompt: a monorepo's findings list can be long, and the
# narrative doesn't improve past a representative sample.
_MAX_FINDINGS_IN_PROMPT = 40


def resolve_repository_subject(repository: Repository) -> Subject:
    """Build a Subject for a repository. Identical `repo:<uuid>` form to
    `app.agents.documentation.agent.resolve_repository_subject` — both
    repository-scoped Workspace agents share the one subject_reference
    format `POST /agent-runs` already resolves."""
    return Subject(
        subject_id=f"repo:{repository.id}",
        subject_type="repository",
        graph_node_ids=[],
        display_name=repository.full_name,
    )


def _extract_repository_uuid(subject_id: str) -> uuid.UUID:
    if not subject_id.startswith("repo:"):
        raise NotFoundError(
            f"Documentation Health Agent expects subject_id 'repo:<uuid>', got '{subject_id}'."
        )
    raw = subject_id[len("repo:") :]
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(
            f"subject_id '{subject_id}' does not contain a valid UUID after 'repo:': {exc}"
        ) from exc


def _strip_json_fence(text: str) -> str:
    """Strip a ```json ... ``` fence if present. Bedrock/Haiku wraps JSON
    responses in a fence often enough in practice — confirmed on live runs
    while building the sibling documentation agent — that
    `ResponseFormat.JSON` plus a prompt instruction is not sufficient on
    its own. No-op when the response is already bare JSON."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return without_open.rsplit("```", 1)[0].strip()


_SYSTEM_PROMPT = (
    "You are writing the narrative sections of a Documentation Health Report "
    "for a software repository. You are given the repository's documentation "
    "statistics, its computed health score, and a list of findings that were "
    "already determined deterministically.\n\n"
    "The score and findings are FACTS. Do not recompute, dispute, or invent "
    "them, and do not invent files or findings that are not in the input.\n\n"
    "Respond as JSON matching exactly this shape:\n"
    "{\n"
    '  "summary": "2-4 sentences on the overall state of this documentation",\n'
    '  "strengths": ["what this documentation genuinely does well"],\n'
    '  "areas_for_improvement": ["what most needs attention, most important first"],\n'
    '  "suggested_next_actions": ["concrete, specific next steps a maintainer can take"]\n'
    "}\n\n"
    "Rules:\n"
    "- Ground every statement in the supplied stats and findings.\n"
    "- If there are no findings, say so plainly and keep strengths honest — a "
    "small repository with one good README is not 'comprehensive documentation'.\n"
    "- suggested_next_actions should be actionable and specific (name the file or "
    "the gap), not generic advice like 'improve documentation'.\n"
    "- Do not recommend tools, CI checks, or process changes — only documentation work."
)


class DocumentationHealthAgent:
    """Implements IAgent for goal=analyze_documentation_health. Stateless."""

    async def run(self, context: AgentContext) -> AgentOutput:
        db: AsyncSession = context.extras["db"]
        user_id = context.extras.get("user_id")
        repository_id = _extract_repository_uuid(context.subject.subject_id)
        repository = await self._load_repository(db, repository_id, user_id)

        evidence: list[Evidence] = []
        access_token = await get_decrypted_access_token(db, repository.user_id)

        try:
            async with clone_repository(
                repository.html_url, repository.default_branch, access_token
            ) as repo_path:
                files = discover_markdown_files(repo_path)
                findings = analyze_documentation(repo_path, files)
                stats = build_stats(files)
                file_summaries = summarize_files(files)
        except RepositoryCloneError as exc:
            logger.warning(
                "documentation_health_clone_failed repository=%s error=%s",
                repository.full_name,
                exc,
            )
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="repository_cloner:clone_repository",
                    summary=f"Could not clone {repository.full_name}: {exc}",
                    status="failed",
                )
            )
            return self._failed_output(context, repository, evidence, str(exc))

        score, grade, breakdown = score_findings(findings)

        evidence.append(
            Evidence(
                kind="tool_call",
                reference="discovery:discover_markdown_files",
                summary=(
                    f"Found {len(files)} Markdown file(s) across "
                    f"{stats.distinct_doc_directories} director(ies) in {repository.full_name}."
                ),
                status="success",
            )
        )
        evidence.append(
            Evidence(
                kind="tool_call",
                reference="analysis:score_findings",
                summary=(
                    f"Documentation health score {score}/100 ({grade}) from "
                    f"{len(findings)} deterministic finding(s) across "
                    f"{len(breakdown)} categor(ies)."
                ),
                status="success",
            )
        )

        narrative, narrative_evidence = await self._synthesize(
            context, repository, stats, score, grade, findings
        )
        evidence.append(narrative_evidence)

        report = DocumentationHealthReport(
            repository_full_name=repository.full_name,
            health_score=score,
            grade=grade,
            summary=narrative.get("summary", "")
            or f"Documentation health score {score}/100 ({grade}).",
            stats=stats,
            files_reviewed=file_summaries,
            findings=findings,
            score_breakdown=breakdown,
            strengths=self._string_list(narrative.get("strengths")),
            areas_for_improvement=self._string_list(narrative.get("areas_for_improvement")),
            suggested_next_actions=self._string_list(narrative.get("suggested_next_actions")),
        )

        return AgentOutput(
            agent_id="documentation_health",
            subject_id=context.subject.subject_id,
            confidence=Confidence(
                # The score itself is deterministic, so confidence reflects
                # how complete the *input* was, not model certainty: a
                # repository with no Markdown at all gives this agent very
                # little to reason about.
                score=0.9 if files else 0.5,
                reasoning=(
                    f"Health score {score}/100 computed deterministically from "
                    f"{len(files)} Markdown file(s) and {len(findings)} finding(s); "
                    "narrative sections generated by the LLM over those facts."
                ),
            ),
            evidence=evidence,
            result=report.model_dump(),
            prompt_version=report.prompt_version,
            output_ref=f"documentation-health:{repository.id}",
        )

    # -- steps -------------------------------------------------------------

    async def _load_repository(
        self, db: AsyncSession, repository_id: uuid.UUID, user_id: Any
    ) -> Repository:
        result = await db.execute(
            select(Repository).where(Repository.id == repository_id, Repository.user_id == user_id)
        )
        repository = result.scalar_one_or_none()
        if repository is None:
            raise NotFoundError(f"Repository '{repository_id}' not found for this account.")
        return repository

    async def _synthesize(
        self, context, repository, stats, score, grade, findings
    ) -> tuple[dict[str, Any], Evidence]:
        user_prompt = json.dumps(
            {
                "repository": repository.full_name,
                "health_score": score,
                "grade": grade,
                "stats": stats.model_dump(),
                "findings": [f.model_dump() for f in findings[:_MAX_FINDINGS_IN_PROMPT]],
                "total_findings": len(findings),
            }
        )
        try:
            provider = StageAwareLLMProvider(
                stage=stage_for(context.extras, STAGE_DOCUMENTATION_HEALTH), model=context.model
            )
            response = await provider.complete(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                options=LLMRequestOptions(response_format=ResponseFormat.JSON),
            )
            parsed = json.loads(_strip_json_fence(response.text))
            return parsed, Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary="Generated the report's narrative sections from the computed findings.",
                status="success",
            )
        except (AppError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "documentation_health_synthesis_failed repository=%s error=%s",
                repository.full_name,
                exc,
            )
            return {}, Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=(
                    f"Narrative could not be generated ({exc}); the score and findings "
                    "below are unaffected."
                ),
                status="failed",
            )

    def _string_list(self, value: Any) -> list[str]:
        """The narrative fields are free-form model output — coerce to a
        clean list of strings rather than trusting the shape."""
        if not isinstance(value, list):
            return []
        return [
            str(item) for item in value if isinstance(item, str | int | float) and str(item).strip()
        ]

    def _failed_output(
        self, context: AgentContext, repository: Repository, evidence: list[Evidence], error: str
    ) -> AgentOutput:
        report = DocumentationHealthReport(
            repository_full_name=repository.full_name,
            health_score=0,
            grade="critical",
            summary=f"Documentation health analysis could not be completed: {error}",
        )
        return AgentOutput(
            agent_id="documentation_health",
            subject_id=context.subject.subject_id,
            confidence=Confidence(score=0.0, reasoning=error),
            evidence=evidence,
            result=report.model_dump(),
            output_ref=f"documentation-health:{repository.id}",
        )
