"""Unit tests for pure CODEOWNERS parsing - no I/O."""

from app.ai.agent.codeowners import match_owners, parse_codeowners


def test_parse_codeowners_skips_comments_and_blank_lines() -> None:
    content = """
    # This is a comment

    *.py @alice
    """
    entries = parse_codeowners(content)
    assert entries == [("*.py", ["@alice"])]


def test_parse_codeowners_supports_multiple_owners_per_line() -> None:
    content = "src/orders/* @alice @bob"
    entries = parse_codeowners(content)
    assert entries == [("src/orders/*", ["@alice", "@bob"])]


def test_parse_codeowners_ignores_malformed_lines() -> None:
    content = "justapattern\n*.py @alice"
    entries = parse_codeowners(content)
    assert entries == [("*.py", ["@alice"])]


def test_match_owners_last_match_wins() -> None:
    content = "\n".join(
        [
            "*.py @alice",
            "src/orders/*.py @bob",
        ]
    )
    owners = match_owners({"src/orders/order.py", "other/thing.py"}, content)
    assert owners["src/orders/order.py"] == ["@bob"]
    assert owners["other/thing.py"] == ["@alice"]


def test_match_owners_unmatched_path_is_empty() -> None:
    content = "*.py @alice"
    owners = match_owners({"README.md"}, content)
    assert owners["README.md"] == []


def test_match_owners_matches_root_level_pattern_with_leading_slash() -> None:
    content = "/CODEOWNERS @alice"
    owners = match_owners({"CODEOWNERS"}, content)
    assert owners["CODEOWNERS"] == ["@alice"]
