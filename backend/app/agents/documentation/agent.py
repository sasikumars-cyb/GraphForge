"""Documentation Agent — goal=review_documentation.

Standalone AI Workspace capability (see manifest.py's docstring for why
this is deliberately not a Workflow stage). Given a tracked, indexed
repository:

1. Clones it (reusing `app.indexer.scanner.repository_cloner.
   clone_repository` — the exact same cloning code the indexing pipeline
   itself uses, per "do not duplicate repository scanning logic").
2. Discovers its Markdown files (`discovery.discover_markdown_files`) and
   runs two cheap deterministic checks: broken internal links and
   byte-identical duplicates.
3. Reads the repository's already-indexed architecture graph (Neo4j
   `Component` nodes — the same primitive
   `TraverseArchitectureGraphTool`/`GraphInvestigator` already use, called
   directly here since only one repository is ever in scope) to compare
   against.
4. Hands the LLM the Markdown content, the deterministic findings, and the
   component list, and asks for: additional findings a text-only pass can
   catch (outdated content, missing documentation, files needing updates)
   plus proposed Markdown for updates and brand-new documents. Nothing is
   ever written back to the repository automatically — this only ever
   produces a proposal (see the (optional) create-PR endpoint in
   app.api.v1.routers.documentation for the one place that can).
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, AgentOutput, Confidence, Evidence, Subject
from app.agents.documentation.discovery import (
    MarkdownFile,
    discover_markdown_files,
    find_broken_links,
    find_duplicate_documents,
)
from app.agents.documentation.schemas import (
    DocumentationFinding,
    DocumentationReviewResult,
    MarkdownFileSummary,
    ProposedDocumentUpdate,
    ProposedNewDocument,
)
from app.agents.llm import STAGE_DOCUMENTATION_REVIEW, StageAwareLLMProvider, stage_for
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.core.exceptions import AppError, NotFoundError
from app.graph.interfaces import IGraphRepository
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.scanner.repository_cloner import RepositoryCloneError, clone_repository
from app.models.repository import Repository
from app.services.github_service import get_decrypted_access_token

logger = logging.getLogger(__name__)

# Bounds how much raw Markdown content reaches the LLM prompt — a large
# monorepo's docs/ tree can be megabytes; this keeps the request bounded
# the same way `_MAX_TOOL_TURNS` bounds confluence_context.py's loop.
# Files beyond this count are still discovered/reported (findings around
# "how many files exist" stay accurate) but their content isn't sent to
# the model, and they're never candidates for proposed updates.
_MAX_FILES_WITH_CONTENT = 25
_MAX_CONTENT_CHARS_PER_FILE = 6000
_MAX_COMPONENTS_IN_PROMPT = 80


def resolve_repository_subject(repository: Repository) -> Subject:
    """Build a Subject for a repository — the repository-reference
    resolver, same role as `review_adapter.resolve_pr_subject` for PRs.

    subject_id format: "repo:<uuid>", parsed back by `_extract_repository_uuid`.
    """
    return Subject(
        subject_id=f"repo:{repository.id}",
        subject_type="repository",
        graph_node_ids=[],
        display_name=repository.full_name,
    )


def _extract_repository_uuid(subject_id: str) -> uuid.UUID:
    if not subject_id.startswith("repo:"):
        raise NotFoundError(f"Documentation Agent expects subject_id 'repo:<uuid>', got '{subject_id}'.")
    raw = subject_id[len("repo:") :]
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(
            f"subject_id '{subject_id}' does not contain a valid UUID after 'repo:': {exc}"
        ) from exc


def _strip_json_fence(text: str) -> str:
    """Strip a leading/trailing ```json ... ``` (or bare ```) code fence, if
    present. `ResponseFormat.JSON` asks the model for raw JSON and the
    system prompt repeats that instruction, but Bedrock/Haiku still wraps
    its response in a fence often enough in practice (confirmed against a
    real repository during this feature's own verification) that trusting
    the instruction alone left synthesis failing on real runs. Defensive,
    not a workaround for a specific provider — a no-op when the response
    is already bare JSON."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return without_open.rsplit("```", 1)[0].strip()


_SYSTEM_PROMPT = (
    "You are reviewing a software repository's Markdown documentation against "
    "its actual, indexed architecture. You are given: (1) the content of its "
    "Markdown files, (2) a list of real components (Controllers, Services, "
    "FeignClients, etc.) the repository's code actually contains, and (3) "
    "deterministic findings already computed (broken links, exact "
    "duplicates) — do not repeat those verbatim as new findings.\n\n"
    "Your job, respond as JSON matching exactly this shape:\n"
    "{\n"
    '  "summary": "2-3 sentence overview of documentation health",\n'
    '  "findings": [{"finding_type": "outdated"|"missing"|"duplicate"|"needs_update", '
    '"severity": "low"|"medium"|"high", "file_path": "path or \'(missing)\' for a '
    'missing-documentation finding", "description": "..."}],\n'
    '  "proposed_updates": [{"file_path": "existing file path from the input", '
    '"rationale": "...", "proposed_markdown": "full replacement Markdown content"}],\n'
    '  "proposed_new_documents": [{"file_path": "new file path, e.g. docs/architecture.md", '
    '"title": "...", "rationale": "...", "proposed_markdown": "full Markdown content"}]\n'
    "}\n\n"
    "Rules:\n"
    "- 'outdated': documentation describes something the component list contradicts "
    "(e.g. references a component/service that no longer exists).\n"
    "- 'missing': a major component or capability has no documentation coverage at all.\n"
    "- 'needs_update': documentation is present and not wrong, but is thin, unclear, or "
    "stale in a way that isn't strictly 'outdated'.\n"
    "- proposed_updates.file_path must be one of the existing files you were given.\n"
    "- Only propose what's actually warranted — an empty findings/proposals list for a "
    "repository whose docs are in good shape is a correct, complete answer, not a failure.\n"
    "- Never invent component names not present in the component list."
)


class DocumentationReviewAgent:
    """Implements IAgent for goal=review_documentation. Stateless singleton."""

    async def run(self, context: AgentContext) -> AgentOutput:
        db: AsyncSession = context.extras["db"]
        user_id = context.extras.get("user_id")
        repository_id = _extract_repository_uuid(context.subject.subject_id)

        repository = await self._load_repository(db, repository_id, user_id)

        evidence: list[Evidence] = []

        components = await self._load_components(str(repository.id))
        evidence.append(
            Evidence(
                kind="graph_traversal",
                reference="graph:get_components",
                summary=f"Read {len(components)} indexed component(s) for {repository.full_name}.",
            )
        )

        access_token = await get_decrypted_access_token(db, repository.user_id)

        try:
            async with clone_repository(
                repository.html_url, repository.default_branch, access_token
            ) as repo_path:
                files = discover_markdown_files(repo_path)
                broken_links = find_broken_links(repo_path, files)
                duplicate_pairs = find_duplicate_documents(files)
                file_contents = self._read_bounded_content(files)
        except RepositoryCloneError as exc:
            logger.warning("documentation_agent_clone_failed repository=%s error=%s", repository.full_name, exc)
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

        deterministic_findings = self._deterministic_findings(broken_links, duplicate_pairs)
        for f in deterministic_findings:
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference=f"discovery:{f.finding_type}",
                    summary=f"{f.file_path}: {f.description}",
                    status="success",
                )
            )

        llm_result, llm_evidence = await self._synthesize(
            context, repository, file_contents, components, deterministic_findings
        )
        evidence.append(llm_evidence)

        result = DocumentationReviewResult(
            repository_full_name=repository.full_name,
            summary=llm_result.get("summary", ""),
            files_reviewed=[
                MarkdownFileSummary(path=f.relative_path, category=f.category, size_bytes=f.size_bytes)
                for f in files
            ],
            findings=deterministic_findings + self._parse_llm_findings(llm_result),
            proposed_updates=self._parse_llm_updates(llm_result, {f.relative_path for f in files}),
            proposed_new_documents=self._parse_llm_new_documents(llm_result),
        )

        confidence_score = 0.8 if llm_result else 0.4
        return AgentOutput(
            agent_id="documentation_review",
            subject_id=context.subject.subject_id,
            confidence=Confidence(
                score=confidence_score,
                reasoning=(
                    f"Reviewed {len(files)} Markdown file(s) against {len(components)} indexed "
                    "component(s); findings combine deterministic checks (broken links, exact "
                    "duplicates) with an LLM comparison pass."
                ),
            ),
            evidence=evidence,
            result=result.model_dump(),
            prompt_version=result.prompt_version,
            output_ref=f"documentation-review:{repository.id}",
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

    async def _load_components(self, repository_id: str) -> list[dict[str, str]]:
        graph_repository: IGraphRepository = Neo4jGraphRepository(get_driver())
        try:
            nodes = await graph_repository.get_nodes_by_label(repository_id, "Component")
        except Exception:
            logger.warning("documentation_agent_graph_read_failed repository_id=%s", repository_id, exc_info=True)
            return []
        return [
            {
                "name": node.properties.get("name", node.id),
                "type": next((label for label in node.labels if label != "Component"), "Component"),
                "file_path": node.properties.get("file_path", ""),
            }
            for node in nodes
        ]

    def _read_bounded_content(self, files: list[MarkdownFile]) -> list[dict[str, str]]:
        contents: list[dict[str, str]] = []
        for f in files[:_MAX_FILES_WITH_CONTENT]:
            try:
                text = f.absolute_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            contents.append({"path": f.relative_path, "content": text[:_MAX_CONTENT_CHARS_PER_FILE]})
        return contents

    def _deterministic_findings(self, broken_links, duplicate_pairs) -> list[DocumentationFinding]:
        findings: list[DocumentationFinding] = []
        for link in broken_links:
            findings.append(
                DocumentationFinding(
                    finding_type="broken_link",
                    severity="medium",
                    file_path=link.source_file,
                    description=f"Link target '{link.target}' does not resolve to a file in this repository.",
                    broken_link_target=link.target,
                )
            )
        for original, duplicate in duplicate_pairs:
            findings.append(
                DocumentationFinding(
                    finding_type="duplicate",
                    severity="low",
                    file_path=duplicate.relative_path,
                    description=f"Content is identical to '{original.relative_path}'.",
                    duplicate_of=original.relative_path,
                )
            )
        return findings

    async def _synthesize(
        self,
        context: AgentContext,
        repository: Repository,
        file_contents: list[dict[str, str]],
        components: list[dict[str, str]],
        deterministic_findings: list[DocumentationFinding],
    ) -> tuple[dict[str, Any], Evidence]:
        user_prompt = json.dumps(
            {
                "repository": repository.full_name,
                "markdown_files": file_contents,
                "components": components[:_MAX_COMPONENTS_IN_PROMPT],
                "already_found": [f.model_dump() for f in deterministic_findings],
            }
        )
        try:
            provider = StageAwareLLMProvider(
                stage=stage_for(context.extras, STAGE_DOCUMENTATION_REVIEW), model=context.model
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
                summary=f"LLM synthesis produced {len(parsed.get('findings', []))} additional finding(s).",
                status="success",
            )
        except (AppError, json.JSONDecodeError) as exc:
            logger.warning("documentation_agent_synthesis_failed repository=%s error=%s", repository.full_name, exc)
            return {}, Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=f"LLM synthesis could not be completed: {exc}",
                status="failed",
            )

    def _parse_llm_findings(self, llm_result: dict[str, Any]) -> list[DocumentationFinding]:
        findings: list[DocumentationFinding] = []
        for raw in llm_result.get("findings", []) or []:
            try:
                findings.append(DocumentationFinding.model_validate(raw))
            except Exception:
                continue
        return findings

    def _parse_llm_updates(
        self, llm_result: dict[str, Any], known_files: set[str]
    ) -> list[ProposedDocumentUpdate]:
        updates: list[ProposedDocumentUpdate] = []
        for raw in llm_result.get("proposed_updates", []) or []:
            try:
                update = ProposedDocumentUpdate.model_validate(raw)
            except Exception:
                continue
            if update.file_path in known_files:
                updates.append(update)
        return updates

    def _parse_llm_new_documents(self, llm_result: dict[str, Any]) -> list[ProposedNewDocument]:
        docs: list[ProposedNewDocument] = []
        for raw in llm_result.get("proposed_new_documents", []) or []:
            try:
                docs.append(ProposedNewDocument.model_validate(raw))
            except Exception:
                continue
        return docs

    def _empty_result(
        self,
        context: AgentContext,
        repository: Repository,
        evidence: list[Evidence],
        error: str,
    ) -> AgentOutput:
        result = DocumentationReviewResult(
            repository_full_name=repository.full_name,
            summary=f"Documentation review could not be completed: {error}",
        )
        return AgentOutput(
            agent_id="documentation_review",
            subject_id=context.subject.subject_id,
            confidence=Confidence(score=0.0, reasoning=error),
            evidence=evidence,
            result=result.model_dump(),
            output_ref=f"documentation-review:{repository.id}",
        )
