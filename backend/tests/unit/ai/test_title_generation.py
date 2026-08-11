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
        "app.agents.title_generation.StageAwareLLMProvider",
        return_value=_mock_provider('"Refactor Authentication Module"'),
    ):
        title = await generate_title("Please refactor the auth module to use JWT.")

    assert title == "Refactor Authentication Module"


@pytest.mark.asyncio
async def test_generate_title_passes_model_through_to_factory() -> None:
    mock_factory = MagicMock(return_value=_mock_provider("A Title"))
    with patch("app.agents.title_generation.StageAwareLLMProvider", mock_factory):
        await generate_title("Do the thing.", model="gpt-5")

    mock_factory.assert_called_once_with(stage=None, model="gpt-5")


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_provider_error() -> None:
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        side_effect=AIProviderRateLimitError("Rate limited."),
    ):
        title = await generate_title("Add caching to the search service.")

    assert title == "Add caching to the search service."


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_not_configured_error() -> None:
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        side_effect=AppError("OPENAI_API_KEY is not configured."),
    ):
        title = await generate_title("Short objective.")

    assert title == "Short objective."


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_empty_provider_response() -> None:
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
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
        "app.agents.title_generation.StageAwareLLMProvider",
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
        "app.agents.title_generation.StageAwareLLMProvider",
        side_effect=AppError("Not configured."),
    ):
        title = await generate_title("Line one\n\n  Line two\twith tabs")

    assert "\n" not in title
    assert "\t" not in title
    assert title == "Line one Line two with tabs"


@pytest.mark.asyncio
async def test_generate_title_fallback_never_empty_for_empty_objective() -> None:
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        side_effect=AppError("Not configured."),
    ):
        title = await generate_title("   ")

    assert title == "Untitled"


@pytest.mark.asyncio
async def test_generate_title_falls_back_when_provider_returns_a_refusal_sentence() -> None:
    """Regression: given a bare Jira URL as the objective, the model has no
    tools of its own to resolve it (only the Planning Agent that runs
    afterward actually fetches ticket content) and can respond with a full
    refusal sentence instead of a title. That response is non-empty, so it
    used to pass straight through and become the workflow's literal title —
    e.g. "I don't have access to external URLs or Jira tickets. Please
    share the ticket details..." shown as the workflow's title in the UI."""
    objective = "Prepare plan for https://uplightinc.atlassian.net/browse/PROT-5723"
    refusal = (
        "I don't have access to external URLs or Jira tickets. Please share "
        "the ticket details or description, and I'll generate an appropriate "
        "title for you."
    )
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        return_value=_mock_provider(refusal),
    ):
        title = await generate_title(objective)

    assert title == "Prepare plan for…"  # word-boundary-truncated fallback
    assert "I don't have access" not in title


@pytest.mark.asyncio
async def test_generate_title_falls_back_when_provider_returns_multiple_lines() -> None:
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        return_value=_mock_provider("A Title\nWith extra commentary on a second line."),
    ):
        title = await generate_title("Add caching to the search service.")

    assert title == "Add caching to the search service."


# ---------------------------------------------------------------------------
# UX audit P1.5 — misleading run titles.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_repository_goal_never_calls_the_llm() -> None:
    """Regression: Repository Understanding — read-only, no code changes —
    was titled "Refactor Payment Service Java Module" because the LLM's own
    example titles are all action-verb phrasings. Read-only, repository-
    scoped goals must never reach the LLM (and therefore can never produce
    an action-verb title) at all."""
    mock_factory = MagicMock(side_effect=AssertionError("must not call the LLM"))
    with patch("app.agents.title_generation.StageAwareLLMProvider", mock_factory):
        title = await generate_title(
            "sasikumars-cyb/payment-service-java",
            goal="analyze_repository_understanding",
        )

    mock_factory.assert_not_called()
    assert title == "Repository Understanding — Payment Service Java"
    # No action verb ("Refactor", "Fix", "Add", ...) was ever generated.
    assert "Refactor" not in title


@pytest.mark.parametrize(
    ("goal", "objective", "expected"),
    [
        (
            "analyze_documentation_health",
            "org/order-service-python",
            "Documentation Health — Order Service Python",
        ),
        (
            "analyze_api_intelligence",
            "org/order-service-python",
            "API Intelligence — Order Service Python",
        ),
        (
            "analyze_repository_understanding",
            "org/payment-service-java",
            "Repository Understanding — Payment Service Java",
        ),
    ],
)
@pytest.mark.asyncio
async def test_read_only_titles_are_distinguishable_per_agent(
    goal: str, objective: str, expected: str
) -> None:
    """Regression: Documentation Health and API Intelligence run against the
    same repository used to produce indistinguishable titles in Run
    History — neither said which agent produced it."""
    title = await generate_title(objective, goal=goal)
    assert title == expected


@pytest.mark.asyncio
async def test_llm_generated_title_is_prefixed_with_its_agent_label() -> None:
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        return_value=_mock_provider("Add Idempotency Key Handling"),
    ):
        title = await generate_title(
            "Add an idempotency key to the payment endpoint.", goal="plan_freeform"
        )

    assert title == "Planning — Add Idempotency Key Handling"


@pytest.mark.asyncio
async def test_fallback_title_is_also_prefixed_with_its_agent_label() -> None:
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        side_effect=AppError("Not configured."),
    ):
        title = await generate_title("Add retry support.", goal="develop_change_plan")

    assert title == "Development — Add retry support."


@pytest.mark.asyncio
async def test_unknown_goal_is_not_prefixed_and_keeps_prior_behavior() -> None:
    """Every existing caller that omits `goal` (or passes one this module
    doesn't recognize) must see byte-identical behavior to before this
    fix — no accidental prefix on workflow titles, which cover a whole
    multi-stage objective rather than one agent's goal."""
    with patch(
        "app.agents.title_generation.StageAwareLLMProvider",
        return_value=_mock_provider("A Title"),
    ):
        title = await generate_title("Do the thing.", goal="some_future_goal")

    assert title == "A Title"
