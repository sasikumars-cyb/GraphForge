"""`build_repository_understanding_prompt` — pure, no I/O."""

from __future__ import annotations

import json

from app.agents.repository_understanding.prompt import build_repository_understanding_prompt
from app.services.engineering_intelligence.contracts import RepositoryProfile


def test_prompt_serializes_the_full_profile() -> None:
    profile = RepositoryProfile(
        repository_id="repo-1",
        apis=("GET /orders",),
        databases=("orders",),
        queues=("order-events",),
        integrations=("BillingClient",),
        dependencies=("org.apache:commons-lang3",),
        architecture_summary="1 API(s), 1 database table(s).",
    )

    spec = build_repository_understanding_prompt(profile)
    payload = json.loads(spec.user_prompt)

    assert payload["repository_id"] == "repo-1"
    assert payload["apis"] == ["GET /orders"]
    assert payload["databases"] == ["orders"]
    assert payload["queues"] == ["order-events"]
    assert payload["integrations"] == ["BillingClient"]
    assert payload["dependencies"] == ["org.apache:commons-lang3"]
    assert spec.stage == "repository_understanding"


def test_prompt_system_prompt_instructs_grounding_only() -> None:
    profile = RepositoryProfile(repository_id="repo-1")
    spec = build_repository_understanding_prompt(profile)

    assert "FACTS" in spec.system_prompt
    assert "do not invent" in spec.system_prompt.lower()
