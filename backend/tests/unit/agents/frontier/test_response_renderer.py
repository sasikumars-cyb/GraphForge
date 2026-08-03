"""Pure unit tests for `ResponseRenderer` — no I/O, no AgentContext."""

from __future__ import annotations

from app.agents.frontier.response_renderer import (
    to_executive_summary,
    to_json,
    to_markdown,
    to_ui_sections,
)


def test_to_markdown_renders_text_and_list_sections_in_order() -> None:
    markdown = to_markdown(
        "Repository Profile",
        {"Summary": "A checkout service.", "APIs": ["GET /orders", "POST /orders"]},
    )

    assert markdown.startswith("# Repository Profile")
    assert "### Summary\n\nA checkout service." in markdown
    assert "### APIs\n\n- GET /orders\n- POST /orders" in markdown
    assert markdown.index("Summary") < markdown.index("APIs")


def test_to_markdown_omits_empty_sections() -> None:
    markdown = to_markdown("Title", {"Empty list": [], "Empty text": "", "Present": "value"})

    assert "Empty list" not in markdown
    assert "Empty text" not in markdown
    assert "Present" in markdown


def test_to_json_is_deterministic_and_sorted() -> None:
    first = to_json({"b": "2", "a": "1"})
    second = to_json({"a": "1", "b": "2"})
    assert first == second
    assert first.index('"a"') < first.index('"b"')


def test_to_ui_sections_tags_kind_and_drops_empty() -> None:
    sections = to_ui_sections({"Summary": "text", "Items": ["a", "b"], "Empty": []})

    assert sections == [
        {"heading": "Summary", "kind": "text", "content": "text"},
        {"heading": "Items", "kind": "list", "content": ["a", "b"]},
    ]


def test_to_executive_summary_appends_bounded_key_points() -> None:
    summary = to_executive_summary("Overview.", ["a", "b", "c", "d", "e", "f"], max_key_points=3)

    assert summary.startswith("Overview.")
    assert "- a" in summary
    assert "- c" in summary
    assert "- d" not in summary


def test_to_executive_summary_returns_bare_summary_with_no_key_points() -> None:
    assert to_executive_summary("Overview.", []) == "Overview."
