import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, ChevronLeft, ChevronRight } from "lucide-react";
import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { Table, type TableColumn } from "../components/Table";
import { formatRelativeTime } from "../lib/formatDate";
import { useAuth } from "../app/auth-context";
import { listWorkflows } from "../lib/api/workflows";
import { getAgentRun } from "../lib/api/agentRuns";
import type { DevelopmentPlanResult, ImplementationPhase, WorkflowListItem } from "../types/agent";

const PAGE_SIZE = 20; // modest — each row triggers one getAgentRun call (see below)

interface QueueRow {
  workflow: WorkflowListItem;
  /** null while loading, or if the Development stage has no result to
   * show yet — never fabricated, only ever real data or absent. */
  development: DevelopmentPlanResult | null;
}

function scopeLabel(phases: ImplementationPhase[] | undefined): string {
  if (!phases || phases.length === 0) return "—";
  const complexities = new Set(
    phases.map((p) => p.estimated_complexity).filter((c): c is string => Boolean(c)),
  );
  const complexityLabel = complexities.size === 1 ? [...complexities][0] : "mixed";
  return `${phases.length} phase${phases.length === 1 ? "" : "s"} · ${complexityLabel}`;
}

function repositoryLabel(row: QueueRow): { text: string; title: string } {
  const repos = row.development?.repositories ?? [];
  if (repos.length === 0) return { text: "—", title: "" };
  return {
    text: repos.map((r) => r.name).join(", "),
    title: repos.map((r) => `${r.name} (${r.owner}) — ${r.reason}`).join("\n"),
  };
}

const columns: TableColumn<QueueRow>[] = [
  {
    key: "title",
    header: "Title",
    render: ({ workflow }) => (
      <Link
        to={`/workflows/${workflow.workflow_id}`}
        className="block truncate font-medium text-fg hover:underline"
        title={workflow.title}
      >
        {workflow.title}
      </Link>
    ),
  },
  {
    key: "repository",
    header: "Repository",
    render: (row) => {
      const { text, title } = repositoryLabel(row);
      return (
        <span className="text-sm text-fg-secondary" title={title}>
          {text}
        </span>
      );
    },
  },
  {
    key: "scope",
    header: "Estimated Scope",
    render: ({ development }) => (
      <span className="text-sm text-fg-secondary">
        {scopeLabel(development?.implementation_phases)}
      </span>
    ),
  },
  {
    key: "approved_date",
    header: "Approval Date",
    render: ({ workflow }) => (
      <span className="text-sm text-fg-muted">{formatRelativeTime(workflow.updated_at)}</span>
    ),
  },
  {
    key: "approved_by",
    header: "Approved By",
    render: ({ workflow }) => (
      <span className="text-sm text-fg-secondary">{workflow.approved_by ?? "—"}</span>
    ),
  },
  {
    key: "status",
    header: "Status",
    render: () => <StatusBadge label="Approved" tone="success" />,
  },
  {
    key: "action",
    header: "",
    render: () => (
      <span
        aria-disabled="true"
        title="Turning an approved blueprint into code isn't available yet"
        className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line-muted opacity-60"
      >
        Start Implementation
        <span className="rounded-full bg-surface-raised px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
          Coming soon
        </span>
      </span>
    ),
  },
];

export function ApprovedQueuePage() {
  const { token } = useAuth();
  const [items, setItems] = useState<WorkflowListItem[]>([]);
  const [developmentByWorkflowId, setDevelopmentByWorkflowId] = useState<
    Record<string, DevelopmentPlanResult | null>
  >({});
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    listWorkflows(
      token,
      { status: "approved", workflow_type: "planning", page, page_size: PAGE_SIZE },
      controller.signal,
    )
      .then((res) => {
        if (controller.signal.aborted) return;
        setItems(res.items);
        setTotal(res.total);
        setHasMore(res.has_more);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Failed to load the approved queue.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
    return () => controller.abort();
  }, [token, page]);

  useEffect(() => {
    if (!token || items.length === 0) return;
    let cancelled = false;
    void Promise.all(
      items.map(async (workflow) => {
        const developmentStage = workflow.stages.find((s) => s.stage === "development");
        if (!developmentStage?.run_id) return [workflow.workflow_id, null] as const;
        try {
          const run = await getAgentRun(token, developmentStage.run_id);
          const result =
            (run.steps[0]?.result as unknown as DevelopmentPlanResult | undefined) ?? null;
          return [workflow.workflow_id, result] as const;
        } catch {
          return [workflow.workflow_id, null] as const;
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      setDevelopmentByWorkflowId(Object.fromEntries(results));
    });
    return () => {
      cancelled = true;
    };
  }, [token, items]);

  const rows: QueueRow[] = items.map((workflow) => ({
    workflow,
    development: developmentByWorkflowId[workflow.workflow_id] ?? null,
  }));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="rounded-lg bg-success-bg p-2 ring-1 ring-inset ring-success-line/30">
          <CheckCircle2 className="h-5 w-5 text-success-fg" aria-hidden="true" />
        </div>
        <div>
          <h2 className="text-xl font-semibold text-fg">Approved Queue</h2>
          <p className="text-sm text-fg-muted">
            {total} approved blueprint{total === 1 ? "" : "s"}
          </p>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      )}

      <Card>
        <Table
          columns={columns}
          data={rows}
          getRowKey={(row) => row.workflow.workflow_id}
          emptyMessage={isLoading ? "Loading…" : "No approved blueprints yet."}
        />

        {total > 0 && (
          <div className="mt-4 flex items-center justify-between border-t border-line-muted pt-4">
            <p className="text-xs text-fg-muted">
              Page {page} of {Math.ceil(total / PAGE_SIZE)}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage(page - 1)}
                disabled={page <= 1}
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="Previous page"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Prev
              </button>
              <button
                type="button"
                onClick={() => setPage(page + 1)}
                disabled={!hasMore}
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="Next page"
              >
                Next
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
