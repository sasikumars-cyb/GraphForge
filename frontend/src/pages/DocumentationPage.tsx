import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { FileText, Send, RotateCcw, History, GitPullRequest } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import { createDocumentationPR } from "../lib/api/documentation";
import type { TrackedRepository } from "../types/github";

const SEVERITY_STYLES: Record<string, string> = {
  high: "border-danger-line/30 bg-danger-bg text-danger-fg",
  medium: "border-warning-line/30 bg-warning-bg text-warning-fg",
  low: "border-line-muted bg-surface text-fg-muted",
};

const FINDING_TYPE_LABELS: Record<string, string> = {
  outdated: "Outdated",
  missing: "Missing",
  duplicate: "Duplicate",
  broken_link: "Broken Link",
  needs_update: "Needs Update",
};

export function DocumentationPage() {
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
    submit({ subject_reference: `repo:${selectedRepoId}`, goal: "review_documentation" });
  };

  const handleNewAnalysis = () => {
    reset();
  };

  const hasResult = run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-5-bg p-2 ring-1 ring-inset ring-cat-5-line/30">
            <FileText className="h-5 w-5 text-cat-5-fg" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-fg">Documentation</h2>
            <p className="text-sm text-fg-muted">
              Review a repository&apos;s Markdown documentation against its indexed architecture —
              outdated, missing, duplicate docs, and broken links, with proposed updates.
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
              <label htmlFor="documentation-repo" className="block text-sm font-medium text-fg-secondary">
                Repository
              </label>
              {reposError ? (
                <p className="mt-2 text-xs text-danger-fg">{reposError}</p>
              ) : (
                <select
                  id="documentation-repo"
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
                className="inline-flex items-center gap-2 rounded-lg bg-cat-5-solid px-4 py-2 text-sm font-medium text-cat-5-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Run documentation analysis"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Run Analysis"}
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
            onClick={handleNewAnalysis}
            className="ml-3 text-danger-fg underline hover:text-danger-fg"
          >
            Try again
          </button>
        </div>
      )}

      {hasResult && <DocumentationResultView run={run} onNewAnalysis={handleNewAnalysis} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-component
// ---------------------------------------------------------------------------

function DocumentationResultView({
  run,
  onNewAnalysis,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewAnalysis: () => void;
}) {
  const { token } = useAuth();
  const step = run.steps[0];
  const result = step?.result as Record<string, unknown> | undefined;
  const evidence = step?.evidence ?? [];

  const summary = (result?.summary as string) ?? "";
  const filesReviewed = (result?.files_reviewed as Array<Record<string, unknown>>) ?? [];
  const findings = (result?.findings as Array<Record<string, unknown>>) ?? [];
  const proposedUpdates = (result?.proposed_updates as Array<Record<string, unknown>>) ?? [];
  const proposedNewDocuments = (result?.proposed_new_documents as Array<Record<string, unknown>>) ?? [];

  const [creatingPR, setCreatingPR] = useState(false);
  const [prResult, setPrResult] = useState<{ url: string; filesChanged: number } | null>(null);
  const [prError, setPrError] = useState<string | null>(null);

  const canCreatePR =
    run.status === "completed" && (proposedUpdates.length > 0 || proposedNewDocuments.length > 0);

  const handleCreatePR = async () => {
    if (!token) return;
    setCreatingPR(true);
    setPrError(null);
    try {
      const response = await createDocumentationPR(token, run.run_id);
      setPrResult({ url: response.pull_request_url, filesChanged: response.files_changed });
    } catch (err) {
      setPrError(err instanceof Error ? err.message : "Could not create the pull request.");
    } finally {
      setCreatingPR(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {step?.confidence && <ConfidenceBadge confidence={step.confidence} />}
        </div>
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

      {summary && (
        <Card title="Documentation Summary">
          <p className="text-sm text-fg-secondary">{summary}</p>
        </Card>
      )}

      <Card title="Files Reviewed" description={`${filesReviewed.length} Markdown file(s)`}>
        {filesReviewed.length === 0 ? (
          <p className="text-sm text-fg-muted">No Markdown files were found in this repository.</p>
        ) : (
          <ul className="space-y-1.5" role="list">
            {filesReviewed.map((f, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <span className="rounded bg-surface-raised px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-fg-muted">
                  {f.category as string}
                </span>
                <span className="font-mono text-xs text-fg-secondary">{f.path as string}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {findings.length > 0 && (
        <Card title="Findings" description={`${findings.length} found`}>
          <ul className="space-y-2" role="list">
            {findings.map((f, i) => (
              <li
                key={i}
                className={`rounded-lg border px-4 py-3 ${SEVERITY_STYLES[f.severity as string] ?? SEVERITY_STYLES.low}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide">
                    {FINDING_TYPE_LABELS[f.finding_type as string] ?? (f.finding_type as string)}
                  </span>
                  <span className="font-mono text-xs opacity-80">{f.file_path as string}</span>
                </div>
                <p className="mt-1 text-sm">{f.description as string}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {proposedUpdates.length > 0 && (
        <Card title="Suggested Updates" description={`${proposedUpdates.length} proposed`}>
          <ul className="space-y-3" role="list">
            {proposedUpdates.map((u, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <span className="font-mono text-xs font-medium text-fg-secondary">{u.file_path as string}</span>
                <p className="mt-1 text-sm text-fg-muted">{u.rationale as string}</p>
                <pre className="mt-2 max-h-48 overflow-auto rounded bg-surface-raised p-3 text-xs text-fg-secondary">
                  {u.proposed_markdown as string}
                </pre>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {proposedNewDocuments.length > 0 && (
        <Card title="Proposed New Documents" description={`${proposedNewDocuments.length} proposed`}>
          <ul className="space-y-3" role="list">
            {proposedNewDocuments.map((d, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-medium text-fg-secondary">{d.file_path as string}</span>
                  <span className="text-xs text-fg-muted">— {d.title as string}</span>
                </div>
                <p className="mt-1 text-sm text-fg-muted">{d.rationale as string}</p>
                <pre className="mt-2 max-h-48 overflow-auto rounded bg-surface-raised p-3 text-xs text-fg-secondary">
                  {d.proposed_markdown as string}
                </pre>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {canCreatePR && (
        <Card title="Create Documentation PR" description="Optional — opens one PR with every proposed change above.">
          {prResult ? (
            <p className="text-sm text-success-fg">
              Opened a pull request ({prResult.filesChanged} file(s) changed):{" "}
              <a href={prResult.url} target="_blank" rel="noreferrer" className="underline">
                {prResult.url}
              </a>
            </p>
          ) : (
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={handleCreatePR}
                disabled={creatingPR}
                className="inline-flex items-center gap-2 rounded-lg bg-cat-5-solid px-4 py-2 text-sm font-medium text-cat-5-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <GitPullRequest className="h-4 w-4" aria-hidden="true" />
                {creatingPR ? "Creating…" : "Create Documentation PR"}
              </button>
              {prError && <span className="text-xs text-danger-fg">{prError}</span>}
            </div>
          )}
        </Card>
      )}

      <EvidencePanel evidence={evidence} />
    </div>
  );
}
