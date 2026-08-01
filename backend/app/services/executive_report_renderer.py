"""Executive Report HTML Renderer — deterministic HTML template generation.

Produces a self-contained HTML dashboard from structured ExecutiveReportData.
No LLM calls, no external resources — pure template rendering with inline CSS.
The output renders inside a sandboxed iframe (same as existing workflow reports)
and supports both light/dark themes via CSS custom properties and media queries.
"""

from __future__ import annotations

from typing import Any


def render_executive_html(data: dict[str, Any]) -> str:
    """Render a self-contained executive HTML dashboard from structured data.

    Args:
        data: A dict matching the ExecutiveReportData schema from the
              executive_report router.

    Returns:
        A complete HTML document string with inline CSS, no external deps.
    """
    return _TEMPLATE_HEAD + _render_body(data) + _TEMPLATE_TAIL


_TEMPLATE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Executive Workflow Report</title>
<style>
:root {
  --bg: #f8fafc; --surface: #ffffff; --surface-raised: #f1f5f9;
  --fg: #0f172a; --fg-secondary: #334155; --fg-muted: #64748b;
  --line: #e2e8f0; --accent: #8425ff; --accent-light: #f3e8ff;
  --success: #10b981; --success-bg: #d1fae5;
  --warning: #f59e0b; --warning-bg: #fef3c7;
  --danger: #ef4444; --danger-bg: #fee2e2;
  --info: #0ea5e9; --info-bg: #e0f2fe;
  --radius: 0.75rem; --shadow: 0 1px 3px rgba(0,0,0,0.08);
  --font: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #020617; --surface: #0f172a; --surface-raised: #1e293b;
    --fg: #f1f5f9; --fg-secondary: #cbd5e1; --fg-muted: #94a3b8;
    --line: #334155; --accent: #a855f7; --accent-light: #2d1b4e;
    --success: #34d399; --success-bg: #064e3b;
    --warning: #fbbf24; --warning-bg: #451a03;
    --danger: #f87171; --danger-bg: #450a0a;
    --info: #38bdf8; --info-bg: #0c4a6e;
    --shadow: 0 1px 3px rgba(0,0,0,0.3);
  }
}
</style>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: var(--font); background: var(--bg); color: var(--fg);
  line-height: 1.6; padding: 2rem; max-width: 1200px; margin: 0 auto;
}
h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 0.25rem; }
h2 { font-size: 1.125rem; font-weight: 600; color: var(--fg); margin-bottom: 0.75rem; }
h3 { font-size: 0.875rem; font-weight: 600; color: var(--fg-secondary); margin-bottom: 0.5rem; }
.subtitle { color: var(--fg-muted); font-size: 0.875rem; }
.card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 1.25rem;
  box-shadow: var(--shadow); margin-bottom: 1rem;
}
.grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
.grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; }
@media (max-width: 768px) {
  .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
  body { padding: 1rem; }
}
</style>
<style>
.stat-card {
  background: var(--surface-raised); border-radius: 0.5rem;
  padding: 0.75rem 1rem;
}
.stat-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--fg-muted); font-weight: 500; }
.stat-value { font-size: 1.25rem; font-weight: 700; font-family: var(--font-mono);
  color: var(--fg); margin-top: 0.125rem; }
.stat-hint { font-size: 0.7rem; color: var(--fg-muted); margin-top: 0.125rem; }
.badge {
  display: inline-flex; align-items: center; padding: 0.25rem 0.625rem;
  border-radius: 9999px; font-size: 0.7rem; font-weight: 600;
}
.badge-success { background: var(--success-bg); color: var(--success); }
.badge-warning { background: var(--warning-bg); color: var(--warning); }
.badge-danger { background: var(--danger-bg); color: var(--danger); }
.badge-info { background: var(--info-bg); color: var(--info); }
.badge-neutral { background: var(--surface-raised); color: var(--fg-muted); }
.section { margin-bottom: 1.5rem; }
.section-header {
  display: flex; align-items: center; justify-content: space-between;
  cursor: pointer; user-select: none; padding: 0.5rem 0;
}
.section-header:hover h2 { color: var(--accent); }
.chevron { transition: transform 0.2s ease; font-size: 1rem; color: var(--fg-muted); }
.collapsed .chevron { transform: rotate(-90deg); }
.collapsed .section-content { display: none; }
</style>
<style>
/* Timeline */
.timeline { display: flex; align-items: center; gap: 0; padding: 1rem 0; overflow-x: auto; }
.timeline-stage {
  display: flex; flex-direction: column; align-items: center;
  flex: 1; min-width: 100px; position: relative;
}
.timeline-dot {
  width: 2rem; height: 2rem; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 0.75rem; font-weight: 700; color: white; z-index: 1;
}
.timeline-dot.completed { background: var(--success); }
.timeline-dot.running { background: var(--info); animation: pulse 1.5s infinite; }
.timeline-dot.failed { background: var(--danger); }
.timeline-dot.skipped { background: var(--fg-muted); opacity: 0.5; }
.timeline-dot.pending { background: var(--line); color: var(--fg-muted); }
.timeline-label { font-size: 0.7rem; margin-top: 0.5rem; color: var(--fg-muted);
  text-transform: capitalize; text-align: center; }
.timeline-connector {
  position: absolute; top: 1rem; left: 50%; width: 100%;
  height: 2px; background: var(--line); z-index: 0;
}
.timeline-stage:last-child .timeline-connector { display: none; }
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(14, 165, 233, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(14, 165, 233, 0); }
}
</style>
<style>
/* Bar charts */
.chart-container { padding: 0.5rem 0; }
.bar-row { display: flex; align-items: center; margin-bottom: 0.5rem; gap: 0.5rem; }
.bar-label { font-size: 0.7rem; color: var(--fg-muted); min-width: 100px;
  text-transform: capitalize; white-space: nowrap; }
.bar-track { flex: 1; height: 1.25rem; background: var(--surface-raised);
  border-radius: 0.25rem; overflow: hidden; position: relative; }
.bar-fill { height: 100%; border-radius: 0.25rem; transition: width 0.3s ease;
  min-width: 2px; }
.bar-value { font-size: 0.65rem; font-family: var(--font-mono); color: var(--fg-muted);
  min-width: 60px; text-align: right; }
.bar-fill.accent { background: var(--accent); }
.bar-fill.success { background: var(--success); }
.bar-fill.info { background: var(--info); }
.bar-fill.warning { background: var(--warning); }

/* Review table */
.review-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
.review-table th { text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--line);
  color: var(--fg-muted); font-weight: 500; font-size: 0.7rem; text-transform: uppercase; }
.review-table td { padding: 0.5rem; border-bottom: 1px solid var(--line); color: var(--fg-secondary); }
.review-table tr:last-child td { border-bottom: none; }

/* Recommendations */
.rec-item { padding: 0.5rem 0.75rem; border-left: 3px solid var(--line);
  margin-bottom: 0.5rem; font-size: 0.8rem; color: var(--fg-secondary); }
.rec-item.risk { border-color: var(--danger); }
.rec-item.action { border-color: var(--info); }
.rec-item.blocker { border-color: var(--warning); }
</style>
<style>
/* Print styles */
@media print {
  body { padding: 0.5rem; max-width: 100%; background: white; color: #0f172a; }
  .card { box-shadow: none; border: 1px solid #e2e8f0; break-inside: avoid; }
  .section { break-inside: avoid; }
  .collapsed .section-content { display: block !important; }
  .chevron { display: none; }
  .no-print { display: none; }
  @page { margin: 1cm; }
}
</style>
</head>
<body>
"""

_TEMPLATE_TAIL = """
<script>
document.querySelectorAll('.section-header').forEach(function(header) {
  header.addEventListener('click', function() {
    this.parentElement.classList.toggle('collapsed');
  });
});
</script>
</body>
</html>"""


def _esc(text: str | None) -> str:
    """HTML-escape text."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _format_duration(ms: int | None) -> str:
    """Format milliseconds into human-readable duration."""
    if ms is None:
        return "—"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"


def _format_cost(cost: float) -> str:
    """Format USD cost."""
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _format_tokens(tokens: int) -> str:
    """Format token count."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    return str(tokens)


def _status_badge(status: str) -> str:
    """Render a status badge."""
    tone_map = {
        "completed": "success", "approved": "success", "ready": "success",
        "pass": "success", "running": "info", "in_progress": "info",
        "conditional": "warning", "partial": "warning", "warning": "warning",
        "failed": "danger", "not_ready": "danger", "danger": "danger",
        "fail": "danger",
    }
    tone = tone_map.get(status.lower(), "neutral")
    return f'<span class="badge badge-{tone}">{_esc(status.replace("_", " ").title())}</span>'


def _render_executive_summary(data: dict[str, Any]) -> str:
    """Render the executive summary section."""
    confidence = data.get("overall_confidence")
    confidence_pct = f"{confidence * 100:.0f}%" if confidence else "—"

    return f"""
<div class="section">
  <div class="section-header"><h2>Executive Summary</h2><span class="chevron">&#9660;</span></div>
  <div class="section-content">
    <div class="card">
      <h1>{_esc(data.get("workflow_title", "Workflow Report"))}</h1>
      <p class="subtitle" style="margin-bottom: 1rem;">{_esc(data.get("original_prompt", "")[:200])}</p>
      <div class="grid-4">
        <div class="stat-card">
          <div class="stat-label">Status</div>
          <div class="stat-value">{_status_badge(data.get("status", "unknown"))}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Duration</div>
          <div class="stat-value">{_format_duration(data.get("duration_ms"))}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">AI Cost</div>
          <div class="stat-value">{_format_cost(data.get("total_cost_usd", 0))}</div>
          <div class="stat-hint">{_format_tokens(data.get("total_tokens", 0))} tokens</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Confidence</div>
          <div class="stat-value">{confidence_pct}</div>
        </div>
      </div>
    </div>
  </div>
</div>"""


def _render_timeline(data: dict[str, Any]) -> str:
    """Render the workflow timeline section."""
    stages = data.get("stages", [])
    if not stages:
        return ""

    # Define standard stage order for display
    stage_order = [
        "context_discovery", "planning", "development",
        "testing", "documentation_planning", "engineering_review",
    ]
    stage_map = {s["stage"]: s for s in stages}

    dots = []
    for stage_name in stage_order:
        stage = stage_map.get(stage_name)
        status = stage["status"] if stage else "pending"
        status_class = status if status in ("completed", "running", "failed") else "pending"
        if stage and status not in ("completed", "running", "failed"):
            status_class = "skipped"

        icon = {"completed": "&#10003;", "running": "&#9679;", "failed": "&#10005;",
                "skipped": "&#8212;", "pending": "&#183;"}.get(status_class, "&#183;")

        label = stage_name.replace("_", " ")
        dots.append(f"""
      <div class="timeline-stage">
        <div class="timeline-connector"></div>
        <div class="timeline-dot {status_class}">{icon}</div>
        <div class="timeline-label">{_esc(label)}</div>
      </div>""")

    return f"""
<div class="section">
  <div class="section-header"><h2>Workflow Timeline</h2><span class="chevron">&#9660;</span></div>
  <div class="section-content">
    <div class="card">
      <div class="timeline">{"".join(dots)}
      </div>
    </div>
  </div>
</div>"""


def _render_ai_metrics(data: dict[str, Any]) -> str:
    """Render AI usage metrics section."""
    stages = data.get("stages", [])
    if not stages:
        return ""

    rows = []
    for s in stages:
        rows.append(f"""
        <tr>
          <td>{_esc(s["stage"].replace("_", " ").title())}</td>
          <td><code>{_esc(s.get("model") or "—")}</code></td>
          <td style="font-family:var(--font-mono)">{_format_tokens(s.get("total_tokens", 0))}</td>
          <td style="font-family:var(--font-mono)">{_format_duration(s.get("latency_ms"))}</td>
          <td style="font-family:var(--font-mono)">{_format_cost(s.get("estimated_cost_usd", 0))}</td>
        </tr>""")

    return f"""
<div class="section">
  <div class="section-header"><h2>AI Usage Metrics</h2><span class="chevron">&#9660;</span></div>
  <div class="section-content">
    <div class="card">
      <div class="grid-3" style="margin-bottom: 1rem;">
        <div class="stat-card">
          <div class="stat-label">Primary Model</div>
          <div class="stat-value" style="font-size:0.85rem">{_esc(data.get("primary_model") or "—")}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Total LLM Calls</div>
          <div class="stat-value">{data.get("total_llm_calls", 0)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Provider</div>
          <div class="stat-value" style="font-size:0.85rem">{_esc(data.get("primary_provider") or "—")}</div>
        </div>
      </div>
      <table class="review-table">
        <thead><tr><th>Stage</th><th>Model</th><th>Tokens</th><th>Latency</th><th>Cost</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>
  </div>
</div>"""


def _render_charts(data: dict[str, Any]) -> str:
    """Render bar charts for execution time, tokens, and cost by stage."""
    stages = data.get("stages", [])
    if not stages:
        return ""

    # Compute max values for scaling
    max_duration = max((s.get("duration_ms") or 0) for s in stages) or 1
    max_tokens = max((s.get("total_tokens") or 0) for s in stages) or 1
    max_cost = max((s.get("estimated_cost_usd") or 0) for s in stages) or 1

    duration_bars = []
    token_bars = []
    cost_bars = []

    for s in stages:
        label = s["stage"].replace("_", " ").title()
        dur = s.get("duration_ms") or 0
        tok = s.get("total_tokens") or 0
        cost = s.get("estimated_cost_usd") or 0

        dur_pct = (dur / max_duration) * 100
        tok_pct = (tok / max_tokens) * 100
        cost_pct = (cost / max_cost) * 100

        duration_bars.append(
            f'<div class="bar-row"><span class="bar-label">{_esc(label)}</span>'
            f'<div class="bar-track"><div class="bar-fill accent" style="width:{dur_pct:.1f}%"></div></div>'
            f'<span class="bar-value">{_format_duration(dur)}</span></div>'
        )
        token_bars.append(
            f'<div class="bar-row"><span class="bar-label">{_esc(label)}</span>'
            f'<div class="bar-track"><div class="bar-fill info" style="width:{tok_pct:.1f}%"></div></div>'
            f'<span class="bar-value">{_format_tokens(tok)}</span></div>'
        )
        cost_bars.append(
            f'<div class="bar-row"><span class="bar-label">{_esc(label)}</span>'
            f'<div class="bar-track"><div class="bar-fill success" style="width:{cost_pct:.1f}%"></div></div>'
            f'<span class="bar-value">{_format_cost(cost)}</span></div>'
        )

    return f"""
<div class="section">
  <div class="section-header"><h2>Performance Charts</h2><span class="chevron">&#9660;</span></div>
  <div class="section-content">
    <div class="card">
      <h3>Execution Time by Stage</h3>
      <div class="chart-container">{"".join(duration_bars)}</div>
    </div>
    <div class="card">
      <h3>Token Usage by Stage</h3>
      <div class="chart-container">{"".join(token_bars)}</div>
    </div>
    <div class="card">
      <h3>Cost by Stage</h3>
      <div class="chart-container">{"".join(cost_bars)}</div>
    </div>
  </div>
</div>"""


def _render_repository_impact(data: dict[str, Any]) -> str:
    """Render repository impact section."""
    impact = data.get("repository_impact", {})
    repos = impact.get("repositories_affected", [])
    files = impact.get("files_changed", 0)
    components = impact.get("components_affected", [])
    deps = impact.get("dependency_impact", [])

    if not repos and not files and not components:
        return ""

    repo_list = "".join(f"<li>{_esc(r)}</li>" for r in repos) if repos else "<li>—</li>"
    comp_list = "".join(f"<li>{_esc(c)}</li>" for c in components) if components else "<li>—</li>"
    dep_list = "".join(f"<li>{_esc(d)}</li>" for d in deps) if deps else "<li>None identified</li>"

    return f"""
<div class="section">
  <div class="section-header"><h2>Repository Impact</h2><span class="chevron">&#9660;</span></div>
  <div class="section-content">
    <div class="card">
      <div class="grid-3">
        <div class="stat-card">
          <div class="stat-label">Repositories</div>
          <div class="stat-value">{len(repos)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Files Changed</div>
          <div class="stat-value">{files}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Components</div>
          <div class="stat-value">{len(components)}</div>
        </div>
      </div>
      <div class="grid-3" style="margin-top: 1rem;">
        <div>
          <h3>Repositories Affected</h3>
          <ul style="font-size:0.8rem; color:var(--fg-secondary); padding-left:1rem;">
            {repo_list}
          </ul>
        </div>
        <div>
          <h3>Components Affected</h3>
          <ul style="font-size:0.8rem; color:var(--fg-secondary); padding-left:1rem;">
            {comp_list}
          </ul>
        </div>
        <div>
          <h3>Dependency Impact</h3>
          <ul style="font-size:0.8rem; color:var(--fg-secondary); padding-left:1rem;">
            {dep_list}
          </ul>
        </div>
      </div>
    </div>
  </div>
</div>"""


def _render_review_results(data: dict[str, Any]) -> str:
    """Render review results section."""
    reviews = data.get("review_results", [])
    if not reviews:
        return ""

    rows = []
    for r in reviews:
        status = r.get("status", "not_evaluated")
        issues = r.get("issues", [])
        issue_str = "; ".join(issues[:3]) if issues else "—"
        rows.append(f"""
        <tr>
          <td style="font-weight:500">{_esc(r.get("category", ""))}</td>
          <td>{_status_badge(status)}</td>
          <td>{_esc(r.get("summary", "") or issue_str)}</td>
        </tr>""")

    return f"""
<div class="section">
  <div class="section-header"><h2>Review Results</h2><span class="chevron">&#9660;</span></div>
  <div class="section-content">
    <div class="card">
      <table class="review-table">
        <thead><tr><th>Category</th><th>Status</th><th>Summary</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>
  </div>
</div>"""


def _render_recommendations(data: dict[str, Any]) -> str:
    """Render recommendations section."""
    rec = data.get("recommendations", {})
    merge_readiness = rec.get("merge_readiness", "not_evaluated")
    risks = rec.get("risks", [])
    actions = rec.get("next_actions", [])
    blockers = rec.get("blocking_items", [])

    if merge_readiness == "not_evaluated" and not risks and not actions:
        return ""

    risk_items = "".join(
        f'<div class="rec-item risk">{_esc(r)}</div>' for r in risks
    ) or '<div class="rec-item">No significant risks identified.</div>'

    action_items = "".join(
        f'<div class="rec-item action">{_esc(a)}</div>' for a in actions
    ) or '<div class="rec-item">No outstanding actions.</div>'

    blocker_items = "".join(
        f'<div class="rec-item blocker">{_esc(b)}</div>' for b in blockers
    ) if blockers else ""

    return f"""
<div class="section">
  <div class="section-header"><h2>Recommendations</h2><span class="chevron">&#9660;</span></div>
  <div class="section-content">
    <div class="card">
      <div style="margin-bottom: 1rem;">
        <span style="font-size:0.8rem; color:var(--fg-muted); margin-right:0.5rem;">Merge Readiness:</span>
        {_status_badge(merge_readiness)}
      </div>
      {f'<h3>Blocking Items</h3>{blocker_items}' if blocker_items else ""}
      <div class="grid-2">
        <div><h3>Risks</h3>{risk_items}</div>
        <div><h3>Next Actions</h3>{action_items}</div>
      </div>
    </div>
  </div>
</div>"""


def _render_body(data: dict[str, Any]) -> str:
    """Render the full body content from all sections."""
    sections = [
        _render_executive_summary(data),
        _render_timeline(data),
        _render_ai_metrics(data),
        _render_charts(data),
        _render_repository_impact(data),
        _render_review_results(data),
        _render_recommendations(data),
    ]
    return "\n".join(s for s in sections if s)
