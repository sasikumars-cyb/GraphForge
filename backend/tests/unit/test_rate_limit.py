"""Tests for app.core.rate_limit — the sliding-window limiter and its
key-eviction/memory-bounding behavior."""

from __future__ import annotations

import pytest

from app.core import rate_limit
from app.core.exceptions import RateLimitedError


@pytest.fixture(autouse=True)
def _clean_state():
    """Every test gets a fresh `_hits` dict — this module is a shared,
    module-level singleton, so tests would otherwise interfere with each
    other's request counts."""
    rate_limit._hits.clear()
    yield
    rate_limit._hits.clear()


def test_allows_requests_under_the_limit():
    for _ in range(3):
        rate_limit.check_rate_limit("k", max_requests=3, window_seconds=60.0)


def test_raises_once_limit_exceeded():
    for _ in range(3):
        rate_limit.check_rate_limit("k", max_requests=3, window_seconds=60.0)
    with pytest.raises(RateLimitedError):
        rate_limit.check_rate_limit("k", max_requests=3, window_seconds=60.0)


def test_independent_keys_have_independent_budgets():
    for _ in range(3):
        rate_limit.check_rate_limit("a", max_requests=3, window_seconds=60.0)
    # "b" has never been hit — must not be affected by "a" being exhausted.
    rate_limit.check_rate_limit("b", max_requests=3, window_seconds=60.0)


def test_old_hits_expire_out_of_the_window(monkeypatch: pytest.MonkeyPatch):
    fake_now = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: fake_now[0])

    for _ in range(3):
        rate_limit.check_rate_limit("k", max_requests=3, window_seconds=10.0)
    with pytest.raises(RateLimitedError):
        rate_limit.check_rate_limit("k", max_requests=3, window_seconds=10.0)

    fake_now[0] += 11.0  # past the 10s window
    rate_limit.check_rate_limit("k", max_requests=3, window_seconds=10.0)


def test_key_with_fully_expired_hits_is_removed_not_left_empty(monkeypatch: pytest.MonkeyPatch):
    """Regression test: `_hits` used to be a defaultdict that never removed
    a key once its deque emptied out — a slow, unbounded memory leak over a
    long-running process with many distinct keys (worse once a key can be
    attacker-controlled, e.g. a per-email login limiter)."""
    fake_now = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: fake_now[0])

    rate_limit.check_rate_limit("k", max_requests=5, window_seconds=10.0)
    assert "k" in rate_limit._hits

    fake_now[0] += 11.0
    # Re-querying "k" itself is what triggers its own per-key eviction (the
    # dict has no way to know "k"'s window lapsed until something asks
    # about "k" again) — a fresh hit, so it ends up back in `_hits`, but
    # never as a stale *empty* deque left over from the expired one.
    rate_limit.check_rate_limit("k", max_requests=5, window_seconds=10.0)
    assert list(rate_limit._hits["k"]) == [fake_now[0]]


def test_sweep_bounds_total_dict_size(monkeypatch: pytest.MonkeyPatch):
    """Regression test for the same leak as above, exercised via the
    periodic sweep rather than per-key lazy cleanup: once `_hits` grows
    past the sweep threshold, keys whose window has fully lapsed are
    dropped even though nothing queried them individually."""
    fake_now = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: fake_now[0])
    monkeypatch.setattr(rate_limit, "_SWEEP_THRESHOLD", 5)

    for i in range(5):
        rate_limit.check_rate_limit(f"stale-{i}", max_requests=100, window_seconds=1.0)

    fake_now[0] += 3700.0  # past _sweep_stale_keys' 1-hour horizon
    # This call pushes len(_hits) past the (patched) threshold, triggering
    # the sweep — every "stale-*" key should be gone afterward.
    rate_limit.check_rate_limit("fresh", max_requests=100, window_seconds=1.0)

    assert not any(k.startswith("stale-") for k in rate_limit._hits)
    assert "fresh" in rate_limit._hits
