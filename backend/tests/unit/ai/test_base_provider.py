"""Unit tests for BaseAnalysisProvider._parse_response.

Regression coverage for the "AI provider returned invalid JSON" bug: some
providers (Bedrock/Claude in particular — see
app.agents.prompt_utils.strip_markdown_fence's docstring) wrap their JSON
response in a ```json ... ``` fence despite the system prompt instructing
otherwise. The transitional analyze() path must tolerate that the same way
the invoke_llm_json path already does.
"""

from __future__ import annotations

import pytest

from app.ai.providers.base import BaseAnalysisProvider
from app.ai.providers.errors import AIProviderResponseError

_VALID_PAYLOAD = '{"executive_summary": "Looks good.", "confidence": {"score": 0.8}}'


class _StubProvider(BaseAnalysisProvider):
    """Minimal concrete provider — only _parse_response is under test."""


def _provider() -> _StubProvider:
    return _StubProvider()


def test_parse_response_accepts_plain_json() -> None:
    result = _provider()._parse_response(_VALID_PAYLOAD)
    assert result.executive_summary == "Looks good."


def test_parse_response_strips_json_fenced_markdown() -> None:
    fenced = f"```json\n{_VALID_PAYLOAD}\n```"
    result = _provider()._parse_response(fenced)
    assert result.executive_summary == "Looks good."


def test_parse_response_strips_bare_fenced_markdown() -> None:
    fenced = f"```\n{_VALID_PAYLOAD}\n```"
    result = _provider()._parse_response(fenced)
    assert result.executive_summary == "Looks good."


def test_parse_response_raises_on_genuinely_invalid_json() -> None:
    with pytest.raises(AIProviderResponseError, match="invalid JSON"):
        _provider()._parse_response("not json at all")
