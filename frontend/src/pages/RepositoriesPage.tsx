import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { StatCard } from "../components/StatCard";
import { Pagination } from "../components/Pagination";
import { Table, type TableColumn } from "../components/Table";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../app/auth-context";
import {
  formatIndexingDuration,
  useDebounced,
  useRepositoriesOverview,
  type RepositoryOverviewItem,
} from "../hooks/useRepositoriesOverview";
import { getLatestIndexingJob, triggerIndexing } from "../lib/api/repositories";
import { repositoryHealthPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  FolderGit2,
  GitPullRequest,
  LayoutDashboard,
  Search,
} from "lucide-react";

type IndexingFilter = "all" | "indexed" | "not_indexed" | "failed";
type HealthFilter = "all" | "critical" | "attention" | "healthy";

const PAGE_SIZE_OPTIONS = [24, 48, 96];

// Bulk indexing polls every selected repo's job status; capped so a hung
// backend job can't keep this loop (and its setState calls) running forever.
const BULK_INDEX_POLL_INTERVAL_MS = 1500;
const BULK_INDEX_POLL_MAX_MS = 5 * 60 * 1000;

const INDEXING_FILTER_LABELS: Record<IndexingFilter, string> = {
  all: "All",
  indexed: "Indexed",
  not_indexed: "Not indexed",
  failed: "Index failed",
};

const HEALTH_FILTER_LABELS: Record<HealthFilter, string> = {
  all: "All",
  critical: "Critical",
  attention: "Needs attention",
  healthy: "Healthy",
};

/**
 * Repositories — a paginated, server-filtered list.
 *
 * Every figure on this page (health, open-PR counts, indexing status,
 * headline stats) comes from a single `GET /repositories/overview` request
 * for the *current page*. It used to be assembled client-side from one PR
 * list and one indexing job per repository plus one analysis per open PR,
 * and rendered as one card per repository with no pagination at all — both
 * the request count and the DOM grew with the size of the account rather
 * than the size of the screen, which is untenable at a few hundred
 * repositories and hopeless at a thousand.
 */
export function RepositoriesPage() {
  const { token } = useAuth();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE_OPTIONS[0]);
  const [search, setSearch] = useState("");
  const [indexingFilter, setIndexingFilter] = useState<IndexingFilter>("all");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("all");
  const debouncedSearch = useDebounced(search);

  const { items, stats, total, isLoading, error, refetch } = useRepositoriesOverview({
    page,
    pageSize,
    q: debouncedSearch,
    indexing: indexingFilter,
    health: healthFilter,
  });

  // Any narrowing of the result set invalidates the current page number —
  // staying on page 7 of a filter that now has two pages shows an empty
  // list with rows that do exist just out of reach.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, indexingFilter, healthFilter, pageSize]);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
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

  const pageIds = useMemo(() => items.map((item) => item.id), [items]);
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Selection is per-page but *cumulative* — paging away no longer silently
  // drops what you'd already picked, which matters now that reaching a
  // given repository can take several pages.
  function toggleAllOnPage() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) pageIds.forEach((id) => next.delete(id));
      else pageIds.forEach((id) => next.add(id));
      return next;
    });
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
    // The rows this loop just changed are server-derived — re-read them
    // rather than patching status client-side and drifting from the same
    // page's own stats.
    refetch();
  }

  const columns: TableColumn<RepositoryOverviewItem>[] = [
    {
      key: "select",
      header: (
        <input
          type="checkbox"
          checked={allOnPageSelected}
          onChange={toggleAllOnPage}
          aria-label="Select all repositories on this page"
        />
      ),
      render: (repo) => (
        <input
          type="checkbox"
          checked={selectedIds.has(repo.id)}
          onChange={() => toggleOne(repo.id)}
          aria-label={`Select ${repo.full_name}`}
        />
      ),
    },
    {
      key: "name",
      header: "Repository",
      render: (repo) => (
        <Link to={`/repositories/${repo.id}`} className="hover:underline">
          {repo.full_name}
        </Link>
      ),
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
    },
    {
      key: "health",
      header: "Health",
      render: (repo) => {
        const { label, tone } = repositoryHealthPresentation(repo.health);
        return <StatusBadge label={label} tone={tone} />;
      },
    },
    {
      key: "openPrs",
      header: "Open PRs",
      render: (repo) => repo.open_pull_requests,
    },
    {
      key: "indexing",
      header: "Indexing status",
      render: (repo) => <IndexingBadge repo={repo} />,
    },
    {
      key: "lastIndexed",
      header: "Last indexed",
      render: (repo) => (repo.last_indexed_at ? formatRelativeTime(repo.last_indexed_at) : "—"),
    },
  ];

  // Column sorting was client-side over the full list; with the list now
  // paginated server-side it could only ever sort the visible page, which
  // reads as sorting but isn't. The server's own ordering (most urgent
  // first, then alphabetical) is applied across every page instead, and
  // the filters above are how you narrow to what you're looking for.

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-fg">Repositories</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Repositories tracked and indexed by GraphForge.
        </p>
      </div>

      {/* Operational snapshot — account-wide, so it deliberately does not
          move when the filters below narrow the list. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Repositories monitored"
          value={isLoading ? "—" : stats.repositories_monitored.toLocaleString()}
          hint={`across ${stats.organization_count} organization${stats.organization_count === 1 ? "" : "s"}`}
          icon={FolderGit2}
        />
        <StatCard
          label="Open pull requests"
          value={isLoading ? "—" : stats.open_pull_request_count.toLocaleString()}
          hint={`${stats.awaiting_analysis_count} awaiting analysis`}
          icon={GitPullRequest}
        />
        <StatCard
          label="High risk changes"
          value={isLoading ? "—" : stats.high_risk_this_week_count.toLocaleString()}
          hint="critical or high this week"
          icon={LayoutDashboard}
        />
        <StatCard
          label="Avg. indexing time"
          value={isLoading ? "—" : formatIndexingDuration(stats.avg_indexing_time_ms)}
          hint="per repository"
          icon={Clock}
        />
      </div>

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      )}

      {/* Search + filters drive both views below — one set of controls, so
          the card grid and the management table can never disagree about
          which repositories are under discussion. */}
      <div className="flex flex-col gap-3">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle"
            aria-hidden="true"
          />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search repositories by name…"
            aria-label="Search repositories"
            className="focus-ring w-full rounded-lg border border-line bg-surface py-2 pl-9 pr-3 text-sm text-fg placeholder:text-fg-subtle"
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <FilterChips
            legend="Indexing"
            options={["all", "indexed", "not_indexed", "failed"] as const}
            labels={INDEXING_FILTER_LABELS}
            value={indexingFilter}
            onChange={setIndexingFilter}
          />
          <FilterChips
            legend="Health"
            options={["all", "critical", "attention", "healthy"] as const}
            labels={HEALTH_FILTER_LABELS}
            value={healthFilter}
            onChange={setHealthFilter}
          />
        </div>
      </div>

      {/* ── Repository intelligence — "why should I care", not "here's a
          database row". One page of cards, never the whole account. ── */}
      {isLoading && items.length === 0 ? (
        <p className="py-16 text-center text-sm text-fg-muted">Loading…</p>
      ) : items.length === 0 ? (
        <Card>
          <p className="py-12 text-center text-sm text-fg-muted">
            {stats.repositories_monitored === 0
              ? "No repositories tracked yet. Connect GitHub and select repositories in Settings → Integrations."
              : "No repositories match these filters."}
          </p>
        </Card>
      ) : (
        <RepositoryIntelligenceGrid repositories={items} />
      )}

      {total > 0 && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          itemLabel="repositories"
          pageSizeOptions={PAGE_SIZE_OPTIONS}
          onPageSizeChange={setPageSize}
        />
      )}

      <details className="group rounded-xl border border-line-muted open:bg-surface/40">
        <summary className="focus-ring flex cursor-pointer list-none items-center justify-between gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-fg-secondary hover:bg-surface-raised">
          <span>Manage &amp; bulk index</span>
          <ChevronDown
            className="h-4 w-4 shrink-0 text-fg-muted transition-transform group-open:rotate-180"
            aria-hidden="true"
          />
        </summary>
        <div className="px-4 pb-4">
          <Card>
            {/* Shows exactly the page of repositories listed above rather
                than every repository in the account — expanding this used
                to render the entire list in one go. */}
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-fg-muted">
                Showing the {items.length.toLocaleString()} repositor
                {items.length === 1 ? "y" : "ies"} on this page. Use the search and filters above to
                reach others — selections are kept as you page.
              </p>
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
                <span className="text-success-fg">
                  ✓ Successfully indexed: {bulkResult.success}
                </span>
                {bulkResult.failed > 0 && (
                  <span className="ml-4 text-danger-fg">⚠ Failed: {bulkResult.failed}</span>
                )}
              </div>
            )}

            <Table
              columns={columns}
              data={items}
              getRowKey={(repo) => repo.id}
              emptyMessage={isLoading ? "Loading…" : "No repositories match these filters."}
            />

            {total > 0 && (
              <div className="mt-4">
                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={total}
                  onPageChange={setPage}
                  itemLabel="repositories"
                />
              </div>
            )}
          </Card>
        </div>
      </details>
    </div>
  );
}

function FilterChips<T extends string>({
  legend,
  options,
  labels,
  value,
  onChange,
}: {
  legend: string;
  options: readonly T[];
  labels: Record<T, string>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium uppercase tracking-wide text-fg-subtle">{legend}</span>
      {options.map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
            value === option
              ? "border-info-line bg-info-bg text-info-fg"
              : "border-line text-fg-secondary hover:border-line-strong"
          }`}
        >
          {labels[option]}
        </button>
      ))}
    </div>
  );
}

function IndexingBadge({ repo }: { repo: RepositoryOverviewItem }) {
  if (repo.indexing_in_progress) return <StatusBadge label="Indexing…" tone="info" />;
  if (repo.indexing_status === "indexed") return <StatusBadge label="Indexed" tone="success" />;
  if (repo.indexing_status === "failed") return <StatusBadge label="Index failed" tone="danger" />;
  return <StatusBadge label="Not indexed" tone="neutral" />;
}

/** Health/indexing/activity, in one glance per repository — the "why
 * should I care" read the plain table couldn't give without opening every
 * row. Every field is real and server-derived: `health` from actual PR
 * risk analyses, `open_pull_requests` and `source` as-tracked, indexing
 * status from the repository's latest indexing job. Ordering (most urgent
 * first, then alphabetical) is the server's, so it holds across pages
 * rather than only within the slice that happens to be loaded. */
function RepositoryIntelligenceGrid({
  repositories,
}: {
  repositories: RepositoryOverviewItem[];
}) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {repositories.map((repo) => {
        const { label: healthLabel, tone: healthTone } = repositoryHealthPresentation(repo.health);
        return (
          <Link
            key={repo.id}
            to={`/repositories/${repo.id}`}
            className="focus-ring flex flex-col gap-3 rounded-xl border border-line-muted bg-surface p-4 transition-colors hover:border-line-strong hover:bg-surface-hover"
          >
            <div className="flex items-start justify-between gap-2">
              {/* `name` not `full_name`: every card in a single-org
                  dataset repeated an identical "org/" prefix, truncating
                  the one part of the name that actually varies — the
                  full name is still one hover/click away on the detail
                  page. */}
              <p className="min-w-0 truncate text-sm font-semibold text-fg" title={repo.full_name}>
                {repo.name}
              </p>
              <StatusBadge label={healthLabel} tone={healthTone} />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-fg-muted">
              <span className="flex items-center gap-1.5">
                <GitPullRequest className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {repo.open_pull_requests} open PR{repo.open_pull_requests === 1 ? "" : "s"}
              </span>
              <span className="flex items-center gap-1.5">
                <FolderGit2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {repo.source === "local" ? "Local" : "GitHub"}
              </span>
            </div>
            <div className="mt-auto flex items-center gap-1.5 border-t border-line-muted pt-2.5 text-xs">
              {repo.indexing_status === "failed" ? (
                <span className="flex items-center gap-1 font-medium text-danger-fg">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  Index failed
                </span>
              ) : repo.indexing_status === "indexed" ? (
                <span className="flex items-center gap-1 text-fg-muted">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success-fg" aria-hidden="true" />
                  Indexed
                  {repo.last_indexed_at && ` · ${formatRelativeTime(repo.last_indexed_at)}`}
                </span>
              ) : repo.indexing_in_progress ? (
                <span className="text-info-fg">Indexing…</span>
              ) : (
                <span className="text-fg-subtle">Not indexed yet</span>
              )}
            </div>
          </Link>
        );
      })}
    </div>
  );
}
