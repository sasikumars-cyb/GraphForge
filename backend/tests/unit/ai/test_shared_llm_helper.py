"""Unit tests for shared LLM helper (app.agents._llm).

Focus: HTTP status/error mapping and preserving provider rate-limit
messages for 429 responses.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.agents._llm import call_chat_completion_json
from app.core.exceptions import AppError


class DummyLLMError(AppError):
    status_code = 502
    error_code = "dummy_llm_error"


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force deterministic provider/model config for shared helper tests."""
    monkeypatch.setattr(
        "app.agents._llm.get_settings",
        lambda: SimpleNamespace(
            ai_provider="groq",
            groq_api_key="dummy",
            groq_model="llama-3.3-70b-versatile",
            openai_api_key="dummy-openai",
            openai_model="gpt-4o",
            openai_temperature=0.2,
            openai_max_tokens=4096,
        ),
    )


@pytest.mark.asyncio
async def test_429_uses_provider_error_message_when_present(caplog: pytest.LogCaptureFixture) -> None:
    """When provider returns 429 with error.message, preserve it."""
    caplog.set_level(logging.WARNING, logger="app.agents._llm")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=429,
            json={
                "error": {
                    "message": "Rate limit reached for model llama-3.3-70b-versatile.",
                    "type": "tokens",
                    "code": "rate_limit_exceeded",
                }
            },
            headers={"retry-after": "12"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DummyLLMError) as exc_info:
        await call_chat_completion_json(
            system_prompt="sys",
            user_prompt="user",
            error_cls=DummyLLMError,
            http_client=client,
        )

    await client.aclose()

    assert str(exc_info.value) == "Rate limit reached for model llama-3.3-70b-versatile."
    assert hasattr(exc_info.value, "provider_error")
    provider_error = getattr(exc_info.value, "provider_error")
    assert provider_error["provider"] == "groq"
    assert provider_error["model"] == "llama-3.3-70b-versatile"
    assert provider_error["status_code"] == 429
    assert provider_error["retry_after"] == "12"
    assert provider_error["error_type"] == "tokens"
    assert provider_error["error_code"] == "rate_limit_exceeded"

    log = next(r for r in caplog.records if r.getMessage().startswith("llm_provider_error"))
    text = log.getMessage()
    assert "status_code=429" in text
    assert "retry_after=12" in text
    assert "error_type=tokens" in text
    assert "error_code=rate_limit_exceeded" in text


@pytest.mark.asyncio
async def test_429_with_malformed_json_falls_back_to_generic_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed JSON body on 429 should fall back safely."""
    caplog.set_level(logging.WARNING, logger="app.agents._llm")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=429,
            content=b"{this is not valid json",
            headers={"content-type": "application/json"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DummyLLMError) as exc_info:
        await call_chat_completion_json(
            system_prompt="sys",
            user_prompt="user",
            error_cls=DummyLLMError,
            http_client=client,
        )

    await client.aclose()

    assert str(exc_info.value) == "LLM rate limit exceeded."
    provider_error = getattr(exc_info.value, "provider_error")
    assert provider_error["status_code"] == 429
    assert provider_error["error_message"] is None
    assert provider_error["error_type"] is None
    assert provider_error["error_code"] is None

    log = next(r for r in caplog.records if r.getMessage().startswith("llm_provider_error"))
    text = log.getMessage()
    assert "status_code=429" in text
    assert "error_type=None" in text
    assert "error_code=None" in text


@pytest.mark.asyncio
async def test_429_with_missing_error_message_falls_back_to_generic_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If error.message is absent, use the existing generic message."""
    caplog.set_level(logging.WARNING, logger="app.agents._llm")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=429,
            json={"error": {"type": "tokens", "code": "rate_limit_exceeded"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DummyLLMError) as exc_info:
        await call_chat_completion_json(
            system_prompt="sys",
            user_prompt="user",
            error_cls=DummyLLMError,
            http_client=client,
        )

    await client.aclose()

    assert str(exc_info.value) == "LLM rate limit exceeded."
    provider_error = getattr(exc_info.value, "provider_error")
    assert provider_error["status_code"] == 429
    assert provider_error["error_message"] is None
    assert provider_error["error_type"] == "tokens"
    assert provider_error["error_code"] == "rate_limit_exceeded"

    log = next(r for r in caplog.records if r.getMessage().startswith("llm_provider_error"))
    text = log.getMessage()
    assert "error_type=tokens" in text
    assert "error_code=rate_limit_exceeded" in text


@pytest.mark.asyncio
async def test_non_429_error_behavior_is_unchanged() -> None:
    """Non-429 responses should keep existing mapping behavior."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, text="Internal error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DummyLLMError) as exc_info:
        await call_chat_completion_json(
            system_prompt="sys",
            user_prompt="user",
            error_cls=DummyLLMError,
            http_client=client,
        )

    await client.aclose()

    assert str(exc_info.value) == "LLM returned HTTP 500."
    provider_error = getattr(exc_info.value, "provider_error")
    assert provider_error["status_code"] == 500


@pytest.mark.asyncio
async def test_successful_request_behavior_is_unchanged() -> None:
    """Successful responses keep the exact existing content path."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"ok": True, "message": "hello"}),
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    content = await call_chat_completion_json(
        system_prompt="sys",
        user_prompt="user",
        error_cls=DummyLLMError,
        http_client=client,
    )
    await client.aclose()

    assert content == '{"ok": true, "message": "hello"}'
