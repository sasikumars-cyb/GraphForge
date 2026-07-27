"""Per-user request rate limiting for expensive (LLM-triggering) endpoints.

Mitigates OWASP LLM10 (Unbounded Consumption): without this, an authenticated
user can fire workflow-create/continue requests as fast as the client can
send them, each one triggering a real (and, for paid providers, billed) LLM
call — exactly the failure mode a rate-limit-exhaustion error (see the
Gemini 429 hit during manual testing) is a symptom of, not the cause of.

In-memory sliding window, one process. Deliberately not Redis-backed: this
app runs a single backend replica (see docker-compose), and the roadmap's
Redis-backed RunContext hasn't landed yet either — adding a Redis dependency
just for rate limiting ahead of that would be scope creep. Revisit when the
backend is ever run with >1 replica.
"""

from __future__ import annotations

import time
from collections import deque

from app.core.exceptions import RateLimitedError

# limiter key -> deque of monotonic() timestamps of recent requests.
_hits: dict[str, deque[float]] = {}

# Bounds total memory instead of letting `_hits` grow forever. A key whose
# window has fully lapsed and is never queried again would otherwise sit in
# this dict indefinitely — slow for ordinary per-user keys, but a real
# memory-exhaustion vector for any limiter keyed on attacker-controlled
# input from an unauthenticated endpoint (e.g. login rate-limited by the
# submitted email — see auth.py's login). Sweeping only once this many
# distinct keys have accumulated keeps the common case cheap (no per-call
# scan) while still bounding worst-case size.
_SWEEP_THRESHOLD = 10_000


def check_rate_limit(key: str, *, max_requests: int, window_seconds: float) -> None:
    """Raise RateLimitedError if `key` has exceeded max_requests within the
    trailing window_seconds. Records this call as a hit otherwise.

    `key` is caller-composed (e.g. f"workflow_create:{user_id}") so different
    endpoints keep independent budgets for the same user.
    """
    now = time.monotonic()

    hits = _hits.get(key)
    if hits is not None:
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if not hits:
            # Every hit aged out — don't keep an empty deque under this key
            # forever just because nothing has evicted it yet.
            del _hits[key]
            hits = None

    if hits is not None and len(hits) >= max_requests:
        retry_after = max(0, window_seconds - (now - hits[0]))
        raise RateLimitedError(
            f"Too many requests. Try again in {retry_after:.0f}s.",
        )

    if hits is None:
        hits = deque()
        _hits[key] = hits
    hits.append(now)

    if len(_hits) > _SWEEP_THRESHOLD:
        _sweep_stale_keys(now)


def _sweep_stale_keys(now: float) -> None:
    """Drop every key whose most recent hit is old enough that no caller's
    `window_seconds` could still consider it active.

    Different call sites use different windows, and a key's own window
    isn't recorded anywhere — so this uses a generous fixed horizon (1
    hour) rather than trying to reconstruct the original window. Safe
    either way: a key that's genuinely swept too early just starts a fresh
    window on its very next hit, identical to a first-time key.
    """
    stale_horizon_seconds = 3600.0
    stale_keys = [k for k, v in _hits.items() if not v or now - v[-1] > stale_horizon_seconds]
    for k in stale_keys:
        del _hits[k]
