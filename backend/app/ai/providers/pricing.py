"""Rough per-call cost estimation from token counts.

Deliberately not a billing ledger (see AIProviderUsage's own docstring for
that same disclaimer at the aggregate level) — vendor list pricing changes
without notice and this table is not kept in sync with it automatically.
It exists so a workflow's Log tab can answer "roughly how much did this
cost" instead of nothing at all, which is the actual gap this closes: today
LLMTrace records latency but nothing about spend, so a quota exhaustion
(see the provider-fallback feature) is the first anyone learns a run was
expensive, never a heads-up beforehand.

Prices are USD per 1M tokens, input/output, as of the models this app ships
configured for. Update when a configured model's list price changes;
returns None for anything not in the table rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

# (input $/1M tokens, output $/1M tokens)
_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gemini-3.6-flash": (0.075, 0.30),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    # Bedrock global-endpoint list price; cross-region ("us."-prefixed)
    # inference profiles may carry a premium over this (~10% per public
    # reporting as of when this was added) — not itself modeled here, so
    # this is a slight underestimate for cross-region calls, not an
    # overestimate.
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (1.00, 5.00),
}


@dataclass(frozen=True)
class CostEstimate:
    prompt_cost_usd: float
    completion_cost_usd: float

    @property
    def total_usd(self) -> float:
        return self.prompt_cost_usd + self.completion_cost_usd


def estimate_cost_usd(
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> CostEstimate | None:
    """Return a rough cost estimate, or None if `model` isn't in the
    pricing table or either token count is unknown (a provider that
    doesn't report usage — see each provider's `complete()` — leaves
    LLMResponse's token fields None rather than a fabricated guess)."""
    if prompt_tokens is None or completion_tokens is None:
        return None
    rates = _PRICING_PER_1M.get(model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    return CostEstimate(
        prompt_cost_usd=(prompt_tokens / 1_000_000) * input_rate,
        completion_cost_usd=(completion_tokens / 1_000_000) * output_rate,
    )
