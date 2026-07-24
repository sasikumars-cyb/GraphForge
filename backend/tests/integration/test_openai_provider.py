"""Integration tests for OpenAI provider — mocks only HTTP."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ai.providers.factory import (
    UnsupportedModelError,
    UnsupportedProviderError,
    create_llm_provider,
)
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.openai_provider import (
    AIProviderAuthError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
    OpenAIProvider,
)
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext, ContextBuilder
from app.core.config import Settings

# -- Helpers ----------------------------------------------------------------


def _sample_context() -> AIContext:
    return (
        ContextBuilder()
        .with_repository(name="order-svc", owner="acme", default_branch="main")
        .with_pull_request(title="Fix payment flow", number=42, head_ref="fix/pay", base_ref="main")
        .with_changed_files(["src/payments.py"])
        .with_repositories(
            [
                {
                    "id": "r1",
                    "owner": "acme",
                    "name": "order-svc",
                    "full_name": "acme/order-svc",
                    "relation": "current",
                },
                {
                    "id": "r2",
                    "owner": "acme",
                    "name": "inventory-svc",
                    "full_name": "acme/inventory-svc",
                    "relation": "downstream",
                },
            ]
        )
        .build()
    )


def _valid_ai_result() -> dict[str, object]:
    return {
        "executive_summary": "Minor payment flow fix with no breaking changes.",
        "breaking_changes": [],
        "migration_advice": [],
        "suggested_reviewers": [
            {
                "reviewer": "payments-team",
                "reason": "Owns the payment module",
                "confidence": {"score": 0.9, "reasoning": "Direct ownership"},
            }
        ],
        "regression_tests": [
            {
                "component": "PaymentService",
                "test_description": "Verify payment processing still works",
                "priority": "high",
                "confidence": {"score": 0.85, "reasoning": "Critical path"},
            }
        ],
        "release_coordination_plan": {
            "deployment_order": [
                {
                    "order": 1,
                    "repository": "order-svc",
                    "action": "Deploy first",
                    "reason": "Producer of the changed event",
                },
                {
                    "order": 2,
                    "repository": "inventory-svc",
                    "action": "Deploy after order-svc",
                    "reason": "Consumes the changed event",
                },
            ],
            "repositories_to_notify": [
                {
                    "repository": "inventory-svc",
                    "reason": "Consumes the affected Kafka topic",
                    "urgency": "blocking",
                }
            ],
            "rollout_strategy": "Deploy order-svc behind a feature flag first.",
            "backward_compatibility_advice": "Keep the event schema backward compatible.",
            "communication_summary": "Notify inventory-svc before rollout.",
            "rollout_risks": ["Kafka deserialization failures during rollout"],
        },
        "confidence": {"score": 0.88, "reasoning": "High confidence analysis"},
    }


def _openai_response(content: dict[str, Any] | str, status: int = 200) -> httpx.Response:
    """Build a fake OpenAI HTTP response."""
    if status != 200:
        return httpx.Response(status_code=status, text=str(content))

    body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(content) if isinstance(content, dict) else content,
                }
            }
        ]
    }
    return httpx.Response(status_code=200, json=body)


def _gemini_response(
    text: str,
    *,
    status: int = 200,
    finish_reason: str = "STOP",
    model_version: str = "gemini-3.6-flash",
    usage: dict[str, Any] | None = None,
) -> httpx.Response:
    if status != 200:
        return httpx.Response(status_code=status, text=text)

    body: dict[str, Any] = {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": finish_reason,
            }
        ],
        "modelVersion": model_version,
    }
    if usage is not None:
        body["usageMetadata"] = usage
    return httpx.Response(status_code=200, json=body)


# -- Tests: OpenAI Provider -------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_success() -> None:
    """Happy path: OpenAI returns valid JSON matching AIAnalysisResult."""
    transport = httpx.MockTransport(lambda request: _openai_response(_valid_ai_result()))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    result = await provider.analyze(_sample_context())

    assert isinstance(result, AIAnalysisResult)
    assert result.executive_summary == "Minor payment flow fix with no breaking changes."
    assert len(result.suggested_reviewers) == 1
    assert result.suggested_reviewers[0].reviewer == "payments-team"
    assert len(result.regression_tests) == 1
    assert result.prompt_version == "1.4"

    plan = result.release_coordination_plan
    assert len(plan.deployment_order) == 2
    assert plan.deployment_order[0].repository == "order-svc"
    assert plan.deployment_order[1].repository == "inventory-svc"
    assert len(plan.repositories_to_notify) == 1
    assert plan.repositories_to_notify[0].repository == "inventory-svc"
    assert plan.rollout_risks == ["Kafka deserialization failures during rollout"]


@pytest.mark.asyncio
async def test_analyze_timeout() -> None:
    """Provider raises AIProviderTimeoutError on timeout."""

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out")

    transport = httpx.MockTransport(timeout_handler)
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderTimeoutError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_invalid_api_key() -> None:
    """Provider raises AIProviderAuthError on 401."""
    transport = httpx.MockTransport(lambda request: _openai_response("Unauthorized", status=401))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-invalid", http_client=client)

    with pytest.raises(AIProviderAuthError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_rate_limit() -> None:
    """Provider raises AIProviderRateLimitError on 429."""
    transport = httpx.MockTransport(lambda request: _openai_response("Rate limited", status=429))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderRateLimitError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_server_error() -> None:
    """Provider raises AIProviderError on 5xx."""
    transport = httpx.MockTransport(lambda request: _openai_response("Internal error", status=500))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_rate_limit_preserves_provider_message() -> None:
    """429 preserves provider's own message when available."""
    body = {
        "error": {
            "message": "Rate limit reached for this project. Please retry in 15s.",
            "type": "rate_limit_error",
            "code": "rate_limit_exceeded",
        }
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code=429, json=body))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderRateLimitError, match="Rate limit reached for this project"):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_malformed_json() -> None:
    """Provider raises AIProviderResponseError when OpenAI returns non-JSON."""
    body = {"choices": [{"message": {"content": "not valid json {"}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code=200, json=body))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderResponseError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_malformed_structure() -> None:
    """Provider raises AIProviderResponseError when response structure is wrong."""
    body = {"unexpected": "structure"}
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code=200, json=body))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderResponseError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_schema_validation_failure() -> None:
    """Provider raises AIProviderResponseError when JSON doesn't match schema."""
    invalid_result = {"executive_summary": "ok", "confidence": {"score": 99.0}}
    transport = httpx.MockTransport(lambda request: _openai_response(invalid_result))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderResponseError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_rejects_arbitrary_urgency_value() -> None:
    """A model that ignores the prompt's closed urgency vocabulary fails
    validation rather than reaching the client with an unrenderable value."""
    result = _valid_ai_result()
    result["release_coordination_plan"]["repositories_to_notify"][0]["urgency"] = "ASAP"
    transport = httpx.MockTransport(lambda request: _openai_response(result))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderResponseError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_analyze_clears_single_repository_deployment_order() -> None:
    """Even if the model produces a single-repository "deployment order",
    the parsed result never carries one - enforced by the schema, not the
    prompt."""
    result = _valid_ai_result()
    result["release_coordination_plan"]["deployment_order"] = [
        {
            "order": 1,
            "repository": "order-svc",
            "action": "Deploy carefully",
            "reason": "Solo change",
        }
    ]
    transport = httpx.MockTransport(lambda request: _openai_response(result))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    parsed = await provider.analyze(_sample_context())

    assert parsed.release_coordination_plan.deployment_order == []


@pytest.mark.asyncio
async def test_complete_returns_normalized_response_with_usage() -> None:
    """complete() — the transport-only entry point migrated agents use —
    returns raw text plus normalized usage metadata, not a parsed
    AIAnalysisResult (that's analyze()'s job, not complete()'s)."""
    body = {
        "choices": [{"message": {"content": "hello world"}, "finish_reason": "stop"}],
        "model": "gpt-5.5",
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code=200, json=body))
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    from app.ai.providers.base import LLMRequestOptions, ResponseFormat

    response = await provider.complete(
        system_prompt="You are helpful.",
        user_prompt="Say hello.",
        options=LLMRequestOptions(response_format=ResponseFormat.JSON),
    )

    assert response.text == "hello world"
    assert response.model_name == "gpt-5.5"
    assert response.finish_reason == "stop"
    assert response.prompt_tokens == 10
    assert response.completion_tokens == 3
    assert response.total_tokens == 13


@pytest.mark.asyncio
async def test_complete_sends_response_format_json_when_requested() -> None:
    """ResponseFormat.JSON must actually translate to OpenAI's
    response_format param; ResponseFormat.TEXT must omit it."""
    from app.ai.providers.base import LLMRequestOptions, ResponseFormat

    captured: dict[str, Any] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _openai_response("plain text reply")

    transport = httpx.MockTransport(capture)
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    await provider.complete(
        system_prompt="sys",
        user_prompt="usr",
        options=LLMRequestOptions(response_format=ResponseFormat.JSON),
    )
    assert captured["body"]["response_format"] == {"type": "json_object"}

    await provider.complete(
        system_prompt="sys",
        user_prompt="usr",
        options=LLMRequestOptions(response_format=ResponseFormat.TEXT),
    )
    assert "response_format" not in captured["body"]


@pytest.mark.asyncio
async def test_complete_propagates_rate_limit_error() -> None:
    """Transport-level error mapping is shared with analyze() via
    _send_completion — this just confirms complete() itself surfaces it,
    not only the transitional analyze() path."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status_code=429, json={"error": {"message": "Too many requests."}}
        )
    )
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIProvider(api_key="sk-test-key", http_client=client)

    with pytest.raises(AIProviderRateLimitError, match="Too many requests."):
        await provider.complete(system_prompt="sys", user_prompt="usr")


# -- Tests: Gemini Provider -------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_analyze_success() -> None:
    payload = json.dumps(_valid_ai_result())
    transport = httpx.MockTransport(lambda request: _gemini_response(payload))
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="gk-test-key", http_client=client)

    result = await provider.analyze(_sample_context())

    assert isinstance(result, AIAnalysisResult)
    assert result.executive_summary == "Minor payment flow fix with no breaking changes."
    assert result.prompt_version == "1.4"


@pytest.mark.asyncio
async def test_gemini_invalid_api_key() -> None:
    body = {
        "error": {
            "code": 401,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "UNAUTHENTICATED",
        }
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code=401, json=body))
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="bad-key", http_client=client)

    with pytest.raises(AIProviderAuthError, match="API key not valid"):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_gemini_rate_limit_preserves_provider_message() -> None:
    body = {
        "error": {
            "code": 429,
            "message": "Rate limit reached for model gemini-3.6-flash. Try again later.",
            "status": "RESOURCE_EXHAUSTED",
        }
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code=429, json=body, headers={"retry-after": "8"})
    )
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="gk-test", http_client=client)

    with pytest.raises(AIProviderRateLimitError, match="Rate limit reached for model") as exc_info:
        await provider.analyze(_sample_context())

    provider_error = exc_info.value.provider_error
    assert provider_error["provider"] == "gemini"
    assert provider_error["status_code"] == 429
    assert provider_error["retry_after"] == "8"


@pytest.mark.asyncio
async def test_gemini_malformed_response() -> None:
    body = {"candidates": [{"content": {"parts": [{}]}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code=200, json=body))
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="gk-test", http_client=client)

    with pytest.raises(AIProviderResponseError):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_gemini_provider_error_response() -> None:
    body = {
        "error": {
            "code": 500,
            "message": "The model backend is currently unavailable.",
            "status": "INTERNAL",
        }
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code=500, json=body))
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="gk-test", http_client=client)

    with pytest.raises(AIProviderError, match="The model backend is currently unavailable"):
        await provider.analyze(_sample_context())


@pytest.mark.asyncio
async def test_gemini_usage_metadata_parsing() -> None:
    payload = json.dumps(_valid_ai_result())
    transport = httpx.MockTransport(
        lambda request: _gemini_response(
            payload,
            usage={
                "promptTokenCount": 123,
                "candidatesTokenCount": 456,
                "totalTokenCount": 579,
            },
        )
    )
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="gk-test", http_client=client)

    llm_response = await provider._request_completion("raw prompt")  # noqa: SLF001

    assert llm_response.prompt_tokens == 123
    assert llm_response.completion_tokens == 456
    assert llm_response.total_tokens == 579
    assert llm_response.model_name == "gemini-3.6-flash"
    assert llm_response.finish_reason == "STOP"


@pytest.mark.asyncio
async def test_gemini_complete_accepts_but_ignores_response_format() -> None:
    """Gemini's generateContent API has no JSON-mode param — complete()
    must still accept LLMRequestOptions(response_format=...) without
    erroring, silently ignoring what it can't honor."""
    from app.ai.providers.base import LLMRequestOptions, ResponseFormat

    transport = httpx.MockTransport(lambda request: _gemini_response("plain reply"))
    client = httpx.AsyncClient(transport=transport)
    provider = GeminiProvider(api_key="gk-test", http_client=client)

    response = await provider.complete(
        system_prompt="sys",
        user_prompt="usr",
        options=LLMRequestOptions(response_format=ResponseFormat.JSON),
    )

    assert response.text == "plain reply"


# -- Tests: Factory ---------------------------------------------------------


def test_factory_creates_openai_provider() -> None:
    settings = Settings(ai_provider="openai", openai_api_key="sk-test")
    provider = create_llm_provider(settings)
    assert isinstance(provider, OpenAIProvider)


def test_factory_missing_api_key_raises() -> None:
    settings = Settings(ai_provider="openai", openai_api_key=None)
    with pytest.raises(Exception, match="OPENAI_API_KEY"):
        create_llm_provider(settings)


def test_factory_claude_not_implemented() -> None:
    settings = Settings(ai_provider="claude", openai_api_key="sk-test")
    with pytest.raises(UnsupportedProviderError):
        create_llm_provider(settings)


def test_factory_creates_gemini_provider() -> None:
    settings = Settings(
        ai_provider="gemini", gemini_api_key="gk-test", gemini_model="gemini-3.6-flash"
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, GeminiProvider)
    assert provider._model == "gemini-3.6-flash"  # noqa: SLF001


def test_factory_ollama_not_implemented() -> None:
    settings = Settings(ai_provider="ollama", openai_api_key="sk-test")
    with pytest.raises(UnsupportedProviderError):
        create_llm_provider(settings)


def test_factory_unknown_provider_raises() -> None:
    settings = Settings(ai_provider="unknown_vendor", openai_api_key="sk-test")
    with pytest.raises(UnsupportedProviderError):
        create_llm_provider(settings)


def test_factory_model_override_selects_requested_model() -> None:
    settings = Settings(ai_provider="openai", openai_api_key="sk-test", openai_model="gpt-5")
    provider = create_llm_provider(settings, model="gpt-5-mini")
    assert provider._model == "gpt-5-mini"  # noqa: SLF001


def test_factory_no_override_falls_back_to_configured_default() -> None:
    settings = Settings(ai_provider="openai", openai_api_key="sk-test", openai_model="gpt-5")
    provider = create_llm_provider(settings)
    assert provider._model == "gpt-5"  # noqa: SLF001


def test_factory_rejects_unsupported_model() -> None:
    settings = Settings(ai_provider="openai", openai_api_key="sk-test")
    with pytest.raises(UnsupportedModelError):
        create_llm_provider(settings, model="gpt-3.5-turbo")


def test_factory_creates_groq_provider_pointed_at_groq_url() -> None:
    settings = Settings(
        ai_provider="groq", groq_api_key="gsk-test", groq_model="llama-3.3-70b-versatile"
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, OpenAIProvider)
    assert provider._model == "llama-3.3-70b-versatile"  # noqa: SLF001
    assert provider._base_url == "https://api.groq.com/openai/v1/chat/completions"  # noqa: SLF001


def test_factory_groq_missing_api_key_raises() -> None:
    settings = Settings(ai_provider="groq", groq_api_key=None)
    with pytest.raises(Exception, match="GROQ_API_KEY"):
        create_llm_provider(settings)


def test_factory_gemini_missing_api_key_raises() -> None:
    settings = Settings(ai_provider="gemini", gemini_api_key=None)
    with pytest.raises(Exception, match="GEMINI_API_KEY"):
        create_llm_provider(settings)
