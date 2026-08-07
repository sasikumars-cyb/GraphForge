"""Unit tests for app.ai.providers.pricing — cost estimation from token counts."""

from __future__ import annotations

from app.ai.providers.pricing import estimate_cost_usd


def test_deepseek_v4_flash_cost_estimate() -> None:
    estimate = estimate_cost_usd(
        "deepseek-v4-flash", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert estimate is not None
    assert estimate.prompt_cost_usd == 0.27
    assert estimate.completion_cost_usd == 1.10
    assert estimate.total_usd == 0.27 + 1.10


def test_deepseek_v4_pro_cost_estimate() -> None:
    estimate = estimate_cost_usd(
        "deepseek-v4-pro", prompt_tokens=500_000, completion_tokens=200_000
    )
    assert estimate is not None
    assert estimate.prompt_cost_usd == 0.5 * 0.55
    assert estimate.completion_cost_usd == 0.2 * 2.19


def test_unknown_model_returns_none() -> None:
    assert estimate_cost_usd("deepseek-not-a-real-model", 100, 100) is None


def test_missing_token_counts_return_none() -> None:
    assert estimate_cost_usd("deepseek-v4-flash", None, 100) is None
    assert estimate_cost_usd("deepseek-v4-flash", 100, None) is None
