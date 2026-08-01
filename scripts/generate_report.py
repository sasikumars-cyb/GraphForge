#!/usr/bin/env python3
"""Generate a self-contained interactive HTML report of GraphForge activity.

Queries Postgres and Neo4j directly (no app server required) and writes a
single HTML file with embedded Chart.js (CDN), inline CSS, and inline JS.

Usage (from repo root):
    # Against the local Docker dev stack:
    python scripts/generate_report.py

    # Custom connection:
    python scripts/generate_report.py \
        --db-url "postgresql://graphforge:graphforge@localhost:5433/graphforge" \
        --neo4j-uri "bolt://localhost:7687" \
        --neo4j-user neo4j \
        --neo4j-password graphforge-dev \
        --output report.html

Dependencies (stdlib + already in backend venv):
    psycopg2-binary  OR  psycopg (psycopg3)  — try both
    neo4j            — neo4j Python driver

Run inside the backend container where deps are guaranteed:
    docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml \
        exec backend python /app/scripts/generate_report.py \
        --db-url postgresql://graphforge:graphforge@db:5432/graphforge \
        --neo4j-uri bolt://neo4j:7687 \
        --output /app/scripts/report.html
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _pg_connect(db_url: str):
    """Return a sync Postgres connection.

    Preference order:
    1. psycopg2
    2. psycopg (v3)
    3. asyncpg via a small sync wrapper (backend dependency in this repo)
    """
    try:
        import psycopg2

        return psycopg2.connect(db_url)
    except ImportError:
        pass
    try:
        import psycopg

        return psycopg.connect(db_url)
    except ImportError:
        pass

    try:
        import asyncpg

        return _AsyncpgSyncConnection(asyncpg, db_url)
    except ImportError:
        pass

    raise ImportError(
        "No PostgreSQL driver found. Install psycopg2-binary or psycopg:\n"
        "  pip install psycopg2-binary\n\n"
        "This repo also supports asyncpg fallback if run from backend env."
    )


class _AsyncpgSyncConnection:
    """Small sync facade over asyncpg for this one-off reporting script."""

    def __init__(self, asyncpg_module, db_url: str) -> None:
        self._loop = asyncio.new_event_loop()
        self._conn = self._loop.run_until_complete(asyncpg_module.connect(dsn=db_url))

    def cursor(self):
        return _AsyncpgSyncCursor(self._conn, self._loop)

    def close(self) -> None:
        self._loop.run_until_complete(self._conn.close())
        self._loop.close()


class _AsyncpgSyncCursor:
    def __init__(self, conn, loop) -> None:
        self._conn = conn
        self._loop = loop
        self._rows = []
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=()):
        self._rows = self._loop.run_until_complete(self._conn.fetch(sql, *(params or ())))
        if self._rows:
            self.description = [(key,) for key in self._rows[0].keys()]
        else:
            self.description = []

    def fetchall(self):
        return [tuple(row[key] for key in row.keys()) for row in self._rows]


def _fetchall(conn, sql: str, params=None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetchone(conn, sql: str, params=None) -> dict:
    rows = _fetchall(conn, sql, params)
    return rows[0] if rows else {}


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_postgres(db_url: str) -> dict:
    conn = _pg_connect(db_url)
    try:
        # ── Summary counts ────────────────────────────────────────────────
        summary = _fetchone(conn, """
            SELECT
                (SELECT count(*) FROM workflows)                                      AS total_workflows,
                (SELECT count(*) FROM workflows WHERE status = 'completed')           AS completed_workflows,
                (SELECT count(*) FROM agent_runs WHERE status = 'completed')          AS completed_runs,
                (SELECT count(*) FROM llm_invocations)                                AS total_llm_calls,
                (SELECT coalesce(sum(total_tokens), 0) FROM llm_invocations)          AS total_tokens,
                (SELECT coalesce(round(cast(sum(estimated_cost_usd) as numeric), 4), 0)
                 FROM llm_invocations)                                                AS total_cost_usd,
                (SELECT coalesce(round(cast(avg(latency_ms) as numeric), 0), 0)
                 FROM llm_invocations WHERE status = 'completed')                     AS avg_latency_ms,
                (SELECT count(*) FROM repositories)                                   AS indexed_repos
        """)

        # ── Cost by day (last 30 days) ────────────────────────────────────
        cost_by_day = _fetchall(conn, """
            SELECT
                date_trunc('day', started_at)::date::text  AS day,
                round(cast(sum(estimated_cost_usd) as numeric), 4) AS cost_usd,
                sum(total_tokens)                                   AS tokens
            FROM llm_invocations
            WHERE started_at >= now() - interval '30 days'
            GROUP BY 1
            ORDER BY 1
        """)

        # ── Cost by provider ─────────────────────────────────────────────
        cost_by_provider = _fetchall(conn, """
            SELECT
                provider,
                count(*)                                              AS calls,
                round(cast(sum(estimated_cost_usd) as numeric), 4)   AS cost_usd,
                sum(total_tokens)                                     AS tokens
            FROM llm_invocations
            GROUP BY provider
            ORDER BY cost_usd DESC
        """)

        # ── Cost by stage ─────────────────────────────────────────────────
        cost_by_stage = _fetchall(conn, """
            SELECT
                coalesce(stage, 'unknown')                            AS stage,
                count(*)                                              AS calls,
                round(cast(sum(estimated_cost_usd) as numeric), 4)   AS cost_usd,
                sum(total_tokens)                                     AS tokens
            FROM llm_invocations
            GROUP BY stage
            ORDER BY cost_usd DESC
        """)

        # ── Recent workflows ──────────────────────────────────────────────
        recent_workflows = _fetchall(conn, """
            SELECT
                w.id::text,
                w.title,
                w.status,
                w.current_stage,
                w.workflow_type,
                w.created_at::text,
                w.updated_at::text,
                coalesce(round(cast(sum(li.estimated_cost_usd) as numeric), 4), 0) AS cost_usd,
                coalesce(sum(li.total_tokens), 0)                                  AS tokens
            FROM workflows w
            LEFT JOIN agent_runs ar ON ar.workflow_id = w.id
            LEFT JOIN llm_invocations li ON li.run_id = ar.id
            GROUP BY w.id, w.title, w.status, w.current_stage,
                     w.workflow_type, w.created_at, w.updated_at
            ORDER BY w.created_at DESC
            LIMIT 20
        """)

        # ── Run success rate by stage ─────────────────────────────────────
        run_by_stage = _fetchall(conn, """
            SELECT
                coalesce(workflow_stage, goal)  AS stage,
                count(*)                        AS total,
                sum(case when status='completed' then 1 else 0 end) AS succeeded,
                sum(case when status='failed'    then 1 else 0 end) AS failed
            FROM agent_runs
            GROUP BY 1
            ORDER BY total DESC
        """)

        # ── Model usage ───────────────────────────────────────────────────
        model_usage = _fetchall(conn, """
            SELECT
                model,
                provider,
                count(*)                                              AS calls,
                round(cast(sum(estimated_cost_usd) as numeric), 4)   AS cost_usd
            FROM llm_invocations
            GROUP BY model, provider
            ORDER BY calls DESC
            LIMIT 10
        """)

        return {
            "summary": {k: float(v) if v is not None else 0 for k, v in summary.items()},
            "cost_by_day": cost_by_day,
            "cost_by_provider": cost_by_provider,
            "cost_by_stage": cost_by_stage,
            "recent_workflows": recent_workflows,
            "run_by_stage": run_by_stage,
            "model_usage": model_usage,
        }
    finally:
        conn.close()


def collect_neo4j(uri: str, user: str, password: str) -> dict:
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("Warning: neo4j driver not installed — graph metrics will be empty.", file=sys.stderr)
        return {"repos": [], "total_nodes": 0, "total_edges": 0}

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            # Components per repository
            repos = session.run("""
                MATCH (n)
                WHERE n.repository IS NOT NULL
                RETURN n.repository AS repo, count(n) AS components
                ORDER BY components DESC
            """).data()

            totals = session.run("""
                MATCH (n) WITH count(n) AS nodes
                MATCH ()-[r]->() WITH nodes, count(r) AS edges
                RETURN nodes, edges
            """).data()

        driver.close()
        total = totals[0] if totals else {"nodes": 0, "edges": 0}
        return {
            "repos": repos,
            "total_nodes": total.get("nodes", 0),
            "total_edges": total.get("edges", 0),
        }
    except Exception as exc:
        print(f"Warning: Neo4j query failed ({exc}) — graph metrics will be empty.", file=sys.stderr)
        return {"repos": [], "total_nodes": 0, "total_edges": 0}


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_STATUS_COLORS = {
    "completed": "#22c55e",
    "in_progress": "#3b82f6",
    "awaiting_approval": "#f59e0b",
    "awaiting_clarification": "#a855f7",
    "failed": "#ef4444",
    "rejected": "#ef4444",
}


def _status_badge(status: str) -> str:
    color = _STATUS_COLORS.get(status, "#6b7280")
    label = status.replace("_", " ").title()
    return (
        f'<span style="background:{color}22;color:{color};'
        f'padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:600">'
        f"{label}</span>"
    )


def render_html(pg: dict, neo: dict, generated_at: str) -> str:
    summary = pg["summary"]

    # Chart data as JSON for Chart.js
    days = [r["day"] for r in pg["cost_by_day"]]
    day_costs = [float(r["cost_usd"] or 0) for r in pg["cost_by_day"]]
    day_tokens = [int(r["tokens"] or 0) for r in pg["cost_by_day"]]

    providers = [r["provider"] or "unknown" for r in pg["cost_by_provider"]]
    provider_costs = [float(r["cost_usd"] or 0) for r in pg["cost_by_provider"]]

    stages = [r["stage"] for r in pg["cost_by_stage"]]
    stage_costs = [float(r["cost_usd"] or 0) for r in pg["cost_by_stage"]]

    neo_repos = [r["repo"] for r in neo["repos"]]
    neo_components = [int(r["components"]) for r in neo["repos"]]

    run_stages = [r["stage"] for r in pg["run_by_stage"]]
    run_succeeded = [int(r["succeeded"] or 0) for r in pg["run_by_stage"]]
    run_failed = [int(r["failed"] or 0) for r in pg["run_by_stage"]]

    workflow_rows = ""
    for w in pg["recent_workflows"]:
        created = (w.get("created_at") or "")[:10]
        cost = f"${float(w.get('cost_usd') or 0):.4f}"
        tokens = f"{int(w.get('tokens') or 0):,}"
        stage = (w.get("current_stage") or "").replace("_", " ").title()
        wtype = (w.get("workflow_type") or "").replace("_", " ").title()
        title = str(w.get("title") or "Untitled")[:80]
        badge = _status_badge(w.get("status") or "unknown")
        workflow_rows += f"""
        <tr>
          <td style="padding:8px 12px;color:#e2e8f0;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{title}">{title}</td>
          <td style="padding:8px 12px;text-align:center">{badge}</td>
          <td style="padding:8px 12px;color:#94a3b8;text-align:center">{stage}</td>
          <td style="padding:8px 12px;color:#94a3b8;text-align:center">{wtype}</td>
          <td style="padding:8px 12px;color:#94a3b8;text-align:center">{created}</td>
          <td style="padding:8px 12px;color:#22c55e;text-align:right;font-family:monospace">{cost}</td>
          <td style="padding:8px 12px;color:#94a3b8;text-align:right;font-family:monospace">{tokens}</td>
        </tr>"""

    model_rows = ""
    for m in pg["model_usage"]:
        model_rows += f"""
        <tr>
          <td style="padding:8px 12px;color:#e2e8f0;font-family:monospace">{m.get('model','')}</td>
          <td style="padding:8px 12px;color:#94a3b8">{m.get('provider','')}</td>
          <td style="padding:8px 12px;color:#94a3b8;text-align:right">{int(m.get('calls') or 0):,}</td>
          <td style="padding:8px 12px;color:#22c55e;text-align:right;font-family:monospace">${float(m.get('cost_usd') or 0):.4f}</td>
        </tr>"""

    total_cost = float(summary.get("total_cost_usd", 0))
    total_tokens = int(summary.get("total_tokens", 0))
    total_calls = int(summary.get("total_llm_calls", 0))
    avg_latency = int(summary.get("avg_latency_ms", 0))
    total_workflows = int(summary.get("total_workflows", 0))
    completed_wf = int(summary.get("completed_workflows", 0))
    completed_runs = int(summary.get("completed_runs", 0))
    indexed_repos = int(summary.get("indexed_repos", 0))
    avg_cost_per_workflow = (total_cost / total_workflows) if total_workflows else 0.0

    completion_rate = round(completed_wf / total_workflows * 100) if total_workflows else 0

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GraphForge — Activity Report</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
    .header {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
               border-bottom: 1px solid #1e293b; padding: 24px 32px;
               display: flex; align-items: center; justify-content: space-between; }}
    .logo {{ display: flex; align-items: center; gap: 12px; }}
    .logo-icon {{ width: 36px; height: 36px; background: linear-gradient(135deg, #6366f1, #8b5cf6);
                  border-radius: 8px; display: flex; align-items: center; justify-content: center;
                  font-size: 20px; }}
    .logo-text {{ font-size: 20px; font-weight: 700; color: #f1f5f9; }}
    .logo-sub {{ font-size: 12px; color: #64748b; margin-top: 2px; }}
    .gen-time {{ font-size: 12px; color: #475569; }}
    .main {{ max-width: 1400px; margin: 0 auto; padding: 32px; }}
    .section {{ margin-bottom: 32px; }}
    .section-header {{ display: flex; align-items: center; justify-content: space-between;
                       margin-bottom: 16px; cursor: pointer; user-select: none; }}
    .section-title {{ font-size: 16px; font-weight: 600; color: #f1f5f9;
                      display: flex; align-items: center; gap: 8px; }}
    .chevron {{ transition: transform 0.2s; color: #475569; font-size: 12px; }}
    .section-body {{ overflow: hidden; transition: max-height 0.3s ease; }}
    .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
    @media (max-width: 900px) {{ .grid-4, .grid-3 {{ grid-template-columns: repeat(2, 1fr); }} }}
    @media (max-width: 600px) {{ .grid-4, .grid-3, .grid-2 {{ grid-template-columns: 1fr; }} }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }}
    .stat-label {{ font-size: 12px; color: #64748b; text-transform: uppercase;
                   letter-spacing: 0.05em; margin-bottom: 8px; }}
    .stat-value {{ font-size: 28px; font-weight: 700; color: #f1f5f9; line-height: 1; }}
    .stat-sub {{ font-size: 12px; color: #475569; margin-top: 6px; }}
    .stat-accent {{ color: #6366f1; }}
    .chart-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px;
                   padding: 20px; }}
    .chart-title {{ font-size: 13px; font-weight: 600; color: #94a3b8;
                    margin-bottom: 16px; text-transform: uppercase; letter-spacing: 0.05em; }}
    canvas {{ max-height: 260px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ padding: 10px 12px; text-align: left; font-size: 11px; font-weight: 600;
          color: #475569; text-transform: uppercase; letter-spacing: 0.05em;
          border-bottom: 1px solid #334155; }}
    tr:hover td {{ background: #ffffff08; }}
    .divider {{ height: 1px; background: #1e293b; margin: 8px 0; }}
    .empty {{ color: #475569; font-size: 13px; text-align: center; padding: 32px; }}
    .badge-pill {{ display: inline-flex; align-items: center; gap: 6px;
                   padding: 4px 10px; border-radius: 9999px; font-size: 11px;
                   font-weight: 600; }}
  </style>
</head>
<body>

<div class="header">
  <div class="logo">
    <div class="logo-icon">⬡</div>
    <div>
      <div class="logo-text">GraphForge</div>
      <div class="logo-sub">Activity Report</div>
    </div>
  </div>
  <div class="gen-time">Generated {generated_at}</div>
</div>

<div class="main">

  <!-- ── Summary Cards ─────────────────────────────────────────────────── -->
  <section class="section">
    <div class="section-header" onclick="toggle('summary')">
      <div class="section-title">
        <span>📊</span> Overview
      </div>
      <span class="chevron" id="chevron-summary">▼</span>
    </div>
    <div class="section-body" id="body-summary">
      <div class="grid-4">
        <div class="card">
          <div class="stat-label">Workflows</div>
          <div class="stat-value">{total_workflows}</div>
          <div class="stat-sub"><span class="stat-accent">{completed_wf}</span> completed · {completion_rate}% rate</div>
        </div>
        <div class="card">
          <div class="stat-label">Agent Runs</div>
          <div class="stat-value">{completed_runs}</div>
          <div class="stat-sub">Completed runs</div>
        </div>
        <div class="card">
          <div class="stat-label">Indexed Repositories</div>
          <div class="stat-value">{indexed_repos}</div>
          <div class="stat-sub"><span class="stat-accent">{neo.get('total_nodes', 0):,}</span> nodes · <span class="stat-accent">{neo.get('total_edges', 0):,}</span> edges</div>
        </div>
        <div class="card">
          <div class="stat-label">LLM Calls</div>
          <div class="stat-value">{total_calls:,}</div>
          <div class="stat-sub">avg <span class="stat-accent">{avg_latency:,}ms</span> latency</div>
        </div>
        <div class="card">
          <div class="stat-label">Total AI Cost</div>
          <div class="stat-value" style="color:#22c55e">${total_cost:.4f}</div>
          <div class="stat-sub">USD</div>
        </div>
        <div class="card">
          <div class="stat-label">Total Tokens</div>
          <div class="stat-value">{total_tokens:,}</div>
          <div class="stat-sub">prompt + completion</div>
        </div>
        <div class="card">
          <div class="stat-label">Avg Cost / Workflow</div>
          <div class="stat-value" style="color:#22c55e">${avg_cost_per_workflow:.4f}</div>
          <div class="stat-sub">USD per workflow</div>
        </div>
        <div class="card">
          <div class="stat-label">Avg Tokens / Call</div>
          <div class="stat-value">{total_tokens // total_calls if total_calls else 0:,}</div>
          <div class="stat-sub">tokens per LLM call</div>
        </div>
      </div>
    </div>
  </section>

  <!-- ── AI Cost & Token Analysis ──────────────────────────────────────── -->
  <section class="section">
    <div class="section-header" onclick="toggle('cost')">
      <div class="section-title"><span>💰</span> AI Cost &amp; Token Analysis</div>
      <span class="chevron" id="chevron-cost">▼</span>
    </div>
    <div class="section-body" id="body-cost">
      <div class="grid-2" style="margin-bottom:16px">
        <div class="chart-card">
          <div class="chart-title">Cost over time (last 30 days)</div>
          <canvas id="costDayChart"></canvas>
        </div>
        <div class="chart-card">
          <div class="chart-title">Tokens over time (last 30 days)</div>
          <canvas id="tokenDayChart"></canvas>
        </div>
      </div>
      <div class="grid-2">
        <div class="chart-card">
          <div class="chart-title">Cost by Provider</div>
          <canvas id="providerChart"></canvas>
        </div>
        <div class="chart-card">
          <div class="chart-title">Cost by Stage</div>
          <canvas id="stageChart"></canvas>
        </div>
      </div>
    </div>
  </section>

  <!-- ── Model Usage ───────────────────────────────────────────────────── -->
  <section class="section">
    <div class="section-header" onclick="toggle('models')">
      <div class="section-title"><span>🤖</span> Model Usage</div>
      <span class="chevron" id="chevron-models">▼</span>
    </div>
    <div class="section-body" id="body-models">
      <div class="card">
        {'<table><thead><tr><th>Model</th><th>Provider</th><th style="text-align:right">Calls</th><th style="text-align:right">Cost (USD)</th></tr></thead><tbody>' + model_rows + '</tbody></table>' if model_rows else '<div class="empty">No LLM invocations recorded yet.</div>'}
      </div>
    </div>
  </section>

  <!-- ── Repository Graph ──────────────────────────────────────────────── -->
  <section class="section">
    <div class="section-header" onclick="toggle('repos')">
      <div class="section-title"><span>🗂️</span> Repository Graph</div>
      <span class="chevron" id="chevron-repos">▼</span>
    </div>
    <div class="section-body" id="body-repos">
      <div class="chart-card">
        <div class="chart-title">Components per indexed repository</div>
        {'<canvas id="repoChart"></canvas>' if neo_repos else '<div class="empty">No Neo4j data available. Ensure Neo4j is running and repositories are indexed.</div>'}
      </div>
    </div>
  </section>

  <!-- ── Run Success by Stage ──────────────────────────────────────────── -->
  <section class="section">
    <div class="section-header" onclick="toggle('runs')">
      <div class="section-title"><span>📈</span> Run Success Rate by Stage</div>
      <span class="chevron" id="chevron-runs">▼</span>
    </div>
    <div class="section-body" id="body-runs">
      <div class="chart-card">
        <canvas id="runStageChart"></canvas>
      </div>
    </div>
  </section>

  <!-- ── Workflow Timeline ─────────────────────────────────────────────── -->
  <section class="section">
    <div class="section-header" onclick="toggle('workflows')">
      <div class="section-title"><span>⏱️</span> Recent Workflows</div>
      <span class="chevron" id="chevron-workflows">▼</span>
    </div>
    <div class="section-body" id="body-workflows">
      <div class="card" style="overflow-x:auto">
        {'<table><thead><tr><th>Title</th><th style="text-align:center">Status</th><th style="text-align:center">Stage</th><th style="text-align:center">Type</th><th style="text-align:center">Created</th><th style="text-align:right">Cost</th><th style="text-align:right">Tokens</th></tr></thead><tbody>' + workflow_rows + '</tbody></table>' if workflow_rows else '<div class="empty">No workflows found.</div>'}
      </div>
    </div>
  </section>

</div>

<script>
// ── Chart defaults ────────────────────────────────────────────────────────
Chart.defaults.color = '#64748b';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
Chart.defaults.font.size = 11;

const COLORS = ['#6366f1','#22c55e','#f59e0b','#3b82f6','#ec4899','#14b8a6','#a855f7','#ef4444'];

// ── Collapse/expand ──────────────────────────────────────────────────────
function toggle(id) {{
  const body = document.getElementById('body-' + id);
  const chevron = document.getElementById('chevron-' + id);
  if (body.style.display === 'none') {{
    body.style.display = '';
    chevron.style.transform = '';
  }} else {{
    body.style.display = 'none';
    chevron.style.transform = 'rotate(-90deg)';
  }}
}}

// ── Cost over time ───────────────────────────────────────────────────────
new Chart(document.getElementById('costDayChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(days)},
    datasets: [{{
      label: 'Cost (USD)',
      data: {json.dumps(day_costs)},
      backgroundColor: '#6366f144',
      borderColor: '#6366f1',
      borderWidth: 1,
      borderRadius: 4,
    }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ callback: v => '$'+v.toFixed(4) }} }},
               x: {{ grid: {{ display: false }} }} }} }}
}});

// ── Tokens over time ─────────────────────────────────────────────────────
new Chart(document.getElementById('tokenDayChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(days)},
    datasets: [{{
      label: 'Tokens',
      data: {json.dumps(day_tokens)},
      borderColor: '#22c55e',
      backgroundColor: '#22c55e22',
      fill: true,
      tension: 0.3,
      pointRadius: 3,
    }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ grid: {{ color: '#1e293b' }} }},
               x: {{ grid: {{ display: false }} }} }} }}
}});

// ── Cost by provider ──────────────────────────────────────────────────────
new Chart(document.getElementById('providerChart'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(providers)},
    datasets: [{{
      data: {json.dumps(provider_costs)},
      backgroundColor: COLORS,
      borderWidth: 0,
      hoverOffset: 6,
    }}]
  }},
  options: {{ responsive: true, cutout: '60%',
    plugins: {{ legend: {{ position: 'right' }},
      tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': $' + ctx.raw.toFixed(4) }} }} }} }}
}});

// ── Cost by stage ─────────────────────────────────────────────────────────
new Chart(document.getElementById('stageChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(stages)},
    datasets: [{{
      label: 'Cost (USD)',
      data: {json.dumps(stage_costs)},
      backgroundColor: COLORS.map(c => c + '88'),
      borderColor: COLORS,
      borderWidth: 1,
      borderRadius: 4,
    }}]
  }},
  options: {{ responsive: true, indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ callback: v => '$'+v.toFixed(4) }} }},
               y: {{ grid: {{ display: false }} }} }} }}
}});

// ── Repo components ───────────────────────────────────────────────────────
{ f"""new Chart(document.getElementById('repoChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(neo_repos)},
    datasets: [{{
      label: 'Components',
      data: {json.dumps(neo_components)},
      backgroundColor: '#3b82f644',
      borderColor: '#3b82f6',
      borderWidth: 1,
      borderRadius: 4,
    }}]
  }},
  options: {{ responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ grid: {{ color: '#1e293b' }} }},
               x: {{ grid: {{ display: false }} }} }} }}
}});""" if neo_repos else "// No Neo4j data" }

// ── Run success by stage ──────────────────────────────────────────────────
new Chart(document.getElementById('runStageChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(run_stages)},
    datasets: [
      {{ label: 'Succeeded', data: {json.dumps(run_succeeded)}, backgroundColor: '#22c55e88', borderColor: '#22c55e', borderWidth: 1, borderRadius: 4 }},
      {{ label: 'Failed',    data: {json.dumps(run_failed)},    backgroundColor: '#ef444488', borderColor: '#ef4444', borderWidth: 1, borderRadius: 4 }}
    ]
  }},
  options: {{ responsive: true,
    plugins: {{ legend: {{ position: 'top' }} }},
    scales: {{ x: {{ stacked: false, grid: {{ display: false }} }},
               y: {{ grid: {{ color: '#1e293b' }}, beginAtZero: true }} }} }}
}});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate GraphForge HTML activity report")
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
            .replace("postgresql+asyncpg://", "postgresql://"),
        help="PostgreSQL connection URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--neo4j-uri",
        default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j Bolt URI",
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.environ.get("NEO4J_USER", "neo4j"),
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.environ.get("NEO4J_PASSWORD", "graphforge-dev"),
    )
    parser.add_argument(
        "--output",
        default=f"graphforge_report_{datetime.now().strftime('%Y-%m-%d')}.html",
        help="Output HTML file path",
    )
    args = parser.parse_args()

    # Resolve DB URL — strip asyncpg driver if present
    db_url = args.db_url.replace("postgresql+asyncpg://", "postgresql://")
    if not db_url:
        # Fallback for local Docker dev stack
        db_url = "postgresql://graphforge:graphforge@localhost:5433/graphforge"

    print(f"Connecting to Postgres: {db_url.split('@')[-1]}")
    pg_data = collect_postgres(db_url)

    print(f"Connecting to Neo4j: {args.neo4j_uri}")
    neo_data = collect_neo4j(args.neo4j_uri, args.neo4j_user, args.neo4j_password)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = render_html(pg_data, neo_data, generated_at)

    out = Path(args.output)
    out.write_text(html, encoding="utf-8")
    print(f"\n✓ Report written to: {out.resolve()}")
    print(f"  Open in browser: file://{out.resolve()}")


if __name__ == "__main__":
    main()
