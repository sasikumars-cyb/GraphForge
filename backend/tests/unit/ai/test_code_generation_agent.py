"""Unit tests for the Code Generation Agent.

Covers:
- Happy path: successful generation, schema validation, evidence/result shape
- Deterministic repository verification: valid, untracked, out-of-workflow-scope
- Deterministic file operation validation: unsafe path, unknown modify target
- Deterministic confidence: computed from verification evidence, never from
  the LLM's own self-reported value
- Validation errors: malformed JSON, missing fields, invalid operations,
  duplicate paths, empty files, content violations
- LLM failure propagation
- Manifest and registration
- Selector routing
- Artifact persistence (result stored in AgentOutput.result)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.code_generation.agent import (
    CodeGenerationAgent,
    CodeGenerationLLMError,
    CodeGenerationRepositoryError,
    CodeGenerationValidationError,
    _validate_and_parse,
)
from app.agents.code_generation.manifest import CODE_GENERATION_MANIFEST
from app.agents.code_generation.schemas import GeneratedCodeResult, GeneratedFile

_USER_ID = uuid.uuid4()
_REPO = "demo-org/api-gateway"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_step(result: dict) -> SimpleNamespace:
    return SimpleNamespace(result=result)


def _make_run(stage: str, result: dict, status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(
        workflow_stage=stage,
        status=status,
        created_at=datetime.now(UTC),
        steps=[_make_step(result)],
    )


def _make_workflow(runs: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(runs=runs)


def _make_source_workflow(
    repository: str = _REPO, file_paths: list[str] | None = None
) -> SimpleNamespace:
    """A source (Planning) workflow whose Development stage's own graph
    traversal already consulted `repository` — the deterministic ground
    truth `verify_repository` checks the LLM's claim against.

    `file_path_verification: "verified"` (ADR 0027) is set on every
    component here because this fixture represents a genuinely real,
    correctly-attributed Development result — the exact case
    `_collect_known_file_paths` is supposed to trust. A test that instead
    needs an UNVERIFIED/absent-verification component (to prove a
    modify/delete is correctly rejected) builds its own `components` list
    directly, as `test_code_generation_agent_reads_full_untruncated_blueprint_context`
    already does."""
    components = [
        {"repository": repository, "file_path": path, "file_path_verification": "verified"}
        for path in (file_paths or ["src/main/java/com/example/RateLimiterConfig.java"])
    ]
    development_result = {
        "repositories_consulted": [repository],
        "components": components,
    }
    return _make_workflow([_make_run("development", development_result)])


class _FakeReposScalar:
    def __init__(self, found: bool) -> None:
        self._found = found

    def scalar_one_or_none(self):
        return object() if self._found else None


def _make_db(tracked: bool = True) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeReposScalar(tracked))
    return db


def _make_context(
    display_name: str = (
        "Implement rate limiting\n"
        "--- Blueprint context from Development stage (source workflow) ---\n"
        "Implement Redis-based rate limiter in api-gateway\n"
        "Affected Repositories: demo-org/api-gateway\n"
        "Implementation Phases: Phase 1 - RateLimiterService, Phase 2 - Config"
    ),
    *,
    db: AsyncMock | None = None,
    user_id: uuid.UUID | None = _USER_ID,
    workflow: SimpleNamespace | None = None,
    source_workflow: SimpleNamespace | None = None,
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:blueprint-exec",
        subject_type="freetext",
        display_name=display_name,
    )
    extras = {
        "db": db if db is not None else _make_db(),
        "user_id": user_id,
        "workflow": workflow,
        "source_workflow": (
            source_workflow if source_workflow is not None else _make_source_workflow()
        ),
    }
    return AgentContext(subject=subject, goal="generate_code", extras=extras)


def _make_llm_response(
    repository: str = _REPO,
    files: list[dict] | None = None,
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
                "operation": "modify",
                "content": (
                    "package com.example;\n\npublic class RateLimiterConfig { /* updated */ }"
                ),
            },
        ]
    return json.dumps(
        {
            "executive_summary": "Generated rate limiter service and config.",
            "repository": repository,
            "commit_message": "feat: add Redis-based rate limiter",
            # An LLM-reported confidence is included here on purpose: the
            # agent must never read it (see
            # test_code_generation_agent_ignores_llm_reported_confidence).
            "confidence": 0.99,
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
    assert output.result["repository"] == _REPO
    assert output.result["commit_message"] == "feat: add Redis-based rate limiter"
    assert len(output.result["files"]) == 2

    # AgentOutput metadata
    assert output.agent_id == "code_generation"
    assert output.subject_id == "freetext:blueprint-exec"
    assert output.prompt_version == "1.0"


@pytest.mark.asyncio
async def test_code_generation_agent_reads_full_untruncated_blueprint_context() -> None:
    """context.subject.display_name is truncated to 256 chars by
    app.context.resolvers.freetext.resolve() — a limit sized for a short
    label, not a multi-stage blueprint. The agent must build its prompt
    from the untruncated Development stage result (get_stage_result), not
    just the short display_name, exactly like Engineering Review already
    does."""
    long_change_description = "x" * 2000
    source_workflow = _make_source_workflow(file_paths=["src/main/Foo.java"])
    source_workflow.runs[0].steps[0].result["components"] = [
        {
            "repository": _REPO,
            "file_path": "src/main/Foo.java",
            "change_description": long_change_description,
        }
    ]
    files = [
        {
            "path": "src/main/java/com/example/NewThing.java",
            "operation": "create",
            "content": "package com.example;\n\npublic class NewThing {}",
        }
    ]

    context = _make_context(
        display_name="short label",  # well under 256 chars either way
        source_workflow=source_workflow,
    )

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(files=files)),
    ) as mock_call_llm:
        agent = CodeGenerationAgent()
        await agent.run(context)

    prompt = mock_call_llm.call_args.kwargs["user_prompt"]
    assert long_change_description in prompt
    assert "Development Stage" in prompt


@pytest.mark.asyncio
async def test_code_generation_agent_falls_back_to_display_name_without_workflow() -> None:
    """No workflow/source_workflow at all (a standalone run) — prompt
    construction must not crash and must fall back to
    context.subject.display_name exactly as before this fix. (The run
    still fails afterward on repository verification — no prior-stage
    evidence at all means no repository can ever be confirmed in scope —
    but that is a separate, already-covered gate; this test is only about
    blueprint context assembly.)"""
    subject = Subject(
        subject_id="freetext:blueprint-exec",
        subject_type="freetext",
        display_name="Implement rate limiting",
    )
    context = AgentContext(
        subject=subject,
        goal="generate_code",
        extras={"db": _make_db(), "user_id": _USER_ID, "workflow": None, "source_workflow": None},
    )

    with (
        patch(
            "app.agents.code_generation.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response()),
        ) as mock_call_llm,
        pytest.raises(CodeGenerationRepositoryError),
    ):
        agent = CodeGenerationAgent()
        await agent.run(context)

    prompt = mock_call_llm.call_args.kwargs["user_prompt"]
    assert "Implement rate limiting" in prompt


@pytest.mark.asyncio
async def test_code_generation_agent_ignores_llm_reported_confidence() -> None:
    """The LLM response claims confidence=0.99 (see _make_llm_response).
    The agent must never surface that value — only its own deterministic
    computation."""
    context = _make_context()

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        agent = CodeGenerationAgent()
        output = await agent.run(context)

    assert output.confidence.score != 0.99
    assert output.result["confidence"] != 0.99
    assert "Deterministic confidence" in output.confidence.reasoning


@pytest.mark.asyncio
async def test_code_generation_agent_delete_operation() -> None:
    known_path = "src/old/DeprecatedService.java"
    context = _make_context(
        source_workflow=_make_source_workflow(
            file_paths=[known_path, "src/main/java/com/example/NewService.java"]
        )
    )
    files = [
        {"path": known_path, "operation": "delete", "content": ""},
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
# Repository verification (Part 1.3 / Part 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_generation_agent_rejects_untracked_repository() -> None:
    """Repository is in workflow scope (Development consulted it) but this
    user never tracked/selected it — must fail."""
    context = _make_context(db=_make_db(tracked=False))

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        agent = CodeGenerationAgent()
        with pytest.raises(CodeGenerationRepositoryError, match="not tracked"):
            await agent.run(context)


@pytest.mark.asyncio
async def test_code_generation_agent_rejects_repository_outside_workflow_scope() -> None:
    """LLM names a real-looking repository the workflow never actually
    consulted — must fail rather than silently substitute anything."""
    context = _make_context(
        source_workflow=_make_source_workflow(repository="other-org/other-repo")
    )

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(repository=_REPO)),
    ):
        agent = CodeGenerationAgent()
        with pytest.raises(CodeGenerationRepositoryError, match="outside the scope"):
            await agent.run(context)


@pytest.mark.asyncio
async def test_code_generation_agent_rejects_malformed_repository_name() -> None:
    context = _make_context()

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(repository="not-a-repo-name")),
    ):
        agent = CodeGenerationAgent()
        with pytest.raises(CodeGenerationRepositoryError, match="owner/repo"):
            await agent.run(context)


# ---------------------------------------------------------------------------
# File operation validation (Part 1.4 / Part 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_generation_agent_rejects_modify_of_unknown_file() -> None:
    """Development's own graph traversal reported known file paths for
    this repository, and the claimed 'modify' target isn't one of them."""
    context = _make_context()
    files = [
        {
            "path": "src/main/java/com/example/DoesNotExist.java",
            "operation": "modify",
            "content": "package com.example;",
        }
    ]

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(files=files)),
    ):
        agent = CodeGenerationAgent()
        with pytest.raises(CodeGenerationValidationError, match="does not appear"):
            await agent.run(context)


@pytest.mark.asyncio
async def test_code_generation_agent_rejects_path_traversal_destination() -> None:
    context = _make_context()
    files = [
        {
            "path": "../../etc/passwd",
            "operation": "create",
            "content": "malicious",
        }
    ]

    with patch(
        "app.agents.code_generation.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(files=files)),
    ):
        agent = CodeGenerationAgent()
        with pytest.raises(CodeGenerationValidationError, match="unsafe or invalid"):
            await agent.run(context)


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


def test_validate_never_reads_llm_reported_confidence() -> None:
    """Whatever the LLM puts in `confidence` — a plausible float, an
    absurd one, or garbage — _validate_and_parse must never surface it.
    Execution confidence is computed later, deterministically, in
    CodeGenerationAgent.run."""
    for bogus_confidence in (5.0, "high", -3, None):
        data = {
            "executive_summary": "x",
            "repository": "org/repo",
            "commit_message": "feat: x",
            "confidence": bogus_confidence,
            "files": [{"path": "a.py", "operation": "create", "content": "x"}],
        }
        result = _validate_and_parse(json.dumps(data))
        assert result.confidence == 0.0  # schema default — never LLM-derived


def test_validate_happy_path_returns_result() -> None:
    raw = _make_llm_response()
    result = _validate_and_parse(raw)
    assert isinstance(result, GeneratedCodeResult)
    assert result.repository == _REPO
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
    assert result.confidence == 0.0
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
    assert deserialized["repository"] == _REPO
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
