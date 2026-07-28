"""Format a prior workflow stage's full structured result as an LLM-ready
text block.

Reads the same structured artifacts `app.agents.git_ops._artifact_reader.
get_stage_result()` returns — the untruncated `AgentStep.result` JSON, not
the summarized/truncated text `workflow_service.build_stage_context()`
produces via `resolve_freetext()` (hard-capped at 256 chars; see
app.context.resolvers.freetext). Originally written for the Engineering
Review agent (which reads all three prior stages this way, since it runs
no tools of its own), factored out here so Development and Testing can use
the same untruncated hand-off for the one stage immediately before them —
see their own agent.py docstrings for why that hand-off previously lost
almost everything past the original request text.

Field names are cross-checked directly against planning/schemas.py,
development/schemas.py, and testing/schemas.py; nothing here is guessed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _join(items: list[Any], render: Callable[[Any], str]) -> str:
    return "; ".join(render(i) for i in items)


def format_planning_block(result: dict[str, Any] | None) -> str:
    if not result:
        return "### Planning Stage\n(No completed Planning stage result available.)"

    lines = [
        "### Planning Stage",
        f"Summary: {result.get('executive_summary') or '(none)'}",
    ]
    if steps := result.get("implementation_steps"):
        lines.append(
            "Implementation steps: "
            + _join(
                steps,
                lambda s: (
                    f"{s.get('order', '?')}. {s.get('description', '')}"
                    f" (risk: {s.get('risk_note') or 'none noted'})"
                ),
            )
        )
    if comps := result.get("affected_components"):
        lines.append("Affected components: " + ", ".join(comps))
    if topics := result.get("kafka_topics_involved"):
        lines.append("Kafka topics involved: " + ", ".join(topics))
    if risks := result.get("risk_considerations"):
        lines.append("Risk considerations: " + ", ".join(risks))
    if repos := result.get("repositories_consulted"):
        lines.append("Repositories consulted: " + ", ".join(repos))
    return "\n".join(lines)


def format_development_block(result: dict[str, Any] | None) -> str:
    if not result:
        return "### Development Stage\n(No completed Development stage result available.)"

    lines = [
        "### Development Stage",
        f"Summary: {result.get('executive_summary') or '(none)'}",
    ]
    if repos := result.get("repositories"):
        lines.append(
            "Repositories: "
            + _join(
                repos,
                lambda r: f"{r.get('name', '?')} ({r.get('owner', '?')}) — {r.get('reason', '')}",
            )
        )
    if comps := result.get("components"):
        lines.append(
            "Components: "
            + _join(
                comps,
                lambda c: (
                    f"{c.get('name', '?')} [{c.get('component_type', '')}]"
                    f" {c.get('file_path', '')} — {c.get('change_description', '')}"
                ),
            )
        )
    if deps := result.get("dependencies"):
        lines.append(
            "Dependencies: "
            + _join(
                deps,
                lambda d: (
                    f"{d.get('source', '?')} —[{d.get('relationship', '')}]→ {d.get('target', '?')}"
                    f" (risk: {d.get('risk_note') or 'none noted'})"
                ),
            )
        )
    if reuse := result.get("reusable_implementations"):
        lines.append(
            "Reusable implementations: "
            + _join(
                reuse,
                lambda r: (
                    f"{r.get('name', '?')} ({r.get('repository', '')}) — {r.get('reason', '')}"
                ),
            )
        )
    if phases := result.get("implementation_phases"):
        lines.append(
            "Implementation phases: "
            + _join(
                phases,
                lambda p: (
                    f"{p.get('order', '?')}. {p.get('title', '')}"
                    f" [{p.get('estimated_complexity') or 'unspecified'} complexity]"
                    f" — {p.get('description', '')}"
                ),
            )
        )
    if risks := result.get("risks"):
        lines.append(
            "Risks: "
            + _join(
                risks,
                lambda r: (
                    f"{r.get('description', '')} [{r.get('severity') or 'unspecified'}]"
                    f" — mitigation: {r.get('mitigation') or 'none stated'}"
                ),
            )
        )
    if recs := result.get("recommendations"):
        lines.append("Recommendations: " + "; ".join(recs))
    return "\n".join(lines)


def format_documentation_block(result: dict[str, Any] | None) -> str:
    if not result:
        return (
            "### Documentation Planning Stage\n"
            "(No completed Documentation Planning stage result available.)"
        )

    lines = [
        "### Documentation Planning Stage",
        f"Summary: {result.get('executive_summary') or '(none)'}",
    ]
    if impact := result.get("documentation_impact"):
        explanation = result.get("impact_explanation") or ""
        suffix = f" — {explanation}" if explanation else ""
        lines.append(f"Documentation impact: {impact}{suffix}")
    if updates := result.get("required_updates"):
        lines.append(
            "Required updates: "
            + _join(
                updates,
                lambda u: (
                    f"{u.get('document', '?')} [{u.get('action', '')}, "
                    f"{u.get('priority') or 'unspecified'} priority] — {u.get('reason', '')}"
                ),
            )
        )
    if new_docs := result.get("new_documentation"):
        lines.append(
            "New documentation: "
            + _join(new_docs, lambda n: f"{n.get('name', '?')} — {n.get('purpose', '')}")
        )
    if risks := result.get("risks"):
        lines.append(
            "Documentation risks: "
            + _join(
                risks,
                lambda r: f"{r.get('description', '')} [{r.get('severity') or 'unspecified'}]",
            )
        )
    if recs := result.get("recommendations"):
        lines.append("Recommendations: " + "; ".join(recs))
    if notes := result.get("release_notes_draft"):
        lines.append("Release notes draft: " + "; ".join(notes))
    return "\n".join(lines)


def format_testing_block(result: dict[str, Any] | None) -> str:
    if not result:
        return "### Testing Stage\n(No completed Testing stage result available.)"

    lines = [
        "### Testing Stage",
        f"Summary: {result.get('executive_summary') or '(none)'}",
    ]
    if scope := result.get("test_scope"):
        in_scope = ", ".join(scope.get("in_scope", [])) or "(none stated)"
        out_scope = ", ".join(scope.get("out_of_scope", [])) or "(none stated)"
        lines.append(f"Test scope: in scope — {in_scope}; out of scope — {out_scope}")
    if repos := result.get("affected_repositories"):
        lines.append("Affected repositories: " + ", ".join(repos))
    if comps := result.get("affected_components"):
        lines.append("Affected components: " + ", ".join(comps))
    if regression := result.get("regression_tests"):
        lines.append(
            "Regression tests: "
            + _join(
                regression,
                lambda t: (
                    f"{t.get('component', '?')}: {t.get('description', '')}"
                    f" [{t.get('priority') or 'unspecified'}]"
                ),
            )
        )
    if integration := result.get("integration_tests"):
        lines.append(
            "Integration tests: "
            + _join(
                integration,
                lambda t: (
                    f"{t.get('source_component', '?')} → {t.get('target_component', '?')}"
                    f" [{t.get('relationship', '')}]: {t.get('description', '')}"
                ),
            )
        )
    if edge_cases := result.get("edge_cases"):
        lines.append(
            "Edge cases: "
            + _join(
                edge_cases,
                lambda e: f"{e.get('description', '')} [{e.get('severity') or 'unspecified'}]",
            )
        )
    if envs := result.get("environment_requirements"):
        lines.append(
            "Environment requirements: "
            + _join(envs, lambda e: f"{e.get('name', '?')} — {e.get('description', '')}")
        )
    if exec_order := result.get("execution_order"):
        lines.append(
            "Execution order: "
            + _join(
                exec_order,
                lambda p: (
                    f"{p.get('order', '?')}. {p.get('title', '')} — {p.get('description', '')}"
                ),
            )
        )
    if automation := result.get("automation_candidates"):
        lines.append(
            "Automation candidates: "
            + _join(automation, lambda a: f"{a.get('description', '')} ({a.get('test_type', '')})")
        )
    if manual := result.get("manual_validations"):
        lines.append(
            "Manual validations: "
            + _join(manual, lambda m: f"{m.get('description', '')} — {m.get('reason', '')}")
        )
    if risks := result.get("risks"):
        lines.append(
            "Risks: "
            + _join(
                risks,
                lambda r: (
                    f"{r.get('description', '')} [{r.get('severity') or 'unspecified'}]"
                    f" — mitigation: {r.get('mitigation') or 'none stated'}"
                ),
            )
        )
    if recs := result.get("recommendations"):
        lines.append("Recommendations: " + "; ".join(recs))
    return "\n".join(lines)
