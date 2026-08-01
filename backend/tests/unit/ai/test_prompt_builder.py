"""Unit tests for PromptBuilder."""

from pathlib import Path

import pytest

from app.ai.services.prompt_builder import PromptBuilder


@pytest.fixture()
def builder() -> PromptBuilder:
    return PromptBuilder()


def test_load_impact_analysis_template(builder: PromptBuilder) -> None:
    raw = builder.load("impact_analysis")
    assert "Impact Analysis" in raw
    assert "{{ repository }}" in raw
    assert "{{ impacted_repositories }}" in raw
    assert "{{ dependency_paths }}" in raw
    assert "release coordination plan" in raw.lower()


def test_load_reviewer_template(builder: PromptBuilder) -> None:
    raw = builder.load("reviewer")
    assert "Reviewer Suggestion" in raw


def test_load_regression_tests_template(builder: PromptBuilder) -> None:
    raw = builder.load("regression_tests")
    assert "Regression Test" in raw


def test_load_nonexistent_template_raises(builder: PromptBuilder) -> None:
    with pytest.raises(FileNotFoundError):
        builder.load("nonexistent_template")


def test_extract_version_impact_analysis(builder: PromptBuilder) -> None:
    version = builder.extract_version("impact_analysis")
    assert version == "1.5"


def test_extract_version_reviewer(builder: PromptBuilder) -> None:
    version = builder.extract_version("reviewer")
    assert version == "1.0"


def test_extract_version_regression_tests(builder: PromptBuilder) -> None:
    version = builder.extract_version("regression_tests")
    assert version == "1.0"


def test_render_substitutes_variables(builder: PromptBuilder) -> None:
    rendered = builder.render(
        "impact_analysis",
        {
            "repository": "my-org/my-repo",
            "pull_request_title": "Fix order service",
            "deterministic_analysis": "Risk: HIGH",
            "changed_files": "src/orders.py",
        },
    )
    assert "my-org/my-repo" in rendered
    assert "Fix order service" in rendered
    assert "Risk: HIGH" in rendered
    assert "src/orders.py" in rendered


def test_render_strips_front_matter(builder: PromptBuilder) -> None:
    rendered = builder.render("impact_analysis", {})
    assert "---" not in rendered
    assert "version:" not in rendered


def test_render_with_custom_prompts_dir(tmp_path: Path) -> None:
    template = tmp_path / "custom.md"
    template.write_text(
        '---\nversion: "2.0"\n---\n\nHello {{ name }}!\n',
        encoding="utf-8",
    )
    builder = PromptBuilder(prompts_dir=tmp_path)
    assert builder.extract_version("custom") == "2.0"
    rendered = builder.render("custom", {"name": "World"})
    assert "Hello World!" in rendered


def test_extract_version_no_front_matter(tmp_path: Path) -> None:
    template = tmp_path / "bare.md"
    template.write_text("No front matter here\n", encoding="utf-8")
    builder = PromptBuilder(prompts_dir=tmp_path)
    assert builder.extract_version("bare") == ""
