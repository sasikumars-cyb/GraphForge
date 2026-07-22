"""Integration tests for OpenAI provider — mocks only HTTP."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ai.providers.factory import (
    UnsupportedProviderError,
    create_llm_provider,
)
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
    assert result.prompt_version == "1.2"

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


def test_factory_gemini_not_implemented() -> None:
    settings = Settings(ai_provider="gemini", openai_api_key="sk-test")
    with pytest.raises(UnsupportedProviderError):
        create_llm_provider(settings)


def test_factory_ollama_not_implemented() -> None:
    settings = Settings(ai_provider="ollama", openai_api_key="sk-test")
    with pytest.raises(UnsupportedProviderError):
        create_llm_provider(settings)


def test_factory_unknown_provider_raises() -> None:
    settings = Settings(ai_provider="unknown_vendor", openai_api_key="sk-test")
    with pytest.raises(UnsupportedProviderError):
        create_llm_provider(settings)
