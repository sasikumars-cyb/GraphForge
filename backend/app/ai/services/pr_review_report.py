"""Renders a stored `AIAnalysisResult` as a standalone PR Review report in
three formats: JSON, Markdown, and a self-contained HTML executive
dashboard.

Pure formatting only, same rule as `github_comment_formatter.py` next to
this file: no I/O, no DB, no HTTP. The HTML output embeds its own CSS/JS
inline (GraphForge's dark-theme palette, see frontend/src/theme/themes.ts)
so it renders correctly as a single downloaded/shared file with no build
step and no external requests.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime

from app.ai.schemas.analysis_result import AIAnalysisResult, Finding

_SEVERITY_ORDER = ("critical", "high", "medium", "low")
_SEVERITY_COLORS = {
    "critical": "#f43f5e",
    "high": "#f97316",
    "medium": "#eab308",
    "low": "#38bdf8",
}
_MERGE_LABELS = {
    "approve": ("Ready to Merge", "#22c55e"),
    "approve_with_comments": ("Merge with Comments", "#38bdf8"),
    "request_changes": ("Changes Required", "#f97316"),
    "block": ("Reject", "#f43f5e"),
}
_RISK_COLORS = {"low": "#38bdf8", "medium": "#eab308", "high": "#f97316"}


@dataclass(frozen=True)
class ReviewReportContext:
    """Everything the report needs beyond the AI result itself — PR
    identity and run metadata the LLM never produces."""

    repository: str
    pull_request_number: int
    pull_request_title: str
    head_ref: str
    base_ref: str
    analyzed_at: datetime
    model_used: str
    result: AIAnalysisResult


def _group_findings_by_severity(findings: list[Finding]) -> dict[str, list[Finding]]:
    grouped: dict[str, list[Finding]] = {sev: [] for sev in _SEVERITY_ORDER}
    for finding in findings:
        grouped.setdefault(finding.severity, []).append(finding)
    return grouped


def _category_scores(result: AIAnalysisResult) -> dict[str, float | None]:
    return {
        "Quality": result.quality_score,
        "Security": result.security_score,
        "Testing": result.testing_score,
        "Documentation": result.documentation_score,
        "Architecture": result.architecture_score,
        "Performance": result.performance_score,
        "Maintainability": result.maintainability_score,
    }


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def build_json_report(ctx: ReviewReportContext) -> dict:
    """Machine-readable report — the full `AIAnalysisResult` plus the PR
    identity/run metadata that isn't part of that schema."""
    result = ctx.result
    return {
        "executive_summary": {
            "repository": ctx.repository,
            "pull_request_number": ctx.pull_request_number,
            "pull_request_title": ctx.pull_request_title,
            "head_ref": ctx.head_ref,
            "base_ref": ctx.base_ref,
            "analyzed_at": ctx.analyzed_at.isoformat(),
            "model_used": ctx.model_used,
            "quality_score": result.quality_score,
            "risk_score": result.risk_score,
            "merge_recommendation": result.merge_recommendation,
            "confidence": result.confidence.model_dump(),
            "files_reviewed": len(result.file_reviews),
            "summary": result.executive_summary,
        },
        "metrics": _category_scores(result),
        "findings": [f.model_dump() for f in result.findings],
        "file_reviews": [fr.model_dump() for fr in result.file_reviews],
        "breaking_changes": [bc.model_dump() for bc in result.breaking_changes],
        "migration_advice": [ma.model_dump() for ma in result.migration_advice],
        "suggested_reviewers": [sr.model_dump() for sr in result.suggested_reviewers],
        "regression_tests": [rt.model_dump() for rt in result.regression_tests],
        "architecture_observations": result.architecture_observations,
        "maintainability_observations": result.maintainability_observations,
        "reliability_observations": result.reliability_observations,
        "testing_review": result.testing_review,
        "documentation_review": result.documentation_review,
        "positive_findings": result.positive_findings,
        "suggested_improvements": result.suggested_improvements,
        "release_coordination_plan": result.release_coordination_plan.model_dump(),
        "prompt_version": result.prompt_version,
    }


def render_json_report(ctx: ReviewReportContext) -> str:
    return json.dumps(build_json_report(ctx), indent=2, default=str)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def render_markdown_report(ctx: ReviewReportContext) -> str:
    result = ctx.result
    label, _ = _MERGE_LABELS.get(result.merge_recommendation or "", ("Not assessed", ""))
    lines = [
        f"# PR Review Report — {ctx.repository} #{ctx.pull_request_number}",
        "",
        f"**{ctx.pull_request_title}**",
        f"`{ctx.head_ref}` → `{ctx.base_ref}` · analyzed {ctx.analyzed_at.isoformat()} "
        f"· model `{ctx.model_used or 'unknown'}`",
        "",
        "## Executive Summary",
        "",
        result.executive_summary or "_No summary produced._",
        "",
        "## Merge Recommendation",
        "",
        f"**{label}**",
        "",
        "## Scores",
        "",
        "| Metric | Score |",
        "| --- | --- |",
    ]
    lines.append(f"| Quality | {_fmt_score(result.quality_score)} |")
    lines.append(f"| Risk | {_fmt_score(result.risk_score)} |")
    for name, score in _category_scores(result).items():
        if name == "Quality":
            continue
        lines.append(f"| {name} | {_fmt_score(score)} |")
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    grouped = _group_findings_by_severity(result.findings)
    any_findings = False
    for severity in _SEVERITY_ORDER:
        items = grouped.get(severity, [])
        if not items:
            continue
        any_findings = True
        lines.append(f"### {severity.capitalize()}")
        lines.append("")
        for f in items:
            lines.append(f"- **{f.title}** ({f.category}) — {f.description}")
        lines.append("")
    if not any_findings:
        lines.append("_No findings._")
        lines.append("")

    if result.file_reviews:
        lines.append("## Files Reviewed")
        lines.append("")
        lines.append("| File | Complexity | Risk | Summary |")
        lines.append("| --- | --- | --- | --- |")
        for fr in result.file_reviews:
            lines.append(f"| `{fr.file}` | {fr.complexity} | {fr.risk} | {fr.summary} |")
        lines.append("")

    if result.positive_findings:
        lines.append("## What Was Done Well")
        lines.append("")
        lines.extend(f"- {p}" for p in result.positive_findings)
        lines.append("")

    if result.suggested_improvements:
        lines.append("## Suggested Improvements")
        lines.append("")
        lines.extend(f"- {s}" for s in result.suggested_improvements)
        lines.append("")

    return "\n".join(lines)


def _fmt_score(score: float | None) -> str:
    return f"{score:.0f}" if score is not None else "N/A"


# ---------------------------------------------------------------------------
# HTML executive dashboard
# ---------------------------------------------------------------------------


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def _score_bar(label: str, score: float | None) -> str:
    if score is None:
        return (
            f'<div class="score-row"><span class="score-label">{_e(label)}</span>'
            f'<span class="score-value muted">N/A</span></div>'
        )
    color = "#22c55e" if score >= 75 else "#eab308" if score >= 50 else "#f43f5e"
    pct = max(0.0, min(100.0, score))
    return (
        f'<div class="score-row"><span class="score-label">{_e(label)}</span>'
        f'<div class="score-bar"><div class="score-bar-fill" '
        f'style="width:{pct:.0f}%;background:{color}"></div></div>'
        f'<span class="score-value">{score:.0f}</span></div>'
    )


def _finding_card(finding: Finding) -> str:
    color = _SEVERITY_COLORS.get(finding.severity, "#94a3b8")
    return (
        f'<div class="finding-card" data-severity="{finding.severity}" '
        f'style="border-left-color:{color}">'
        f'<div class="finding-header">'
        f'<span class="badge" style="background:{color}22;color:{color}">'
        f"{_e(finding.severity.upper())}</span>"
        f'<span class="finding-category">{_e(finding.category)}</span>'
        f"</div>"
        f'<div class="finding-title">{_e(finding.title)}</div>'
        f'<div class="finding-description">{_e(finding.description)}</div>'
        f'<div class="finding-confidence">Confidence: {finding.confidence.score * 100:.0f}%</div>'
        f"</div>"
    )


def _file_review_card(fr) -> str:
    risk_color = _RISK_COLORS.get(fr.risk, "#94a3b8")
    issues = "".join(f"<li>{_e(i)}</li>" for i in fr.issues) or "<li class='muted'>None noted</li>"
    suggestions = (
        "".join(f"<li>{_e(s)}</li>" for s in fr.suggestions) or "<li class='muted'>None</li>"
    )
    return (
        '<details class="file-card">'
        f'<summary><span class="file-path">{_e(fr.file)}</span>'
        f'<span class="badge" style="background:{risk_color}22;color:{risk_color}">'
        f"{_e(fr.risk.upper())} RISK</span>"
        f'<span class="badge muted">{_e(fr.complexity.upper())} COMPLEXITY</span></summary>'
        f'<div class="file-card-body">'
        f"<p>{_e(fr.summary)}</p>"
        f"<strong>Issues</strong><ul>{issues}</ul>"
        f"<strong>Suggestions</strong><ul>{suggestions}</ul>"
        f"</div></details>"
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #020617; --surface: #0f172a; --surface-2: #0b1222; --border: #1e293b;
    --text: #f1f5f9; --muted: #94a3b8; --accent: #8425ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.5;
  }}
  header {{
    position: sticky; top: 0; z-index: 10; background: rgba(2,6,23,0.92);
    backdrop-filter: blur(8px); border-bottom: 1px solid var(--border);
    padding: 16px 24px; display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 12px;
  }}
  header h1 {{ font-size: 16px; margin: 0; }}
  header .sub {{ color: var(--muted); font-size: 13px; }}
  main {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px; margin-bottom: 24px;
  }}
  .kpi-card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 16px; animation: fadein 0.4s ease-out;
  }}
  .kpi-card .kpi-label {{
    color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  }}
  .kpi-card .kpi-value {{ font-size: 28px; font-weight: 700; margin-top: 6px; }}
  @keyframes fadein {{
    from {{ opacity: 0; transform: translateY(4px); }}
    to {{ opacity: 1; transform: translateY(0); }}
  }}
  section {{ margin-bottom: 32px; }}
  h2 {{
    font-size: 15px; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--muted); margin-bottom: 12px;
  }}
  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px;
  }}
  .score-row {{ display: flex; align-items: center; gap: 12px; padding: 6px 0; }}
  .score-label {{ width: 140px; flex-shrink: 0; font-size: 13px; }}
  .score-bar {{
    flex: 1; height: 8px; background: var(--surface-2); border-radius: 4px; overflow: hidden;
  }}
  .score-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.6s ease-out; }}
  .score-value {{
    width: 40px; text-align: right; font-variant-numeric: tabular-nums; font-size: 13px;
  }}
  .muted {{ color: var(--muted); }}
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 600; margin-right: 6px;
  }}
  .badge.muted {{ background: var(--surface-2); color: var(--muted); }}
  .finding-card {{
    background: var(--surface); border: 1px solid var(--border); border-left: 3px solid;
    border-radius: 8px; padding: 12px 14px; margin-bottom: 10px;
  }}
  .finding-header {{ margin-bottom: 6px; }}
  .finding-category {{ color: var(--muted); font-size: 12px; text-transform: capitalize; }}
  .finding-title {{ font-weight: 600; margin-bottom: 4px; }}
  .finding-description {{ color: var(--muted); font-size: 13px; }}
  .finding-confidence {{ color: var(--muted); font-size: 11px; margin-top: 6px; }}
  .file-card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; margin-bottom: 8px; padding: 4px 14px;
  }}
  .file-card summary {{
    cursor: pointer; padding: 10px 0; display: flex;
    align-items: center; gap: 8px; list-style: none;
  }}
  .file-card summary::-webkit-details-marker {{ display: none; }}
  .file-path {{
    font-family: ui-monospace, SFMono-Regular, monospace; font-size: 13px; flex: 1;
  }}
  .file-card-body {{ padding: 0 0 14px 0; font-size: 13px; color: var(--muted); }}
  .file-card-body ul {{ margin: 4px 0 12px; padding-left: 18px; }}
  .merge-banner {{
    border-radius: 12px; padding: 18px; display: flex;
    align-items: center; justify-content: space-between;
    border: 1px solid var(--border); margin-bottom: 24px;
  }}
  .merge-banner .label {{ font-size: 20px; font-weight: 700; }}
  input#search {{
    width: 100%; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--surface-2); color: var(--text); margin-bottom: 12px; font-size: 13px;
  }}
  .filter-row {{ display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }}
  .filter-chip {{
    padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--muted); font-size: 12px; cursor: pointer; user-select: none;
  }}
  .filter-chip.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
  ul.plain {{ padding-left: 18px; margin: 0; }}
  ul.plain li {{ margin-bottom: 4px; font-size: 13px; }}
  @media print {{
    header {{ position: static; }}
    .filter-row, input#search {{ display: none; }}
    .file-card {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>
<header>
  <div>
    <h1>{repository} · PR #{pr_number}</h1>
    <div class="sub">{pr_title}</div>
  </div>
  <div class="sub">{head_ref} → {base_ref} · {analyzed_at} · {model_used}</div>
</header>
<main>

<div class="merge-banner" style="border-left: 4px solid {merge_color}">
  <div>
    <div class="muted" style="font-size:12px;text-transform:uppercase;">Merge Recommendation</div>
    <div class="label" style="color:{merge_color}">{merge_label}</div>
  </div>
  <div class="muted" style="max-width:60%;font-size:13px;">{executive_summary}</div>
</div>

<div class="kpi-grid">
  <div class="kpi-card">
    <div class="kpi-label">Quality Score</div><div class="kpi-value">{quality_score}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Risk Score</div><div class="kpi-value">{risk_score}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Files Reviewed</div><div class="kpi-value">{files_reviewed}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Confidence</div><div class="kpi-value">{confidence_pct}%</div>
  </div>
</div>

<section>
  <h2>Metrics</h2>
  <div class="card">{score_rows}</div>
</section>

<section>
  <h2>Findings by Severity</h2>
  <div class="filter-row">
    <span class="filter-chip active" data-filter="all">All ({findings_total})</span>
    {severity_chips}
  </div>
  <input id="search" type="text" placeholder="Search findings...">
  <div id="findings-list">{findings_html}</div>
</section>

<section>
  <h2>Files Reviewed ({files_reviewed})</h2>
  {file_reviews_html}
</section>

<section>
  <h2>What Was Done Well</h2>
  <div class="card"><ul class="plain">{positive_findings_html}</ul></div>
</section>

<section>
  <h2>Suggested Improvements</h2>
  <div class="card"><ul class="plain">{suggested_improvements_html}</ul></div>
</section>

</main>
<script>
(function() {{
  var search = document.getElementById('search');
  var chips = document.querySelectorAll('.filter-chip');
  var cards = document.querySelectorAll('#findings-list .finding-card');
  var activeSeverity = 'all';

  function apply() {{
    var q = (search.value || '').toLowerCase();
    cards.forEach(function(card) {{
      var sev = card.getAttribute('data-severity');
      var text = card.textContent.toLowerCase();
      var matchesSeverity = activeSeverity === 'all' || sev === activeSeverity;
      var matchesSearch = !q || text.indexOf(q) !== -1;
      card.style.display = (matchesSeverity && matchesSearch) ? '' : 'none';
    }});
  }}

  chips.forEach(function(chip) {{
    chip.addEventListener('click', function() {{
      chips.forEach(function(c) {{ c.classList.remove('active'); }});
      chip.classList.add('active');
      activeSeverity = chip.getAttribute('data-filter');
      apply();
    }});
  }});
  search.addEventListener('input', apply);
}})();
</script>
</body>
</html>
"""


def render_html_report(ctx: ReviewReportContext) -> str:
    result = ctx.result
    merge_label, merge_color = _MERGE_LABELS.get(
        result.merge_recommendation or "", ("Not assessed", "#94a3b8")
    )
    grouped = _group_findings_by_severity(result.findings)

    findings_html = (
        "".join(_finding_card(f) for sev in _SEVERITY_ORDER for f in grouped.get(sev, []))
        or '<p class="muted">No findings.</p>'
    )

    severity_chips = "".join(
        f'<span class="filter-chip" data-filter="{sev}">{sev.capitalize()} '
        f"({len(grouped.get(sev, []))})</span>"
        for sev in _SEVERITY_ORDER
    )

    score_rows = "".join(
        _score_bar(name, score) for name, score in _category_scores(result).items()
    )

    file_reviews_html = (
        "".join(_file_review_card(fr) for fr in result.file_reviews)
        if result.file_reviews
        else '<p class="muted">No per-file review was produced.</p>'
    )

    positive_html = (
        "".join(f"<li>{_e(p)}</li>" for p in result.positive_findings)
        or '<li class="muted">None noted.</li>'
    )
    improvements_html = (
        "".join(f"<li>{_e(s)}</li>" for s in result.suggested_improvements)
        or '<li class="muted">None noted.</li>'
    )

    title = f"PR Review — {ctx.repository} #{ctx.pull_request_number}"

    return _HTML_TEMPLATE.format(
        title=_e(title),
        repository=_e(ctx.repository),
        pr_number=ctx.pull_request_number,
        pr_title=_e(ctx.pull_request_title),
        head_ref=_e(ctx.head_ref),
        base_ref=_e(ctx.base_ref),
        analyzed_at=_e(ctx.analyzed_at.isoformat()),
        model_used=_e(ctx.model_used or "unknown model"),
        merge_color=merge_color,
        merge_label=_e(merge_label),
        executive_summary=_e(result.executive_summary or "No summary produced."),
        quality_score=_fmt_score(result.quality_score),
        risk_score=_fmt_score(result.risk_score),
        files_reviewed=len(result.file_reviews),
        confidence_pct=f"{result.confidence.score * 100:.0f}",
        score_rows=score_rows,
        findings_total=len(result.findings),
        severity_chips=severity_chips,
        findings_html=findings_html,
        file_reviews_html=file_reviews_html,
        positive_findings_html=positive_html,
        suggested_improvements_html=improvements_html,
    )
