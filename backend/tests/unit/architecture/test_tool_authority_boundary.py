"""Phase 2, guardrails 5/7 — the Tool MUST NOT become an authorization
authority, and no NEW code path may reach a Tool implementation directly,
bypassing the Capability/Tool boundary Phase 0 already began enforcing
for `ToolExecutor` itself.
"""

from __future__ import annotations

import inspect

from app.tools.interfaces import ITool
from tests.unit.architecture._source_scan import (
    find_imports_matching_prefix,
    iter_python_files,
    relative,
)

# Direct Tool-IMPLEMENTATION imports (as opposed to ToolExecutor dispatch,
# already covered by test_capability_execution_boundary.py) that predate
# this guardrail — confirmed via find_imports_matching_prefix (AST-based,
# not a textual scan: an earlier version of this check used a substring
# search and produced false positives from files whose DOCSTRINGS merely
# *mention* `app.tools.implementations.jira_tool` in prose, e.g.
# `agents/git_ops/_authorization.py`, without importing anything).
# `context_pipeline/providers.py` constructs GitHubTool/GoogleDriveTool
# instances directly because their credentials are per-user, not
# install-wide (see app.tools.executor.execute_instance's own docstring
# on exactly this case) — one of several real, load-bearing pre-existing
# exceptions, grandfathered here rather than silently treated as
# compliant. See this Phase's final report for the full classification
# table.
_ALLOWED_LEGACY_TOOL_IMPLEMENTATION_IMPORTERS: frozenset[str] = frozenset(
    {
        "app/api/v1/routers/jira.py",
        "app/context_pipeline/providers.py",
        "app/context_pipeline/reasoning/investigators.py",
        "app/context_pipeline/reference_detection.py",
        "app/services/refinement_grounding.py",
        "app/services/testrail_service.py",
    }
)
_INTERNAL_TO_TOOL_LAYER_PREFIX = "app/tools/"
_RESERVED_CONTROL_PLANE_PREFIX = "app/control_plane/"
_CAPABILITIES_LAYER_PREFIX = "app/capabilities/"


def test_no_new_direct_tool_implementation_imports() -> None:
    """No file outside the grandfathered set, the Tool layer itself, the
    reserved Control Plane namespace, or the Capabilities layer imports a
    concrete Tool implementation class directly (`app.tools.
    implementations.*`) — new code must go through the registry/executor
    seam, or (once Phase 3 exists) the Capability/Control Plane seam."""
    hits = find_imports_matching_prefix("app.tools.implementations")

    offenders = {
        relative(path)
        for path in hits
        if not relative(path).startswith(_INTERNAL_TO_TOOL_LAYER_PREFIX)
        and not relative(path).startswith(_RESERVED_CONTROL_PLANE_PREFIX)
        and not relative(path).startswith(_CAPABILITIES_LAYER_PREFIX)
        and relative(path) not in _ALLOWED_LEGACY_TOOL_IMPLEMENTATION_IMPORTERS
    }

    assert not offenders, (
        f"New direct Tool-implementation import found: {sorted(offenders)}. "
        "New code must reach a Tool through app.tools.registry/executor "
        "(or, once it exists, the Capability/Control Plane seam) — not by "
        "importing an implementation class directly."
    )


def test_grandfathered_tool_implementation_importers_still_exist() -> None:
    hits = find_imports_matching_prefix("app.tools.implementations")
    current = {relative(path) for path in hits}

    stale = _ALLOWED_LEGACY_TOOL_IMPLEMENTATION_IMPORTERS - current
    assert not stale, (
        f"Grandfathered entries no longer import a Tool implementation: "
        f"{sorted(stale)}. Remove the stale allowlist entry."
    )


def test_itool_protocol_declares_no_authorization_capability() -> None:
    """Structural, not documentary: the ITool Protocol itself has no
    method whose name suggests it could assert its own authorization,
    issue a Grant, or evaluate Policy — Cap §4/§12: "neither the
    component that proposes an Action nor the component that would
    execute it may compute or assert its own authorization." A Tool
    implementing ITool physically cannot expose such a method as part of
    the contract every caller relies on."""
    forbidden_substrings = ("authoriz", "grant", "policy", "permit")
    members = {name for name, _ in inspect.getmembers(ITool) if not name.startswith("_")}
    offending = {m for m in members if any(f in m.lower() for f in forbidden_substrings)}
    assert not offending, (
        f"ITool declares authorization-shaped member(s): {offending}. A "
        "Tool must never be able to assert its own authorization (Cap §4)."
    )


def test_no_tool_implementation_references_policy_concepts() -> None:
    """Forward-looking guard: Policy (Phase 3+) does not exist as code
    yet, so this currently passes vacuously — it exists so the moment
    Policy IS introduced, no Tool implementation can quietly start
    importing or referencing it without this test failing first."""
    offenders: set[str] = set()
    implementations_dir_prefix = "app/tools/implementations/"
    for path in iter_python_files():
        rel = relative(path)
        if not rel.startswith(implementations_dir_prefix):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "app.policy" in source or "app.control_plane" in source:
            offenders.add(rel)

    assert not offenders, (
        f"Tool implementation references Policy/Control-Plane modules "
        f"directly: {sorted(offenders)}. A Tool must never modify or "
        "consult Policy itself (Cap §12) — only the Control Plane may."
    )
