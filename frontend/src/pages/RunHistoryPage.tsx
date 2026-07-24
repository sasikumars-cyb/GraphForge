import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { History, ChevronLeft, ChevronRight, RefreshCw, GitMerge } from "lucide-react";
import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { Table, type TableColumn } from "../components/Table";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { formatRelativeTime } from "../lib/formatDate";
import { deriveWorkflowState, stageLabel, workflowStatusDisplay } from "../lib/workflowDerived";
import { useRunHistory } from "../hooks/useRunHistory";
import { useAuth } from "../app/auth-context";
import { getWorkflow } from "../lib/api/workflows";
import type { RunListItem, WorkflowDetail } from "../types/agent";

const GOAL_LABELS: Record<string, string> = {
  plan_freeform: "Planning",
  review_pr: "PR Review",
  develop_change_plan: "Development",
  plan_tests: "Testing",
};

function StatusCell({ row }: { row: RunListItem }) {
  return <RunStatusBadge status={row.status} />;
}

function ConfidenceCell({ row }: { row: RunListItem }) {
  return row.confidence_score != null ? (
    <ConfidenceBadge confidence={{ score: row.confidence_score, reasoning: "" }} />
  ) : (
    <span className="text-xs text-slate-500">—</span>
  );
}

function StartedCell({ row }: { row: RunListItem }) {
  return row.started_at ? (
    <span className="text-sm text-slate-400">{formatRelativeTime(row.started_at)}</span>
  ) : (
    <span className="text-xs text-slate-500">—</span>
  );
}

function DurationCell({ row }: { row: RunListItem }) {
  if (!row.started_at || !row.completed_at)
    return <span className="text-xs text-slate-500">—</span>;
  const ms = new Date(row.completed_at).getTime() - new Date(row.started_at).getTime();
  return <span className="text-sm text-slate-400">{(ms / 1000).toFixed(1)}s</span>;
}

function ProviderCell({ row }: { row: RunListItem }) {
  return row.provider ? (
    <span className="text-sm text-slate-300">{row.provider}</span>
  ) : (
    <span className="text-xs text-slate-500">—</span>
  );
}

// Standalone (non-workflow) runs — unchanged from before grouping was added.
const standaloneColumns: TableColumn<RunListItem>[] = [
  {
    key: "subject",
    header: "Title",
    render: (row) => (
      <Link to={`/runs/${row.run_id}`} className="block hover:underline">
        <p
          className="truncate font-medium text-slate-100"
          title={row.title ?? row.subject.display_name}
        >
          {row.title ?? row.subject.display_name ?? row.subject.subject_id}
        </p>
        <p className="truncate text-xs text-slate-500">
          {row.repository ?? row.subject.subject_type}
        </p>
      </Link>
    ),
  },
  {
    key: "goal",
    header: "Goal",
    render: (row) => (
      <span className="text-sm text-slate-300">{GOAL_LABELS[row.goal] ?? row.goal}</span>
    ),
  },
  { key: "status", header: "Status", render: (row) => <StatusCell row={row} /> },
  { key: "provider", header: "Provider", render: (row) => <ProviderCell row={row} /> },
  { key: "confidence", header: "Confidence", render: (row) => <ConfidenceCell row={row} /> },
  { key: "started", header: "Started", render: (row) => <StartedCell row={row} /> },
  { key: "duration", header: "Duration", render: (row) => <DurationCell row={row} /> },
];

// A workflow's stage runs, shown inside its expanded group — same cell
// renderers as standaloneColumns, "Subject" swapped for "Stage" since every
// row here already belongs to one known workflow.
const stageColumns: TableColumn<RunListItem>[] = [
  {
    key: "stage",
    header: "Stage",
    render: (row) => (
      <Link to={`/runs/${row.run_id}`} className="block hover:underline">
        <p className="text-sm font-medium text-slate-100">
          {row.workflow_stage
            ? stageLabel(row.workflow_stage)
            : (GOAL_LABELS[row.goal] ?? row.goal)}
        </p>
      </Link>
    ),
  },
  { key: "status", header: "Status", render: (row) => <StatusCell row={row} /> },
  { key: "confidence", header: "Confidence", render: (row) => <ConfidenceCell row={row} /> },
  { key: "started", header: "Started", render: (row) => <StartedCell row={row} /> },
  { key: "duration", header: "Duration", render: (row) => <DurationCell row={row} /> },
];

interface WorkflowGroup {
  workflowId: string;
  runs: RunListItem[];
}

/** Groups the already-fetched, already recency-sorted runs by workflow_id —
 * the first run of a given workflow fixes that group's position (most
 * recent activity first, since the page itself is sorted desc); later runs
 * of the same workflow fold into that group instead of appearing again.
 * Standalone (ad-hoc, non-workflow) runs are returned separately. */
function groupByWorkflow(runs: RunListItem[]): {
  groups: WorkflowGroup[];
  standalone: RunListItem[];
} {
  const groups: WorkflowGroup[] = [];
  const groupByid = new Map<string, WorkflowGroup>();
  const standalone: RunListItem[] = [];

  for (const run of runs) {
    if (!run.workflow_id) {
      standalone.push(run);
      continue;
    }
    const existing = groupByid.get(run.workflow_id);
    if (existing) {
      existing.runs.push(run);
    } else {
      const group: WorkflowGroup = { workflowId: run.workflow_id, runs: [run] };
      groupByid.set(run.workflow_id, group);
      groups.push(group);
    }
  }

  return { groups, standalone };
}

function WorkflowGroupRow({
  group,
  workflow,
}: {
  group: WorkflowGroup;
  workflow: WorkflowDetail | undefined;
}) {
  const state = workflow ? deriveWorkflowState(workflow) : null;
  const status = state && workflow ? workflowStatusDisplay(workflow, state.phase) : null;
  const completedCount = workflow?.stages.filter((s) => s.status === "completed").length ?? 0;
  const totalStages = workflow?.stages.length ?? group.runs.length;

  return (
    <details>
      <summary className="flex cursor-pointer list-none items-center gap-4 px-3 py-3 text-slate-200 hover:bg-slate-800/40">
        <GitMerge className="h-4 w-4 shrink-0 text-indigo-400" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-slate-100">
            {workflow?.title ?? "Loading workflow…"}
          </p>
          <p className="text-xs text-slate-500">
            {completedCount}/{totalStages} stage{totalStages === 1 ? "" : "s"} complete
          </p>
        </div>
        {status && <StatusBadge label={status.label} tone={status.tone} />}
        <span className="text-xs text-slate-500">
          {group.runs[0]?.started_at ? formatRelativeTime(group.runs[0].started_at) : "—"}
        </span>
      </summary>
      <div className="border-t border-slate-800/70 bg-slate-950/40 pl-8">
        <Table columns={stageColumns} data={group.runs} getRowKey={(r) => r.run_id} />
      </div>
    </details>
  );
}

export function RunHistoryPage() {
  const { token } = useAuth();
  const { runs, total, page, hasMore, isLoading, error, setPage, refresh } = useRunHistory();
  const [workflowsById, setWorkflowsById] = useState<Record<string, WorkflowDetail>>({});

  const { groups, standalone } = groupByWorkflow(runs);

  useEffect(() => {
    if (!token || groups.length === 0) return;
    const missingIds = groups.map((g) => g.workflowId).filter((id) => !(id in workflowsById));
    if (missingIds.length === 0) return;
    let cancelled = false;
    void Promise.all(missingIds.map((id) => getWorkflow(token, id).catch(() => null))).then(
      (results) => {
        if (cancelled) return;
        setWorkflowsById((prev) => {
          const next = { ...prev };
          results.forEach((detail, i) => {
            if (detail) next[missingIds[i]] = detail;
          });
          return next;
        });
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, runs]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-violet-500/10 p-2 ring-1 ring-inset ring-violet-500/30">
            <History className="h-5 w-5 text-violet-400" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-50">Run History</h2>
            <p className="text-sm text-slate-400">
              {total} total run{total === 1 ? "" : "s"}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={isLoading}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
          aria-label="Refresh run history"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`}
            aria-hidden="true"
          />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {groups.length > 0 && (
        <Card title="Workflows" description="Multi-stage runs, grouped — expand to see each stage">
          <div className="divide-y divide-slate-800/70">
            {groups.map((group) => (
              <WorkflowGroupRow
                key={group.workflowId}
                group={group}
                workflow={workflowsById[group.workflowId]}
              />
            ))}
          </div>
        </Card>
      )}

      <Card title={groups.length > 0 ? "Standalone Runs" : undefined}>
        <Table
          columns={standaloneColumns}
          data={standalone}
          getRowKey={(row) => row.run_id}
          emptyMessage={
            isLoading
              ? "Loading…"
              : groups.length > 0
                ? "No standalone runs."
                : "No agent runs yet."
          }
        />

        {/* Pagination */}
        {total > 0 && (
          <div className="mt-4 flex items-center justify-between border-t border-slate-800 pt-4">
            <p className="text-xs text-slate-500">
              Page {page} of {Math.ceil(total / 25)}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage(page - 1)}
                disabled={page <= 1}
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="Previous page"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Prev
              </button>
              <button
                type="button"
                onClick={() => setPage(page + 1)}
                disabled={!hasMore}
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-30"
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
