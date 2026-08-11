import { Link } from "react-router-dom";
import { AlertTriangle, ChevronDown, RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { Card } from "../components/Card";
import { EmptyState, SampleChart } from "../components/EmptyState";
import { StatusBadge, type StatusTone } from "../components/StatusBadge";
import { Table, type TableColumn } from "../components/Table";
import { ProvenanceTag } from "../components/intelligence/ProvenanceTag";
import {
  BarChart,
  HorizontalBarChart,
  LineChart,
  StackedBarChart,
} from "../components/charts/SimpleCharts";
import { useReportsData } from "../hooks/useReportsData";
import { formatCount, formatLabel, formatUsd, shortenIsoDate } from "../lib/formatMetrics";
import { computeMetricsSignals, type MetricsSignal } from "../lib/metricsSignals";
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

/** Operational intelligence for GraphForge itself — what's happening,
 * where effort is going, where problems occur, and what actually deserves
 * attention, in that order. Redesigned from a flat "eight stat tiles plus
 * six independent chart cards" layout (no hierarchy — a completion rate
 * and a token count read as equally important) into the same executive
 * summary → trends → efficiency → health → detail structure a Delivery
 * Manager actually scans a report in.
 *
 * Every number is still read straight from `MetricsReportResponse` —
 * nothing is fabricated. The one new thing is `computeMetricsSignals`:
 * plain arithmetic over that same data (failure rate by stage, cost
 * share by stage, a two-half cost trend), tagged `derived` via
 * `ProvenanceTag` rather than presented as an AI judgment it isn't. */
export function MetricsPage() {
  const { report, scope, setScope, isLoading, error, refresh } = useReportsData();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-[0.14em] text-accent-fg uppercase">
            Operational intelligence
          </p>
          <h1 className="mt-1 font-display text-2xl font-bold tracking-tight text-fg">Metrics</h1>
          <p className="mt-1 text-sm text-fg-muted">
            What GraphForge is doing, where effort is going, and what deserves attention.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ScopeToggle scope={scope} onChange={setScope} />
          <button
            type="button"
            onClick={refresh}
            disabled={isLoading}
            className="focus-ring inline-flex items-center gap-1.5 rounded-lg border border-line-muted bg-surface px-3 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
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
          <div className="h-20 animate-pulse rounded-xl bg-surface" />
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
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
          <p className="text-xs text-fg-subtle">
            Generated {new Date(report.generated_at).toLocaleString()} · last {report.window_days}{" "}
            days · scope: {report.scope}
          </p>

          {/* ── Executive summary — the handful of numbers that matter,
              not every number collected. Indexed-repository/node/edge
              counts (this page used to lead with all three) live on
              Architecture now, which already tells that story better. ── */}
          <ExecutiveSummary report={report} />

          {/* ── What deserves attention — computed, not collected.
              Same "count chip + ranked list" vocabulary as Mission
              Control's and Architecture's own Needs Attention sections,
              so this reads as the same product concept in a third
              place. ─────────────────────────────────────────────────── */}
          <SignalsSection report={report} />

          {/* ── Trends — what's changing over the window. ──────────── */}
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-fg">Trends</h2>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="mb-3 text-xs font-semibold tracking-wide text-fg-muted uppercase">
                  AI cost per day
                </h3>
                <BarChart
                  data={report.cost_by_day.map((d) => ({ label: d.day, value: d.cost_usd }))}
                  valueFormatter={formatUsd}
                  labelFormatter={shortenIsoDate}
                  label="AI cost per day"
                />
              </div>
              <div>
                <h3 className="mb-3 text-xs font-semibold tracking-wide text-fg-muted uppercase">
                  Tokens per day
                </h3>
                <LineChart
                  data={report.cost_by_day.map((d) => ({ label: d.day, value: d.tokens }))}
                  valueFormatter={formatCount}
                  labelFormatter={shortenIsoDate}
                  label="Tokens per day"
                />
              </div>
            </div>
          </section>

          {/* ── Investigation efficiency — where cost/effort goes. ──── */}
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-fg">Where effort is going</h2>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="mb-3 text-xs font-semibold tracking-wide text-fg-muted uppercase">
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
              <div>
                <h3 className="mb-3 text-xs font-semibold tracking-wide text-fg-muted uppercase">
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
            </div>
            <Card title="Model usage">
              <ModelUsageTable rows={report.model_usage} />
            </Card>
          </section>

          {/* ── Workflow health — where failures happen. ────────────── */}
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-fg">Workflow health</h2>
            <Card description="Each bar is scaled to its own stage's total, so rates are comparable; the run count is shown alongside.">
              <StackedBarChart
                data={report.run_success_by_stage.map((r) => ({
                  label: formatLabel(r.stage),
                  succeeded: r.succeeded,
                  failed: r.failed,
                }))}
                label="Run success rate by stage"
              />
            </Card>
          </section>

          {/* ── Detailed analytics — progressive disclosure, not deleted.
              Recent Workflows and the repository component chart are
              real, useful data; they're just not the story most visits
              to this page are here for. ─────────────────────────────── */}
          <details className="group rounded-xl border border-line-muted">
            <summary className="focus-ring flex cursor-pointer list-none items-center justify-between gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-fg-secondary hover:bg-surface-raised">
              <span>Detailed analytics</span>
              <ChevronDown
                className="h-4 w-4 shrink-0 text-fg-muted transition-transform group-open:rotate-180"
                aria-hidden="true"
              />
            </summary>
            <div className="flex flex-col gap-6 px-4 pb-4">
              <Card title="Recent workflows">
                <RecentWorkflowsTable rows={report.recent_workflows} />
              </Card>
              <Card title="Repository graph" description="Components per indexed repository">
                <BarChart
                  data={report.repository_components.map((r) => ({
                    label: r.name,
                    value: r.components,
                  }))}
                  valueFormatter={formatCount}
                  color="var(--gf-info-fg, #096e9c)"
                  label="Components per indexed repository"
                />
              </Card>
            </div>
          </details>
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

/** Four numbers, not eight — the ones a Delivery Manager actually opens
 * this page to check. No card border per tile (see the redesign's own
 * "don't over-card" rule): one shared surface, dividers between values
 * rather than four separate rounded rectangles. */
function ExecutiveSummary({ report }: { report: MetricsReportResponse }) {
  const { overview } = report;
  const completionRate = overview.total_workflows
    ? Math.round((overview.completed_workflows / overview.total_workflows) * 100)
    : 0;
  const avgCostPerWorkflow = overview.total_workflows
    ? overview.total_cost_usd / overview.total_workflows
    : 0;

  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-5 rounded-xl border border-line-muted bg-surface p-5 sm:grid-cols-4">
      <SummaryStat
        label="Workflows"
        value={formatCount(overview.total_workflows)}
        hint={`${completionRate}% completed`}
      />
      <SummaryStat
        label="AI cost"
        value={formatUsd(overview.total_cost_usd)}
        hint={`${formatUsd(avgCostPerWorkflow)} / workflow`}
      />
      <SummaryStat
        label="LLM calls"
        value={formatCount(overview.total_llm_calls)}
        hint={`avg ${formatCount(overview.avg_latency_ms)}ms latency`}
      />
      <Link to="/runs" className="transition-opacity hover:opacity-80">
        <SummaryStat label="Agent runs" value={formatCount(overview.completed_runs)} hint="completed" />
      </Link>
    </div>
  );
}

function SummaryStat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div>
      <p className="font-display text-2xl font-semibold tabular-nums text-fg">{value}</p>
      <p className="mt-0.5 text-xs font-medium text-fg-secondary">{label}</p>
      <p className="text-[11px] text-fg-muted">{hint}</p>
    </div>
  );
}

function SignalsSection({ report }: { report: MetricsReportResponse }) {
  const signals = computeMetricsSignals(report);
  if (signals.length === 0) return null;

  return (
    <section className="flex flex-col gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
        <span
          aria-hidden="true"
          className="flex h-5 w-5 items-center justify-center rounded-full bg-warning-solid text-[11px] font-bold text-warning-on-solid"
        >
          {signals.length}
        </span>
        What deserves attention
      </h2>
      <div className="divide-y divide-line-muted rounded-xl border border-warning-line/30 bg-warning-bg/40">
        {signals.map((signal) => (
          <SignalRow key={signal.kind} signal={signal} />
        ))}
      </div>
    </section>
  );
}

function SignalRow({ signal }: { signal: MetricsSignal }) {
  if (signal.kind === "stage_failure") {
    return (
      <div className="flex items-center gap-3 px-4 py-3">
        <AlertTriangle className="h-4 w-4 shrink-0 text-danger-fg" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg">
            {formatLabel(signal.stage)} is failing {Math.round(signal.failureRate * 100)}% of runs
          </p>
          <p className="text-xs text-fg-muted">
            {signal.failed} of {signal.total} runs failed in this window
          </p>
        </div>
        <ProvenanceTag kind="derived" />
      </div>
    );
  }
  if (signal.kind === "stage_cost") {
    return (
      <div className="flex items-center gap-3 px-4 py-3">
        <AlertTriangle className="h-4 w-4 shrink-0 text-warning-fg" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg">
            {formatLabel(signal.stage)} accounts for {Math.round(signal.shareOfTotal * 100)}% of AI
            spend
          </p>
          <p className="text-xs text-fg-muted">{formatUsd(signal.costUsd)} in this window</p>
        </div>
        <ProvenanceTag kind="derived" />
      </div>
    );
  }
  const Icon = signal.direction === "up" ? TrendingUp : TrendingDown;
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <Icon
        className={`h-4 w-4 shrink-0 ${signal.direction === "up" ? "text-warning-fg" : "text-success-fg"}`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-fg">
          AI cost is {signal.direction === "up" ? "up" : "down"} {Math.round(signal.changeFraction * 100)}%
          vs. the earlier half of this window
        </p>
        <p className="text-xs text-fg-muted">
          {formatUsd(signal.priorCostUsd)} → {formatUsd(signal.recentCostUsd)}
        </p>
      </div>
      <ProvenanceTag kind="derived" />
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
