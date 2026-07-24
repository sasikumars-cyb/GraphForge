"""Unit tests for the Code Generation Agent.

Covers:
- Happy path: successful generation, schema validation, evidence/result shape
- Validation errors: malformed JSON, missing fields, invalid operations,
  duplicate paths, empty files, content violations
- LLM failure propagation
- Manifest and registration
- Selector routing
- Artifact persistence (result stored in AgentOutput.result)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.code_generation.agent import (
    CodeGenerationAgent,
    CodeGenerationLLMError,
    CodeGenerationValidationError,
    _validate_and_parse,
)
from app.agents.code_generation.manifest import CODE_GENERATION_MANIFEST
from app.agents.code_generation.schemas import GeneratedCodeResult, GeneratedFile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_context(
    display_name: str = (
        "Implement rate limiting\n"
        "--- Blueprint context from Development stage (source workflow) ---\n"
        "Implement Redis-based rate limiter in api-gateway\n"
        "Affected Repositories: demo-org/api-gateway\n"
        "Implementation Phases: Phase 1 - RateLimiterService, Phase 2 - Config"
    ),
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:blueprint-exec",
        subject_type="freetext",
        display_name=display_name,
    )
    return AgentContext(subject=subject, goal="generate_code", extras={"db": AsyncMock()})


def _make_llm_response(
    repository: str = "demo-org/api-gateway",
    files: list[dict] | None = None,
    confidence: float = 0.85,
) -> str:
    if files is None:
        files = [
            {
                "path": "src/main/java/com/example/RateLimiterService.java",
                "operation": "create",
                "content": "package com.example;\n\npublic class RateLimiterService {}",
            },
            {
                "path": "src/main/java/com/example/RateLimiterConfig.java",
                "operation": "create",
                "content": "package com.example;\n\npublic class RateLimiterConfig {}",
            },
        ]
    return json.dumps(
        {
            "executive_summary": "Generated rate limiter service and config.",
            "repository": repository,
            "commit_message": "feat: add Redis-based rate limiter",
            "confidence": confidence,
            "files": files,
        }
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_generation_agent_happy_path() -> None:
    context = _make_context()

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        agent = CodeGenerationAgent()
        output = await agent.run(context)

    # Evidence shape
    evidence_kinds = {e.kind for e in output.evidence}
    assert "tool_call" in evidence_kinds
    assert "llm_reasoning" in evidence_kinds

    # Result shape
    assert output.result["executive_summary"] == "Generated rate limiter service and config."
    assert output.result["repository"] == "demo-org/api-gateway"
    assert output.result["commit_message"] == "feat: add Redis-based rate limiter"
    assert len(output.result["files"]) == 2
    assert output.result["files"][0]["path"] == "src/main/java/com/example/RateLimiterService.java"
    assert output.result["files"][0]["operation"] == "create"
    assert "RateLimiterService" in output.result["files"][0]["content"]
    assert output.result["confidence"] == 0.85

    # AgentOutput metadata
    assert output.agent_id == "code_generation"
    assert output.subject_id == "freetext:blueprint-exec"
    assert output.prompt_version == "1.0"
    assert output.confidence.score == 0.85


@pytest.mark.asyncio
async def test_code_generation_agent_confidence_tracks_llm_value() -> None:
    context = _make_context()

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(confidence=0.6)),
    ):
        agent = CodeGenerationAgent()
        output = await agent.run(context)

    assert output.confidence.score == 0.6
    assert output.result["confidence"] == 0.6


@pytest.mark.asyncio
async def test_code_generation_agent_delete_operation() -> None:
    context = _make_context()
    files = [
        {
            "path": "src/old/DeprecatedService.java",
            "operation": "delete",
            "content": "",
        },
        {
            "path": "src/main/java/com/example/NewService.java",
            "operation": "create",
            "content": "package com.example;\n\npublic class NewService {}",
        },
    ]

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(files=files)),
    ):
        agent = CodeGenerationAgent()
        output = await agent.run(context)

    assert output.result["files"][0]["operation"] == "delete"
    assert output.result["files"][0]["content"] == ""
    assert output.result["files"][1]["operation"] == "create"


# ---------------------------------------------------------------------------
# LLM failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_generation_agent_llm_failure_raises() -> None:
    context = _make_context()

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(side_effect=CodeGenerationLLMError("Timeout")),
    ):
        agent = CodeGenerationAgent()
        with pytest.raises(CodeGenerationLLMError):
            await agent.run(context)


# ---------------------------------------------------------------------------
# Validation: _validate_and_parse
# ---------------------------------------------------------------------------


def test_validate_rejects_malformed_json() -> None:
    with pytest.raises(CodeGenerationValidationError, match="not valid JSON"):
        _validate_and_parse("not json at all {{{")


def test_validate_rejects_non_object() -> None:
    with pytest.raises(CodeGenerationValidationError, match="must be a JSON object"):
        _validate_and_parse(json.dumps([1, 2, 3]))


def test_validate_rejects_missing_required_fields() -> None:
    with pytest.raises(CodeGenerationValidationError, match="missing required fields"):
        _validate_and_parse(json.dumps({"executive_summary": "x"}))


def test_validate_rejects_empty_files_list() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "files": [],
    }
    with pytest.raises(CodeGenerationValidationError, match="non-empty list"):
        _validate_and_parse(json.dumps(data))


def test_validate_rejects_invalid_operation() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "files": [{"path": "a.py", "operation": "patch", "content": "x"}],
    }
    with pytest.raises(CodeGenerationValidationError, match="invalid operation"):
        _validate_and_parse(json.dumps(data))


def test_validate_rejects_duplicate_paths() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "files": [
            {"path": "a.py", "operation": "create", "content": "x"},
            {"path": "a.py", "operation": "modify", "content": "y"},
        ],
    }
    with pytest.raises(CodeGenerationValidationError, match="Duplicate file path"):
        _validate_and_parse(json.dumps(data))


def test_validate_rejects_empty_content_for_create() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "files": [{"path": "a.py", "operation": "create", "content": ""}],
    }
    with pytest.raises(CodeGenerationValidationError, match="requires non-empty content"):
        _validate_and_parse(json.dumps(data))


def test_validate_rejects_empty_content_for_modify() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "files": [{"path": "a.py", "operation": "modify", "content": ""}],
    }
    with pytest.raises(CodeGenerationValidationError, match="requires non-empty content"):
        _validate_and_parse(json.dumps(data))


def test_validate_allows_empty_content_for_delete() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "files": [{"path": "a.py", "operation": "delete", "content": ""}],
    }
    result = _validate_and_parse(json.dumps(data))
    assert result.files[0].operation == "delete"
    assert result.files[0].content == ""


def test_validate_rejects_missing_path() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "files": [{"operation": "create", "content": "x"}],
    }
    with pytest.raises(CodeGenerationValidationError, match="missing 'path'"):
        _validate_and_parse(json.dumps(data))


def test_validate_clamps_confidence() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "confidence": 5.0,
        "files": [{"path": "a.py", "operation": "create", "content": "x"}],
    }
    result = _validate_and_parse(json.dumps(data))
    assert result.confidence == 1.0


def test_validate_defaults_confidence_on_invalid_type() -> None:
    data = {
        "executive_summary": "x",
        "repository": "org/repo",
        "commit_message": "feat: x",
        "confidence": "high",
        "files": [{"path": "a.py", "operation": "create", "content": "x"}],
    }
    result = _validate_and_parse(json.dumps(data))
    assert result.confidence == 0.7


def test_validate_happy_path_returns_result() -> None:
    raw = _make_llm_response()
    result = _validate_and_parse(raw)
    assert isinstance(result, GeneratedCodeResult)
    assert result.repository == "demo-org/api-gateway"
    assert result.goal == "generate_code"
    assert len(result.files) == 2


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_generated_code_result_schema_defaults() -> None:
    result = GeneratedCodeResult(
        goal="generate_code",
        executive_summary="x",
        repository="org/repo",
        commit_message="feat: x",
    )
    assert result.files == []
    assert result.confidence == 0.7
    assert result.prompt_version == "1.0"


def test_generated_file_schema() -> None:
    f = GeneratedFile(path="a.py", operation="create", content="print('hello')")
    assert f.path == "a.py"
    assert f.operation == "create"
    assert f.content == "print('hello')"


def test_generated_file_delete_content_default() -> None:
    f = GeneratedFile(path="old.py", operation="delete")
    assert f.content == ""


# ---------------------------------------------------------------------------
# Artifact persistence (result stored via AgentOutput.result)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_generation_result_is_serializable_dict() -> None:
    """The result stored in AgentStep.result must be a plain dict
    (JSON-serializable) — not a Pydantic model instance."""
    context = _make_context()

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        agent = CodeGenerationAgent()
        output = await agent.run(context)

    # result is a plain dict
    assert isinstance(output.result, dict)
    # It round-trips through JSON
    serialized = json.dumps(output.result)
    deserialized = json.loads(serialized)
    assert deserialized["repository"] == "demo-org/api-gateway"
    assert len(deserialized["files"]) == 2


# ---------------------------------------------------------------------------
# Manifest and registration
# ---------------------------------------------------------------------------


def test_code_generation_manifest_fields() -> None:
    assert CODE_GENERATION_MANIFEST.agent_id == "code_generation"
    assert "generate_code" in CODE_GENERATION_MANIFEST.goals
    assert "freetext" in CODE_GENERATION_MANIFEST.accepted_subject_types
    assert CODE_GENERATION_MANIFEST.output_schema_name == "GeneratedCodeResult"
    assert CODE_GENERATION_MANIFEST.cost_class == "expensive"


def test_code_generation_agent_registered_in_global_registry() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry

    register_agents()
    agent_ids = {m.agent_id for m in global_registry.all_manifests()}
    assert "code_generation" in agent_ids


def test_selector_routes_generate_code_goal() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry
    from app.orchestrator.selector import AgentSelector

    register_agents()
    selector = AgentSelector(global_registry)
    assert selector.select("generate_code") == "code_generation"
