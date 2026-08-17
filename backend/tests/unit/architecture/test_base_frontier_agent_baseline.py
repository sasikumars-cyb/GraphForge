"""Phase 0, guardrail 3 — no new `BaseFrontierAgent` subclass for new
world-facing Capability behavior without explicit architectural review.

The Implementation Gap Audit (this conversation's own accepted finding)
established that `BaseFrontierAgent` is a *single-LLM-call* shape —
`build_service_requests` -> `build_prompt` -> one call ->
`render_response` — with no ActionProposal, no Control Plane check, and
no Grant between "the model decided something" and "a tool ran". The
final adversarial sequencing review named the concrete risk: it is the
easy, familiar pattern, so a new agent for a new world-facing action can
be built on it without anyone noticing it skipped the pipeline being
built out in later phases.

`BaseFrontierAgent` itself is not touched by Phase 0 (per the sequencing
plan: "Do not delete or rewrite BaseFrontierAgent. Do not migrate
existing agents yet."). This test only pins the *current* set of
subclasses so that adding a new one is a visible, reviewable diff against
this file — the "explicit architectural review" gate — rather than a
silent addition indistinguishable from the three that already,
legitimately, predate this contract.
"""

from __future__ import annotations

import ast

from tests.unit.architecture._source_scan import iter_python_files, relative

# Every file that currently defines a class inheriting BaseFrontierAgent,
# as of Phase 0. Adding a new agent here is not forbidden — it requires
# updating this baseline explicitly, which is the review gate itself.
KNOWN_BASE_FRONTIER_AGENT_SUBCLASS_FILES: frozenset[str] = frozenset(
    {
        "app/agents/dependency_query/agent.py",
        "app/agents/impact_analysis/agent.py",
        "app/agents/repository_understanding/agent.py",
    }
)

_BASE_CLASS_NAME = "BaseFrontierAgent"
_DEFINING_MODULE_FILE = "app/agents/frontier/base_frontier_agent.py"


def _files_defining_a_base_frontier_agent_subclass() -> set[str]:
    matches: set[str] = set()
    for path in iter_python_files():
        rel = relative(path)
        if rel == _DEFINING_MODULE_FILE:
            continue  # the base class's own definition is not a subclass
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {base.id for base in node.bases if isinstance(base, ast.Name)} | {
                base.attr for base in node.bases if isinstance(base, ast.Attribute)
            }
            if _BASE_CLASS_NAME in base_names:
                matches.add(rel)
                break
    return matches


def test_no_new_base_frontier_agent_subclass_without_baseline_update() -> None:
    """Fails if any file outside the known baseline defines a
    `BaseFrontierAgent` subclass.

    This does not forbid adding a new agent — it forbids adding one
    *silently*. Extending `KNOWN_BASE_FRONTIER_AGENT_SUBCLASS_FILES` here
    is the explicit review step; a PR that adds a new subclass without
    touching this file is exactly the drift this guardrail exists to
    surface.
    """
    current = _files_defining_a_base_frontier_agent_subclass()

    new = current - KNOWN_BASE_FRONTIER_AGENT_SUBCLASS_FILES
    assert not new, (
        f"New BaseFrontierAgent subclass(es) found, not in the Phase 0 "
        f"baseline: {sorted(new)}. Per the final adversarial sequencing "
        "review, a new agent for new world-facing behavior must not be "
        "built on the single-LLM-call BaseFrontierAgent pattern without "
        "explicit review — if this is legitimate (e.g. new behavior that "
        "genuinely has no world-facing side effect and is not itself an "
        "architectural regression), add it to "
        "KNOWN_BASE_FRONTIER_AGENT_SUBCLASS_FILES explicitly, as its own "
        "reviewed decision, not as a side effect of this test failing."
    )


def test_baseline_entries_still_exist() -> None:
    """Guards the baseline against silently going stale if an agent is
    deleted or refactored away from BaseFrontierAgent."""
    current = _files_defining_a_base_frontier_agent_subclass()

    stale = KNOWN_BASE_FRONTIER_AGENT_SUBCLASS_FILES - current
    assert not stale, (
        f"KNOWN_BASE_FRONTIER_AGENT_SUBCLASS_FILES contains entries that no "
        f"longer define a BaseFrontierAgent subclass: {sorted(stale)}. "
        "Remove them rather than leaving a stale baseline entry."
    )
