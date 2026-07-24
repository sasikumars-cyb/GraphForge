"""Unit tests for app.agents.title_generation.generate_title()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.title_generation import generate_title
from app.ai.providers.base import LLMResponse
from app.ai.providers.errors import AIProviderRateLimitError
from app.core.exceptions import AppError


def _mock_provider(text: str) -> MagicMock:
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=LLMResponse(text=text))
    return provider


@pytest.mark.asyncio
async def test_generate_title_returns_provider_text_stripped_of_quotes() -> None:
    with patch(
        "app.agents.title_generation.create_llm_provider",
        return_value=_mock_provider('"Refactor Authentication Module"'),
    ):
        title = await generate_title("Please refactor the auth module to use JWT.")

    assert title == "Refactor Authentication Module"


@pytest.mark.asyncio
async def test_generate_title_passes_model_through_to_factory() -> None:
    mock_factory = MagicMock(return_value=_mock_provider("A Title"))
    with patch("app.agents.title_generation.create_llm_provider", mock_factory):
        await generate_title("Do the thing.", model="gpt-5")

    mock_factory.assert_called_once_with(model="gpt-5")


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_provider_error() -> None:
    with patch(
        "app.agents.title_generation.create_llm_provider",
        side_effect=AIProviderRateLimitError("Rate limited."),
    ):
        title = await generate_title("Add caching to the search service.")

    assert title == "Add caching to the search service."


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_not_configured_error() -> None:
    with patch(
        "app.agents.title_generation.create_llm_provider",
        side_effect=AppError("OPENAI_API_KEY is not configured."),
    ):
        title = await generate_title("Short objective.")

    assert title == "Short objective."


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_empty_provider_response() -> None:
    with patch(
        "app.agents.title_generation.create_llm_provider",
        return_value=_mock_provider("   "),
    ):
        title = await generate_title("Investigate the CI pipeline failure.")

    assert title == "Investigate the CI pipeline failure."


@pytest.mark.asyncio
async def test_generate_title_fallback_truncates_long_objective_at_word_boundary() -> None:
    objective = (
        "Project: etl-customer-orders. We receive daily customer and order CSV "
        "files and need a new report calculating the Top 10 customers by total "
        "purchase amount for each month, ignoring cancelled and refunded orders."
    )
    with patch(
        "app.agents.title_generation.create_llm_provider",
        side_effect=AppError("Not configured."),
    ):
        title = await generate_title(objective)

    assert len(title) <= 61  # 60 chars + the ellipsis character
    assert title.endswith("…")
    assert not objective.startswith(title[:-1].split(" ")[-1] + "x")  # sanity: not mid-word garbage
    # Every "word" in the fallback must be a real, complete prefix word.
    for word in title.rstrip("…").split(" "):
        assert word in objective


@pytest.mark.asyncio
async def test_generate_title_fallback_collapses_whitespace() -> None:
    with patch(
        "app.agents.title_generation.create_llm_provider",
        side_effect=AppError("Not configured."),
    ):
        title = await generate_title("Line one\n\n  Line two\twith tabs")

    assert "\n" not in title
    assert "\t" not in title
    assert title == "Line one Line two with tabs"


@pytest.mark.asyncio
async def test_generate_title_fallback_never_empty_for_empty_objective() -> None:
    with patch(
        "app.agents.title_generation.create_llm_provider",
        side_effect=AppError("Not configured."),
    ):
        title = await generate_title("   ")

    assert title == "Untitled"
