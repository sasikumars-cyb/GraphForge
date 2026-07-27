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
from collections import defaultdict, deque

from app.core.exceptions import RateLimitedError

# user_id -> deque of monotonic() timestamps of recent requests, per limiter key
_hits: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(key: str, *, max_requests: int, window_seconds: float) -> None:
    """Raise RateLimitedError if `key` has exceeded max_requests within the
    trailing window_seconds. Records this call as a hit otherwise.

    `key` is caller-composed (e.g. f"workflow_create:{user_id}") so different
    endpoints keep independent budgets for the same user.
    """
    now = time.monotonic()
    hits = _hits[key]
    while hits and now - hits[0] > window_seconds:
        hits.popleft()
    if len(hits) >= max_requests:
        retry_after = max(0, window_seconds - (now - hits[0]))
        raise RateLimitedError(
            f"Too many requests. Try again in {retry_after:.0f}s.",
        )
    hits.append(now)
