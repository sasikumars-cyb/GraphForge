import { useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, PenLine, ShieldQuestion, XCircle } from "lucide-react";

interface WorkflowApprovalBannerProps {
  workflowTitle: string;
  /** This workflow's own id — threaded into "Refine"'s parentId so the
   * next draft carries this one's blueprint forward instead of starting
   * cold (see NewWorkflowPage's parentId handling). */
  workflowId: string;
  /** The real workflow.status — "awaiting_approval" is the only
   * actionable state; "approved"/"rejected" are terminal and render a
   * read-only confirmation instead of buttons. */
  status: string;
  isSubmitting: boolean;
  onApprove: () => void;
  onReject: () => void;
}

/** Phase 4 — the Planning workflow's terminal, workflow-level gate: once
 * Engineering Review completes, a human decides whether the whole
 * blueprint is ready, not whether to continue to one more stage (that's
 * ApprovalGateBanner's job, a level down). Approve/Reject call the real
 * /approve and /reject endpoints — the same pattern ApprovalGateBanner's
 * per-stage Approve/Reject already use (both call their caller's
 * onApprove/onReject straight through to the real backend mutation), just
 * scoped to the whole workflow rather than one stage. */
export function WorkflowApprovalBanner({
  workflowTitle,
  workflowId,
  status,
  isSubmitting,
  onApprove,
  onReject,
}: WorkflowApprovalBannerProps) {
  const navigate = useNavigate();

  const refineHref = `/workflows/new?title=${encodeURIComponent(workflowTitle)}&parentId=${encodeURIComponent(workflowId)}`;

  if (status === "approved") {
    return (
      <div className="flex items-start justify-between gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-5 py-4">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" aria-hidden="true" />
          <p className="text-sm text-emerald-200">
            <strong className="text-emerald-100">Blueprint approved.</strong> Planning is complete and
            this blueprint is ready for implementation. Turning an approved blueprint into code isn't
            available yet — this workflow stays here as your approved plan of record.
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate(refineHref)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-emerald-300 ring-1 ring-inset ring-emerald-500/30 transition-colors hover:bg-emerald-500/10"
        >
          <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
          Refine
        </button>
      </div>
    );
  }

  if (status === "rejected") {
    return (
      <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4 text-sm text-slate-400">
        <p>
          This blueprint was rejected. Nothing further will run automatically — refine it into a new
          version if you'd like to try again.
        </p>
        <button
          type="button"
          onClick={() => navigate(refineHref)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-slate-300 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800"
        >
          <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
          Refine
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-brand-500/30 bg-brand-500/5 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <ShieldQuestion className="mt-0.5 h-5 w-5 shrink-0 text-brand-400" aria-hidden="true" />
        <p className="text-sm text-brand-100">
          Engineering Review is complete. Review the blueprint above, then approve it as ready or
          reject it to end this workflow here.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={() => navigate(refineHref)}
          disabled={isSubmitting}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
        >
          <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
          Refine
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={isSubmitting}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-rose-300 ring-1 ring-inset ring-rose-500/30 transition-colors hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Reject
        </button>
        <button
          type="button"
          onClick={onApprove}
          disabled={isSubmitting}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-brand-400 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {isSubmitting ? "Submitting…" : "Approve Blueprint"}
        </button>
      </div>
    </div>
  );
}
