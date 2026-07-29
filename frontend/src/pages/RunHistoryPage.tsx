import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { History, ChevronLeft, ChevronRight, RefreshCw, GitMerge, Trash2 } from "lucide-react";
import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { Table, type TableColumn } from "../components/Table";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { formatRelativeTime } from "../lib/formatDate";
import { deriveWorkflowState, stageLabel, workflowStatusDisplay } from "../lib/workflowDerived";
import { useRunHistory } from "../hooks/useRunHistory";
import { useAuth } from "../app/auth-context";
import { getWorkflow, deleteWorkflow } from "../lib/api/workflows";
import { deleteAgentRun } from "../lib/api/agentRuns";
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
    <span className="text-xs text-fg-muted">—</span>
  );
}

function StartedCell({ row }: { row: RunListItem }) {
  return row.started_at ? (
    <span className="text-sm text-fg-muted">{formatRelativeTime(row.started_at)}</span>
  ) : (
    <span className="text-xs text-fg-muted">—</span>
  );
}

function DurationCell({ row }: { row: RunListItem }) {
  if (!row.started_at || !row.completed_at)
    return <span className="text-xs text-fg-muted">—</span>;
  const ms = new Date(row.completed_at).getTime() - new Date(row.started_at).getTime();
  return <span className="text-sm text-fg-muted">{(ms / 1000).toFixed(1)}s</span>;
}

function ProviderCell({ row }: { row: RunListItem }) {
  return row.provider ? (
    <span className="text-sm text-fg-secondary">{row.provider}</span>
  ) : (
    <span className="text-xs text-fg-muted">—</span>
  );
}

function DeleteRunButton({ row, onDeleted }: { row: RunListItem; onDeleted: () => void }) {
  const { token } = useAuth();
  const [isDeleting, setIsDeleting] = useState(false);
  const isActive = row.status === "queued" || row.status === "running";

  async function handleDelete() {
    if (!token) return;
    const confirmMsg = isActive
      ? "This run is still in progress — deleting it will cancel and remove it. Continue?"
      : "Delete this run? This can't be undone.";
    if (!window.confirm(confirmMsg)) return;
    setIsDeleting(true);
    try {
      await deleteAgentRun(token, row.run_id);
      onDeleted();
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void handleDelete()}
      disabled={isDeleting}
      title={isActive ? "Cancel and delete this run" : "Delete run"}
      className="text-fg-muted hover:text-danger-fg disabled:cursor-not-allowed disabled:opacity-30"
    >
      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
    </button>
  );
}

// Standalone (non-workflow) runs — unchanged from before grouping was added.
function buildStandaloneColumns(onDeleted: () => void): TableColumn<RunListItem>[] {
  return [
    {
      key: "subject",
      header: "Title",
      render: (row) => (
        <Link to={`/runs/${row.run_id}`} className="block hover:underline">
          <p
            className="truncate font-medium text-fg"
            title={row.title ?? row.subject.display_name}
          >
            {row.title ?? row.subject.display_name ?? row.subject.subject_id}
          </p>
          <p className="truncate text-xs text-fg-muted">
            {row.repository ?? row.subject.subject_type}
          </p>
        </Link>
      ),
    },
    {
      key: "goal",
      header: "Goal",
      render: (row) => (
        <span className="text-sm text-fg-secondary">{GOAL_LABELS[row.goal] ?? row.goal}</span>
      ),
    },
    { key: "status", header: "Status", render: (row) => <StatusCell row={row} /> },
    { key: "provider", header: "Provider", render: (row) => <ProviderCell row={row} /> },
    { key: "confidence", header: "Confidence", render: (row) => <ConfidenceCell row={row} /> },
    { key: "started", header: "Started", render: (row) => <StartedCell row={row} /> },
    { key: "duration", header: "Duration", render: (row) => <DurationCell row={row} /> },
    {
      key: "actions",
      header: "",
      render: (row) => <DeleteRunButton row={row} onDeleted={onDeleted} />,
    },
  ];
}

// A workflow's stage runs, shown inside its expanded group — same cell
// renderers as buildStandaloneColumns, "Subject" swapped for "Stage" since every
// row here already belongs to one known workflow.
function buildStageColumns(onDeleted: () => void): TableColumn<RunListItem>[] {
  return [
    {
      key: "stage",
      header: "Stage",
      render: (row) => (
        <Link to={`/runs/${row.run_id}`} className="block hover:underline">
          <p className="text-sm font-medium text-fg">
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
    {
      key: "actions",
      header: "",
      render: (row) => <DeleteRunButton row={row} onDeleted={onDeleted} />,
    },
  ];
}

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
  onRunDeleted,
}: {
  group: WorkflowGroup;
  workflow: WorkflowDetail | undefined;
  onRunDeleted: () => void;
}) {
  const { token } = useAuth();
  const [isDeleting, setIsDeleting] = useState(false);
  const stageColumns = buildStageColumns(onRunDeleted);
  const state = workflow ? deriveWorkflowState(workflow) : null;
  const status = state && workflow ? workflowStatusDisplay(workflow, state.phase) : null;
  const completedCount = workflow?.stages.filter((s) => s.status === "completed").length ?? 0;
  const totalStages = workflow?.stages.length ?? group.runs.length;

  async function handleDeleteWorkflow(e: React.MouseEvent) {
    e.stopPropagation();
    if (!token) return;
    if (!window.confirm("Delete this workflow and all its stage runs? This can't be undone.")) return;
    setIsDeleting(true);
    try {
      await deleteWorkflow(token, group.workflowId);
      onRunDeleted();
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <details>
      <summary className="flex cursor-pointer list-none items-center gap-4 px-3 py-3 text-fg-secondary hover:bg-surface-raised">
        <GitMerge className="h-4 w-4 shrink-0 text-cat-7-fg" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <Link
            to={`/workflows/${group.workflowId}`}
            onClick={(e) => e.stopPropagation()}
            className="truncate text-sm font-medium text-fg hover:text-accent-fg hover:underline"
          >
            {workflow?.title ?? "Loading workflow…"}
          </Link>
          <p className="text-xs text-fg-muted">
            {completedCount}/{totalStages} stage{totalStages === 1 ? "" : "s"} complete
          </p>
        </div>
        {status && <StatusBadge label={status.label} tone={status.tone} />}
        <span className="text-xs text-fg-muted">
          {group.runs[0]?.started_at ? formatRelativeTime(group.runs[0].started_at) : "—"}
        </span>
        <button
          type="button"
          onClick={(e) => void handleDeleteWorkflow(e)}
          disabled={isDeleting}
          title="Delete workflow"
          className="shrink-0 text-fg-muted hover:text-danger-fg disabled:cursor-not-allowed disabled:opacity-30"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </summary>
      <div className="border-t border-line-muted bg-canvas pl-8">
        {workflow?.original_prompt && (
          <p className="whitespace-pre-wrap px-3 py-2.5 text-xs text-fg-muted">
            <span className="font-medium text-fg-secondary">Original request: </span>
            {workflow.original_prompt}
          </p>
        )}
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
          <div className="rounded-lg bg-cat-7-bg p-2 ring-1 ring-inset ring-cat-7-line/30">
            <History className="h-5 w-5 text-cat-7-fg" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-fg">Run History</h2>
            <p className="text-sm text-fg-muted">
              {total} total run{total === 1 ? "" : "s"}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={isLoading}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary disabled:opacity-50"
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
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      )}

      {groups.length > 0 && (
        <Card title="Workflows" description="Multi-stage runs, grouped — expand to see each stage">
          <div className="divide-y divide-line-muted">
            {groups.map((group) => (
              <WorkflowGroupRow
                key={group.workflowId}
                group={group}
                workflow={workflowsById[group.workflowId]}
                onRunDeleted={refresh}
              />
            ))}
          </div>
        </Card>
      )}

      <Card title={groups.length > 0 ? "Standalone Runs" : undefined}>
        <Table
          columns={buildStandaloneColumns(refresh)}
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
          <div className="mt-4 flex items-center justify-between border-t border-line-muted pt-4">
            <p className="text-xs text-fg-muted">
              Page {page} of {Math.ceil(total / 25)}
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
