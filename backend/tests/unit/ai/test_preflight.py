"""Unit tests for app.orchestrator.preflight — direct tests of the check
functions themselves, not just RunCoordinator's handling of their return
values (see tests/unit/ai/test_run_coordinator.py for those). This module
had zero permanent tests before this file; every branch below was verified
live against real infrastructure first (see the increment reports), then
locked in here.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentManifest
from app.ai.config.resolver import ResolvedProvider
from app.ai.providers.registry import (
    ProviderBuildConfig,
    UnsupportedProviderError,
    get_provider_spec,
)
from app.models.agent_step import AgentStep
from app.orchestrator import preflight as preflight_module
from app.orchestrator.preflight import (
    ALL_DEPENDENCIES,
    DEPENDENCY_GITHUB_WRITE,
    DEPENDENCY_LLM,
    DEPENDENCY_NEO4J,
    PreflightWarning,
    WarningCheck,
    agent_requires,
    check_llm_provider_configured,
    check_neo4j_reachable,
    collect_preflight_warnings,
    record_preflight_warnings,
    resolve_preflight_stage,
)
from app.tools.interfaces import ToolHealth

# ---------------------------------------------------------------------------
# resolve_preflight_stage
# ---------------------------------------------------------------------------


def test_resolve_preflight_stage_prefers_real_workflow_stage() -> None:
    assert resolve_preflight_stage("planning", "some_other_stage") == "some_other_stage"


def test_resolve_preflight_stage_falls_back_to_agent_default() -> None:
    assert resolve_preflight_stage("planning", None) == "planning"
    assert resolve_preflight_stage("review", None) == "review"


def test_resolve_preflight_stage_none_for_agent_with_no_llm_surface() -> None:
    # git_ops agents (e.g. create_branch) make no LLM call at all.
    assert resolve_preflight_stage("create_branch", None) is None


# ---------------------------------------------------------------------------
# check_llm_provider_configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_check_passes_when_credentials_present() -> None:
    resolved = ResolvedProvider(
        spec=get_provider_spec("openai"),
        config=ProviderBuildConfig(
            api_key="sk-real", model="gpt-4", temperature=0.2, max_tokens=1000
        ),
        source="environment",
    )
    with patch("app.orchestrator.preflight.resolve", return_value=resolved):
        result = check_llm_provider_configured("planning", "planning")
    assert result is None


@pytest.mark.asyncio
async def test_llm_check_fails_when_api_key_missing() -> None:
    resolved = ResolvedProvider(
        spec=get_provider_spec("openai"),
        config=ProviderBuildConfig(api_key=None, model="gpt-4", temperature=0.2, max_tokens=1000),
        source="environment",
    )
    with patch("app.orchestrator.preflight.resolve", return_value=resolved):
        result = check_llm_provider_configured("planning", "planning")
    assert result is not None
    assert "OpenAI" in result
    assert "planning" in result


@pytest.mark.asyncio
async def test_llm_check_passes_for_credential_free_provider() -> None:
    resolved = ResolvedProvider(
        spec=get_provider_spec("bedrock"),
        config=ProviderBuildConfig(api_key=None, model="claude", temperature=0.2, max_tokens=1000),
        source="environment",
    )
    with patch("app.orchestrator.preflight.resolve", return_value=resolved):
        result = check_llm_provider_configured("planning", "planning")
    assert result is None


@pytest.mark.asyncio
async def test_llm_check_skips_entirely_for_agent_with_no_llm_surface() -> None:
    # No workflow_stage and no LLM-surface agent -> resolve() must never be called.
    with patch("app.orchestrator.preflight.resolve") as mock_resolve:
        result = check_llm_provider_configured("create_branch", None)
    assert result is None
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
async def test_llm_check_propagates_resolve_raising() -> None:
    """Regression coverage for the exact defect found and fixed in
    RunCoordinator: check_llm_provider_configured itself does not swallow
    an exception from resolve() — callers are responsible for handling it
    (RunCoordinator does, inside the same try/except that guards
    agent.run() — see test_run_coordinator.py)."""
    with (
        patch(
            "app.orchestrator.preflight.resolve",
            side_effect=UnsupportedProviderError("Unknown AI provider: 'deprecated-vendor'."),
        ),
        pytest.raises(UnsupportedProviderError, match="deprecated-vendor"),
    ):
        check_llm_provider_configured("planning", "planning")


# ---------------------------------------------------------------------------
# check_neo4j_reachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_neo4j_check_skipped_when_agent_never_reads_the_graph() -> None:
    with patch("app.orchestrator.preflight.get_tool_registry") as mock_get_registry:
        result = await check_neo4j_reachable(0)
    assert result is None
    mock_get_registry.assert_not_called()


@pytest.mark.asyncio
async def test_neo4j_check_passes_when_healthy() -> None:
    mock_registry = AsyncMock()
    mock_registry.check_health = AsyncMock(return_value=ToolHealth.HEALTHY)
    with patch("app.orchestrator.preflight.get_tool_registry", return_value=mock_registry):
        result = await check_neo4j_reachable(2)
    assert result is None
    mock_registry.check_health.assert_called_once_with("neo4j_graph")


@pytest.mark.parametrize(
    "health", [ToolHealth.OFFLINE, ToolHealth.UNCONFIGURED, ToolHealth.UNAVAILABLE]
)
@pytest.mark.asyncio
async def test_neo4j_check_fails_when_unhealthy(health: ToolHealth) -> None:
    mock_registry = AsyncMock()
    mock_registry.check_health = AsyncMock(return_value=health)
    with patch("app.orchestrator.preflight.get_tool_registry", return_value=mock_registry):
        result = await check_neo4j_reachable(2)
    assert result is not None
    assert "not reachable" in result
    assert health.value in result


# ---------------------------------------------------------------------------
# record_preflight_warnings (ADR 0011, OD-1 — persistence primitive)
# ---------------------------------------------------------------------------


def _step(preflight_warnings: list[dict] | None = None) -> AgentStep:
    step = AgentStep(id=uuid.uuid4(), run_id=uuid.uuid4(), agent_id="planning", status="running")
    # A freshly constructed, not-yet-flushed AgentStep reads `None` for
    # every JSON column (the Core `default=list` only applies at INSERT
    # time) — set explicitly where a test needs a starting list, matching
    # what a real flushed row would already have.
    if preflight_warnings is not None:
        step.preflight_warnings = preflight_warnings
    return step


def test_record_preflight_warnings_zero_warnings_is_a_no_op() -> None:
    step = _step(preflight_warnings=[])
    record_preflight_warnings(step, [])
    assert step.preflight_warnings == []


def test_record_preflight_warnings_zero_warnings_does_not_touch_unflushed_none() -> None:
    # Not-yet-flushed step (preflight_warnings is None) — an empty warnings
    # list must never attempt to read/iterate that None.
    step = _step()
    assert step.preflight_warnings is None
    record_preflight_warnings(step, [])
    assert step.preflight_warnings is None


def test_record_preflight_warnings_single_warning() -> None:
    step = _step(preflight_warnings=[])
    record_preflight_warnings(
        step,
        [
            PreflightWarning(
                code="jira_reachable", dependency="Jira", message="unreachable", checked_at="t0"
            )
        ],
    )
    assert step.preflight_warnings == [
        {
            "code": "jira_reachable",
            "dependency": "Jira",
            "message": "unreachable",
            "checked_at": "t0",
        }
    ]


def test_record_preflight_warnings_multiple_warnings_preserve_execution_order() -> None:
    step = _step(preflight_warnings=[])
    record_preflight_warnings(
        step,
        [
            PreflightWarning(
                code="jira_reachable", dependency="Jira", message="m1", checked_at="t0"
            ),
            PreflightWarning(
                code="confluence_reachable", dependency="Confluence", message="m2", checked_at="t1"
            ),
        ],
    )
    codes = [w["code"] for w in step.preflight_warnings]
    assert codes == ["jira_reachable", "confluence_reachable"]


def test_record_preflight_warnings_never_overwrites_previous_warnings() -> None:
    step = _step(preflight_warnings=[])
    record_preflight_warnings(
        step,
        [PreflightWarning(code="jira_reachable", dependency="Jira", message="m1", checked_at="t0")],
    )
    record_preflight_warnings(
        step,
        [
            PreflightWarning(
                code="confluence_reachable", dependency="Confluence", message="m2", checked_at="t1"
            )
        ],
    )
    codes = [w["code"] for w in step.preflight_warnings]
    assert codes == ["jira_reachable", "confluence_reachable"]
    assert len(step.preflight_warnings) == 2


def test_record_preflight_warnings_reassigns_rather_than_mutates_in_place() -> None:
    """SQLAlchemy's JSON column type does not track in-place list mutation
    — the implementation must assign a new list object, not call
    `.append()` on the existing one, or the change would silently never be
    flushed. Assert the object identity actually changes."""
    step = _step(preflight_warnings=[])
    original = step.preflight_warnings
    record_preflight_warnings(
        step,
        [PreflightWarning(code="jira_reachable", dependency="Jira", message="m1", checked_at="t0")],
    )
    assert step.preflight_warnings is not original


def test_agent_step_preflight_warnings_column_default_is_empty_list() -> None:
    """The model-level default (applied at flush/INSERT, per
    app.models.agent_step) is `list`, i.e. an empty list — verified here
    against the mapped column's own default, not by round-tripping through
    a real INSERT (that's covered by the integration test)."""
    column = AgentStep.__table__.c.preflight_warnings
    assert column.nullable is False
    assert column.default.arg(None) == []


# ---------------------------------------------------------------------------
# Dependency constants + agent_requires (ADR 0011, OD-3)
# ---------------------------------------------------------------------------


def _manifest(required_dependencies: frozenset[str] = frozenset()) -> AgentManifest:
    return AgentManifest(
        agent_id="test",
        purpose="Test",
        goals=frozenset({"test_goal"}),
        accepted_subject_types=frozenset({"freetext"}),
        cost_class="cheap",
        required_dependencies=required_dependencies,
    )


def test_dependency_constants_are_distinct_strings() -> None:
    values = {DEPENDENCY_LLM, DEPENDENCY_NEO4J, DEPENDENCY_GITHUB_WRITE}
    assert len(values) == 3  # no accidental aliasing


def test_all_dependencies_contains_exactly_the_three_constants() -> None:
    assert {DEPENDENCY_LLM, DEPENDENCY_NEO4J, DEPENDENCY_GITHUB_WRITE} == ALL_DEPENDENCIES


def test_agent_requires_true_when_dependency_declared() -> None:
    manifest = _manifest(frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}))
    assert agent_requires(manifest, DEPENDENCY_LLM) is True
    assert agent_requires(manifest, DEPENDENCY_NEO4J) is True


def test_agent_requires_false_when_dependency_not_declared() -> None:
    manifest = _manifest(frozenset({DEPENDENCY_LLM}))
    assert agent_requires(manifest, DEPENDENCY_NEO4J) is False
    assert agent_requires(manifest, DEPENDENCY_GITHUB_WRITE) is False


def test_agent_requires_false_for_default_empty_declaration() -> None:
    manifest = _manifest()
    assert agent_requires(manifest, DEPENDENCY_LLM) is False
    assert agent_requires(manifest, DEPENDENCY_NEO4J) is False
    assert agent_requires(manifest, DEPENDENCY_GITHUB_WRITE) is False


def test_agent_requires_is_generic_membership_not_agent_specific() -> None:
    """The whole point of OD-3: this must work identically for any
    dependency string, including one that doesn't exist yet (a future
    Jira/Confluence check) — no special-casing inside agent_requires
    itself."""
    manifest = _manifest(frozenset({"some_future_dependency"}))
    assert agent_requires(manifest, "some_future_dependency") is True
    assert agent_requires(manifest, "another_future_dependency") is False


# ---------------------------------------------------------------------------
# _check_github_write_available (ADR 0011 — WARNING-producing check, PR3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_write_check_returns_none_when_connection_present() -> None:
    with patch.object(
        preflight_module, "get_decrypted_access_token", AsyncMock(return_value="gh-token")
    ):
        result = await preflight_module._check_github_write_available(AsyncMock(), uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_github_write_check_returns_warning_when_connection_absent() -> None:
    with patch.object(preflight_module, "get_decrypted_access_token", AsyncMock(return_value=None)):
        result = await preflight_module._check_github_write_available(AsyncMock(), uuid.uuid4())
    assert result is not None
    assert result.code == "github_write_available"
    assert result.dependency == "GitHub"
    assert "GitHub connection" in result.message
    assert result.checked_at  # non-empty ISO timestamp


@pytest.mark.asyncio
async def test_github_write_check_never_raises_for_a_missing_connection() -> None:
    """A missing connection is a normal, expected outcome (most users
    haven't connected GitHub) — must produce a warning, not propagate as an
    exception that could be mistaken for a genuine check bug."""
    with patch.object(preflight_module, "get_decrypted_access_token", AsyncMock(return_value=None)):
        result = await preflight_module._check_github_write_available(AsyncMock(), uuid.uuid4())
    assert isinstance(result, PreflightWarning)


# ---------------------------------------------------------------------------
# collect_preflight_warnings (ADR 0011 — WARNING-check registry, PR3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_preflight_warnings_empty_when_dependency_not_required() -> None:
    """Dependency filtering: an agent that doesn't declare github_write
    must never even reach `_check_github_write_available` — proven here by
    patching it to raise, which would fail this test if it were called."""
    manifest = _manifest(frozenset({DEPENDENCY_LLM}))
    with patch.object(
        preflight_module, "get_decrypted_access_token", AsyncMock(side_effect=AssertionError)
    ):
        result = await collect_preflight_warnings(manifest, AsyncMock(), uuid.uuid4())
    assert result == []


@pytest.mark.asyncio
async def test_collect_preflight_warnings_empty_when_required_and_healthy() -> None:
    manifest = _manifest(frozenset({DEPENDENCY_GITHUB_WRITE}))
    with patch.object(
        preflight_module, "get_decrypted_access_token", AsyncMock(return_value="gh-token")
    ):
        result = await collect_preflight_warnings(manifest, AsyncMock(), uuid.uuid4())
    assert result == []


@pytest.mark.asyncio
async def test_collect_preflight_warnings_returns_warning_when_required_and_unavailable() -> None:
    manifest = _manifest(frozenset({DEPENDENCY_GITHUB_WRITE}))
    with patch.object(preflight_module, "get_decrypted_access_token", AsyncMock(return_value=None)):
        result = await collect_preflight_warnings(manifest, AsyncMock(), uuid.uuid4())
    assert len(result) == 1
    assert result[0].code == "github_write_available"


@pytest.mark.asyncio
async def test_collect_preflight_warnings_preserves_registry_order() -> None:
    """Multiple applicable, simultaneously-unavailable checks must come
    back in `WARNING_CHECKS` tuple order — proven with two synthetic
    checks patched in, since only one real check exists today."""

    async def _first(db, user_id):
        return PreflightWarning(code="first", dependency="A", message="m1", checked_at="t0")

    async def _second(db, user_id):
        return PreflightWarning(code="second", dependency="B", message="m2", checked_at="t1")

    synthetic_checks = (
        WarningCheck(dependency="dep_a", run=_first),
        WarningCheck(dependency="dep_b", run=_second),
    )
    manifest = _manifest(frozenset({"dep_a", "dep_b"}))
    with patch.object(preflight_module, "WARNING_CHECKS", synthetic_checks):
        result = await collect_preflight_warnings(manifest, AsyncMock(), uuid.uuid4())
    assert [w.code for w in result] == ["first", "second"]


@pytest.mark.asyncio
async def test_collect_preflight_warnings_only_calls_applicable_checks() -> None:
    """A second, synthetic check whose dependency the manifest does not
    declare must never run — dependency filtering across multiple
    registry entries, not just the single real one."""

    async def _applicable(db, user_id):
        return PreflightWarning(code="applicable", dependency="A", message="m", checked_at="t0")

    async def _not_applicable(db, user_id):
        raise AssertionError("must not be called — dependency not declared")

    synthetic_checks = (
        WarningCheck(dependency="dep_a", run=_applicable),
        WarningCheck(dependency="dep_b", run=_not_applicable),
    )
    manifest = _manifest(frozenset({"dep_a"}))
    with patch.object(preflight_module, "WARNING_CHECKS", synthetic_checks):
        result = await collect_preflight_warnings(manifest, AsyncMock(), uuid.uuid4())
    assert [w.code for w in result] == ["applicable"]
