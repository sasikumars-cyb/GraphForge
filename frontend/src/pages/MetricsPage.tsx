import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { Card } from "../components/Card";
import { EmptyState, SampleChart } from "../components/EmptyState";
import { StatCard } from "../components/StatCard";
import { StatusBadge, type StatusTone } from "../components/StatusBadge";
import { Table, type TableColumn } from "../components/Table";
import {
  BarChart,
  HorizontalBarChart,
  LineChart,
  StackedBarChart,
} from "../components/charts/SimpleCharts";
import { useReportsData } from "../hooks/useReportsData";
import { formatCount, formatLabel, formatUsd, shortenIsoDate } from "../lib/formatMetrics";
import type {
  MetricsReportResponse,
  MetricsScope,
  ModelUsage,
  WorkflowSummary,
} from "../types/metrics";

const WORKFLOW_STATUS_TONE: Record<string, StatusTone> = {
  in_progress: "info",
  completed: "success",
  approved: "success",
  awaiting_approval: "warning",
  awaiting_clarification: "warning",
  rejected: "danger",
  failed: "danger",
};

/** Live activity dashboard — workflows, AI cost/tokens, and indexed
 * architecture. Separate from ReportsPage, which is the PR-review evidence
 * packet feature. */
export function MetricsPage() {
  const { report, scope, setScope, isLoading, error, refresh } = useReportsData();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-fg">Metrics</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Live activity metrics — workflows, AI cost/tokens, and indexed architecture.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ScopeToggle scope={scope} onChange={setScope} />
          <button
            type="button"
            onClick={refresh}
            disabled={isLoading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line-muted bg-surface px-3 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`}
              aria-hidden="true"
            />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      )}

      {!report && isLoading && (
        <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading metrics">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
              <div key={i} className="h-24 animate-pulse rounded-xl bg-surface" />
            ))}
          </div>
          <div className="h-72 animate-pulse rounded-xl bg-surface" />
        </div>
      )}

      {/* Zero activity is not the same as zero data-to-chart: rendering eight
          "0" tiles above six "No data to show." charts tells a first-run user
          nothing they can act on. */}
      {report && report.overview.total_workflows === 0 && report.overview.total_llm_calls === 0 && (
        <Card>
          <EmptyState
            illustration={<SampleChart />}
            title="Usage and cost metrics appear here"
            description={`Once workflows run, this page tracks AI spend, token usage, model mix, and per-stage success rates. Nothing has run in the last ${report.window_days} days${report.scope === "user" ? " for your account — try the Global scope above" : ""}.`}
            actions={[
              { label: "New Workflow", to: "/workflows/new" },
              { label: "Browse AI Workspace", to: "/workspace" },
            ]}
          />
        </Card>
      )}

      {report && !(report.overview.total_workflows === 0 && report.overview.total_llm_calls === 0) && (
        <>
          <p className="text-xs text-fg-muted">
            Generated {new Date(report.generated_at).toLocaleString()} · last {report.window_days}{" "}
            days · scope: {report.scope}
          </p>

          <OverviewSection report={report} />

          <Card title="AI Cost & Token Analysis">
            <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Cost over time
                </h3>
                <BarChart
                  data={report.cost_by_day.map((d) => ({ label: d.day, value: d.cost_usd }))}
                  valueFormatter={formatUsd}
                  labelFormatter={shortenIsoDate}
                  label="AI cost per day"
                />
              </div>
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Tokens over time
                </h3>
                <LineChart
                  data={report.cost_by_day.map((d) => ({ label: d.day, value: d.tokens }))}
                  valueFormatter={formatCount}
                  labelFormatter={shortenIsoDate}
                  label="Tokens per day"
                />
              </div>
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Cost by provider
                </h3>
                <HorizontalBarChart
                  data={report.cost_by_provider.map((p) => ({
                    label: p.provider,
                    value: p.cost_usd,
                  }))}
                  valueFormatter={formatUsd}
                  label="Cost by provider"
                />
              </div>
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Cost by stage
                </h3>
                <HorizontalBarChart
                  data={report.cost_by_stage.map((s) => ({
                    label: formatLabel(s.stage),
                    value: s.cost_usd,
                  }))}
                  valueFormatter={formatUsd}
                  label="Cost by stage"
                />
              </div>
            </div>
          </Card>

          <Card title="Model Usage">
            <ModelUsageTable rows={report.model_usage} />
          </Card>

          <Card title="Repository Graph" description="Components per indexed repository">
            {/* No `labelFormatter` — these labels are repository names, not
                dates. The axis used to trim five characters off every label
                unconditionally, which silently mangled them here. */}
            <BarChart
              data={report.repository_components.map((r) => ({
                label: r.name,
                value: r.components,
              }))}
              valueFormatter={formatCount}
              color="var(--gf-info-fg, #3b82f6)"
              label="Components per indexed repository"
            />
          </Card>

          <Card
            title="Run Success Rate by Stage"
            description="Each bar is scaled to its own stage's total, so rates are comparable; the run count is shown alongside."
          >
            <StackedBarChart
              data={report.run_success_by_stage.map((r) => ({
                label: formatLabel(r.stage),
                succeeded: r.succeeded,
                failed: r.failed,
              }))}
              label="Run success rate by stage"
            />
          </Card>

          <Card title="Recent Workflows">
            <RecentWorkflowsTable rows={report.recent_workflows} />
          </Card>
        </>
      )}
    </div>
  );
}

function ScopeToggle({
  scope,
  onChange,
}: {
  scope: MetricsScope;
  onChange: (scope: MetricsScope) => void;
}) {
  return (
    <div className="flex items-center rounded-lg border border-line-muted bg-surface p-0.5 text-xs">
      {(["user", "global"] as const).map((value) => (
        <button
          key={value}
          type="button"
          onClick={() => onChange(value)}
          className={`rounded-md px-3 py-1 font-medium transition-colors ${
            scope === value ? "bg-info-bg text-info-fg" : "text-fg-muted hover:text-fg-secondary"
          }`}
        >
          {value === "user" ? "My Data" : "Global"}
        </button>
      ))}
    </div>
  );
}

function OverviewSection({ report }: { report: MetricsReportResponse }) {
  const { overview } = report;
  const completionRate = overview.total_workflows
    ? Math.round((overview.completed_workflows / overview.total_workflows) * 100)
    : 0;
  const avgCostPerWorkflow = overview.total_workflows
    ? overview.total_cost_usd / overview.total_workflows
    : 0;
  const avgTokensPerCall = overview.total_llm_calls
    ? Math.round(overview.total_tokens / overview.total_llm_calls)
    : 0;

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
      <StatCard
        label="Workflows"
        value={formatCount(overview.total_workflows)}
        hint={`${overview.completed_workflows} completed · ${completionRate}% rate`}
      />
      <Link to="/runs" className="block transition-opacity hover:opacity-80">
        <StatCard
          label="Agent Runs"
          value={formatCount(overview.completed_runs)}
          hint="Completed runs"
        />
      </Link>
      <StatCard
        label="Indexed Repositories"
        value={formatCount(overview.indexed_repositories)}
        hint={`${formatCount(overview.total_graph_nodes)} nodes · ${formatCount(overview.total_graph_edges)} edges`}
      />
      <StatCard
        label="LLM Calls"
        value={formatCount(overview.total_llm_calls)}
        hint={`avg ${formatCount(overview.avg_latency_ms)}ms latency`}
      />
      <StatCard label="Total AI Cost" value={formatUsd(overview.total_cost_usd)} hint="USD" />
      <StatCard
        label="Total Tokens"
        value={formatCount(overview.total_tokens)}
        hint="prompt + completion"
      />
      <StatCard
        label="Avg Cost / Workflow"
        value={formatUsd(avgCostPerWorkflow)}
        hint="USD per workflow"
      />
      <StatCard
        label="Avg Tokens / Call"
        value={formatCount(avgTokensPerCall)}
        hint="tokens per LLM call"
      />
    </div>
  );
}

function ModelUsageTable({ rows }: { rows: ModelUsage[] }) {
  const columns: TableColumn<ModelUsage>[] = [
    {
      key: "model",
      header: "Model",
      render: (r) => <span className="font-mono text-xs">{r.model}</span>,
      sortValue: (r) => r.model,
    },
    { key: "provider", header: "Provider", render: (r) => r.provider, sortValue: (r) => r.provider },
    {
      key: "calls",
      header: "Calls",
      render: (r) => formatCount(r.calls),
      className: "text-right",
      sortValue: (r) => r.calls,
    },
    {
      key: "cost",
      header: "Cost (USD)",
      render: (r) => <span className="font-mono text-success-fg">{formatUsd(r.cost_usd)}</span>,
      className: "text-right",
      sortValue: (r) => r.cost_usd,
    },
  ];
  return (
    <Table
      columns={columns}
      data={rows}
      getRowKey={(r) => `${r.model}:${r.provider}`}
      emptyMessage="No LLM invocations recorded yet."
      defaultSort={{ key: "cost", direction: "desc" }}
    />
  );
}

function RecentWorkflowsTable({ rows }: { rows: WorkflowSummary[] }) {
  const columns: TableColumn<WorkflowSummary>[] = [
    {
      key: "title",
      header: "Title",
      render: (r) => (
        <Link
          to={`/metrics/workflows/${r.id}`}
          className="block max-w-[260px] truncate hover:underline"
          title={`View LLM usage for "${r.title}"`}
        >
          {r.title}
        </Link>
      ),
      sortValue: (r) => r.title.toLowerCase(),
    },
    {
      key: "status",
      header: "Status",
      render: (r) => (
        <StatusBadge
          label={formatLabel(r.status)}
          tone={WORKFLOW_STATUS_TONE[r.status] ?? "neutral"}
        />
      ),
      sortValue: (r) => r.status,
    },
    {
      key: "stage",
      header: "Stage",
      render: (r) => formatLabel(r.current_stage),
      sortValue: (r) => r.current_stage,
    },
    {
      key: "type",
      header: "Type",
      render: (r) => formatLabel(r.workflow_type),
      sortValue: (r) => r.workflow_type,
    },
    {
      key: "created",
      header: "Created",
      render: (r) => new Date(r.created_at).toLocaleDateString(),
      sortValue: (r) => new Date(r.created_at).getTime(),
    },
    {
      key: "cost",
      header: "Cost",
      render: (r) => <span className="font-mono text-success-fg">{formatUsd(r.cost_usd)}</span>,
      className: "text-right",
      sortValue: (r) => r.cost_usd,
    },
    {
      key: "tokens",
      header: "Tokens",
      render: (r) => formatCount(r.tokens),
      className: "text-right",
      sortValue: (r) => r.tokens,
    },
  ];
  return (
    <Table
      columns={columns}
      data={rows}
      getRowKey={(r) => r.id}
      emptyMessage="No workflows found."
      defaultSort={{ key: "created", direction: "desc" }}
    />
  );
}
