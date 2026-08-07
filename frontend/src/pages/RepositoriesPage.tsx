import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { Card } from "../components/Card";
import { StatCard } from "../components/StatCard";
import { Table, type TableColumn } from "../components/Table";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../app/auth-context";
import { useDashboardData, type DashboardRepositoryRow } from "../hooks/useDashboardData";
import { getLatestIndexingJob, triggerIndexing } from "../lib/api/repositories";
import { repositoryHealthPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import type { IndexingJob } from "../types/graph";
import { FolderGit2, GitPullRequest, LayoutDashboard, Clock } from "lucide-react";

type IndexingFilter = "all" | "indexed" | "not_indexed" | "failed";

// Bulk indexing polls every selected repo's job status; capped so a hung
// backend job can't keep this loop (and its setState calls) running forever.
const BULK_INDEX_POLL_INTERVAL_MS = 1500;
const BULK_INDEX_POLL_MAX_MS = 5 * 60 * 1000;

function indexingStatusOf(job: IndexingJob | null | undefined): IndexingFilter {
  if (!job) return "not_indexed";
  if (job.status === "completed") return "indexed";
  if (job.status === "failed") return "failed";
  return "not_indexed";
}

export function RepositoriesPage() {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const { stats, repositories, isLoading, error } = useDashboardData();
  // KAN-37 — one cached, deduplicated query per repository's latest
  // indexing job, keyed identically to `RepositoryDetailPage`'s own poll of
  // the same endpoint, so navigating between the two never refetches a job
  // the other page just loaded. `.data` is `undefined` while in flight and
  // `null` once resolved with no job — the `jobsByRepoId[repo.id] ===
  // undefined` check below (rendered as "Loading…") depends on that
  // distinction, so a rejected fetch is caught to `null` (never indexed)
  // rather than left to throw.
  const jobQueries = useQueries({
    queries: repositories.map((repo) => ({
      queryKey: ["indexing-job", repo.id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        getLatestIndexingJob(token as string, repo.id, signal).catch(() => null),
      enabled: token !== null,
    })),
  });
  const jobsByRepoId: Record<string, IndexingJob | null | undefined> = Object.fromEntries(
    repositories.map((repo, i) => [repo.id, jobQueries[i]?.data]),
  );
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<IndexingFilter>("all");
  const [bulkProgress, setBulkProgress] = useState<{ current: number; total: number } | null>(null);
  const [bulkResult, setBulkResult] = useState<{ success: number; failed: number } | null>(null);
  // Tracks component lifetime — handleIndexSelected's poll loop below is
  // started from a click handler, not a `useEffect`, so it has no cleanup
  // path of its own otherwise, and would keep calling setState after this
  // page unmounts (route change) while a bulk index is still in flight.
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

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
    const startedAt = Date.now();
    while (pending.size > 0 && isMountedRef.current) {
      if (Date.now() - startedAt > BULK_INDEX_POLL_MAX_MS) {
        // Whatever's still pending is left as-is (still indexing on the
        // backend) rather than reported as failed — this loop is just
        // giving up on watching it, not cancelling the actual job.
        break;
      }
      const statuses = await Promise.all(
        Array.from(pending).map((id) =>
          getLatestIndexingJob(token, id)
            .catch(() => null)
            .then((job) => [id, job] as const),
        ),
      );
      if (!isMountedRef.current) return;
      for (const [id, job] of statuses) {
        if (job?.status === "completed" || job?.status === "failed") {
          pending.delete(id);
          if (job.status === "completed") success++;
          else failed++;
          // Writes straight into the same cache entry `jobQueries` reads
          // above, rather than a separate bit of local state — the row
          // updates immediately without a redundant refetch of what this
          // loop just fetched itself.
          queryClient.setQueryData(["indexing-job", id], job);
        }
      }
      setBulkProgress({ current: ids.length - pending.size, total: ids.length });
      if (pending.size > 0) {
        await new Promise((resolve) => setTimeout(resolve, BULK_INDEX_POLL_INTERVAL_MS));
      }
    }

    if (!isMountedRef.current) return;
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
          checked={
            filteredRepositories.length > 0 && selectedIds.size === filteredRepositories.length
          }
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
      sortValue: (repo) => repo.fullName.toLowerCase(),
    },
    {
      key: "source",
      header: "Source",
      render: (repo) => (
        <StatusBadge
          label={repo.source === "local" ? "Local" : "GitHub"}
          tone={repo.source === "local" ? "info" : "neutral"}
        />
      ),
      sortValue: (repo) => repo.source,
    },
    {
      key: "health",
      header: "Health",
      render: (repo) => {
        const { label, tone } = repositoryHealthPresentation(repo.health);
        return <StatusBadge label={label} tone={tone} />;
      },
      sortValue: (repo) => repositoryHealthPresentation(repo.health).label,
    },
    {
      key: "openPrs",
      header: "Open PRs",
      render: (repo) => repo.openPullRequests,
      sortValue: (repo) => repo.openPullRequests,
    },
    {
      key: "indexing",
      header: "Indexing status",
      render: (repo) => {
        const job = jobsByRepoId[repo.id];
        if (job === undefined) return <span className="text-xs text-fg-muted">Loading…</span>;
        if (!job) return <StatusBadge label="Not indexed" tone="neutral" />;
        if (job.status === "completed") return <StatusBadge label="Indexed" tone="success" />;
        if (job.status === "failed") return <StatusBadge label="Index failed" tone="danger" />;
        return <StatusBadge label="Indexing…" tone="info" />;
      },
      // Ranked, not alphabetical — "failed" first (needs attention), then
      // "not indexed"/"indexing" (in progress or pending), "indexed" last
      // (nothing to do). Loading is a transient client state, not a real
      // status, so it sorts with "not indexed" rather than getting its own
      // rank the data will never actually settle on.
      sortValue: (repo) => {
        const job = jobsByRepoId[repo.id];
        const status = indexingStatusOf(job);
        // `indexingStatusOf` never actually returns "all" (that value only
        // exists for the page's own filter dropdown) — the fallback is here
        // purely so this stays exhaustive against IndexingFilter's full type
        // rather than assuming that stays true forever.
        const rank: Record<IndexingFilter, number> = {
          failed: 0,
          not_indexed: 1,
          indexed: 2,
          all: 1,
        };
        return rank[status];
      },
    },
    {
      key: "lastIndexed",
      header: "Last indexed",
      render: (repo) => {
        const job = jobsByRepoId[repo.id];
        return job?.finished_at ? formatRelativeTime(job.finished_at) : "—";
      },
      sortValue: (repo) => {
        const job = jobsByRepoId[repo.id];
        return job?.finished_at ? new Date(job.finished_at).getTime() : null;
      },
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-fg">Repositories</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Repositories tracked and indexed by GraphForge.
        </p>
      </div>

      {/* Operational snapshot — moved here from the Dashboard, which now
          only answers "what am I working on / what's next / what
          happened", not ambient repository/PR metrics. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Repositories monitored"
          value={isLoading ? "—" : String(stats.repositoriesMonitored)}
          hint={`across ${stats.organizationCount} organization${stats.organizationCount === 1 ? "" : "s"}`}
          icon={FolderGit2}
        />
        <StatCard
          label="Open pull requests"
          value={isLoading ? "—" : String(stats.openPullRequestCount)}
          hint={`${stats.awaitingAnalysisCount} awaiting analysis`}
          icon={GitPullRequest}
        />
        <StatCard
          label="High risk changes"
          value={isLoading ? "—" : String(stats.highRiskThisWeekCount)}
          hint="critical or high this week"
          icon={LayoutDashboard}
        />
        <StatCard
          label="Avg. indexing time"
          value={isLoading ? "—" : stats.avgIndexingTimeLabel}
          hint="per repository"
          icon={Clock}
        />
      </div>

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
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
                    ? "border-info-line bg-info-bg text-info-fg"
                    : "border-line text-fg-secondary hover:border-line-strong"
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
                <span className="text-xs text-fg-muted">{selectedIds.size} selected</span>
                <button
                  type="button"
                  onClick={() => setSelectedIds(new Set())}
                  className="text-xs text-fg-muted underline hover:text-fg-secondary"
                >
                  Clear selection
                </button>
              </>
            )}
            <button
              type="button"
              onClick={() => void handleIndexSelected()}
              disabled={selectedIds.size === 0 || bulkProgress !== null}
              className="rounded-md bg-info-solid px-3 py-1.5 text-sm font-medium text-info-on-solid hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {bulkProgress
                ? `Indexing ${bulkProgress.current} of ${bulkProgress.total} repositories…`
                : "Index Selected"}
            </button>
          </div>
        </div>

        {bulkResult && (
          <div className="mb-4 rounded-md border border-line bg-surface px-3 py-2 text-sm">
            <span className="text-success-fg">✓ Successfully indexed: {bulkResult.success}</span>
            {bulkResult.failed > 0 && (
              <span className="ml-4 text-danger-fg">⚠ Failed: {bulkResult.failed}</span>
            )}
          </div>
        )}

        <Table
          columns={columns}
          data={filteredRepositories}
          getRowKey={(repo) => repo.id}
          emptyMessage={
            isLoading
              ? "Loading…"
              : repositories.length === 0
                ? "No repositories tracked yet. Connect GitHub and select repositories in Settings → Integrations."
                : "No repositories match this filter."
          }
        />
      </Card>
    </div>
  );
}
