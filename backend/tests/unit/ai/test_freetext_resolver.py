"""Unit tests for the FreeText Entry Resolver (PW-5)."""

from __future__ import annotations

import pytest

from app.context.resolvers.freetext import resolve


def test_resolve_returns_freetext_subject_type() -> None:
    s = resolve("Plan a new payment retry feature")
    assert s.subject_type == "freetext"


def test_resolve_subject_id_starts_with_freetext() -> None:
    s = resolve("What services depend on order-service?")
    assert s.subject_id.startswith("freetext:")


def test_resolve_display_name_matches_input() -> None:
    text = "Plan a new Kafka consumer for order events"
    s = resolve(text)
    assert s.display_name == text


def test_resolve_is_deterministic() -> None:
    text = "Same input always gives same id"
    assert resolve(text).subject_id == resolve(text).subject_id


def test_resolve_different_inputs_give_different_ids() -> None:
    s1 = resolve("Plan feature A")
    s2 = resolve("Plan feature B")
    assert s1.subject_id != s2.subject_id


def test_resolve_strips_whitespace() -> None:
    s = resolve("  Hello  ")
    assert s.display_name == "Hello"


def test_resolve_empty_raises() -> None:
    with pytest.raises(ValueError):
        resolve("")


def test_resolve_whitespace_only_raises() -> None:
    with pytest.raises(ValueError):
        resolve("   ")


def test_resolve_long_input_truncated_to_256() -> None:
    long_text = "a" * 300
    s = resolve(long_text)
    assert len(s.display_name) == 256


def test_resolve_graph_node_ids_empty() -> None:
    s = resolve("Any input")
    assert s.graph_node_ids == []
