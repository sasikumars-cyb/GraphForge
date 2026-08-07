import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Card } from "../components/Card";
import { StatCard } from "../components/StatCard";
import { Table, type TableColumn } from "../components/Table";
import { HorizontalBarChart } from "../components/charts/SimpleCharts";
import { useAuth } from "../app/auth-context";
import { getWorkflowLLMUsage } from "../lib/api/metrics";
import { formatCount, formatLabel, formatUsd } from "../lib/formatMetrics";
import type { WorkflowStageLLMUsage } from "../types/metrics";

/** Per-stage LLM usage breakdown for one workflow — reached by clicking a
 * row in the Metrics page's "Recent Workflows" table. Answers "which
 * stage actually drove this workflow's cost/latency", not just the
 * workflow's rolled-up total the overview report already shows. */
export function WorkflowLLMUsagePage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const { token } = useAuth();

  const query = useQuery({
    queryKey: ["workflow-llm-usage", workflowId],
    queryFn: ({ signal }) => getWorkflowLLMUsage(token as string, workflowId as string, signal),
    enabled: token !== null && workflowId !== undefined,
    // A workflow lookup 404s deterministically (wrong id, or - the
    // common real case - a workflow this user doesn't own, gated by the
    // same 404-not-403 ownership check every workflow endpoint uses) -
    // retrying it can never succeed, unlike the shared queryClient's
    // default `retry: 1` (meant for a genuine transient network blip).
    // Worth overriding here specifically, not just skipping: a retry
    // that's paused (TanStack Query's retryer also gates continuation on
    // `focusManager.isFocused()`, alongside network status) can leave
    // this query stuck reporting `isPending` indefinitely instead of
    // ever reaching `isError` - observed directly while verifying this
    // page. `retry: false` sidesteps that failure mode entirely rather
    // than relying on focus/online state ever resolving it.
    retry: false,
  });

  const data = query.data;
  const stages = data?.stages ?? [];

  const totals = stages.reduce(
    (acc, s) => ({
      calls: acc.calls + s.calls,
      input_tokens: acc.input_tokens + s.input_tokens,
      output_tokens: acc.output_tokens + s.output_tokens,
      total_tokens: acc.total_tokens + s.total_tokens,
      cost_usd: acc.cost_usd + s.cost_usd,
    }),
    { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_usd: 0 },
  );

  const columns: TableColumn<WorkflowStageLLMUsage>[] = [
    { key: "stage", header: "Stage", render: (r) => formatLabel(r.stage) },
    {
      key: "models",
      header: "Model(s)",
      render: (r) => (
        <span className="font-mono text-xs" title={r.models.join(", ")}>
          {r.models.join(", ") || "—"}
        </span>
      ),
    },
    { key: "calls", header: "Calls", render: (r) => formatCount(r.calls), className: "text-right" },
    {
      key: "input_tokens",
      header: "Input Tokens",
      render: (r) => formatCount(r.input_tokens),
      className: "text-right",
    },
    {
      key: "output_tokens",
      header: "Output Tokens",
      render: (r) => formatCount(r.output_tokens),
      className: "text-right",
    },
    {
      key: "total_tokens",
      header: "Total Tokens",
      render: (r) => formatCount(r.total_tokens),
      className: "text-right",
    },
    {
      key: "cost",
      header: "Cost",
      render: (r) => <span className="font-mono text-success-fg">{formatUsd(r.cost_usd)}</span>,
      className: "text-right",
    },
    {
      key: "latency",
      header: "Avg Latency",
      render: (r) => `${formatCount(r.avg_latency_ms)}ms`,
      className: "text-right",
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <Link
        to="/metrics"
        className="inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg-secondary"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to Metrics
      </Link>

      {query.isPending && (
        <Card>
          <p className="py-8 text-center text-sm text-fg-muted">Loading LLM usage…</p>
        </Card>
      )}

      {query.isError && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          Failed to load this workflow's LLM usage.
        </div>
      )}

      {data && (
        <>
          <div>
            <h1 className="text-xl font-semibold text-fg">{data.workflow_title}</h1>
            <p className="mt-1 text-sm text-fg-muted">
              LLM usage by stage — model, tokens, cost, and latency for each stage this workflow
              ran, so you can see which stage actually drove the total.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard label="Stages" value={formatCount(stages.length)} hint="ran an LLM call" />
            <StatCard label="LLM Calls" value={formatCount(totals.calls)} />
            <StatCard label="Total Tokens" value={formatCount(totals.total_tokens)} />
            <StatCard label="Total Cost" value={formatUsd(totals.cost_usd)} hint="USD" />
          </div>

          {stages.length === 0 ? (
            <Card>
              <p className="py-8 text-center text-sm text-fg-muted">
                No LLM calls recorded for this workflow yet.
              </p>
            </Card>
          ) : (
            <>
              <Card title="Cost by Stage" description="Which stage consumed the most so far">
                <HorizontalBarChart
                  data={stages.map((s) => ({ label: formatLabel(s.stage), value: s.cost_usd }))}
                  valueFormatter={formatUsd}
                />
              </Card>

              <Card
                title="Stage Breakdown"
                description="Model, tokens, cost, latency, and call count per stage"
              >
                <Table
                  columns={columns}
                  data={stages}
                  getRowKey={(r) => r.stage}
                  emptyMessage="No LLM invocations recorded yet."
                />
              </Card>
            </>
          )}
        </>
      )}
    </div>
  );
}
