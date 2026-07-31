"""Provider fallback for recoverable failures.

Disabled by default. When an operator enables it and sets an order, a
completion that fails *recoverably* on the primary provider is retried on the
next provider in the chain.

Recoverable vs not
------------------
Only transient conditions fall through:

  recoverable   rate limit, timeout, upstream 5xx
  not           auth failure, malformed request, unparseable response

Retrying an auth failure just burns another provider's quota to fail the same
way; retrying a malformed-request error repeats a deterministic bug. Both stay
fatal on purpose.

This wraps `ILLMProvider.complete()` rather than replacing it, so providers
and agents are untouched.
"""

from __future__ import annotations

import logging
import time

from app.ai.config.resolver import (
    ResolvedProvider,
    fallback_chain,
    profile_fallback_chain,
    resolve,
)
from app.ai.providers.base import LLMRequestOptions, LLMResponse
from app.ai.providers.errors import (
    AIProviderAuthError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)

logger = logging.getLogger(__name__)


def is_recoverable(exc: Exception) -> bool:
    """Whether another provider is worth trying for this failure.

    Non-recoverable types are matched *first and explicitly*. The base
    `AIProviderError` defaults to status 502, so a trailing "5xx is
    recoverable" heuristic would otherwise sweep in every subclass that does
    not override status_code — including malformed-response errors, which are
    deterministic and would just fail again downstream.
    """
    if isinstance(exc, (AIProviderAuthError, AIProviderResponseError)):
        return False
    if isinstance(exc, (AIProviderRateLimitError, AIProviderTimeoutError)):
        return True
    if isinstance(exc, AIProviderError):
        status = getattr(exc, "status_code", None)
        return status is not None and status >= 500
    return False


async def complete_with_fallback(
    *,
    system_prompt: str,
    user_prompt: str,
    options: LLMRequestOptions | None = None,
    provider: str | None = None,
    model: str | None = None,
    stage: str | None = None,
    profile: str | None = None,
    attempts_out: list[int] | None = None,
) -> tuple[LLMResponse, ResolvedProvider]:
    """Run a completion, falling back only on recoverable failures.

    Returns the response together with the provider that actually served it,
    so callers can record which vendor produced a result.

    `attempts_out`, when given, has the number of *failed* attempts that
    preceded the successful one appended to it (0 when the primary provider
    answered) — the count this loop already computes as `index` but
    previously discarded. An out-param rather than a third return value so
    the existing two-tuple contract, which callers unpack positionally,
    is unchanged.
    """
    primary = resolve(provider=provider, model=model, stage=stage, profile=profile)
    attempts: list[ResolvedProvider] = [primary]

    if primary.profile_slug:
        # Profile-defined fallback: each link carries its own model and
        # parameters, so the fallback is a fully-specified behaviour rather
        # than "the same request pointed at another vendor".
        for slug in profile_fallback_chain(primary.profile_slug):
            try:
                attempts.append(resolve(profile=slug, stage=stage))
            except Exception:
                logger.warning("ai_fallback_unresolvable profile=%s", slug)
    else:
        for key in fallback_chain(primary.key):
            try:
                # Model is deliberately not carried across providers — it is
                # vendor-specific. Each fallback resolves its own default.
                attempts.append(resolve(provider=key, stage=stage))
            except Exception:
                logger.warning("ai_fallback_unresolvable provider=%s", key)

    last_error: Exception | None = None
    for index, attempt in enumerate(attempts):
        started = time.monotonic()
        try:
            llm = attempt.spec.build(attempt.config)
            response = await llm.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                options=options,
            )
            if index > 0:
                logger.info(
                    "ai_fallback_succeeded provider=%s model=%s after_failures=%d",
                    attempt.key,
                    attempt.model,
                    index,
                )
            if attempts_out is not None:
                attempts_out.append(index)
            return response, attempt
        except Exception as exc:  # noqa: BLE001 — re-raised below
            last_error = exc
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if not is_recoverable(exc) or index == len(attempts) - 1:
                logger.warning(
                    "ai_request_failed provider=%s model=%s recoverable=%s elapsed_ms=%d",
                    attempt.key,
                    attempt.model,
                    is_recoverable(exc),
                    elapsed_ms,
                )
                raise
            logger.warning(
                "ai_request_recoverable_failure provider=%s model=%s next=%s elapsed_ms=%d",
                attempt.key,
                attempt.model,
                attempts[index + 1].key,
                elapsed_ms,
            )

    # Unreachable: the loop either returns or raises.
    raise last_error if last_error else RuntimeError("No provider attempted.")
