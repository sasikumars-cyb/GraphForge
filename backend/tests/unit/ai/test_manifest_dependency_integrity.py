"""ADR 0011, OD-3 — manifest-level integrity checks for every real,
registered `AgentManifest`.

This is the "manifest-level test catching divergence" ADR 0011 itself
promises: `required_dependencies` is a second, independently-authored
signal alongside `max_graph_hops` (Neo4j) and
`app.agents.llm.default_stage_for_agent` (LLM) — nothing at runtime derives
one from the other, so nothing but a test stops them from silently drifting
apart as manifests are edited over time.

Imports each `*_MANIFEST` constant directly (the same import list
`app/agents/setup.py` uses) rather than going through `app.main`/
`create_app()`, keeping this a fast, DB-free unit test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agents._contract import AgentManifest
from app.agents.code_generation.manifest import CODE_GENERATION_MANIFEST
from app.agents.context_discovery.manifest import CONTEXT_DISCOVERY_MANIFEST
from app.agents.development.manifest import DEVELOPMENT_MANIFEST
from app.agents.documentation_planning.manifest import DOCUMENTATION_PLANNING_MANIFEST
from app.agents.engineering_review.manifest import ENGINEERING_REVIEW_MANIFEST
from app.agents.git_ops.manifests import (
    COMMIT_CHANGES_MANIFEST,
    CREATE_BRANCH_MANIFEST,
    CREATE_PULL_REQUEST_MANIFEST,
    RUN_TESTS_MANIFEST,
)
from app.agents.llm import default_stage_for_agent
from app.agents.planning.manifest import PLANNING_MANIFEST
from app.agents.review_adapter import REVIEW_MANIFEST
from app.agents.testing.manifest import TESTING_MANIFEST
from app.orchestrator.preflight import (
    ALL_DEPENDENCIES,
    DEPENDENCY_GITHUB_WRITE,
    DEPENDENCY_LLM,
    DEPENDENCY_NEO4J,
    agent_requires,
)
from app.orchestrator.registry import AgentRegistry

ALL_MANIFESTS: tuple[AgentManifest, ...] = (
    REVIEW_MANIFEST,
    CONTEXT_DISCOVERY_MANIFEST,
    PLANNING_MANIFEST,
    DEVELOPMENT_MANIFEST,
    TESTING_MANIFEST,
    DOCUMENTATION_PLANNING_MANIFEST,
    ENGINEERING_REVIEW_MANIFEST,
    CODE_GENERATION_MANIFEST,
    CREATE_BRANCH_MANIFEST,
    COMMIT_CHANGES_MANIFEST,
    RUN_TESTS_MANIFEST,
    CREATE_PULL_REQUEST_MANIFEST,
)

_GIT_OPS_AGENT_IDS = frozenset(
    {"create_branch", "commit_changes", "run_tests", "create_pull_request"}
)


def test_every_registered_agent_has_a_manifest_in_this_list() -> None:
    """Guards against this test file's own list going stale (a new agent
    added to app/agents/setup.py but forgotten here)."""
    ids = {m.agent_id for m in ALL_MANIFESTS}
    assert ids == {
        "review",
        "context_discovery",
        "planning",
        "development",
        "testing",
        "documentation_planning",
        "engineering_review",
        "code_generation",
        "create_branch",
        "commit_changes",
        "run_tests",
        "create_pull_request",
    }


def test_every_declared_dependency_is_a_known_constant() -> None:
    """No typos: every string any manifest declares must be one of the
    three closed, centrally-defined constants."""
    for manifest in ALL_MANIFESTS:
        unknown = manifest.required_dependencies - ALL_DEPENDENCIES
        assert not unknown, f"{manifest.agent_id} declares unknown dependency: {unknown}"


def test_neo4j_declaration_matches_max_graph_hops() -> None:
    """agent_requires(manifest, DEPENDENCY_NEO4J) must agree with
    max_graph_hops > 0 for every manifest — the exact consistency ADR 0011's
    OD-3 resolution requires ("every agent's required_dependencies for
    Neo4j is set consistently with its own max_graph_hops")."""
    for manifest in ALL_MANIFESTS:
        expected = manifest.max_graph_hops > 0
        actual = agent_requires(manifest, DEPENDENCY_NEO4J)
        assert actual == expected, (
            f"{manifest.agent_id}: max_graph_hops={manifest.max_graph_hops} implies "
            f"DEPENDENCY_NEO4J should be {'present' if expected else 'absent'}, got {actual}"
        )


def test_llm_declaration_matches_default_stage_for_agent() -> None:
    """agent_requires(manifest, DEPENDENCY_LLM) must agree with whether
    `default_stage_for_agent` resolves a stage for this agent at all — the
    existing implicit LLM-requirement signal `check_llm_provider_configured`
    already relies on."""
    for manifest in ALL_MANIFESTS:
        expected = default_stage_for_agent(manifest.agent_id) is not None
        actual = agent_requires(manifest, DEPENDENCY_LLM)
        assert actual == expected, (
            f"{manifest.agent_id}: default_stage_for_agent(...) is "
            f"{'not None' if expected else 'None'}, implies DEPENDENCY_LLM should be "
            f"{'present' if expected else 'absent'}, got {actual}"
        )


def test_git_ops_agents_declare_github_write_and_nothing_else() -> None:
    for manifest in ALL_MANIFESTS:
        if manifest.agent_id in _GIT_OPS_AGENT_IDS:
            assert manifest.required_dependencies == frozenset({DEPENDENCY_GITHUB_WRITE})


def test_non_git_ops_agents_never_declare_github_write() -> None:
    for manifest in ALL_MANIFESTS:
        if manifest.agent_id not in _GIT_OPS_AGENT_IDS:
            assert DEPENDENCY_GITHUB_WRITE not in manifest.required_dependencies


def test_no_manifest_declares_an_empty_set_that_should_be_non_empty() -> None:
    """Every agent depends on *something* statically-determinable (LLM,
    Neo4j, or GitHub-write) — an entirely empty declaration would mean this
    field was simply forgotten for that manifest."""
    for manifest in ALL_MANIFESTS:
        assert manifest.required_dependencies, (
            f"{manifest.agent_id} declares no required_dependencies at all"
        )


# ---------------------------------------------------------------------------
# Agent registration — the new field must not disturb registry behavior.
# ---------------------------------------------------------------------------


def test_all_manifests_register_successfully_with_the_new_field_present() -> None:
    registry = AgentRegistry()
    for manifest in ALL_MANIFESTS:
        registry.register(manifest, MagicMock())
    registered_ids = {m.agent_id for m in registry.all_manifests()}
    assert registered_ids == {m.agent_id for m in ALL_MANIFESTS}


def test_registered_manifest_round_trips_required_dependencies() -> None:
    registry = AgentRegistry()
    registry.register(PLANNING_MANIFEST, MagicMock())
    result = registry.get("planning")
    assert result is not None
    fetched_manifest, _agent = result
    assert fetched_manifest.required_dependencies == frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J})
