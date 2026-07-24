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

import json
import logging
from pathlib import Path

import httpx

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents._llm import call_chat_completion_json, render_prompt_template
from app.agents.code_generation.schemas import GeneratedCodeResult, GeneratedFile
from app.core.exceptions import AppError

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


async def _call_llm(
    user_prompt: str,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    return await call_chat_completion_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        error_cls=CodeGenerationLLMError,
        model=model,
        http_client=http_client,
    )


def _render_prompt(blueprint_context: str) -> str:
    """Render generate_code.md with the enriched blueprint context."""
    return render_prompt_template(
        _PROMPT_DIR / "generate_code.md", blueprint_context, "", _MAX_CONTEXT_CHARS
    )


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
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodeGenerationValidationError(
            f"LLM response is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise CodeGenerationValidationError(
            "LLM response must be a JSON object."
        )

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
        raise CodeGenerationValidationError(
            "LLM response 'files' must be a non-empty list."
        )

    seen_paths: set[str] = set()
    files: list[GeneratedFile] = []

    for i, f in enumerate(raw_files):
        if not isinstance(f, dict):
            raise CodeGenerationValidationError(
                f"files[{i}] must be an object."
            )

        path = f.get("path", "").strip()
        if not path:
            raise CodeGenerationValidationError(
                f"files[{i}] is missing 'path'."
            )

        if path in seen_paths:
            raise CodeGenerationValidationError(
                f"Duplicate file path: '{path}'."
            )
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

    confidence = data.get("confidence", 0.7)
    if not isinstance(confidence, (int, float)):
        confidence = 0.7
    confidence = max(0.0, min(1.0, float(confidence)))

    return GeneratedCodeResult(
        goal="generate_code",
        executive_summary=data["executive_summary"],
        repository=data["repository"],
        commit_message=data["commit_message"],
        files=files,
        confidence=confidence,
        prompt_version=_PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Code Generation Agent
# ---------------------------------------------------------------------------


class CodeGenerationAgent:
    """Implements IAgent for goal=generate_code.

    Stateless singleton — no DB/graph session needed. The enriched
    blueprint context arrives via context.subject.display_name (same
    mechanism as Engineering Review).
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        blueprint_context: str = context.subject.display_name
        subject_id: str = context.subject.subject_id

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
                    f"({len(blueprint_context)} characters)."
                ),
            )
        ]

        prompt = _render_prompt(blueprint_context)

        try:
            raw_response = await _call_llm(user_prompt=prompt, model=context.model)
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

        logger.info(
            "code_generation_agent_completed subject_id=%s repo=%s "
            "files=%d confidence=%.2f",
            subject_id,
            result.repository,
            len(result.files),
            result.confidence,
        )

        return AgentOutput(
            agent_id="code_generation",
            subject_id=subject_id,
            confidence=Confidence(
                score=result.confidence,
                reasoning=(
                    f"Generated {len(result.files)} file(s) targeting "
                    f"{result.repository}. "
                    f"Confidence based on blueprint clarity and code complexity."
                ),
            ),
            evidence=evidence,
            result=result.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
