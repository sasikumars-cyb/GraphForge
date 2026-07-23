import { Link } from "react-router-dom";
import { History, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { formatRelativeTime } from "../lib/formatDate";
import { useRunHistory } from "../hooks/useRunHistory";
import type { RunListItem } from "../types/agent";

const GOAL_LABELS: Record<string, string> = {
  plan_freeform: "Planning",
  review_pr: "PR Review",
};

const columns: TableColumn<RunListItem>[] = [
  {
    key: "subject",
    header: "Subject",
    render: (row) => (
      <Link
        to={`/runs/${row.run_id}`}
        className="block hover:underline"
      >
        <p className="truncate font-medium text-slate-100" title={row.subject.display_name}>
          {row.subject.display_name || row.subject.subject_id}
        </p>
        <p className="text-xs text-slate-500">{row.subject.subject_type}</p>
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
  {
    key: "status",
    header: "Status",
    render: (row) => <RunStatusBadge status={row.status} />,
  },
  {
    key: "confidence",
    header: "Confidence",
    render: (row) =>
      row.confidence_score != null ? (
        <ConfidenceBadge confidence={{ score: row.confidence_score, reasoning: "" }} />
      ) : (
        <span className="text-xs text-slate-500">—</span>
      ),
  },
  {
    key: "started",
    header: "Started",
    render: (row) =>
      row.started_at ? (
        <span className="text-sm text-slate-400">{formatRelativeTime(row.started_at)}</span>
      ) : (
        <span className="text-xs text-slate-500">—</span>
      ),
  },
  {
    key: "duration",
    header: "Duration",
    render: (row) => {
      if (!row.started_at || !row.completed_at) return <span className="text-xs text-slate-500">—</span>;
      const ms = new Date(row.completed_at).getTime() - new Date(row.started_at).getTime();
      return <span className="text-sm text-slate-400">{(ms / 1000).toFixed(1)}s</span>;
    },
  },
];

export function RunHistoryPage() {
  const { runs, total, page, hasMore, isLoading, error, setPage, refresh } = useRunHistory();

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
          <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} aria-hidden="true" />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      <Card>
        <Table
          columns={columns}
          data={runs}
          getRowKey={(row) => row.run_id}
          emptyMessage={isLoading ? "Loading…" : "No agent runs yet."}
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
