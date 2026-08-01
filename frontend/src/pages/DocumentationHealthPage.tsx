import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { HeartPulse, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import type { TrackedRepository } from "../types/github";

/** Grade -> theme token. Mirrors the backend's `analysis.grade_for` bands;
 * kept as a lookup rather than recomputing thresholds here so the UI can
 * never disagree with the score it was given. */
const GRADE_STYLES: Record<string, { ring: string; text: string; bg: string }> = {
  excellent: { ring: "ring-success-line/40", text: "text-success-fg", bg: "bg-success-bg" },
  good: { ring: "ring-success-line/30", text: "text-success-fg", bg: "bg-success-bg" },
  fair: { ring: "ring-warning-line/40", text: "text-warning-fg", bg: "bg-warning-bg" },
  poor: { ring: "ring-danger-line/30", text: "text-danger-fg", bg: "bg-danger-bg" },
  critical: { ring: "ring-danger-line/40", text: "text-danger-fg", bg: "bg-danger-bg" },
};

const SEVERITY_STYLES: Record<string, string> = {
  high: "border-danger-line/30 bg-danger-bg text-danger-fg",
  medium: "border-warning-line/30 bg-warning-bg text-warning-fg",
  low: "border-line-muted bg-surface text-fg-muted",
};

const CATEGORY_LABELS: Record<string, string> = {
  missing_readme: "Missing README",
  missing_architecture_doc: "Missing Architecture Doc",
  empty_document: "Empty Document",
  placeholder_document: "Placeholder Document",
  duplicate_document: "Duplicate Document",
  duplicate_section: "Duplicate Section",
  broken_link: "Broken Link",
  missing_toc: "Missing Table of Contents",
  undocumented_folder: "Undocumented Folder",
  missing_title: "Missing Title",
  missing_ownership: "Missing Ownership",
  missing_last_updated: "Missing Last Updated",
};

export function DocumentationHealthPage() {
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
    submit({ subject_reference: `repo:${selectedRepoId}`, goal: "analyze_documentation_health" });
  };

  const hasResult =
    run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-5-bg p-2 ring-1 ring-inset ring-cat-5-line/30">
            <HeartPulse className="h-5 w-5 text-cat-5-fg" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-fg">Documentation Health</h2>
            <p className="text-sm text-fg-muted">
              Score a repository&apos;s Markdown documentation and get a health report. Read-only —
              nothing in the repository is modified.
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
                htmlFor="doc-health-repo"
                className="block text-sm font-medium text-fg-secondary"
              >
                Repository
              </label>
              {reposError ? (
                <p className="mt-2 text-xs text-danger-fg">{reposError}</p>
              ) : (
                <select
                  id="doc-health-repo"
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
                aria-label="Run documentation health analysis"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Run Documentation Health"}
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

      {hasResult && <HealthReportView run={run} onNewAnalysis={reset} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report sub-component
// ---------------------------------------------------------------------------

function HealthReportView({
  run,
  onNewAnalysis,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewAnalysis: () => void;
}) {
  const step = run.steps[0];
  const result = step?.result as Record<string, unknown> | undefined;
  const evidence = step?.evidence ?? [];

  const score = (result?.health_score as number) ?? 0;
  const grade = (result?.grade as string) ?? "critical";
  const summary = (result?.summary as string) ?? "";
  const stats = (result?.stats as Record<string, number>) ?? {};
  const filesReviewed = (result?.files_reviewed as Array<Record<string, unknown>>) ?? [];
  const findings = (result?.findings as Array<Record<string, unknown>>) ?? [];
  const breakdown = (result?.score_breakdown as Array<Record<string, unknown>>) ?? [];
  const strengths = (result?.strengths as string[]) ?? [];
  const improvements = (result?.areas_for_improvement as string[]) ?? [];
  const nextActions = (result?.suggested_next_actions as string[]) ?? [];

  const gradeStyle = GRADE_STYLES[grade] ?? GRADE_STYLES.critical;

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

      {/* Score + summary */}
      <Card title="Overall Documentation Health">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div
            className={`flex h-28 w-28 shrink-0 flex-col items-center justify-center rounded-full ${gradeStyle.bg} ring-4 ring-inset ${gradeStyle.ring}`}
            role="img"
            aria-label={`Health score ${score} out of 100, graded ${grade}`}
          >
            <span className={`font-display text-3xl font-semibold ${gradeStyle.text}`}>{score}</span>
            <span className="text-[10px] uppercase tracking-wide text-fg-muted">out of 100</span>
          </div>
          <div className="min-w-0 flex-1">
            <span
              className={`inline-flex items-center rounded-full ${gradeStyle.bg} px-2.5 py-1 text-[10px] font-medium uppercase tracking-wide ${gradeStyle.text}`}
            >
              {grade}
            </span>
            {summary && <p className="mt-2 text-sm text-fg-secondary">{summary}</p>}
          </div>
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-line-muted pt-4 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-fg-muted">Markdown files</dt>
            <dd className="text-fg-secondary">{stats.total_markdown_files ?? 0}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Directories</dt>
            <dd className="text-fg-secondary">{stats.distinct_doc_directories ?? 0}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Headings</dt>
            <dd className="text-fg-secondary">{stats.total_headings ?? 0}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">ADRs</dt>
            <dd className="text-fg-secondary">{stats.adr_count ?? 0}</dd>
          </div>
        </dl>
      </Card>

      {breakdown.length > 0 && (
        <Card title="Score Breakdown" description={`100 − penalties = ${score}`}>
          <ul className="space-y-1.5" role="list">
            {breakdown.map((c, i) => (
              <li key={i} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-fg-secondary">
                  {CATEGORY_LABELS[c.category as string] ?? (c.category as string)}
                  <span className="ml-2 text-xs text-fg-muted">
                    ×{c.finding_count as number}
                    {(c.capped as boolean) ? " (capped)" : ""}
                  </span>
                </span>
                <span className="font-mono text-xs text-danger-fg">
                  −{c.penalty as number}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {strengths.length > 0 && (
        <Card title="Strengths">
          <ul className="list-inside list-disc space-y-1 text-sm text-fg-secondary" role="list">
            {strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </Card>
      )}

      {improvements.length > 0 && (
        <Card title="Areas for Improvement">
          <ul className="list-inside list-disc space-y-1 text-sm text-fg-secondary" role="list">
            {improvements.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </Card>
      )}

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
                    {CATEGORY_LABELS[f.category as string] ?? (f.category as string)}
                  </span>
                  <span className="font-mono text-xs opacity-80">{f.file_path as string}</span>
                </div>
                <p className="mt-1 text-sm">{f.message as string}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {nextActions.length > 0 && (
        <Card title="Suggested Next Actions">
          <ol className="list-inside list-decimal space-y-1 text-sm text-fg-secondary" role="list">
            {nextActions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
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
                <span className="text-xs text-fg-muted">
                  {f.heading_count as number} heading(s)
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <EvidencePanel evidence={evidence} />
    </div>
  );
}
