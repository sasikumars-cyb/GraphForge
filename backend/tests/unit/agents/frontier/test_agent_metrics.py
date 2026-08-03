"""Unit tests for `AgentMetrics` — pure timing/counting, no I/O."""

from __future__ import annotations

import time

from app.agents.frontier.agent_metrics import AgentMetrics


def test_stop_run_without_start_leaves_duration_unset() -> None:
    metrics = AgentMetrics()
    metrics.stop_run()
    assert metrics.total_duration_ms is None


def test_run_duration_is_measured() -> None:
    metrics = AgentMetrics()
    metrics.start_run()
    time.sleep(0.01)
    metrics.stop_run()
    assert metrics.total_duration_ms is not None
    assert metrics.total_duration_ms >= 10


def test_service_and_llm_latency_are_independent() -> None:
    metrics = AgentMetrics()
    metrics.start_service_call()
    time.sleep(0.01)
    metrics.stop_service_call()
    metrics.start_llm_call()
    time.sleep(0.01)
    metrics.stop_llm_call()

    assert metrics.service_latency_ms is not None
    assert metrics.llm_latency_ms is not None
    assert metrics.service_latency_ms >= 10
    assert metrics.llm_latency_ms >= 10


def test_record_confidence_states_tallies_by_state() -> None:
    metrics = AgentMetrics()
    metrics.record_confidence_states(["likely", "likely", "verified"])
    metrics.record_confidence_states(["likely"])

    assert metrics.confidence_distribution == {"likely": 3, "verified": 1}


def test_to_dict_reflects_current_state() -> None:
    metrics = AgentMetrics()
    metrics.token_usage = 42
    metrics.cache_hits = 2
    metrics.cache_misses = 1
    metrics.record_confidence_states(["verified"])

    as_dict = metrics.to_dict()
    assert as_dict["token_usage"] == 42
    assert as_dict["cache_hits"] == 2
    assert as_dict["cache_misses"] == 1
    assert as_dict["confidence_distribution"] == {"verified": 1}
    assert "_run_started_at" not in as_dict
