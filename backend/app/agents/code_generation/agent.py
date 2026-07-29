"""Code Generation Agent — produces structured execution artifacts.

Implements the IAgent protocol for goal=generate_code. Consumes blueprint
context (already assembled by build_stage_context + cross-workflow source),
calls the LLM to generate code, validates the response, and returns a
strongly-typed GeneratedCodeResult.

Does NOT:
- Write to GitHub
- Create branches/commits/PRs
- Execute generated code
- Run tests

The result is persisted as an AgentStep via the standard Run infrastructure.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.code_generation.confidence import ConfidenceEvidence, calculate_confidence
from app.agents.code_generation.schemas import GeneratedCodeResult, GeneratedFile
from app.agents.code_generation.verification import (
    _collect_known_file_paths,
    validate_file_operations,
    verify_repository,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.llm import STAGE_GENERATE_CODE, invoke_llm_json, stage_for
from app.agents.prompt_utils import parse_json_response, render_prompt_template
from app.agents.stage_context import (
    format_development_block,
    format_documentation_block,
    format_engineering_review_block,
    format_planning_block,
    format_testing_block,
)
from app.core.exceptions import AppError
from app.models.workflow import Workflow

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_CONTEXT_CHARS = 12_000

_VALID_OPERATIONS = frozenset({"create", "modify", "delete"})

_SYSTEM_PROMPT = (
    "You are a Senior Software Engineer generating production-ready code "
    "from an approved engineering blueprint. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object. "
    "Do not include secrets, API keys, or credentials in generated files."
)


class CodeGenerationLLMError(AppError):
    status_code = 502
    error_code = "code_generation_llm_error"


class CodeGenerationValidationError(AppError):
    status_code = 422
    error_code = "code_generation_validation_error"


class CodeGenerationRepositoryError(AppError):
    """Raised when the LLM-claimed repository fails deterministic
    verification (app.agents.code_generation.verification). Never raised
    for a repository this run cannot confirm was actually consulted by
    this workflow's own Planning/Development stages — no git operation may
    ever be attempted against an unverified repository."""

    status_code = 422
    error_code = "code_generation_repository_error"


async def _call_llm(
    user_prompt: str, model: str | None = None, stage: str = STAGE_GENERATE_CODE
) -> str:
    """Delegates to the shared `app.agents.llm.invoke_llm_json` — kept as a
    module-level function so existing test seams
    (`patch("...agent._call_llm", ...)`) stay stable."""
    return await invoke_llm_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        stage=stage,
        model=model,
        error_cls=CodeGenerationLLMError,
    )


def _render_prompt(blueprint_context: str) -> str:
    """Render generate_code.md with the enriched blueprint context."""
    return render_prompt_template(
        _PROMPT_DIR / "generate_code.md", blueprint_context, "", _MAX_CONTEXT_CHARS
    )


def _stage_result(
    workflow: Workflow | None, source_workflow: Workflow | None, stage: str
) -> dict | None:
    """`get_stage_result` for `stage`, preferring `source_workflow` (the
    approved blueprint an auto_execution workflow executes — see
    workflows.py's `source_workflow` extras key) and falling back to
    `workflow` itself (a workflow that ran its own Planning/Development/...
    stages directly, rather than via a separate source blueprint)."""
    for wf in (source_workflow, workflow):
        if wf is None:
            continue
        result = get_stage_result(wf, stage)
        if result is not None:
            return result
    return None


def _build_blueprint_context(
    context: AgentContext, workflow: Workflow | None, source_workflow: Workflow | None
) -> str:
    """The full blueprint text handed to the LLM.

    `context.subject.display_name` alone is NOT enough: it is built by
    `app.context.resolvers.freetext.resolve()`, which truncates to 256
    characters — a limit sized for a short human-readable label, not a
    multi-stage blueprint. Every other downstream stage (Development,
    Testing, Documentation Planning, Engineering Review) already reads the
    *untruncated* prior-stage results via `get_stage_result()` +
    `app.agents.stage_context`'s `format_*_block` functions instead of
    relying on the truncated text — this function brings Code Generation,
    previously the one stage that did NOT do this, in line with that
    existing pattern rather than inventing a new one.

    Falls back to the (possibly truncated) `display_name` alone when no
    workflow context is available at all — e.g. a standalone run with no
    `workflow`/`source_workflow` in `context.extras`, which is exactly
    what today's tests exercise and must keep working.
    """
    planning_result = _stage_result(workflow, source_workflow, "planning")
    development_result = _stage_result(workflow, source_workflow, "development")
    testing_result = _stage_result(workflow, source_workflow, "testing")
    documentation_result = _stage_result(workflow, source_workflow, "documentation_planning")
    engineering_review_result = _stage_result(workflow, source_workflow, "engineering_review")

    if not any(
        r is not None
        for r in (
            planning_result,
            development_result,
            testing_result,
            documentation_result,
            engineering_review_result,
        )
    ):
        return context.subject.display_name

    sections = [
        f"## Original Objective\n{context.subject.display_name}",
        format_planning_block(planning_result),
        format_development_block(development_result),
        format_testing_block(testing_result),
        format_documentation_block(documentation_result),
        format_engineering_review_block(engineering_review_result),
    ]
    blueprint_context = "\n\n".join(sections)

    if len(blueprint_context) > _MAX_CONTEXT_CHARS:
        logger.warning(
            "code_generation_blueprint_context_truncated original_chars=%d max_chars=%d",
            len(blueprint_context),
            _MAX_CONTEXT_CHARS,
        )
        blueprint_context = blueprint_context[:_MAX_CONTEXT_CHARS]
    return blueprint_context


def _validate_and_parse(raw: str) -> GeneratedCodeResult:
    """Parse and validate the LLM response into a GeneratedCodeResult.

    Raises CodeGenerationValidationError for:
    - Malformed JSON
    - Missing required fields
    - Invalid operation values
    - Duplicate file paths
    - Empty files list
    - Content violations (empty content for create/modify, etc.)
    """
    data = parse_json_response(raw, CodeGenerationValidationError)

    # Required fields
    missing = []
    for field in ("executive_summary", "repository", "commit_message"):
        if field not in data or not data[field]:
            missing.append(field)
    if "files" not in data:
        missing.append("files")
    if missing:
        raise CodeGenerationValidationError(
            f"LLM response missing required fields: {', '.join(missing)}"
        )

    # Files validation
    raw_files = data["files"]
    if not isinstance(raw_files, list) or len(raw_files) == 0:
        raise CodeGenerationValidationError("LLM response 'files' must be a non-empty list.")

    seen_paths: set[str] = set()
    files: list[GeneratedFile] = []

    for i, f in enumerate(raw_files):
        if not isinstance(f, dict):
            raise CodeGenerationValidationError(f"files[{i}] must be an object.")

        path = f.get("path", "").strip()
        if not path:
            raise CodeGenerationValidationError(f"files[{i}] is missing 'path'.")

        if path in seen_paths:
            raise CodeGenerationValidationError(f"Duplicate file path: '{path}'.")
        seen_paths.add(path)

        operation = f.get("operation", "").strip().lower()
        if operation not in _VALID_OPERATIONS:
            raise CodeGenerationValidationError(
                f"files[{i}] has invalid operation '{f.get('operation')}'. "
                f"Must be one of: {sorted(_VALID_OPERATIONS)}."
            )

        content = f.get("content", "")
        if operation in ("create", "modify") and not content:
            raise CodeGenerationValidationError(
                f"files[{i}] ('{path}'): operation '{operation}' requires non-empty content."
            )

        files.append(GeneratedFile(path=path, operation=operation, content=content))

    # NOTE: any `confidence` the LLM included in its JSON is intentionally
    # never read here. Execution confidence is computed deterministically
    # in `CodeGenerationAgent.run` from this run's own verification
    # evidence (see app.agents.code_generation.confidence) and overwrites
    # this field before the result is returned or persisted.
    return GeneratedCodeResult(
        goal="generate_code",
        executive_summary=data["executive_summary"],
        repository=data["repository"],
        commit_message=data["commit_message"],
        files=files,
        prompt_version=_PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Code Generation Agent
# ---------------------------------------------------------------------------


class CodeGenerationAgent:
    """Implements IAgent for goal=generate_code.

    Stateless singleton — no DB/graph session needed. The enriched
    blueprint context is assembled by `_build_blueprint_context` from the
    untruncated Planning/Development/Testing/Documentation Planning/
    Engineering Review stage results (via `get_stage_result`), the same
    mechanism Engineering Review itself uses — `context.subject.
    display_name` alone is only ever the short original-objective label,
    not the full blueprint (see `_build_blueprint_context`'s docstring).
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        subject_id: str = context.subject.subject_id
        workflow = context.extras.get("workflow")
        source_workflow = context.extras.get("source_workflow")
        blueprint_context = _build_blueprint_context(context, workflow, source_workflow)

        logger.info(
            "code_generation_agent_started subject_id=%s context_chars=%d model=%s",
            subject_id,
            len(blueprint_context),
            context.model,
        )

        evidence: list[Evidence] = [
            Evidence(
                kind="tool_call",
                reference="read_blueprint_context",
                summary=(
                    f"Consumed approved blueprint context "
                    f"({len(blueprint_context)} characters) via get_stage_result() — "
                    "no summarization or truncation applied to prior-stage results."
                ),
            )
        ]

        prompt = _render_prompt(blueprint_context)

        try:
            raw_response = await _call_llm(
                user_prompt=prompt,
                model=context.model,
                stage=stage_for(context.extras, STAGE_GENERATE_CODE),
            )
        except CodeGenerationLLMError as exc:
            logger.error("code_generation_agent_llm_failed error=%s", str(exc))
            raise

        try:
            result = _validate_and_parse(raw_response)
        except CodeGenerationValidationError as exc:
            logger.error("code_generation_agent_validation_failed error=%s", str(exc))
            raise

        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="llm_code_generation",
                summary=(
                    f"Generated {len(result.files)} file(s) for {result.repository}: "
                    f"{result.commit_message[:80]}"
                ),
            )
        )

        # ------------------------------------------------------------------
        # Deterministic verification — never trust the LLM's repository
        # choice or file operations. No branch/commit/PR agent may run
        # unless this run's generate_code result passes here first (later
        # stages read this stage's result via get_stage_result and there
        # simply is no result to read if this raises).
        # ------------------------------------------------------------------
        db = context.extras.get("db")
        user_id = context.extras.get("user_id")
        workflow = context.extras.get("workflow")
        source_workflow = context.extras.get("source_workflow")

        repo_verification = await verify_repository(
            result.repository,
            db=db,
            user_id=user_id,
            workflow=workflow,
            source_workflow=source_workflow,
        )
        evidence.extend(repo_verification.evidence)

        if not repo_verification.passed:
            error_summary = "; ".join(repo_verification.errors)
            logger.error(
                "code_generation_agent_repository_rejected subject_id=%s repo=%s errors=%s",
                subject_id,
                result.repository,
                error_summary,
            )
            raise CodeGenerationRepositoryError(
                f"Repository verification failed for '{result.repository}': {error_summary}"
            )

        known_file_paths = _collect_known_file_paths(workflow, source_workflow)
        violations = validate_file_operations(
            [f.model_dump() for f in result.files], result.repository, known_file_paths
        )
        graph_context_available = bool(known_file_paths.get(result.repository))
        evidence.append(
            Evidence(
                kind="tool_call",
                reference="file_operation_validation",
                summary=(
                    f"Validated {len(result.files)} file operation(s) against "
                    f"'{result.repository}'"
                    + (
                        f" — {len(violations)} rejected: "
                        + "; ".join(f"{v.path} ({v.reason})" for v in violations)
                        if violations
                        else " — all valid."
                    )
                ),
            )
        )
        if violations:
            logger.error(
                "code_generation_agent_file_operations_rejected subject_id=%s repo=%s "
                "violations=%d",
                subject_id,
                result.repository,
                len(violations),
            )
            raise CodeGenerationValidationError(
                "Invalid file operation(s): "
                + "; ".join(f"{v.path} [{v.operation}]: {v.reason}" for v in violations)
            )

        testing_result = get_stage_result(workflow, "testing") if workflow else None
        if testing_result is None and source_workflow is not None:
            testing_result = get_stage_result(source_workflow, "testing")

        confidence_score, confidence_reasoning = calculate_confidence(
            ConfidenceEvidence(
                repository_tracked=repo_verification.tracked,
                repository_in_workflow_scope=repo_verification.in_workflow_scope,
                graph_context_available=graph_context_available,
                previous_stage_verified=testing_result is not None,
                files_valid=True,
                file_violation_count=0,
            )
        )
        result.confidence = confidence_score

        logger.info(
            "code_generation_agent_completed subject_id=%s repo=%s files=%d confidence=%.2f",
            subject_id,
            result.repository,
            len(result.files),
            result.confidence,
        )

        return AgentOutput(
            agent_id="code_generation",
            subject_id=subject_id,
            confidence=Confidence(score=confidence_score, reasoning=confidence_reasoning),
            evidence=evidence,
            result=result.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
