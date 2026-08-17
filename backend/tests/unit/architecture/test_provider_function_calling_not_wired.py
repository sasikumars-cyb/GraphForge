"""Phase 0, guardrail 4 — provider-native function/tool calling must
never become a hidden execution path.

`docs/graphforge/REASONING_ENGINE_ARCHITECTURE.md` §19 requires: "Model
output... is always data with a confidence and a provenance chain — never
self-authorizing." The final adversarial sequencing review named the
concrete mechanism this could be violated through today: some LLM
provider SDKs support directly invoking a bound function from inside the
completion loop — if that native mode were ever wired to a real
GraphForge Tool/Capability, execution would bypass the Reasoning
Plane/Control Plane boundary *inside the provider call itself*, invisibly
to everything else this repository's tests check.

`app/ai/providers/registry.py` already declares a `Capability.TOOL_CALLING`
flag — real, and correctly scoped today as *provider metadata* ("this
model supports a tool-calling API"), never consumed anywhere to actually
dispatch a Tool. This test pins that absence. Providers may produce text,
structured proposal content, or reasoning output; they must not directly
execute a real Capability — see this test's failure message for exactly
what that would mean if it ever changed.
"""

from __future__ import annotations

from tests.unit.architecture._source_scan import find_imports_of, iter_python_files, relative

_DECLARING_MODULE = "app/ai/providers/registry.py"


def test_tool_calling_capability_flag_is_metadata_only() -> None:
    """`Capability.TOOL_CALLING` must be referenced nowhere except its own
    declaring module. If a second module starts consuming it, that module
    is very likely wiring provider-native function-calling to something —
    which is exactly the hidden execution path this guardrail forbids."""
    hits = find_imports_of("app.ai.providers.registry", symbol="Capability")

    consumers = {relative(path) for path in hits if relative(path) != _DECLARING_MODULE}

    # A second, independent check: grep-equivalent textual scan for the
    # enum member's own name, since it can also be referenced via
    # `Capability.TOOL_CALLING` after a bare `from app.ai.providers.registry
    # import Capability` without appearing in the symbol-level import scan
    # above narrowing to a *different* imported symbol.
    textual_hits: set[str] = set()
    for path in iter_python_files():
        rel = relative(path)
        if rel == _DECLARING_MODULE:
            continue
        if "TOOL_CALLING" in path.read_text(encoding="utf-8"):
            textual_hits.add(rel)

    offenders = consumers | textual_hits
    assert not offenders, (
        f"Capability.TOOL_CALLING is referenced outside its declaring "
        f"module: {sorted(offenders)}. Per "
        "docs/graphforge/REASONING_ENGINE_ARCHITECTURE.md §19, provider "
        "output must never become a self-authorizing execution path. If "
        "this reference is genuinely just reading the flag to describe a "
        "model's capability (not dispatching anything), it is still "
        "flagged here deliberately — that distinction is exactly the "
        "judgment call the final adversarial sequencing review said must "
        "not be made silently. Confirm no dispatch is involved, then "
        "update this test's scope explicitly."
    )
