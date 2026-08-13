"""RFC-0019 — unit tests for `extract_config_files`: generic YAML/JSON
key/value flattening, independent of any language-specific parsing. No
key name, value, repository, or ticket is ever hardcoded in the extractor
itself; these tests use synthetic keys/values (`alpha`/`beta`/`widget`)
to prove that."""

from pathlib import Path

from app.indexer.extractors.config_file_extractor import extract_config_files


def test_flattens_nested_yaml_keys_and_values(tmp_path: Path) -> None:
    (tmp_path / "deploy.yml").write_text(
        """
resources:
  jobs:
    MY_JOB:
      parameters:
        - name: widget
          default: alpha
""",
        encoding="utf-8",
    )
    config_files, _ = extract_config_files(tmp_path)
    assert len(config_files) == 1
    text = config_files[0].flattened_text
    assert "widget" in text
    assert "alpha" in text
    assert config_files[0].location.file_path == "deploy.yml"


def test_flattens_nested_json_keys_and_values(tmp_path: Path) -> None:
    (tmp_path / "deploy.json").write_text(
        '{"resources": {"jobs": {"MY_JOB": {"opco": "beta"}}}}', encoding="utf-8"
    )
    config_files, _ = extract_config_files(tmp_path)
    assert len(config_files) == 1
    assert "beta" in config_files[0].flattened_text


def test_yml_and_yaml_both_recognized(tmp_path: Path) -> None:
    (tmp_path / "a.yml").write_text("key: one", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("key: two", encoding="utf-8")
    config_files, _ = extract_config_files(tmp_path)
    assert {c.location.file_path for c in config_files} == {"a.yml", "b.yaml"}


def test_no_config_files_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("hello", encoding="utf-8")
    config_files, references = extract_config_files(tmp_path)
    assert config_files == []
    assert references == []


def test_skip_directories_are_respected(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "vendored.yml").write_text("key: value", encoding="utf-8")
    (tmp_path / "real.yml").write_text("key: value", encoding="utf-8")
    config_files, _ = extract_config_files(tmp_path)
    assert [c.location.file_path for c in config_files] == ["real.yml"]


def test_secret_shaped_filenames_are_never_read(tmp_path: Path) -> None:
    (tmp_path / "secret.yml").write_text("api_key: super-secret-value", encoding="utf-8")
    (tmp_path / ".env.yml").write_text("token: super-secret-value", encoding="utf-8")
    (tmp_path / "safe.yml").write_text("key: value", encoding="utf-8")
    config_files, _ = extract_config_files(tmp_path)
    assert [c.location.file_path for c in config_files] == ["safe.yml"]


def test_malformed_yaml_is_skipped_not_raised(tmp_path: Path) -> None:
    (tmp_path / "broken.yml").write_text("key: [unterminated", encoding="utf-8")
    config_files, references = extract_config_files(tmp_path)
    assert config_files == []
    assert references == []


def test_path_shaped_values_become_references(tmp_path: Path) -> None:
    (tmp_path / "deploy.yml").write_text(
        """
task:
  python_file: ${workspace.file_path}/pipeline/main_pipeline.py
  notebook_path: /Workspace/shared_notebooks/push_event
""",
        encoding="utf-8",
    )
    _, references = extract_config_files(tmp_path)
    referenced = {r.referenced_text for r in references}
    assert "${workspace.file_path}/pipeline/main_pipeline.py" in referenced
    # No path separator + recognized extension -> not treated as a reference.
    assert "/Workspace/shared_notebooks/push_event" not in referenced


def test_value_without_reference_shape_is_not_captured_as_a_reference(tmp_path: Path) -> None:
    (tmp_path / "deploy.yml").write_text("env: production\nregion: us-east-1", encoding="utf-8")
    _, references = extract_config_files(tmp_path)
    assert references == []


def test_flattened_pair_count_is_bounded(tmp_path: Path) -> None:
    """`_MAX_PAIRS` (a count of flattened key/value pairs) is the real,
    generic size bound — not a character-count truncation on the joined
    text (removed in RFC-0020, see that module's own docstring for why:
    it discarded fields based on where they happened to sit in the file,
    not on any principled relevance signal)."""
    huge = {"k" + str(i): "v" * 20 for i in range(500)}
    import yaml as _yaml

    (tmp_path / "big.yml").write_text(_yaml.safe_dump(huge), encoding="utf-8")
    config_files, _ = extract_config_files(tmp_path)
    tokens = config_files[0].flattened_text.split(" ")
    # 200 pairs -> 400 whitespace-separated tokens (key + value each).
    assert len(tokens) <= 400


def test_a_discriminative_field_late_in_a_large_structure_stays_searchable(tmp_path: Path) -> None:
    """RFC-0020 regression: the exact failure mode found in the live
    PROT-5764 audit — a config file's most decisive field sitting after a
    large amount of earlier, less relevant structure must not be silently
    dropped. A generic synthetic fixture, not the real ticket's fields."""
    noise_keys = {f"boilerplate_field_{i}": f"noise_value_{i}" for i in range(150)}
    payload = {**noise_keys, "late_discriminator": "gamma"}
    import yaml as _yaml

    (tmp_path / "large.yml").write_text(_yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config_files, _ = extract_config_files(tmp_path)
    assert "gamma" in config_files[0].flattened_text
