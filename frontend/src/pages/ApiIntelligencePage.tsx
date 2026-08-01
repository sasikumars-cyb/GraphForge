import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Network, Send, RotateCcw, History, Download, ExternalLink } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
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
import type { TrackedRepository } from "../types/github";

const SEVERITY_STYLES: Record<string, string> = {
  critical: "border-danger-line/40 bg-danger-bg text-danger-fg",
  high: "border-danger-line/20 bg-danger-bg/60 text-danger-fg",
  medium: "border-warning-line/30 bg-warning-bg text-warning-fg",
  low: "border-line-muted bg-surface text-fg-muted",
};

const METHOD_STYLES: Record<string, string> = {
  GET: "bg-success-bg text-success-fg",
  POST: "bg-cat-1-bg text-cat-1-fg",
  PUT: "bg-warning-bg text-warning-fg",
  PATCH: "bg-cat-2-bg text-cat-2-fg",
  DELETE: "bg-danger-bg text-danger-fg",
};

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
            <h2 className="text-xl font-semibold text-fg">API Intelligence</h2>
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
  const result = step?.result as Record<string, unknown> | undefined;
  const evidence = step?.evidence ?? [];

  const summary = (result?.executive_summary as string) ?? "";
  const endpoints = (result?.endpoints as Array<Record<string, unknown>>) ?? [];
  const securityFindings = (result?.security_findings as Array<Record<string, unknown>>) ?? [];
  const missingInformation = (result?.missing_information as string[]) ?? [];
  const scores = (result?.scores as Record<string, number>) ?? {};

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

      {summary && (
        <Card title="Executive Summary">
          <p className="text-sm text-fg-secondary">{summary}</p>
        </Card>
      )}

      {Object.keys(scores).length > 0 && (
        <Card title="Scores">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            {Object.entries(scores).map(([key, value]) => (
              <div key={key}>
                <dt className="text-xs capitalize text-fg-muted">{key.replaceAll("_", " ")}</dt>
                <dd className="text-xl font-semibold text-fg">
                  {value}
                  <span className="text-xs text-fg-muted">/100</span>
                </dd>
              </div>
            ))}
          </dl>
        </Card>
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

      {endpoints.length > 0 && (
        <Card title="Endpoints" description={`${endpoints.length} documented`}>
          <ul className="space-y-2" role="list">
            {endpoints.map((e, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className={`rounded px-2 py-0.5 text-xs font-semibold ${METHOD_STYLES[e.method as string] ?? "bg-surface-raised text-fg-muted"}`}>
                    {e.method as string}
                  </span>
                  <span className="font-mono text-sm text-fg-secondary">{e.path as string}</span>
                  {Boolean(e.authentication_required) && (
                    <span className="text-xs text-warning-fg">🔒 Auth</span>
                  )}
                </div>
                {Boolean(e.description) && <p className="mt-1 text-sm text-fg-muted">{e.description as string}</p>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {securityFindings.length > 0 && (
        <Card title="Security Findings" description={`${securityFindings.length} found`}>
          <ul className="space-y-2" role="list">
            {securityFindings.map((f, i) => (
              <li
                key={i}
                className={`rounded-lg border px-4 py-3 ${SEVERITY_STYLES[f.severity as string] ?? SEVERITY_STYLES.low}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide">{f.severity as string}</span>
                  <span className="text-sm font-medium">{f.title as string}</span>
                </div>
                <p className="mt-1 text-sm">{f.description as string}</p>
                <p className="mt-1 text-xs opacity-80">Recommendation: {f.recommendation as string}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {missingInformation.length > 0 && (
        <Card title="Missing Information" description="What should be added — never hallucinated.">
          <ul className="space-y-1.5" role="list">
            {missingInformation.map((item, i) => (
              <li key={i} className="text-sm text-fg-muted">
                • {item}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <EvidencePanel evidence={evidence} />
    </div>
  );
}
