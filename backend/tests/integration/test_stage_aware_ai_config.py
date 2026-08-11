"""End-to-end proof that AI Workspace configuration governs agent execution.

These tests deliberately mock **only the outbound HTTP transport** — each
concrete provider's `_send_completion`. Everything above it is the real
code path:

    agent._call_llm
      -> app.agents.llm.StageAwareLLMProvider.complete
        -> app.ai.providers.factory.validate_resolution
        -> app.ai.config.fallback.complete_with_fallback
          -> app.ai.config.resolver.resolve   (stage / profile / provider)
          -> ProviderSpec.build               (real provider instance)
            -> <provider>._send_completion    (mocked here)

so an assertion about which provider class and model_id arrived at the
transport is a real assertion about resolution, not about a mock.

This is the regression suite for the defect these tests were written
against: every agent used to call `create_llm_provider(model=model)` with
no `stage`, so `resolve()` always ran with `stage=None`, every per-stage
override and AI Profile silently did nothing, and the AI Workspace's
displayed "effective model" per stage did not match what executed.
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from app.agents.development.agent import _call_llm as call_llm_development
from app.agents.engineering_review.agent import _call_llm as call_llm_engineering_review
from app.agents.planning.agent import PlanningLLMError
from app.agents.planning.agent import _call_llm as call_llm_planning
from app.agents.testing.agent import _call_llm as call_llm_testing
from app.ai.config import store
from app.ai.config.store import ConfigSnapshot, ProfileRecord, ProviderRecord
from app.ai.providers.base import LLMResponse
from app.ai.providers.bedrock_provider import BedrockProvider
from app.ai.providers.errors import AIProviderAuthError, AIProviderRateLimitError
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.openai_provider import OpenAIProvider

_PROVIDER_CLASSES = (OpenAIProvider, GeminiProvider, BedrockProvider)

# A Claude model served through Bedrock. The native `anthropic` provider is
# declared `implemented=False` in the registry, so Bedrock is how this
# codebase actually runs Claude today — see ai/providers/registry.py.
_CLAUDE_ON_BEDROCK = "us.anthropic.claude-sonnet-4-20250514"


@contextlib.contextmanager
def captured_transport(failures: dict[str, Exception] | None = None):
    """Patch every concrete provider's outbound call, recording which
    provider class and model id actually reached the wire.

    `failures` maps a provider class name to an exception it should raise
    instead of answering — used to drive the fallback scenarios.
    """
    calls: list[tuple[str, str]] = []
    failures = failures or {}

    def make(cls):
        async def _send(self, *, system_prompt, user_prompt, options):
            name = type(self).__name__
            calls.append((name, self._model))
            if name in failures:
                raise failures[name]
            return LLMResponse(text='{"ok": true}', model_name=self._model)

        return patch.object(cls, "_send_completion", _send)

    with contextlib.ExitStack() as stack:
        for cls in _PROVIDER_CLASSES:
            stack.enter_context(make(cls))
        yield calls


@contextlib.contextmanager
def configured(snapshot: ConfigSnapshot):
    """Install a ConfigSnapshot as if the AI Workspace had written it.

    Patches the module-level snapshot the resolver reads, which is exactly
    what `store.refresh(db)` populates from the `ai_provider_configs` /
    `ai_settings` tables at startup and on every settings change.
    """
    with patch.object(store, "_snapshot", snapshot):
        yield


def _providers() -> dict[str, ProviderRecord]:
    """Three enabled, credentialed providers to choose between."""
    return {
        key: ProviderRecord(
            provider_key=key,
            api_key="test-key",
            model=None,
            base_url=None,
            temperature=None,
            max_tokens=None,
            enabled=True,
            status="ready",
        )
        for key in ("openai", "gemini", "bedrock", "groq")
    }


# ---------------------------------------------------------------------------
# Scenarios 1-3 — stage-specific provider AND model selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_1_planning_configured_for_claude_executes_on_claude() -> None:
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="openai",
        default_model="gpt-5",
        stage_overrides={
            "planning": {"provider": "bedrock", "model": _CLAUDE_ON_BEDROCK},
        },
        loaded=True,
    )
    with configured(snapshot), captured_transport() as calls:
        await call_llm_planning("plan this")

    assert calls == [("BedrockProvider", _CLAUDE_ON_BEDROCK)], (
        "Planning was configured for Claude but did not execute on it. "
        f"Actual transport calls: {calls}"
    )


@pytest.mark.asyncio
async def test_scenario_2_development_configured_for_gpt5_executes_on_gpt5() -> None:
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="bedrock",
        stage_overrides={"development": {"provider": "openai", "model": "gpt-5"}},
        loaded=True,
    )
    with configured(snapshot), captured_transport() as calls:
        await call_llm_development("build this")

    assert calls == [("OpenAIProvider", "gpt-5")], f"Actual: {calls}"


@pytest.mark.asyncio
async def test_scenario_3_testing_configured_for_gemini_executes_on_gemini() -> None:
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="openai",
        default_model="gpt-5",
        stage_overrides={"testing": {"provider": "gemini", "model": "gemini-2.0-flash"}},
        loaded=True,
    )
    with configured(snapshot), captured_transport() as calls:
        await call_llm_testing("test this")

    assert calls == [("GeminiProvider", "gemini-2.0-flash")], f"Actual: {calls}"


@pytest.mark.asyncio
async def test_stages_are_isolated_from_each_other() -> None:
    """Three stages, three different vendors, one snapshot — each agent must
    pick its own row, not the global default and not another stage's."""
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="groq",
        stage_overrides={
            "planning": {"provider": "bedrock", "model": _CLAUDE_ON_BEDROCK},
            "development": {"provider": "openai", "model": "gpt-5"},
            "testing": {"provider": "gemini", "model": "gemini-2.0-flash"},
        },
        loaded=True,
    )
    with configured(snapshot), captured_transport() as calls:
        await call_llm_planning("p")
        await call_llm_development("d")
        await call_llm_testing("t")

    assert calls == [
        ("BedrockProvider", _CLAUDE_ON_BEDROCK),
        ("OpenAIProvider", "gpt-5"),
        ("GeminiProvider", "gemini-2.0-flash"),
    ], f"Actual: {calls}"


@pytest.mark.asyncio
async def test_unconfigured_stage_falls_through_to_global_default() -> None:
    """Backward compatibility: a stage with no override resolves exactly as
    it did before this layer was wired up."""
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="openai",
        default_model="gpt-5",
        stage_overrides={"planning": {"provider": "bedrock"}},
        loaded=True,
    )
    with configured(snapshot), captured_transport() as calls:
        await call_llm_engineering_review("review this")  # no override for this stage

    assert calls == [("OpenAIProvider", "gpt-5")], f"Actual: {calls}"


# ---------------------------------------------------------------------------
# Scenario 4 — AI Profile resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_4_stage_mapped_profile_governs_execution() -> None:
    """A stage mapped to a Profile takes the Profile's provider AND model —
    the Profile is a fully-specified behaviour, not just a vendor pointer."""
    snapshot = ConfigSnapshot(
        providers=_providers(),
        profiles={
            "fast-planner": ProfileRecord(
                slug="fast-planner",
                name="Fast Planner",
                provider_key="gemini",
                model="gemini-2.0-flash",
                temperature=0.1,
                max_tokens=2048,
            )
        },
        default_provider="openai",
        default_model="gpt-5",
        stage_overrides={"planning": {"profile": "fast-planner"}},
        loaded=True,
    )
    with configured(snapshot), captured_transport() as calls:
        await call_llm_planning("plan this")

    assert calls == [("GeminiProvider", "gemini-2.0-flash")], f"Actual: {calls}"


@pytest.mark.asyncio
async def test_scenario_4b_profile_wins_over_stage_provider_override() -> None:
    """Documented precedence: stage profile is checked before stage provider
    override (see resolver.resolve's profile-first block)."""
    snapshot = ConfigSnapshot(
        providers=_providers(),
        profiles={
            "cheap": ProfileRecord(
                slug="cheap", name="Cheap", provider_key="gemini", model="gemini-2.0-flash"
            )
        },
        default_provider="openai",
        stage_overrides={
            "planning": {"profile": "cheap", "provider": "bedrock", "model": _CLAUDE_ON_BEDROCK}
        },
        loaded=True,
    )
    with configured(snapshot), captured_transport() as calls:
        await call_llm_planning("plan this")

    assert calls == [("GeminiProvider", "gemini-2.0-flash")], f"Actual: {calls}"


# ---------------------------------------------------------------------------
# Scenarios 5-6 — the SHARED fallback engine (app.ai.config.fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_5_recoverable_error_falls_back_to_configured_provider() -> None:
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="openai",
        default_model="gpt-5",
        stage_overrides={"planning": {"provider": "openai", "model": "gpt-5"}},
        fallback_enabled=True,
        fallback_order=["gemini"],
        loaded=True,
    )
    failures = {"OpenAIProvider": AIProviderRateLimitError("429 slow down")}
    with configured(snapshot), captured_transport(failures) as calls:
        text = await call_llm_planning("plan this")

    assert text == '{"ok": true}'
    assert [c[0] for c in calls] == [
        "OpenAIProvider",
        "GeminiProvider",
    ], f"Expected a fallback hop to the configured fallback provider. Actual: {calls}"


@pytest.mark.asyncio
async def test_scenario_5b_fallback_is_off_unless_operator_enables_it() -> None:
    """The invariant the old Planning-Agent ladder violated: two configured
    keys must NOT cause a silent cross-vendor hop on their own."""
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="openai",
        default_model="gpt-5",
        stage_overrides={"planning": {"provider": "openai", "model": "gpt-5"}},
        fallback_enabled=False,  # operator has not opted in
        fallback_order=["gemini"],
        loaded=True,
    )
    failures = {"OpenAIProvider": AIProviderRateLimitError("429 slow down")}
    with (
        configured(snapshot),
        captured_transport(failures) as calls,
        pytest.raises(PlanningLLMError),
    ):
        await call_llm_planning("plan this")

    assert [c[0] for c in calls] == [
        "OpenAIProvider"
    ], f"A vendor hop happened without the operator enabling fallback. Actual: {calls}"


@pytest.mark.asyncio
async def test_scenario_6_non_recoverable_error_does_not_fall_back() -> None:
    """Auth failure is deterministic — retrying it on another vendor just
    burns that vendor's quota to fail the same way. Existing failure policy
    (remap to the agent's own error type) must still apply."""
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="openai",
        default_model="gpt-5",
        stage_overrides={"planning": {"provider": "openai", "model": "gpt-5"}},
        fallback_enabled=True,
        fallback_order=["gemini"],
        loaded=True,
    )
    failures = {"OpenAIProvider": AIProviderAuthError("bad key")}
    with (
        configured(snapshot),
        captured_transport(failures) as calls,
        pytest.raises(PlanningLLMError) as exc,
    ):
        await call_llm_planning("plan this")

    assert str(exc.value) == "bad key"
    assert [c[0] for c in calls] == [
        "OpenAIProvider"
    ], f"A non-recoverable error must not hop vendors. Actual: {calls}"


# ---------------------------------------------------------------------------
# Provenance — what the UI reports must be what ran
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_metadata_reports_the_provider_that_actually_served() -> None:
    """After a fallback hop, Planning's LLMTrace must name the vendor that
    really answered, not the configured primary."""
    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="openai",
        stage_overrides={"planning": {"provider": "openai", "model": "gpt-5"}},
        fallback_enabled=True,
        fallback_order=["gemini"],
        loaded=True,
    )
    failures = {"OpenAIProvider": AIProviderRateLimitError("429")}
    metadata: dict = {}
    with configured(snapshot), captured_transport(failures):
        await call_llm_planning("plan this", _metadata_out=metadata)

    assert metadata["provider"] == "gemini"
    assert metadata["model"] == "gemini-3.6-flash"


@pytest.mark.asyncio
async def test_ai_workspace_reported_effective_model_matches_execution() -> None:
    """The AI Workspace computes each stage's effective provider/model via
    `resolve(stage=...)` (see api/v1/routers/ai_workspace.py). That report is
    only truthful if execution resolves identically — this asserts they agree,
    which is precisely what was broken before."""
    from app.ai.config.resolver import resolve

    snapshot = ConfigSnapshot(
        providers=_providers(),
        default_provider="groq",
        stage_overrides={
            "planning": {"provider": "bedrock", "model": _CLAUDE_ON_BEDROCK},
            "testing": {"provider": "gemini", "model": "gemini-2.0-flash"},
        },
        loaded=True,
    )
    with configured(snapshot):
        reported_planning = resolve(stage="planning")
        reported_testing = resolve(stage="testing")
        with captured_transport() as calls:
            await call_llm_planning("p")
            await call_llm_testing("t")

    assert reported_planning.model == calls[0][1]
    assert reported_testing.model == calls[1][1]
    assert (reported_planning.key, reported_testing.key) == ("bedrock", "gemini")
