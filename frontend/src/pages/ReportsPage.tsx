import { useEffect, useRef, useState } from "react";
import { FileBarChart, Loader2, ChevronDown, ChevronUp } from "lucide-react";
import { Card } from "../components/Card";
import { StatusBadge, type StatusTone } from "../components/StatusBadge";
import { formatRelativeTime } from "../lib/formatDate";
import { useAuth } from "../app/auth-context";
import { listReports, getReport, type ReportSummary } from "../lib/api/reports";

// Reports with this status are still being generated in the background —
// worth polling for. "failed" and "completed" are both terminal.
const POLLABLE_STATUSES = new Set(["pending"]);
const POLL_INTERVAL_MS = 5000;

function statusTone(status: ReportSummary["status"]): StatusTone {
  switch (status) {
    case "completed":
      return "success";
    case "failed":
      return "danger";
    default:
      return "info";
  }
}

function statusLabel(status: ReportSummary["status"]): string {
  switch (status) {
    case "completed":
      return "Ready";
    case "failed":
      return "Failed";
    default:
      return "Generating…";
  }
}

function ReportHtmlViewer({ reportId }: { reportId: string }) {
  const { token } = useAuth();
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    (async () => {
      try {
        const detail = await getReport(token, reportId, controller.signal);
        setHtml(detail.html_content ?? "");
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Couldn't load this report.");
        }
      }
    })();
    return () => controller.abort();
  }, [token, reportId]);

  if (error) {
    return <p className="p-4 text-sm text-danger-fg">{error}</p>;
  }
  if (html === null) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-fg-muted">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading report…
      </div>
    );
  }
  return (
    <iframe
      title="Workflow report"
      srcDoc={html}
      sandbox=""
      className="h-[70vh] w-full rounded-b-lg border-0 bg-white"
    />
  );
}

function ReportRow({ report, onStatusSettled }: { report: ReportSummary; onStatusSettled: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const canView = report.status === "completed";
  // A pending report that just turned completed/failed while collapsed
  // should re-poll the parent list rather than sit stale — the parent's
  // own poll loop already does this, this ref just avoids re-triggering
  // per-row work on every re-render.
  const settledRef = useRef(false);
  useEffect(() => {
    if (report.status !== "pending" && !settledRef.current) {
      settledRef.current = true;
      onStatusSettled();
    }
  }, [report.status, onStatusSettled]);

  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-fg">{report.title}</p>
            <StatusBadge label={statusLabel(report.status)} tone={statusTone(report.status)} />
          </div>
          <p className="mt-0.5 truncate text-xs text-fg-muted">
            {report.workflow_title} · {formatRelativeTime(report.created_at)}
          </p>
          {report.status === "failed" && report.error_message && (
            <p className="mt-1 text-xs text-danger-fg">{report.error_message}</p>
          )}
        </div>
        {canView && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
          >
            {expanded ? "Hide" : "View report"}
            {expanded ? (
              <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
      {expanded && canView && (
        <div className="mt-3 overflow-hidden rounded-lg border border-line">
          <ReportHtmlViewer reportId={report.id} />
        </div>
      )}
    </Card>
  );
}

export function ReportsPage() {
  const { token } = useAuth();
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(signal?: AbortSignal) {
    if (!token) return;
    try {
      const data = await listReports(token, signal);
      setReports(data);
      setError(null);
    } catch (err) {
      if (!signal?.aborted) {
        setError(err instanceof Error ? err.message : "Couldn't load reports.");
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Poll only while at least one report is still generating — a workflow
  // report is created the moment a blueprint is approved, so this is what
  // makes a freshly-approved workflow's report appear here without a
  // manual refresh.
  useEffect(() => {
    if (!reports?.some((r) => POLLABLE_STATUSES.has(r.status))) return;
    const id = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reports, token]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-fg">Reports</h2>
        <p className="mt-1 text-sm text-fg-muted">
          High-level reports generated automatically when a workflow's blueprint is approved.
        </p>
      </div>

      {error && (
        <Card>
          <p className="text-sm text-danger-fg">{error}</p>
        </Card>
      )}

      {reports === null && !error && (
        <Card>
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-fg-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading reports…
          </div>
        </Card>
      )}

      {reports?.length === 0 && (
        <Card>
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
            <FileBarChart className="mb-1 h-8 w-8 text-fg-subtle" aria-hidden="true" />
            <p className="text-sm font-medium text-fg-muted">No reports generated yet.</p>
            <p className="max-w-sm text-xs text-fg-muted">
              Approve a Planning workflow's blueprint to generate its report — it'll appear here
              automatically once ready.
            </p>
          </div>
        </Card>
      )}

      {reports && reports.length > 0 && (
        <div className="flex flex-col gap-3">
          {reports.map((report) => (
            <ReportRow key={report.id} report={report} onStatusSettled={() => void load()} />
          ))}
        </div>
      )}
    </div>
  );
}
