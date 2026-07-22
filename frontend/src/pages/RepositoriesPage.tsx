import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../app/auth-context";
import { useDashboardData, type DashboardRepositoryRow } from "../hooks/useDashboardData";
import { getLatestIndexingJob, triggerIndexing } from "../lib/api/repositories";
import { repositoryHealthPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import type { IndexingJob } from "../types/graph";

type IndexingFilter = "all" | "indexed" | "not_indexed" | "failed";

function indexingStatusOf(job: IndexingJob | null | undefined): IndexingFilter {
  if (!job) return "not_indexed";
  if (job.status === "completed") return "indexed";
  if (job.status === "failed") return "failed";
  return "not_indexed";
}

export function RepositoriesPage() {
  const { token } = useAuth();
  const { repositories, isLoading, error } = useDashboardData();
  const [jobsByRepoId, setJobsByRepoId] = useState<Record<string, IndexingJob | null>>({});
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<IndexingFilter>("all");
  const [bulkProgress, setBulkProgress] = useState<{ current: number; total: number } | null>(
    null,
  );
  const [bulkResult, setBulkResult] = useState<{ success: number; failed: number } | null>(null);

  // Reuses the same GET .../index (latest job) endpoint the repository
  // detail page already polls with - just once per row here, not repeatedly.
  useEffect(() => {
    if (!token || repositories.length === 0) return;
    let cancelled = false;
    void Promise.all(
      repositories.map((repo) =>
        getLatestIndexingJob(token, repo.id)
          .catch(() => null)
          .then((job) => [repo.id, job] as const),
      ),
    ).then((entries) => {
      if (!cancelled) setJobsByRepoId(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [token, repositories]);

  const filteredRepositories =
    filter === "all"
      ? repositories
      : repositories.filter((repo) => indexingStatusOf(jobsByRepoId[repo.id]) === filter);

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelectedIds((prev) =>
      prev.size === filteredRepositories.length
        ? new Set()
        : new Set(filteredRepositories.map((r) => r.id)),
    );
  }

  async function handleIndexSelected() {
    if (!token || selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    setBulkResult(null);
    setBulkProgress({ current: 0, total: ids.length });

    // Reuses the existing single-repository indexing endpoint for every
    // selected repo - the backend already schedules each as its own
    // independent background job, so concurrent requests are already
    // supported; no new backend API is needed for this.
    await Promise.all(ids.map((id) => triggerIndexing(token, id).catch(() => null)));

    const pending = new Set(ids);
    let success = 0;
    let failed = 0;
    while (pending.size > 0) {
      const statuses = await Promise.all(
        Array.from(pending).map((id) =>
          getLatestIndexingJob(token, id)
            .catch(() => null)
            .then((job) => [id, job] as const),
        ),
      );
      for (const [id, job] of statuses) {
        if (job?.status === "completed" || job?.status === "failed") {
          pending.delete(id);
          if (job.status === "completed") success++;
          else failed++;
          setJobsByRepoId((prev) => ({ ...prev, [id]: job }));
        }
      }
      setBulkProgress({ current: ids.length - pending.size, total: ids.length });
      if (pending.size > 0) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    }

    setBulkProgress(null);
    setBulkResult({ success, failed });
    setSelectedIds(new Set());
  }

  const columns: TableColumn<DashboardRepositoryRow>[] = [
    {
      key: "select",
      header: (
        <input
          type="checkbox"
          checked={filteredRepositories.length > 0 && selectedIds.size === filteredRepositories.length}
          onChange={toggleAll}
          aria-label="Select all repositories"
        />
      ),
      render: (repo) => (
        <input
          type="checkbox"
          checked={selectedIds.has(repo.id)}
          onChange={() => toggleOne(repo.id)}
          aria-label={`Select ${repo.fullName}`}
        />
      ),
    },
    {
      key: "name",
      header: "Repository",
      render: (repo) => (
        <Link to={`/repositories/${repo.id}`} className="hover:underline">
          {repo.fullName}
        </Link>
      ),
    },
    {
      key: "health",
      header: "Health",
      render: (repo) => {
        const { label, tone } = repositoryHealthPresentation(repo.health);
        return <StatusBadge label={label} tone={tone} />;
      },
    },
    { key: "openPrs", header: "Open PRs", render: (repo) => repo.openPullRequests },
    {
      key: "indexing",
      header: "Indexing status",
      render: (repo) => {
        const job = jobsByRepoId[repo.id];
        if (job === undefined) return <span className="text-xs text-slate-500">Loading…</span>;
        if (!job) return <StatusBadge label="Not indexed" tone="neutral" />;
        if (job.status === "completed")
          return <StatusBadge label="Indexed" tone="success" />;
        if (job.status === "failed") return <StatusBadge label="Index failed" tone="danger" />;
        return <StatusBadge label="Indexing…" tone="info" />;
      },
    },
    {
      key: "lastIndexed",
      header: "Last indexed",
      render: (repo) => {
        const job = jobsByRepoId[repo.id];
        return job?.finished_at ? formatRelativeTime(job.finished_at) : "—";
      },
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Repositories</h2>
        <p className="mt-1 text-sm text-slate-400">
          Repositories tracked and indexed by ChangeGuard.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      <Card>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            {(["all", "indexed", "not_indexed", "failed"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setFilter(option)}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
                  filter === option
                    ? "border-sky-500 bg-sky-500/10 text-sky-300"
                    : "border-slate-700 text-slate-300 hover:border-slate-500"
                }`}
              >
                {option === "all"
                  ? "All"
                  : option === "indexed"
                    ? "Indexed"
                    : option === "not_indexed"
                      ? "Not Indexed"
                      : "Index Failed"}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3">
            {selectedIds.size > 0 && (
              <>
                <span className="text-xs text-slate-400">{selectedIds.size} selected</span>
                <button
                  type="button"
                  onClick={() => setSelectedIds(new Set())}
                  className="text-xs text-slate-400 underline hover:text-slate-200"
                >
                  Clear selection
                </button>
              </>
            )}
            <button
              type="button"
              onClick={() => void handleIndexSelected()}
              disabled={selectedIds.size === 0 || bulkProgress !== null}
              className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {bulkProgress
                ? `Indexing ${bulkProgress.current} of ${bulkProgress.total} repositories…`
                : "Index Selected"}
            </button>
          </div>
        </div>

        {bulkResult && (
          <div className="mb-4 rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm">
            <span className="text-emerald-400">✓ Successfully indexed: {bulkResult.success}</span>
            {bulkResult.failed > 0 && (
              <span className="ml-4 text-rose-400">⚠ Failed: {bulkResult.failed}</span>
            )}
          </div>
        )}

        <Table
          columns={columns}
          data={filteredRepositories}
          getRowKey={(repo) => repo.id}
          emptyMessage={isLoading ? "Loading…" : "No repositories match this filter."}
        />
      </Card>
    </div>
  );
}
