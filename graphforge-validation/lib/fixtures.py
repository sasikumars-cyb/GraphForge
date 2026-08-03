"""Loads the `validation/expected_*.yaml` files — the only files this
framework's authors expect to need editing when GraphForge's behavior
legitimately changes (see `docs/validation-guide.md`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_VALIDATION_DIR = Path(__file__).resolve().parents[1] / "validation"


def _load(filename: str) -> dict[str, Any]:
    with (_VALIDATION_DIR / filename).open() as f:
        return yaml.safe_load(f) or {}


def load_expected_relationships() -> dict[str, Any]:
    return _load("expected_relationships.yaml")


def load_expected_repository_profiles() -> dict[str, Any]:
    return _load("expected_repository_profiles.yaml")


def load_expected_dependency_queries() -> dict[str, Any]:
    return _load("expected_dependency_queries.yaml")


def load_expected_impact_analysis() -> dict[str, Any]:
    return _load("expected_impact_analysis.yaml")


def load_expected_frontier_hypotheses() -> dict[str, Any]:
    return _load("expected_frontier_hypotheses.yaml")
