"""Unit tests for `app.database.session`'s JSON persistence-boundary
backstop — see `_json_default`'s own docstring for why this exists
alongside (not instead of) fixing the actual bug at its source."""

from __future__ import annotations

import json

from app.database.session import _json_default, _json_serializer


class TestJsonDefault:
    def test_set_becomes_a_sorted_list(self) -> None:
        assert _json_default({"b", "a", "c"}) == ["a", "b", "c"]

    def test_frozenset_becomes_a_sorted_list(self) -> None:
        assert _json_default(frozenset({"z", "y"})) == ["y", "z"]

    def test_set_of_unorderable_items_falls_back_to_an_unsorted_list(self) -> None:
        # A set of mixed types can't be sorted (`TypeError` on comparison)
        # — must still degrade to *some* JSON-safe list, never raise.
        result = _json_default({1, "a"})
        assert isinstance(result, list)
        assert set(result) == {1, "a"}

    def test_bytes_becomes_a_string(self) -> None:
        assert _json_default(b"hello") == "hello"

    def test_unknown_object_becomes_its_str(self) -> None:
        class Weird:
            def __str__(self) -> str:
                return "weird-value"

        assert _json_default(Weird()) == "weird-value"


class TestJsonSerializer:
    def test_a_dict_containing_a_set_serializes_without_raising(self) -> None:
        # The exact production shape: a `set` nested inside an otherwise
        # normal dict, exactly like `WorkingContext.derived["_intelligence_
        # boost_keys"]` used to be.
        payload = {"status": "running", "_intelligence_boost_keys": {"architecture", "work_item"}}
        serialized = _json_serializer(payload)
        # Round-trips back to real, readable data — not silently dropped.
        restored = json.loads(serialized)
        assert restored["status"] == "running"
        assert set(restored["_intelligence_boost_keys"]) == {"architecture", "work_item"}

    def test_ordinary_json_safe_payload_is_unaffected(self) -> None:
        payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}, "d": None}
        assert json.loads(_json_serializer(payload)) == payload
