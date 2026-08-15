import { useEffect, useRef, useState } from "react";
import { FileBarChart, Loader2, ChevronDown, ChevronUp, Trash2 } from "lucide-react";
import { Card } from "../components/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge, type StatusTone } from "../components/StatusBadge";
import { ReportView } from "../components/report/ReportView";
import { formatRelativeTime } from "../lib/formatDate";
import { useAuth } from "../app/auth-context";
import {
  listReports,
  getReport,
  deleteReport,
  isCurrentViewModel,
  type ReportSummary,
  type ReportViewModel,
} from "../lib/api/reports";

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

/**
 * Report V2 Phase 2 (ADR 0024): a completed report's `view_model` is now
 * the authoritative content, rendered through real deterministic
 * components (`ReportView`) — not an LLM-authored HTML string. The
 * sandboxed iframe below is kept only as a fallback for a report
 * generated before `view_model` existed (`view_model === null`).
 */
function ReportContent({ reportId }: { reportId: string }) {
  const { token } = useAuth();
  const [html, setHtml] = useState<string | null>(null);
  const [viewModel, setViewModel] = useState<ReportViewModel | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    (async () => {
      try {
        const detail = await getReport(token, reportId, controller.signal);
        setViewModel(detail.view_model);
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
  if (viewModel === undefined) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-fg-muted">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading report…
      </div>
    );
  }
  // A view model from before the post–Engineering Review sections existed
  // is treated exactly like no view model at all: it renders through the
  // legacy HTML fallback rather than crashing the new renderer on a
  // section it was never written with.
  if (isCurrentViewModel(viewModel)) {
    return (
      <div className="max-h-[80vh] overflow-y-auto bg-canvas p-4">
        <ReportView model={viewModel} />
      </div>
    );
  }
  return (
    <iframe
      title="Workflow report (legacy)"
      srcDoc={html ?? ""}
      sandbox=""
      className="h-[70vh] w-full rounded-b-lg border-0 bg-white"
    />
  );
}

function ReportRow({
  report,
  onStatusSettled,
  onDeleted,
}: {
  report: ReportSummary;
  onStatusSettled: () => void;
  onDeleted: () => void;
}) {
  const { token } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const canView = report.status === "completed";

  async function handleDelete() {
    if (!token) return;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await deleteReport(token, report.id);
      setConfirming(false);
      onDeleted();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Couldn't delete this report.");
    } finally {
      setIsDeleting(false);
    }
  }
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
          {/* The request the user made, verbatim — a report answers a
              question somebody asked, so that question is what identifies
              it here. The AI-generated short title and the workflow it ran
              through are provenance, and read as the smaller line below.
              Clamped rather than truncated to one line: a real request is
              routinely a short paragraph, and the first line alone is
              often just "Investigate whether…". */}
          <p className="line-clamp-3 whitespace-pre-line text-sm font-medium text-fg">
            {report.request || report.title}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-muted">
            <StatusBadge label={statusLabel(report.status)} tone={statusTone(report.status)} />
            <span className="truncate">{report.title}</span>
            <span aria-hidden="true">·</span>
            <span>{formatRelativeTime(report.created_at)}</span>
          </div>
          {report.status === "failed" && report.error_message && (
            <p className="mt-1 text-xs text-danger-fg">{report.error_message}</p>
          )}
          {deleteError && <p className="mt-1 text-xs text-danger-fg">{deleteError}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {canView && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
            >
              {expanded ? "Hide" : "View report"}
              {expanded ? (
                <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          )}
          {/* Offered for every status, not just `completed` — a failed or
              stuck-pending report is exactly the one a user most wants to
              clear out, and it has no "View report" affordance to sit
              beside. */}
          <button
            type="button"
            onClick={() => setConfirming(true)}
            disabled={isDeleting}
            aria-label={`Delete report: ${report.title}`}
            title="Delete report"
            className="focus-ring rounded-md p-1 text-fg-muted transition-colors hover:text-danger-fg disabled:cursor-not-allowed disabled:opacity-30"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
      <ConfirmDialog
        open={confirming}
        title="Delete this report?"
        body={
          // The reassurance is the point: this page sits next to workflow
          // deletion, and a user has no way to know from the button alone
          // that they are discarding a document rather than the
          // investigation that produced it.
          "This removes the generated report only. The investigation behind it — its stages, " +
          "evidence and run history — is kept, and it can be reported on again. This can't be undone."
        }
        consequences={[report.title, `Generated ${formatRelativeTime(report.created_at)}`]}
        confirmLabel="Delete report"
        isSubmitting={isDeleting}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirming(false)}
      />
      {expanded && canView && (
        <div className="mt-3 overflow-hidden rounded-lg border border-line">
          <ReportContent reportId={report.id} />
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
        <h1 className="text-xl font-semibold text-fg">Reports</h1>
        <p className="mt-1 text-sm text-fg-muted">
          What GraphForge found for each request you made — generated automatically once the
          request's blueprint is approved.
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
            <ReportRow
              key={report.id}
              report={report}
              onStatusSettled={() => void load()}
              onDeleted={() => void load()}
            />
          ))}
        </div>
      )}
    </div>
  );
}
