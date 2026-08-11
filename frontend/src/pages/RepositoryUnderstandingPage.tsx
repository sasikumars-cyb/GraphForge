import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { BookOpen, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { RepositoryUnderstandingResultDetails } from "../components/agents/StageResultDetails";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import type { RepositoryUnderstandingResult } from "../types/agent";
import type { TrackedRepository } from "../types/github";

export function RepositoryUnderstandingPage() {
  const { token } = useAuth();
  const [repositories, setRepositories] = useState<TrackedRepository[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const [loadingRepos, setLoadingRepos] = useState(true);
  const [reposError, setReposError] = useState<string | null>(null);
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listTrackedRepositories(token)
      .then((repos) => {
        if (!cancelled) setRepositories(repos);
      })
      .catch((err) => {
        if (!cancelled) {
          setReposError(err instanceof Error ? err.message : "Could not load repositories.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingRepos(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!selectedRepoId) return;
    submit({
      subject_reference: `repo:${selectedRepoId}`,
      goal: "analyze_repository_understanding",
    });
  };

  const hasResult =
    run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-4-bg p-2 ring-1 ring-inset ring-cat-4-line/30">
            <BookOpen className="h-5 w-5 text-cat-4-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">Repository Understanding</h1>
            <p className="text-sm text-fg-muted">
              Explain what a repository does — its APIs, databases, queues, integrations, and
              dependencies. Read-only, computed entirely from the indexed graph.
            </p>
          </div>
        </div>
        <Link
          to="/runs"
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
          aria-label="View run history"
        >
          <History className="h-3.5 w-3.5" aria-hidden="true" />
          History
        </Link>
      </div>

      {!hasResult && (
        <Card>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div>
              <label
                htmlFor="repo-understanding-repo"
                className="block text-sm font-medium text-fg-secondary"
              >
                Repository
              </label>
              {reposError ? (
                <p className="mt-2 text-xs text-danger-fg">{reposError}</p>
              ) : (
                <select
                  id="repo-understanding-repo"
                  value={selectedRepoId}
                  onChange={(e) => setSelectedRepoId(e.target.value)}
                  disabled={isSubmitting || loadingRepos}
                  className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg disabled:opacity-50"
                  aria-required="true"
                >
                  <option value="">
                    {loadingRepos ? "Loading repositories…" : "Select a repository…"}
                  </option>
                  {repositories.map((repo) => (
                    <option key={repo.id} value={repo.id}>
                      {repo.full_name}
                    </option>
                  ))}
                </select>
              )}
              {!loadingRepos && !reposError && repositories.length === 0 && (
                <p className="mt-1 text-xs text-fg-muted">
                  No tracked repositories yet — add one under Repositories first.
                </p>
              )}
            </div>

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={isSubmitting || !selectedRepoId}
                className="inline-flex items-center gap-2 rounded-lg bg-cat-4-solid px-4 py-2 text-sm font-medium text-cat-4-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Run repository understanding analysis"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Run Repository Understanding"}
              </button>
              {isSubmitting && (
                <span className="text-xs text-fg-muted">This may take up to a minute.</span>
              )}
            </div>
          </form>
        </Card>
      )}

      {run && !hasResult && (
        <Card>
          <RunProgress status={run.status} error={run.error_message} />
        </Card>
      )}

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
          <button
            type="button"
            onClick={reset}
            className="ml-3 text-danger-fg underline hover:text-danger-fg"
          >
            Try again
          </button>
        </div>
      )}

      {hasResult && <UnderstandingReportView run={run} onNewAnalysis={reset} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report sub-component
// ---------------------------------------------------------------------------

function UnderstandingReportView({
  run,
  onNewAnalysis,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewAnalysis: () => void;
}) {
  const step = run.steps[0];
  const result = step?.result as unknown as RepositoryUnderstandingResult | undefined;
  const evidence = step?.evidence ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <RunStatusBadge status={run.status} />
        <button
          type="button"
          onClick={onNewAnalysis}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          New Analysis
        </button>
      </div>

      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {result && <RepositoryUnderstandingResultDetails result={result} />}

      <EvidencePanel evidence={evidence} />
    </div>
  );
}
