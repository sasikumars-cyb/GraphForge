import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Network, Send, RotateCcw, History, Download, ExternalLink } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ApiIntelligenceResultDetails } from "../components/agents/StageResultDetails";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import {
  downloadApiIntelligenceExport,
  fetchApiIntelligenceExport,
  type ApiIntelligenceExportFormat,
} from "../lib/api/apiIntelligence";
import type { ApiIntelligenceResult } from "../types/agent";
import type { TrackedRepository } from "../types/github";

export function ApiIntelligencePage() {
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
    submit({ subject_reference: `repo:${selectedRepoId}`, goal: "analyze_api_intelligence" });
  };

  const hasResult = run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-3-bg p-2 ring-1 ring-inset ring-cat-3-line/30">
            <Network className="h-5 w-5 text-cat-3-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">API Intelligence</h1>
            <p className="text-sm text-fg-muted">
              Extract a visual API catalog and security review from a repository&apos;s Markdown
              documentation only — never source code.
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
              <label htmlFor="api-intelligence-repo" className="block text-sm font-medium text-fg-secondary">
                Repository
              </label>
              {reposError ? (
                <p className="mt-2 text-xs text-danger-fg">{reposError}</p>
              ) : (
                <select
                  id="api-intelligence-repo"
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
                className="inline-flex items-center gap-2 rounded-lg bg-accent-solid px-4 py-2 text-sm font-medium text-accent-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Run API intelligence analysis"
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
          <button type="button" onClick={reset} className="ml-3 text-danger-fg underline hover:text-danger-fg">
            Try again
          </button>
        </div>
      )}

      {hasResult && <ApiIntelligenceResultView run={run} onNewAnalysis={reset} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-component
// ---------------------------------------------------------------------------

const EXPORT_BUTTONS: Array<{ format: ApiIntelligenceExportFormat; label: string }> = [
  { format: "openapi", label: "OpenAPI YAML" },
  { format: "postman", label: "Postman Collection" },
  { format: "markdown", label: "Markdown Summary" },
  { format: "json", label: "JSON" },
];

function ApiIntelligenceResultView({
  run,
  onNewAnalysis,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewAnalysis: () => void;
}) {
  const { token } = useAuth();
  const step = run.steps[0];
  const result = step?.result as unknown as ApiIntelligenceResult | undefined;
  const evidence = step?.evidence ?? [];

  const [exportError, setExportError] = useState<string | null>(null);
  const [downloadingFormat, setDownloadingFormat] = useState<ApiIntelligenceExportFormat | null>(null);
  const [dashboardHtml, setDashboardHtml] = useState<string | null>(null);
  const [loadingDashboard, setLoadingDashboard] = useState(false);

  const handleExport = async (format: ApiIntelligenceExportFormat) => {
    if (!token || run.status !== "completed") return;
    setExportError(null);
    setDownloadingFormat(format);
    try {
      const content = await fetchApiIntelligenceExport(token, run.run_id, format);
      downloadApiIntelligenceExport(content, run.run_id, format);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Export failed.");
    } finally {
      setDownloadingFormat(null);
    }
  };

  const handleOpenDashboard = async () => {
    if (!token || run.status !== "completed") return;
    setExportError(null);
    setLoadingDashboard(true);
    try {
      const html = await fetchApiIntelligenceExport(token, run.run_id, "html");
      setDashboardHtml(html);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Could not load the dashboard.");
    } finally {
      setLoadingDashboard(false);
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

      {run.status === "completed" && (
        <Card title="Export & Dashboard" description="Every format is re-rendered from this run's result — no re-analysis.">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={handleOpenDashboard}
              disabled={loadingDashboard}
              className="inline-flex items-center gap-2 rounded-lg bg-accent-solid px-4 py-2 text-sm font-medium text-accent-on-solid transition-colors hover:brightness-110 disabled:opacity-50"
            >
              <ExternalLink className="h-4 w-4" aria-hidden="true" />
              {loadingDashboard ? "Loading…" : "Open Visual Dashboard"}
            </button>
            {EXPORT_BUTTONS.map(({ format, label }) => (
              <button
                key={format}
                type="button"
                onClick={() => handleExport(format)}
                disabled={downloadingFormat === format}
                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary disabled:opacity-50"
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                {downloadingFormat === format ? "Downloading…" : label}
              </button>
            ))}
          </div>
          {exportError && <p className="mt-2 text-xs text-danger-fg">{exportError}</p>}
          {dashboardHtml && (
            <iframe
              title="API Intelligence Dashboard"
              srcDoc={dashboardHtml}
              className="mt-4 h-[900px] w-full rounded-lg border border-line"
              sandbox="allow-scripts"
            />
          )}
        </Card>
      )}

      {result && <ApiIntelligenceResultDetails result={result} />}

      <EvidencePanel evidence={evidence} />
    </div>
  );
}
