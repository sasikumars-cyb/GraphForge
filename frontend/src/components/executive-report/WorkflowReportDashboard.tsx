import { useEffect, useState } from "react";
import { Download, Loader2, Printer } from "lucide-react";
import { useAuth } from "../../app/auth-context";
import {
  getExecutiveReportData,
  downloadExecutiveReportHtml,
} from "../../lib/api/executiveReport";
import {
  mapSummary,
  mapTimeline,
  mapMetrics,
  mapCharts,
  mapRepositoryImpact,
  mapReviewResults,
  mapRecommendations,
} from "../../lib/executiveReportMapper";
import type { ExecutiveReportData } from "../../types/executiveReport";
import { ExecutiveSummary } from "./ExecutiveSummary";
import { WorkflowTimeline } from "./WorkflowTimeline";
import { AIMetrics } from "./AIMetrics";
import { PerformanceCharts } from "./PerformanceCharts";
import { RepositoryImpact } from "./RepositoryImpact";
import { ReviewResults } from "./ReviewResults";
import { Recommendations } from "./Recommendations";

interface WorkflowReportDashboardProps {
  reportId: string;
}

/**
 * Main entry point for the executive report dashboard. Fetches structured
 * data from the API, maps it through the presentation layer, and renders
 * all collapsible dashboard sections.
 */
export function WorkflowReportDashboard({ reportId }: WorkflowReportDashboardProps) {
  const { token } = useAuth();
  const [data, setData] = useState<ExecutiveReportData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();

    (async () => {
      try {
        const result = await getExecutiveReportData(token, reportId, controller.signal);
        setData(result);
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Failed to load report data.");
        }
      }
    })();

    return () => controller.abort();
  }, [token, reportId]);

  async function handleDownload() {
    if (!token) return;
    setDownloading(true);
    try {
      const html = await downloadExecutiveReportHtml(token, reportId);
      const blob = new Blob([html], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `executive-report-${reportId.slice(0, 8)}.html`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      // Silently fail download — user can retry
    } finally {
      setDownloading(false);
    }
  }

  function handlePrint() {
    window.print();
  }

  if (error) {
    return (
      <div className="rounded-lg border border-danger-line bg-danger-bg px-4 py-3 text-sm text-danger-fg">
        {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-sm text-fg-muted">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading executive dashboard...
      </div>
    );
  }

  const summary = mapSummary(data);
  const timeline = mapTimeline(data);
  const metrics = mapMetrics(data);
  const charts = mapCharts(data);
  const impact = mapRepositoryImpact(data);
  const reviews = mapReviewResults(data);
  const recommendations = mapRecommendations(data);

  return (
    <div className="flex flex-col gap-1 print:gap-4">
      {/* Action bar */}
      <div className="flex items-center justify-end gap-2 print:hidden">
        <button
          type="button"
          onClick={handlePrint}
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:bg-surface-raised focus-ring"
        >
          <Printer className="h-3.5 w-3.5" aria-hidden="true" />
          Print
        </button>
        <button
          type="button"
          onClick={handleDownload}
          disabled={downloading}
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:bg-surface-raised disabled:opacity-50 focus-ring"
        >
          {downloading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Download HTML
        </button>
      </div>

      {/* Dashboard sections */}
      <ExecutiveSummary data={summary} />
      <WorkflowTimeline stages={timeline} />
      <AIMetrics data={metrics} />
      <PerformanceCharts data={charts} />
      <RepositoryImpact data={impact} />
      <ReviewResults rows={reviews} />
      <Recommendations data={recommendations} />
    </div>
  );
}
