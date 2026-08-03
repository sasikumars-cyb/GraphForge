#!/usr/bin/env python3
"""Renders the JSON results `run_validation.py` produces into a
self-contained, demo-suitable HTML report — no external JS/CSS
dependencies (works offline, safe to screenshot or project).

Can also be run standalone against an existing results file:
    python scripts/generate_report.py reports/latest.json reports/latest.html
"""

from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import Any

VERDICT_COLOR = {
    "PASS": "#1a8a5f",
    "FAIL": "#c8442f",
    "MISSING": "#b8860b",
    "UNEXPECTED": "#8a5fc8",
    "SKIP": "#8a8f98",
}


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _section_by_id(results: dict[str, Any], validation_id: int) -> dict[str, Any] | None:
    for section in results["sections"]:
        if section["validation_id"] == validation_id:
            return section
    return None


def _checks_matching(section: dict[str, Any] | None, *substrings: str) -> list[dict[str, Any]]:
    if section is None:
        return []
    return [
        c for c in section["checks"] if all(s.lower() in c["name"].lower() for s in substrings)
    ]


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def _summary_cards(results: dict[str, Any]) -> str:
    overall = results["overall"]
    cards = [
        ("Overall Health", f"{overall['overall_health_score'] * 100:.0f}%", overall["overall_result"]),
        ("Relationship Accuracy", f"{overall['relationship_accuracy'] * 100:.0f}%", None),
        ("Agent Accuracy", f"{overall['agent_accuracy'] * 100:.0f}%", None),
        ("Parity Score", f"{overall['parity_score'] * 100:.0f}%", None),
        ("Engineering Memory", f"{overall['engineering_memory_score'] * 100:.0f}%", None),
        ("Frontier Generator", f"{overall['frontier_score'] * 100:.0f}%", None),
    ]
    html_cards = []
    for label, value, badge in cards:
        badge_html = ""
        if badge:
            color = VERDICT_COLOR.get(badge, "#8a8f98")
            badge_html = f'<span class="badge" style="background:{color}">{_esc(badge)}</span>'
        html_cards.append(
            f'<div class="card"><div class="card-label">{_esc(label)}</div>'
            f'<div class="card-value">{_esc(value)}{badge_html}</div></div>'
        )
    return f'<div class="cards">{"".join(html_cards)}</div>'


def _section_table(section: dict[str, Any] | None) -> str:
    if section is None or not section["checks"]:
        reason = section.get("skipped_reason") if section else "section missing"
        return f'<p class="muted">No checks recorded{f": {_esc(reason)}" if reason else "."}</p>'
    rows = []
    for check in section["checks"]:
        color = VERDICT_COLOR.get(check["verdict"], "#8a8f98")
        expected = check.get("expected")
        actual = check.get("actual")
        detail = check.get("detail") or ""
        rows.append(
            "<tr>"
            f'<td><span class="dot" style="background:{color}"></span>{_esc(check["name"])}</td>'
            f'<td><span class="verdict" style="color:{color}">{_esc(check["verdict"])}</span></td>'
            f"<td class=\"mono\">{_esc(expected) if expected is not None else ''}</td>"
            f"<td class=\"mono\">{_esc(actual) if actual is not None else ''}</td>"
            f"<td>{_esc(detail)}</td>"
            "</tr>"
        )
    return (
        '<table class="checks"><thead><tr><th>Check</th><th>Verdict</th>'
        "<th>Expected</th><th>Actual</th><th>Detail</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _validation_sections(results: dict[str, Any]) -> str:
    blocks = []
    for section in results["sections"]:
        overall = section["overall"]
        color = "#1a8a5f" if overall == "PASS" else ("#c8442f" if overall == "FAIL" else "#8a8f98")
        counts = section["counts"]
        blocks.append(
            f'<section class="validation" id="v{section["validation_id"]}">'
            f'<div class="validation-header">'
            f'<h3>Validation {section["validation_id"]} — {_esc(section["title"])}</h3>'
            f'<span class="pill" style="background:{color}">{_esc(overall)}</span>'
            f'<span class="muted">{counts["PASS"]} pass / {counts["FAIL"]} fail / '
            f'{counts["MISSING"]} missing / {counts["UNEXPECTED"]} unexpected</span>'
            "</div>"
            f"{_section_table(section)}"
            "</section>"
        )
    return "".join(blocks)


def _repository_matrix(results: dict[str, Any]) -> str:
    graph_section = _section_by_id(results, 1)
    parity_section = _section_by_id(results, 7)
    memory_section = _section_by_id(results, 6)
    if graph_section is None:
        return '<p class="muted">No repository graph data.</p>'

    repo_names: list[str] = []
    for check in graph_section["checks"]:
        if check["name"].endswith(": tracked in GraphForge") and check["verdict"] == "PASS":
            name = check["name"].split(":")[0]
            if name not in repo_names:
                repo_names.append(name)

    def verdict_for(section: dict[str, Any] | None, repo: str, *substrings: str) -> str | None:
        matches = _checks_matching(section, repo, *substrings)
        if not matches:
            return None
        return "PASS" if all(m["verdict"] == "PASS" for m in matches) else "FAIL"

    rows = []
    for repo in repo_names:
        node_v = verdict_for(graph_section, repo, "node_count")
        edge_v = verdict_for(graph_section, repo, "edge_count")
        parity_checks = _checks_matching(parity_section, repo, "similarity")
        parity_value = parity_checks[0]["actual"] if parity_checks else "—"
        memory_v = verdict_for(memory_section, repo, "relationships persisted")

        def cell(v: str | None) -> str:
            if v is None:
                return '<td class="muted">—</td>'
            color = VERDICT_COLOR.get(v, "#8a8f98")
            return f'<td><span class="dot" style="background:{color}"></span>{v}</td>'

        rows.append(
            f"<tr><td>{_esc(repo)}</td>{cell(node_v)}{cell(edge_v)}"
            f'<td class="mono">{_esc(parity_value)}</td>{cell(memory_v)}</tr>'
        )

    return (
        '<table class="matrix"><thead><tr><th>Repository</th><th>Node Count</th>'
        "<th>Edge Count</th><th>Parity</th><th>Engineering Memory</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _agent_scorecards(results: dict[str, Any]) -> str:
    cards = []
    for validation_id, label in ((3, "Repository Understanding"), (4, "Dependency Query"), (5, "Impact Analysis")):
        section = _section_by_id(results, validation_id)
        if section is None:
            continue
        rate = section["pass_rate"] * 100
        color = "#1a8a5f" if section["overall"] == "PASS" else "#c8442f"
        cards.append(
            f'<div class="scorecard"><div class="scorecard-title">{_esc(label)} Agent</div>'
            f'<div class="scorecard-ring" style="--pct:{rate:.0f}; --color:{color}">'
            f'<span>{rate:.0f}%</span></div>'
            f'<div class="muted">{section["counts"]["PASS"]}/{len(section["checks"])} checks</div></div>'
        )
    return f'<div class="scorecards">{"".join(cards)}</div>'


def _relationship_graph_svg(results: dict[str, Any]) -> str:
    section = _section_by_id(results, 2)
    if section is None:
        return ""
    edges: list[tuple[str, str]] = []
    for check in section["checks"]:
        if check["name"].startswith("cross-repo edge:") and check["verdict"] == "PASS":
            # "cross-repo edge: source -TYPE-> target"
            try:
                rest = check["name"].split("cross-repo edge:", 1)[1].strip()
                source, remainder = rest.split(" -", 1)
                _type, target = remainder.split("-> ", 1)
                edges.append((source.strip(), target.strip()))
            except ValueError:
                continue
    if not edges:
        return '<p class="muted">No cross-repository edges currently present — see Known Gaps in the validation guide.</p>'

    nodes = sorted({n for edge in edges for n in edge})
    import math

    cx, cy, r = 320, 260, 210
    positions = {
        node: (
            cx + r * math.cos(2 * math.pi * i / len(nodes) - math.pi / 2),
            cy + r * math.sin(2 * math.pi * i / len(nodes) - math.pi / 2),
        )
        for i, node in enumerate(nodes)
    }
    lines = []
    for source, target in edges:
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="var(--edge)" stroke-width="1.5" marker-end="url(#arrow)" opacity="0.7" />'
        )
    circles = []
    for node, (x, y) in positions.items():
        circles.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="var(--accent)" />'
            f'<text x="{x:.1f}" y="{y - 10:.1f}" text-anchor="middle" class="node-label">{_esc(node)}</text>'
        )
    return (
        '<svg viewBox="0 0 640 520" class="graph-svg" role="img" aria-label="Cross-repository dependency graph">'
        "<defs><marker id=\"arrow\" markerWidth=\"8\" markerHeight=\"8\" refX=\"7\" refY=\"4\" "
        'orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--edge)" /></marker></defs>'
        f"{''.join(lines)}{''.join(circles)}"
        "</svg>"
    )


def _kafka_topology_block() -> str:
    # Real detected KafkaTopic count is 0 (see Known Gaps) — this renders
    # the *documented* topology from event-contracts for context, clearly
    # labeled as not-yet-detected rather than presented as a live fact.
    rows = [
        ("payment.completed", "payment-service-java", "inventory-service-python, shipping-service-java"),
        ("inventory.updated", "inventory-service-python", "notification-service-python"),
        ("order.created", "order-service-python", "analytics-pipeline-python"),
        ("order.cancelled", "(none — declared, unimplemented)", "(none)"),
    ]
    body = "".join(
        f"<tr><td class=\"mono\">{_esc(t)}</td><td>{_esc(p)}</td><td>{_esc(c)}</td></tr>"
        for t, p, c in rows
    )
    return (
        '<p class="muted">GraphForge currently detects <strong>0</strong> KafkaTopic nodes across '
        "this suite (see Validation 1 / Known Gaps). The topology below is the "
        "<em>documented</em> design from event-contracts, shown for context — not a live "
        "GraphForge finding.</p>"
        '<table class="matrix"><thead><tr><th>Topic</th><th>Producer</th><th>Consumers</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _performance_chart(results: dict[str, Any]) -> str:
    table = results.get("timing_table", [])
    if not table:
        return '<p class="muted">No timing data collected.</p>'
    max_duration = max(row["duration_seconds"] for row in table) or 1
    rows = []
    for row in sorted(table, key=lambda r: -r["duration_seconds"]):
        pct = (row["duration_seconds"] / max_duration) * 100
        rows.append(
            '<div class="bar-row">'
            f'<div class="bar-label">{_esc(row["repository"])} — {_esc(row["phase"])}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div>'
            f'<span class="bar-value">{row["duration_seconds"]:.2f}s</span></div>'
            "</div>"
        )
    return f'<div class="bars">{"".join(rows)}</div>'


def _recommendation(results: dict[str, Any]) -> str:
    overall = results["overall"]
    verdict = overall["overall_result"]
    color = "#1a8a5f" if verdict == "PASS" else "#c8442f"
    text = (
        "GraphForge's Engineering Intelligence Platform is behaving consistently with the "
        "captured baseline across every gating validation. Safe to treat this run as a clean "
        "regression checkpoint."
        if verdict == "PASS"
        else "One or more gating validations regressed against the captured baseline. Review the "
        "FAIL/MISSING/UNEXPECTED checks above before treating this as a clean state — some may "
        "be expected fixture updates (see docs/validation-guide.md), others may be real regressions."
    )
    return (
        f'<div class="recommendation" style="border-color:{color}">'
        f'<div class="recommendation-verdict" style="color:{color}">{_esc(verdict)}</div>'
        f"<p>{text}</p>"
        "<p class=\"muted\">Known, documented gaps (not regressions — see docs/validation-guide.md "
        '"Known Gaps"): Kafka topic detection requires an inline string literal in the same class '
        "as the Kafka client (no Python extractor exists at all); Feign-based cross-repository "
        "matching doesn't bridge language-suffixed repository names "
        "(<code>inventory-service</code> vs. <code>inventory-service-python</code>); Impact "
        "Analysis's blast-radius traversal is scoped to a single repository's Neo4j subgraph, so "
        "cross-repository edge types in <code>_IMPACT_EDGE_TYPES</code> are never actually walked "
        "today.</p>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #f7f6f3; --surface: #ffffff; --text: #1c1b1a; --text-muted: #6b6862;
  --border: #e4e1da; --accent: #2f6f4f; --edge: #9a958a; --code-bg: #f0efe9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181a; --surface: #1f2023; --text: #e9e7e2; --text-muted: #9b9891;
    --border: #34363a; --accent: #59b98a; --edge: #5b5e63; --code-bg: #26282b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 40px 24px 80px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 48px 0 16px; border-top: 1px solid var(--border); padding-top: 32px; }
h3 { font-size: 1rem; margin: 0; }
.subtitle { color: var(--text-muted); margin: 0 0 32px; font-size: 0.92rem; }
.muted { color: var(--text-muted); font-size: 0.88rem; }
.mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.85rem; }
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; background: var(--code-bg); padding: 1px 5px; border-radius: 4px; font-size: 0.85em; }

.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
.card-label { color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }
.card-value { font-size: 1.6rem; font-weight: 600; margin-top: 4px; display: flex; align-items: center; gap: 8px; }
.badge { font-size: 0.65rem; color: white; padding: 2px 8px; border-radius: 999px; font-weight: 600; letter-spacing: 0.03em; }

.validation { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; margin-bottom: 14px; }
.validation-header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.pill { color: white; font-size: 0.72rem; font-weight: 600; padding: 3px 10px; border-radius: 999px; letter-spacing: 0.03em; }

table { width: 100%; border-collapse: collapse; font-size: 0.87rem; }
table.checks th, table.checks td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
table.matrix th, table.matrix td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
table.checks th, table.matrix th { color: var(--text-muted); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; }
.verdict { font-weight: 700; font-size: 0.82rem; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }

.scorecards { display: flex; gap: 20px; flex-wrap: wrap; }
.scorecard { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px; text-align: center; width: 180px; }
.scorecard-title { font-size: 0.85rem; font-weight: 600; margin-bottom: 12px; }
.scorecard-ring {
  width: 88px; height: 88px; border-radius: 50%; margin: 0 auto 10px;
  display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1.1rem;
  background: conic-gradient(var(--color) calc(var(--pct) * 1%), var(--border) 0);
}
.scorecard-ring span { background: var(--surface); width: 66px; height: 66px; border-radius: 50%; display: flex; align-items: center; justify-content: center; }

.bars { display: flex; flex-direction: column; gap: 8px; }
.bar-row { display: grid; grid-template-columns: 260px 1fr; gap: 12px; align-items: center; }
.bar-label { font-size: 0.82rem; color: var(--text-muted); text-align: right; }
.bar-track { background: var(--code-bg); border-radius: 6px; height: 22px; position: relative; overflow: hidden; }
.bar-fill { background: var(--accent); height: 100%; border-radius: 6px; }
.bar-value { position: absolute; right: 8px; top: 2px; font-size: 0.78rem; font-family: ui-monospace, monospace; }

.graph-svg { width: 100%; max-width: 640px; height: auto; display: block; margin: 0 auto; }
.node-label { font-size: 10px; fill: var(--text); }

.recommendation { background: var(--surface); border: 1px solid var(--border); border-left-width: 5px; border-radius: 10px; padding: 20px 24px; }
.recommendation-verdict { font-weight: 800; font-size: 1.1rem; letter-spacing: 0.03em; margin-bottom: 6px; }

.overflow { overflow-x: auto; }
"""


def render(results: dict[str, Any], output_path: Path) -> None:
    overall = results["overall"]
    started = results.get("started_at", "")
    duration = results.get("duration_seconds", 0)

    body = f"""
<div class="wrap">
  <h1>GraphForge Engineering Intelligence — Regression Report</h1>
  <p class="subtitle">Run {_esc(results.get('run_id', ''))} · started {_esc(started)} ·
     completed in {duration}s · target {_esc(results.get('api_base_url', ''))}</p>

  {_summary_cards(results)}

  <h2>Overall Recommendation</h2>
  {_recommendation(results)}

  <h2>Repository Matrix</h2>
  <div class="overflow">{_repository_matrix(results)}</div>

  <h2>Agent Scorecards</h2>
  {_agent_scorecards(results)}

  <h2>Cross-Repository Dependency Graph</h2>
  {_relationship_graph_svg(results)}

  <h2>Kafka Topology</h2>
  {_kafka_topology_block()}

  <h2>Performance</h2>
  {_performance_chart(results)}

  <h2>All Validations</h2>
  {_validation_sections(results)}
</div>
"""

    document = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>GraphForge Regression Report</title>"
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )
    output_path.write_text(document)


if __name__ == "__main__":
    import json

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "reports" / "latest.json"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".html")
    render(json.loads(src.read_text()), dst)
    print(f"Wrote {dst}")
