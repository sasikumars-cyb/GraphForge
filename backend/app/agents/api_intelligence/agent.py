"""API Intelligence Agent — goal=analyze_api_intelligence.

Standalone AI Workspace capability (see manifest.py's docstring for why
this is deliberately not a Workflow stage, and for the Phase 1 Markdown-
only scope). Given a tracked repository:

1. Clones it (reusing `app.indexer.scanner.repository_cloner.
   clone_repository` — the same cloning code the Documentation Agent and
   the indexing pipeline itself use, per "do not duplicate repository
   scanning logic").
2. Discovers its Markdown files (`app.agents.documentation.discovery.
   discover_markdown_files` — reused, not duplicated) and the real internal
   links between them (`discovery.discover_relationships`, this agent's own
   deterministic addition).
3. Hands the LLM every discovered file's content and asks, in one JSON-mode
   call, for: the extracted API surface (endpoints, auth, rate limits,
   versioning, ...), a security review against OWASP API Top 10 categories,
   five 0-100 scores, and an explicit "Missing Information" list for
   anything the documentation doesn't cover — never invented.
4. Never reads source code and never queries the indexed architecture
   graph (see manifest.py) — everything in the result is traceable to a
   Markdown file this run actually read.

Output rendering (OpenAPI/Postman/Markdown/HTML) is deliberately NOT done
here — see `app.agents.api_intelligence.renderers`, called on demand by
`app.api.v1.routers.api_intelligence`'s export endpoints from the
persisted `AgentStep.result`, so re-exporting in a different format never
re-runs the LLM.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, AgentOutput, Confidence, Evidence, Subject
from app.agents.api_intelligence.discovery import discover_relationships
from app.agents.api_intelligence.schemas import (
    ApiIntelligenceResult,
    ApiIntelligenceScores,
    DocumentRelationship,
    MarkdownFileSummary,
)
from app.agents.documentation.discovery import MarkdownFile, discover_markdown_files
from app.agents.llm import STAGE_API_INTELLIGENCE, StageAwareLLMProvider, stage_for
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.core.exceptions import AppError, NotFoundError
from app.indexer.scanner.repository_cloner import RepositoryCloneError, clone_repository
from app.models.repository import Repository
from app.services.github_service import get_decrypted_access_token

logger = logging.getLogger(__name__)

# Same bounding rationale as `app.agents.documentation.agent` — keeps the
# prompt bounded on a large docs/ tree.
_MAX_FILES_WITH_CONTENT = 25
_MAX_CONTENT_CHARS_PER_FILE = 8000


def resolve_repository_subject(repository: Repository) -> Subject:
    """Build a Subject for a repository — same role as
    `documentation.agent.resolve_repository_subject` for the Documentation
    Agent. subject_id format: "repo:<uuid>"."""
    return Subject(
        subject_id=f"repo:{repository.id}",
        subject_type="repository",
        graph_node_ids=[],
        display_name=repository.full_name,
    )


def _extract_repository_uuid(subject_id: str) -> uuid.UUID:
    if not subject_id.startswith("repo:"):
        raise NotFoundError(f"API Intelligence Agent expects subject_id 'repo:<uuid>', got '{subject_id}'.")
    raw = subject_id[len("repo:") :]
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(
            f"subject_id '{subject_id}' does not contain a valid UUID after 'repo:': {exc}"
        ) from exc


def _strip_json_fence(text: str) -> str:
    """Strip a leading/trailing ```json ... ``` (or bare ```) code fence —
    same defensive helper as `app.agents.documentation.agent`'s, kept as a
    separate copy per that module's own docstring precedent (two agents
    calling the same provider in JSON mode independently need the same
    small defense, and it's cheaper to duplicate three lines than to
    introduce a shared-utilities module for them)."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return without_open.rsplit("```", 1)[0].strip()


_SYSTEM_PROMPT = (
    "You are an API documentation intelligence system. You are given the full "
    "content of one repository's Markdown documentation files ONLY — you have "
    "NOT seen any source code and must never assume, guess, or invent anything "
    "beyond what these files literally say.\n\n"
    "Your job, respond as JSON matching exactly this shape:\n"
    "{\n"
    '  "executive_summary": "2-3 sentence overview of the documented API surface",\n'
    '  "base_urls": ["https://api.example.com"],\n'
    '  "endpoints": [{\n'
    '    "method": "GET"|"POST"|"PUT"|"PATCH"|"DELETE"|"HEAD"|"OPTIONS",\n'
    '    "path": "/v1/widgets/{id}",\n'
    '    "base_url": "matches one entry in base_urls, or \'\'",\n'
    '    "description": "...",\n'
    '    "parameters": [{"name": "...", "location": "path"|"query"|"header"|"body"|"cookie", '
    '"type": "string"|"integer"|"boolean"|"object"|"array", "required": true, "description": "..."}],\n'
    '    "request_example": "raw example body as documented, or \'\'",\n'
    '    "response_example": "raw example body as documented, or \'\'",\n'
    '    "status_codes": ["200", "404"],\n'
    '    "authentication_required": true,\n'
    '    "owner": "team/person if documented, else \'\'",\n'
    '    "version": "e.g. v1, if documented, else \'\'",\n'
    '    "source_file": "the exact file path (from the input) this endpoint was documented in"\n'
    "  }],\n"
    '  "authentication": "prose describing the documented auth mechanism, or \'\' if undocumented",\n'
    '  "authorization": "prose, or \'\'",\n'
    '  "rate_limits": "prose, or \'\'",\n'
    '  "pagination": "prose, or \'\'",\n'
    '  "versioning": "prose, or \'\'",\n'
    '  "dependencies": ["external services/APIs this API depends on, if documented"],\n'
    '  "assumptions": ["things you had to assume because the docs were ambiguous"],\n'
    '  "todos": ["literal TODO/FIXME-style notes found in the docs"],\n'
    '  "open_questions": ["genuine ambiguities worth a human answering"],\n'
    '  "security_findings": [{\n'
    '    "category": "authentication"|"authorization"|"input_validation"|"sensitive_data_exposure"|'
    '"rate_limiting"|"replay_protection"|"https_usage"|"token_handling"|"secrets"|"pii"|'
    '"error_leakage"|"owasp_api_top_10",\n'
    '    "severity": "critical"|"high"|"medium"|"low",\n'
    '    "title": "short label",\n'
    '    "description": "what the documentation shows or fails to show",\n'
    '    "why_it_matters": "concrete consequence if unaddressed",\n'
    '    "recommendation": "specific, actionable fix",\n'
    '    "confidence": 0.8\n'
    "  }],\n"
    '  "scores": {"documentation_completeness": 0, "security_score": 0, "api_quality_score": 0, '
    '"readability_score": 0, "consistency_score": 0, "overall_readiness_score": 0},\n'
    '  "missing_information": ["exactly what documentation should be added, one item each"]\n'
    "}\n\n"
    "Rules:\n"
    "- NEVER fabricate an endpoint, parameter, base URL, or example that isn't literally present in "
    "the provided files. If the documentation doesn't specify something (e.g. no rate limit is "
    "mentioned), leave that field empty/'' rather than guessing a plausible-sounding value.\n"
    "- Every 'missing_information' entry must name a SPECIFIC gap (e.g. 'No documented error response "
    "schema for POST /v1/widgets' — not 'improve documentation').\n"
    "- 'source_file' on every endpoint must be one of the file paths you were given, verbatim.\n"
    "- Security findings must be grounded in what the documentation shows or conspicuously omits for "
    "a documented endpoint — e.g. an endpoint with no mentioned auth is a legitimate finding; do not "
    "invent findings about mechanisms the docs never discuss at all in either direction.\n"
    "- Scores are 0-100 integers, each independently justified by what you found — not copies of each "
    "other and not a single overall impression restated five times.\n"
    "- An empty endpoints/security_findings list for thin documentation is a correct, complete answer, "
    "not a failure — reflect that honestly in missing_information and low scores instead of padding "
    "the other lists."
)


class ApiIntelligenceAgent:
    """Implements IAgent for goal=analyze_api_intelligence. Stateless singleton."""

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
                relationships = discover_relationships(repo_path, files)
                file_contents = self._read_bounded_content(files)
        except RepositoryCloneError as exc:
            logger.warning("api_intelligence_clone_failed repository=%s error=%s", repository.full_name, exc)
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="repository_cloner:clone_repository",
                    summary=f"Could not clone {repository.full_name}: {exc}",
                    status="failed",
                )
            )
            return self._empty_result(context, repository, evidence, str(exc))

        evidence.append(
            Evidence(
                kind="tool_call",
                reference="discovery:discover_markdown_files",
                summary=f"Found {len(files)} Markdown file(s) in {repository.full_name}.",
                status="success",
            )
        )
        evidence.append(
            Evidence(
                kind="tool_call",
                reference="discovery:discover_relationships",
                summary=f"Discovered {len(relationships)} internal document link(s).",
                status="success",
            )
        )

        if not file_contents:
            return self._empty_result(
                context, repository, evidence,
                "No Markdown documentation was found in this repository.",
            )

        llm_result, llm_evidence = await self._synthesize(context, repository, file_contents)
        evidence.append(llm_evidence)

        result = self._build_result(repository, files, relationships, llm_result)

        confidence_score = 0.8 if llm_result else 0.2
        return AgentOutput(
            agent_id="api_intelligence",
            subject_id=context.subject.subject_id,
            confidence=Confidence(
                score=confidence_score,
                reasoning=(
                    f"Extracted API intelligence from {len(files)} Markdown file(s); "
                    f"{len(result.endpoints)} endpoint(s) and {len(result.security_findings)} "
                    "security finding(s) identified, derived only from documentation content."
                ),
            ),
            evidence=evidence,
            result=result.model_dump(),
            prompt_version=result.prompt_version,
            output_ref=f"api-intelligence:{repository.id}",
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

    def _read_bounded_content(self, files: list[MarkdownFile]) -> list[dict[str, str]]:
        contents: list[dict[str, str]] = []
        for f in files[:_MAX_FILES_WITH_CONTENT]:
            try:
                text = f.absolute_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            contents.append({"path": f.relative_path, "content": text[:_MAX_CONTENT_CHARS_PER_FILE]})
        return contents

    async def _synthesize(
        self,
        context: AgentContext,
        repository: Repository,
        file_contents: list[dict[str, str]],
    ) -> tuple[dict[str, Any], Evidence]:
        user_prompt = json.dumps({"repository": repository.full_name, "markdown_files": file_contents})
        try:
            provider = StageAwareLLMProvider(
                stage=stage_for(context.extras, STAGE_API_INTELLIGENCE), model=context.model
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
                summary=(
                    f"LLM synthesis extracted {len(parsed.get('endpoints', []))} endpoint(s) and "
                    f"{len(parsed.get('security_findings', []))} security finding(s)."
                ),
                status="success",
            )
        except (AppError, json.JSONDecodeError) as exc:
            logger.warning("api_intelligence_synthesis_failed repository=%s error=%s", repository.full_name, exc)
            return {}, Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=f"LLM synthesis could not be completed: {exc}",
                status="failed",
            )

    def _build_result(
        self,
        repository: Repository,
        files: list[MarkdownFile],
        relationships: list[Any],
        llm_result: dict[str, Any],
    ) -> ApiIntelligenceResult:
        files_reviewed = [
            MarkdownFileSummary(
                path=f.relative_path,
                size_bytes=f.size_bytes,
                heading_count=0,
            )
            for f in files
        ]
        document_relationships = [
            DocumentRelationship(from_file=r.from_file, to_file=r.to_file) for r in relationships
        ]

        scores_raw = llm_result.get("scores") or {}
        try:
            scores = ApiIntelligenceScores.model_validate(scores_raw)
        except Exception:
            scores = ApiIntelligenceScores()

        try:
            return ApiIntelligenceResult(
                repository_full_name=repository.full_name,
                executive_summary=llm_result.get("executive_summary", ""),
                base_urls=llm_result.get("base_urls") or [],
                endpoints=llm_result.get("endpoints") or [],
                authentication=llm_result.get("authentication", ""),
                authorization=llm_result.get("authorization", ""),
                rate_limits=llm_result.get("rate_limits", ""),
                pagination=llm_result.get("pagination", ""),
                versioning=llm_result.get("versioning", ""),
                dependencies=llm_result.get("dependencies") or [],
                assumptions=llm_result.get("assumptions") or [],
                todos=llm_result.get("todos") or [],
                open_questions=llm_result.get("open_questions") or [],
                security_findings=llm_result.get("security_findings") or [],
                scores=scores,
                missing_information=llm_result.get("missing_information") or [],
                files_reviewed=files_reviewed,
                document_relationships=document_relationships,
            )
        except Exception as exc:
            logger.warning("api_intelligence_result_validation_failed repository=%s error=%s", repository.full_name, exc)
            return ApiIntelligenceResult(
                repository_full_name=repository.full_name,
                executive_summary=(
                    "The AI provider's response did not match the expected shape; "
                    "showing what could be salvaged."
                ),
                missing_information=[
                    "Analysis could not be fully parsed — re-run this agent.",
                ],
                files_reviewed=files_reviewed,
                document_relationships=document_relationships,
            )

    def _empty_result(
        self,
        context: AgentContext,
        repository: Repository,
        evidence: list[Evidence],
        error: str,
    ) -> AgentOutput:
        result = ApiIntelligenceResult(
            repository_full_name=repository.full_name,
            executive_summary=f"API intelligence analysis could not be completed: {error}",
            missing_information=[error],
        )
        return AgentOutput(
            agent_id="api_intelligence",
            subject_id=context.subject.subject_id,
            confidence=Confidence(score=0.0, reasoning=error),
            evidence=evidence,
            result=result.model_dump(),
            output_ref=f"api-intelligence:{repository.id}",
        )
