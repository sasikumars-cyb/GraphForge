"""Tests for the Amazon Bedrock provider.

Covers:
- Provider initialization and lazy client creation
- Configuration validation
- Successful inference (mocked Converse API)
- Response extraction and normalization
- Finish reason normalization
- Token usage extraction
- Authentication failures (AccessDeniedException, ExpiredTokenException)
- Model unavailable (ResourceNotFoundException)
- Access denied (AccessDeniedException)
- Rate limiting (ThrottlingException)
- Timeout handling
- Network errors
- Provider switching via registry
- Registry integration (spec, models, capabilities)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ai.config import store
from app.ai.config.resolver import resolve
from app.ai.config.store import ConfigSnapshot, ProviderRecord
from app.ai.providers.base import LLMRequestOptions, LLMResponse, ResponseFormat
from app.ai.providers.bedrock_provider import BedrockProvider
from app.ai.providers.errors import (
    AIProviderAuthError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)
from app.ai.providers.factory import create_llm_provider
from app.ai.providers.registry import (
    Capability,
    get_provider_spec,
    is_known_model,
    require_provider_spec,
)
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_snapshot():
    store.invalidate()
    yield
    store.invalidate()


def _publish(snapshot: ConfigSnapshot) -> None:
    store._snapshot = snapshot  # noqa: SLF001


def _settings(**overrides) -> Settings:
    base = {
        "ai_provider": "bedrock",
        "bedrock_region": "us-west-2",
        "bedrock_model": "us.anthropic.claude-sonnet-4-20250514",
        "openai_api_key": "env-openai-key",
        "openai_model": "gpt-5",
    }
    base.update(overrides)
    return Settings(**base)


def _mock_converse_response(
    text: str = "Hello, world!",
    input_tokens: int = 10,
    output_tokens: int = 5,
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    """Build a realistic Bedrock Converse API response."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": text}],
            }
        },
        "stopReason": stop_reason,
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
        },
        "metrics": {"latencyMs": 250},
        "ResponseMetadata": {
            "HTTPStatusCode": 200,
        },
    }


def _client_error(code: str, message: str, status_code: int = 400) -> Exception:
    """Build a botocore ClientError with the given code and message."""
    from botocore.exceptions import ClientError

    return ClientError(
        error_response={
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        },
        operation_name="Converse",
    )


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestBedrockRegistry:
    def test_bedrock_spec_exists_in_registry(self):
        spec = get_provider_spec("bedrock")
        assert spec is not None
        assert spec.key == "bedrock"
        assert spec.label == "Amazon Bedrock"
        assert spec.implemented is True

    def test_bedrock_requires_no_api_key(self):
        spec = require_provider_spec("bedrock")
        assert spec.requires_api_key is False

    def test_bedrock_has_expected_capabilities(self):
        spec = require_provider_spec("bedrock")
        assert Capability.STRUCTURED_OUTPUT in spec.capabilities
        assert Capability.STREAMING in spec.capabilities
        assert Capability.REASONING in spec.capabilities

    def test_bedrock_has_models(self):
        spec = require_provider_spec("bedrock")
        model_ids = spec.model_ids()
        assert "us.anthropic.claude-sonnet-4-20250514" in model_ids
        assert "us.anthropic.claude-opus-4-20250514" in model_ids
        assert "us.anthropic.claude-haiku-3-5-20250620" in model_ids
        assert "us.amazon.nova-pro-v1:0" in model_ids
        assert "us.meta.llama4-maverick-17b-instruct-v1:0" in model_ids

    def test_bedrock_default_model(self):
        spec = require_provider_spec("bedrock")
        assert spec.resolve_default_model() == "us.anthropic.claude-sonnet-4-20250514"

    def test_bedrock_aliases_resolve(self):
        assert get_provider_spec("aws") is get_provider_spec("bedrock")
        assert get_provider_spec("aws_bedrock") is get_provider_spec("bedrock")
        assert get_provider_spec("amazon_bedrock") is get_provider_spec("bedrock")

    def test_is_known_model_for_bedrock(self):
        assert is_known_model("bedrock", "us.anthropic.claude-sonnet-4-20250514")
        assert not is_known_model("bedrock", "definitely-not-a-model")


# ---------------------------------------------------------------------------
# Provider initialization
# ---------------------------------------------------------------------------


class TestBedrockInitialization:
    def test_provider_creates_with_defaults(self):
        mock_client = MagicMock()
        provider = BedrockProvider(bedrock_client=mock_client)
        assert provider._model == "us.anthropic.claude-sonnet-4-20250514"
        assert provider._region == "us-east-1"
        assert provider._temperature == 0.2
        assert provider._max_tokens == 4096

    def test_provider_accepts_custom_config(self):
        mock_client = MagicMock()
        provider = BedrockProvider(
            model="us.amazon.nova-pro-v1:0",
            temperature=0.5,
            max_tokens=8192,
            region="eu-west-1",
            bedrock_client=mock_client,
        )
        assert provider._model == "us.amazon.nova-pro-v1:0"
        assert provider._region == "eu-west-1"
        assert provider._temperature == 0.5
        assert provider._max_tokens == 8192

    def test_lazy_client_initialization(self):
        """Client is not created until first use."""
        provider = BedrockProvider()
        assert provider._client is None

    @patch("app.ai.providers.bedrock_provider.boto3")
    def test_client_created_on_first_use(self, mock_boto3):
        """When no client is injected, boto3.client() is called."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        provider = BedrockProvider(region="us-west-2")
        result = provider._get_client()
        assert result is mock_client
        mock_boto3.client.assert_called_once()


# ---------------------------------------------------------------------------
# Successful inference
# ---------------------------------------------------------------------------


class TestBedrockInference:
    @pytest.mark.asyncio
    async def test_successful_completion(self):
        mock_client = MagicMock()
        mock_client.converse.return_value = _mock_converse_response(
            text="Test response",
            input_tokens=15,
            output_tokens=8,
        )

        provider = BedrockProvider(
            model="us.anthropic.claude-sonnet-4-20250514",
            bedrock_client=mock_client,
        )

        response = await provider.complete(
            system_prompt="You are helpful.",
            user_prompt="Say hello.",
        )

        assert isinstance(response, LLMResponse)
        assert response.text == "Test response"
        assert response.model_name == "us.anthropic.claude-sonnet-4-20250514"
        assert response.prompt_tokens == 15
        assert response.completion_tokens == 8
        assert response.total_tokens == 23
        assert response.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_converse_called_with_correct_params(self):
        mock_client = MagicMock()
        mock_client.converse.return_value = _mock_converse_response()

        provider = BedrockProvider(
            model="us.amazon.nova-pro-v1:0",
            temperature=0.7,
            max_tokens=2048,
            bedrock_client=mock_client,
        )

        await provider.complete(
            system_prompt="System instructions.",
            user_prompt="User message.",
        )

        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["modelId"] == "us.amazon.nova-pro-v1:0"
        assert call_kwargs["system"] == [{"text": "System instructions."}]
        assert call_kwargs["messages"] == [
            {"role": "user", "content": [{"text": "User message."}]}
        ]
        assert call_kwargs["inferenceConfig"]["temperature"] == 0.7
        assert call_kwargs["inferenceConfig"]["maxTokens"] == 2048

    @pytest.mark.asyncio
    async def test_multi_block_response(self):
        """Multiple text blocks are joined."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "First part."},
                        {"text": "Second part."},
                    ],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 10, "totalTokens": 15},
        }

        provider = BedrockProvider(bedrock_client=mock_client)
        response = await provider.complete(
            system_prompt="sys", user_prompt="usr"
        )
        assert response.text == "First part.\nSecond part."

    @pytest.mark.asyncio
    async def test_response_format_option_accepted(self):
        """Options are accepted without error (JSON format hint is for API symmetry)."""
        mock_client = MagicMock()
        mock_client.converse.return_value = _mock_converse_response(text='{"ok": true}')

        provider = BedrockProvider(bedrock_client=mock_client)
        response = await provider.complete(
            system_prompt="Reply JSON.",
            user_prompt="ping",
            options=LLMRequestOptions(response_format=ResponseFormat.JSON),
        )
        assert response.text == '{"ok": true}'


# ---------------------------------------------------------------------------
# Finish reason normalization
# ---------------------------------------------------------------------------


class TestFinishReasonNormalization:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bedrock_reason,expected",
        [
            ("end_turn", "stop"),
            ("stop_sequence", "stop"),
            ("max_tokens", "length"),
            ("content_filtered", "content_filter"),
            ("tool_use", "tool_calls"),
        ],
    )
    async def test_finish_reasons_normalized(self, bedrock_reason, expected):
        mock_client = MagicMock()
        mock_client.converse.return_value = _mock_converse_response(stop_reason=bedrock_reason)

        provider = BedrockProvider(bedrock_client=mock_client)
        response = await provider.complete(system_prompt="s", user_prompt="u")
        assert response.finish_reason == expected


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    @pytest.mark.asyncio
    async def test_usage_extracted_correctly(self):
        mock_client = MagicMock()
        mock_client.converse.return_value = _mock_converse_response(
            input_tokens=100, output_tokens=50
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        response = await provider.complete(system_prompt="s", user_prompt="u")
        assert response.prompt_tokens == 100
        assert response.completion_tokens == 50
        assert response.total_tokens == 150

    @pytest.mark.asyncio
    async def test_missing_total_tokens_computed(self):
        """When totalTokens is absent, it should be computed from input+output."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [{"text": "hi"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 20, "outputTokens": 10},
        }

        provider = BedrockProvider(bedrock_client=mock_client)
        response = await provider.complete(system_prompt="s", user_prompt="u")
        assert response.total_tokens == 30


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestBedrockErrors:
    @pytest.mark.asyncio
    async def test_access_denied_raises_auth_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "AccessDeniedException",
            "User is not authorized to perform bedrock:InvokeModel",
            403,
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderAuthError) as exc_info:
            await provider.complete(system_prompt="s", user_prompt="u")
        assert "not authorized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_expired_token_raises_auth_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "ExpiredTokenException", "Token has expired", 401
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderAuthError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_invalid_signature_raises_auth_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "InvalidSignatureException", "Signature expired", 403
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderAuthError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_throttling_raises_rate_limit_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "ThrottlingException", "Rate exceeded", 429
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderRateLimitError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_too_many_requests_raises_rate_limit_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "TooManyRequestsException", "Too many requests", 429
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderRateLimitError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_model_not_found_raises_response_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "ResourceNotFoundException",
            "Could not resolve the foundation model",
            404,
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderResponseError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_validation_exception_raises_response_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "ValidationException", "Malformed input", 400
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderResponseError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_model_timeout_raises_timeout_error(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "ModelTimeoutException", "Model timed out", 408
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderTimeoutError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_read_timeout_raises_timeout_error(self):
        from botocore.exceptions import ReadTimeoutError

        mock_client = MagicMock()
        mock_client.converse.side_effect = ReadTimeoutError(endpoint_url="https://bedrock.us-east-1.amazonaws.com")

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderTimeoutError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_endpoint_connection_error_raises_generic(self):
        from botocore.exceptions import EndpointConnectionError

        mock_client = MagicMock()
        mock_client.converse.side_effect = EndpointConnectionError(endpoint_url="https://bedrock.bad-region.amazonaws.com")

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_service_unavailable_raises_generic_503(self):
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "ServiceUnavailableException", "Service is temporarily unavailable", 503
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderError) as exc_info:
            await provider.complete(system_prompt="s", user_prompt="u")
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_malformed_response_raises_response_error(self):
        """A response missing expected keys raises AIProviderResponseError."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {"output": {"message": {"content": []}}}

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderResponseError):
            await provider.complete(system_prompt="s", user_prompt="u")

    @pytest.mark.asyncio
    async def test_error_metadata_attached(self):
        """Provider errors carry structured metadata for diagnostics."""
        mock_client = MagicMock()
        mock_client.converse.side_effect = _client_error(
            "ThrottlingException", "Rate exceeded", 429
        )

        provider = BedrockProvider(bedrock_client=mock_client)
        with pytest.raises(AIProviderRateLimitError) as exc_info:
            await provider.complete(system_prompt="s", user_prompt="u")

        meta = exc_info.value.provider_error
        assert meta is not None
        assert meta["provider"] == "bedrock"
        assert meta["error_code"] == "ThrottlingException"
        assert meta["status_code"] == 429


# ---------------------------------------------------------------------------
# Provider switching
# ---------------------------------------------------------------------------


class TestProviderSwitching:
    def test_resolve_bedrock_from_environment(self):
        resolved = resolve(settings=_settings())
        assert resolved.key == "bedrock"
        assert resolved.model == "us.anthropic.claude-sonnet-4-20250514"
        assert resolved.source == "environment"

    def test_resolve_bedrock_region_flows_to_provider_options(self):
        resolved = resolve(settings=_settings(bedrock_region="eu-west-1"))
        assert resolved.config.provider_options["region"] == "eu-west-1"

    def test_region_is_not_in_api_key(self):
        """Region must never leak into the api_key field."""
        resolved = resolve(settings=_settings(bedrock_region="us-west-2"))
        assert resolved.config.api_key is None

    def test_region_is_not_in_base_url(self):
        """Region must never be carried in base_url."""
        resolved = resolve(settings=_settings(bedrock_region="us-west-2"))
        assert resolved.config.base_url is None

    def test_stored_bedrock_config_overrides_env(self):
        _publish(
            ConfigSnapshot(
                providers={
                    "bedrock": ProviderRecord(
                        "bedrock", None, "us.amazon.nova-pro-v1:0",
                        "us-west-2", None, None, True, "ready"
                    )
                },
                default_provider="bedrock",
                loaded=True,
            )
        )
        resolved = resolve(settings=_settings())
        assert resolved.key == "bedrock"
        assert resolved.model == "us.amazon.nova-pro-v1:0"
        assert resolved.config.provider_options["region"] == "us-west-2"

    def test_switch_from_bedrock_to_openai(self):
        """Explicit provider override switches away from default bedrock."""
        resolved = resolve(provider="openai", settings=_settings())
        assert resolved.key == "openai"
        assert resolved.model == "gpt-5"

    def test_factory_builds_bedrock_provider(self):
        provider = create_llm_provider(settings=_settings(), provider="bedrock")
        assert isinstance(provider, BedrockProvider)

    def test_factory_builds_bedrock_with_stored_config(self):
        _publish(
            ConfigSnapshot(
                providers={
                    "bedrock": ProviderRecord(
                        "bedrock", None, "us.anthropic.claude-haiku-3-5-20250620",
                        "ap-southeast-1", None, None, True, "ready"
                    )
                },
                default_provider="bedrock",
                loaded=True,
            )
        )
        provider = create_llm_provider(settings=_settings())
        assert isinstance(provider, BedrockProvider)
        assert provider._model == "us.anthropic.claude-haiku-3-5-20250620"
        assert provider._region == "ap-southeast-1"

    def test_bedrock_initializes_boto3_with_region_name(self):
        """The boto3 client must be initialized with region_name, not a URL."""
        from unittest.mock import patch

        _publish(
            ConfigSnapshot(
                providers={
                    "bedrock": ProviderRecord(
                        "bedrock", None, "us.anthropic.claude-sonnet-4-20250514",
                        "eu-central-1", None, None, True, "ready"
                    )
                },
                default_provider="bedrock",
                loaded=True,
            )
        )
        provider = create_llm_provider(settings=_settings())
        assert isinstance(provider, BedrockProvider)

        with patch("app.ai.providers.bedrock_provider.boto3") as mock_boto3:
            mock_boto3.client.return_value = MagicMock()
            provider._get_client()
            call_kwargs = mock_boto3.client.call_args
            assert call_kwargs[0][0] == "bedrock-runtime"
            assert call_kwargs[1]["region_name"] == "eu-central-1"

    def test_openai_provider_options_are_empty(self):
        """Non-Bedrock providers get empty provider_options — no pollution."""
        resolved = resolve(provider="openai", settings=_settings())
        assert resolved.config.provider_options == {}
