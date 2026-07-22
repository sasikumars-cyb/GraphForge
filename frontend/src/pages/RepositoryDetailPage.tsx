import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { listTrackedRepositories } from "../lib/api/github";
import { getLatestIndexingJob, listPullRequests, triggerIndexing } from "../lib/api/repositories";
import { formatRelativeTime } from "../lib/formatDate";
import type { TrackedRepository } from "../types/github";
import type { IndexingJob } from "../types/graph";
import type { PullRequest } from "../types/pullRequest";

const JOB_STATUS_TONE: Record<IndexingJob["status"], "neutral" | "info" | "success" | "danger"> = {
  pending: "neutral",
  running: "info",
  completed: "success",
  failed: "danger",
};

const pullRequestColumns: TableColumn<PullRequest>[] = [
  {
    key: "title",
    header: "Pull request",
    render: (pr) => (
      <Link to={`/pull-requests/${pr.id}`} className="hover:underline">
        {pr.title}
        <span className="ml-2 text-xs text-slate-500">#{pr.number}</span>
      </Link>
    ),
  },
  { key: "state", header: "State", render: (pr) => (pr.is_draft ? "Draft" : pr.state) },
  { key: "updated", header: "Updated", render: (pr) => formatRelativeTime(pr.github_updated_at) },
];

export function RepositoryDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();
  const [repository, setRepository] = useState<TrackedRepository | null>(null);
  const [pullRequests, setPullRequests] = useState<PullRequest[]>([]);
  const [indexingJob, setIndexingJob] = useState<IndexingJob | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isIndexing, setIsIndexing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !id) {
      return;
    }
    let cancelled = false;

    async function load() {
      try {
        const [repos, prs, job] = await Promise.all([
          listTrackedRepositories(token!),
          listPullRequests(token!, id!),
          getLatestIndexingJob(token!, id!).catch((err) => {
            if (err instanceof ApiError && err.status === 404) {
              return null;
            }
            throw err;
          }),
        ]);
        if (!cancelled) {
          setRepository(repos.find((r) => r.id === id) ?? null);
          setPullRequests(prs);
          setIndexingJob(job);
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load repository.");
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [token, id]);

  async function handleTriggerIndexing() {
    if (!token || !id) {
      return;
    }
    setIsIndexing(true);
    setError(null);
    try {
      const job = await triggerIndexing(token, id);
      setIndexingJob(job);
      // Indexing runs in a background task; poll until it's no longer pending/running.
      const poll = async () => {
        const latest = await getLatestIndexingJob(token, id);
        setIndexingJob(latest);
        if (latest.status === "pending" || latest.status === "running") {
          setTimeout(poll, 1500);
        } else {
          setIsIndexing(false);
        }
      };
      setTimeout(poll, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to trigger indexing.");
      setIsIndexing(false);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }

  if (!repository) {
    return <p className="text-sm text-slate-500">Repository not found.</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-50">{repository.full_name}</h2>
          <p className="mt-1 text-sm text-slate-400">{repository.html_url}</p>
        </div>
        <Link
          to={`/architecture?repository=${repository.id}`}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500"
        >
          View graph
        </Link>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      <Card title="Indexing" description="Runs tree-sitter parsing and rebuilds the Neo4j graph">
        <div className="flex flex-wrap items-center gap-4">
          {indexingJob ? (
            <>
              <StatusBadge label={indexingJob.status} tone={JOB_STATUS_TONE[indexingJob.status]} />
              {indexingJob.finished_at && (
                <span className="text-xs text-slate-500">
                  Last indexed {formatRelativeTime(indexingJob.finished_at)}
                </span>
              )}
              {indexingJob.result_summary && (
                <span className="text-xs text-slate-500">
                  {Object.entries(indexingJob.result_summary)
                    .map(([key, count]) => `${count} ${key}`)
                    .join(", ")}
                </span>
              )}
            </>
          ) : (
            <span className="text-xs text-slate-500">Not indexed yet.</span>
          )}
          <button
            type="button"
            onClick={() => void handleTriggerIndexing()}
            disabled={
              isIndexing || indexingJob?.status === "pending" || indexingJob?.status === "running"
            }
            className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isIndexing ? "Indexing…" : "Run indexing"}
          </button>
        </div>
      </Card>

      <Card title="Pull requests" description="Pull requests tracked for this repository">
        <Table
          columns={pullRequestColumns}
          data={pullRequests}
          getRowKey={(pr) => pr.id}
          emptyMessage="No pull requests yet."
        />
      </Card>
    </div>
  );
}
