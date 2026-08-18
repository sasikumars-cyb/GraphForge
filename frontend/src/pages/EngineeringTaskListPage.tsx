import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ListChecks, Plus } from "lucide-react";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";
import { Table, type TableColumn } from "../components/Table";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { listEngineeringTasks } from "../lib/api/engineeringTasks";
import { classificationPresentation } from "../lib/engineeringTaskClassification";
import { formatRelativeTime } from "../lib/formatDate";
import type { EngineeringTaskSummary } from "../types/engineeringTask";

/**
 * Engineering Tasks list — Phase 7.2's productization of the Phase 7/7.1
 * Engineering State stack. Read-only, backed by `GET /engineering-tasks`
 * (in turn built from `fold()` over Engineering State — no separate
 * database projection). The only mutating action reachable from this page
 * is the "New Engineering Task" link to `/engineering-tasks/new`, a
 * genuinely separate page — this page itself performs no write.
 */

function DescriptionCell({ row }: { row: EngineeringTaskSummary }) {
  return (
    <Link to={`/engineering-tasks/${row.task_id}`} className="block hover:underline">
      <p className="truncate font-medium text-fg" title={row.description}>
        {row.description}
      </p>
      <p className="truncate font-mono text-xs text-fg-muted">{row.task_id}</p>
    </Link>
  );
}

function ClassificationCell({ row }: { row: EngineeringTaskSummary }) {
  const { label, tone } = classificationPresentation(row.classification);
  return <StatusBadge label={label} tone={tone} />;
}

const COLUMNS: TableColumn<EngineeringTaskSummary>[] = [
  {
    key: "description",
    header: "Goal",
    render: (row) => <DescriptionCell row={row} />,
    sortValue: (row) => row.description,
  },
  {
    key: "classification",
    header: "Status",
    render: (row) => <ClassificationCell row={row} />,
    sortValue: (row) => row.classification,
  },
  {
    key: "created_at",
    header: "Created",
    render: (row) => (
      <span className="text-sm text-fg-muted">{formatRelativeTime(row.created_at)}</span>
    ),
    sortValue: (row) => new Date(row.created_at).getTime(),
  },
  {
    key: "updated_at",
    header: "Updated",
    render: (row) => (
      <span className="text-sm text-fg-muted">{formatRelativeTime(row.updated_at)}</span>
    ),
    sortValue: (row) => new Date(row.updated_at).getTime(),
  },
];

export function EngineeringTaskListPage() {
  const { token } = useAuth();
  const [tasks, setTasks] = useState<EngineeringTaskSummary[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    let cancelled = false;

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const result = await listEngineeringTasks(token!, controller.signal);
        if (!cancelled) setTasks(result);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to load Engineering Tasks.",
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [token]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-accent-bg p-2 ring-1 ring-inset ring-accent-line/30">
            <ListChecks className="h-5 w-5 text-accent-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="font-display text-xl font-bold tracking-tight text-fg">
              Engineering Tasks
            </h1>
            <p className="text-sm text-fg-muted">
              Goal → Plan → Execution → Verification, tracked as Engineering State.
            </p>
          </div>
        </div>
        <Link
          to="/engineering-tasks/new"
          className="focus-ring inline-flex items-center gap-1.5 rounded-lg bg-accent-solid px-3 py-1.5 text-xs font-semibold text-accent-on-solid shadow-xs transition-colors hover:brightness-110"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          New Engineering Task
        </Link>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      )}

      {isLoading ? (
        <Card>
          <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading Engineering Tasks">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-12 animate-pulse rounded-lg bg-surface-raised" />
            ))}
          </div>
        </Card>
      ) : !error && tasks !== null && tasks.length === 0 ? (
        <Card>
          <EmptyState
            title="Engineering Tasks appear here"
            description="An Engineering Task is a Goal GraphForge plans, executes, and independently verifies, with a full record of what happened. Create the first one to see it here."
            actions={[{ label: "New Engineering Task", to: "/engineering-tasks/new" }]}
          />
        </Card>
      ) : tasks !== null && tasks.length > 0 ? (
        <Card title="All tasks" description={`${tasks.length} tracked`}>
          <Table
            columns={COLUMNS}
            data={tasks}
            getRowKey={(row) => row.task_id}
            emptyMessage="No Engineering Tasks."
          />
        </Card>
      ) : null}
    </div>
  );
}
