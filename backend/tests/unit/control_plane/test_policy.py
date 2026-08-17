"""Contract tests for `app.control_plane.policy` — §20.3 most-restrictive-
wins intersection, fail-closed default, and the explicit "no fake
signatures/quorum" honesty guarantee."""

from __future__ import annotations

from app.control_plane.policy import (
    PolicyRule,
    PolicyRuleEffect,
    PolicyScopeLevel,
    PolicyStore,
    PolicyVersion,
    seed_system_policy_allowing,
)


def _version(*rules: PolicyRule, authored_by: str = "test-authority") -> PolicyVersion:
    return PolicyVersion(
        rules=tuple(rules),
        authoring_authority=authored_by,
        effective_at="2026-08-17T00:00:00Z",
        supersedes=None,
    )


class TestFailClosedDefault:
    def test_capability_with_no_policy_loaded_at_all_is_denied(self) -> None:
        store = PolicyStore()
        decision = store.evaluate("query_knowledge_graph")
        assert decision.allowed is False
        assert "fail-closed" in decision.reason

    def test_capability_with_no_matching_rule_at_any_loaded_level_is_denied(self) -> None:
        store = PolicyStore()
        store.load(
            PolicyScopeLevel.SYSTEM,
            _version(
                PolicyRule(
                    capability_id="some_other_capability",
                    effect=PolicyRuleEffect.ALLOW,
                    scope_level=PolicyScopeLevel.SYSTEM,
                    reason="unrelated",
                )
            ),
        )
        decision = store.evaluate("query_knowledge_graph")
        assert decision.allowed is False


class TestAllowPath:
    def test_system_level_allow_with_nothing_else_loaded_allows(self) -> None:
        store = PolicyStore()
        store.load(
            PolicyScopeLevel.SYSTEM,
            seed_system_policy_allowing(
                "query_knowledge_graph", authored_by="ops", effective_at="2026-08-17T00:00:00Z"
            ),
        )
        decision = store.evaluate("query_knowledge_graph")
        assert decision.allowed is True

    def test_allow_at_every_loaded_level_allows(self) -> None:
        store = PolicyStore()
        rule = PolicyRule(
            capability_id="query_knowledge_graph",
            effect=PolicyRuleEffect.ALLOW,
            scope_level=PolicyScopeLevel.SYSTEM,
            reason="seed",
        )
        store.load(PolicyScopeLevel.SYSTEM, _version(rule))
        store.load(
            PolicyScopeLevel.TENANT,
            _version(
                PolicyRule(
                    capability_id="query_knowledge_graph",
                    effect=PolicyRuleEffect.ALLOW,
                    scope_level=PolicyScopeLevel.TENANT,
                    reason="tenant allows too",
                )
            ),
        )
        decision = store.evaluate("query_knowledge_graph")
        assert decision.allowed is True


class TestMostRestrictiveWins:
    def test_deny_at_narrower_scope_overrides_allow_at_broader_scope(self) -> None:
        store = PolicyStore()
        store.load(
            PolicyScopeLevel.SYSTEM,
            seed_system_policy_allowing(
                "query_knowledge_graph", authored_by="ops", effective_at="2026-08-17T00:00:00Z"
            ),
        )
        store.load(
            PolicyScopeLevel.TASK,
            _version(
                PolicyRule(
                    capability_id="query_knowledge_graph",
                    effect=PolicyRuleEffect.DENY,
                    scope_level=PolicyScopeLevel.TASK,
                    reason="this task is under incident review",
                )
            ),
        )
        decision = store.evaluate("query_knowledge_graph")
        assert decision.allowed is False
        assert "task scope" in decision.reason

    def test_deny_at_any_level_denies_regardless_of_evaluation_order(self) -> None:
        store = PolicyStore()
        store.load(
            PolicyScopeLevel.SYSTEM,
            seed_system_policy_allowing(
                "query_knowledge_graph", authored_by="ops", effective_at="2026-08-17T00:00:00Z"
            ),
        )
        store.load(
            PolicyScopeLevel.TENANT,
            _version(
                PolicyRule(
                    capability_id="query_knowledge_graph",
                    effect=PolicyRuleEffect.DENY,
                    scope_level=PolicyScopeLevel.TENANT,
                    reason="tenant-level restriction",
                )
            ),
        )
        assert store.evaluate("query_knowledge_graph").allowed is False


class TestVersionIdentity:
    def test_version_id_is_deterministic_for_identical_content(self) -> None:
        rule = PolicyRule(
            capability_id="query_knowledge_graph",
            effect=PolicyRuleEffect.ALLOW,
            scope_level=PolicyScopeLevel.SYSTEM,
            reason="seed",
        )
        v1 = _version(rule)
        v2 = _version(rule)
        assert v1.version_id == v2.version_id

    def test_version_id_changes_with_content(self) -> None:
        rule_a = PolicyRule(
            capability_id="query_knowledge_graph",
            effect=PolicyRuleEffect.ALLOW,
            scope_level=PolicyScopeLevel.SYSTEM,
            reason="seed",
        )
        rule_b = PolicyRule(
            capability_id="query_knowledge_graph",
            effect=PolicyRuleEffect.DENY,
            scope_level=PolicyScopeLevel.SYSTEM,
            reason="seed",
        )
        assert _version(rule_a).version_id != _version(rule_b).version_id

    def test_policy_version_is_never_marked_signed(self) -> None:
        """The explicit honesty guarantee: this phase does not implement
        cryptographic quorum, and `signed` must never silently read True."""
        version = seed_system_policy_allowing(
            "query_knowledge_graph", authored_by="ops", effective_at="2026-08-17T00:00:00Z"
        )
        assert version.signed is False

    def test_combined_version_signature_changes_when_any_level_changes(self) -> None:
        store = PolicyStore()
        store.load(
            PolicyScopeLevel.SYSTEM,
            seed_system_policy_allowing(
                "query_knowledge_graph", authored_by="ops", effective_at="2026-08-17T00:00:00Z"
            ),
        )
        before = store.combined_version_signature()
        store.load(
            PolicyScopeLevel.TENANT,
            _version(
                PolicyRule(
                    capability_id="unrelated",
                    effect=PolicyRuleEffect.ALLOW,
                    scope_level=PolicyScopeLevel.TENANT,
                    reason="noop",
                )
            ),
        )
        after = store.combined_version_signature()
        assert before != after
