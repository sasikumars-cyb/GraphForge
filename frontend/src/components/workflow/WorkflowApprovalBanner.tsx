import { useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, PenLine, ShieldQuestion, XCircle } from "lucide-react";

interface WorkflowApprovalBannerProps {
  workflowTitle: string;
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
 * /approve and /reject endpoints — unlike ApprovalGateBanner's per-stage
 * "Reject" (deliberately local-only, since rejecting one stage there just
 * leaves the workflow as-is), rejecting a blueprint here is a genuine,
 * persisted, terminal decision. */
export function WorkflowApprovalBanner({
  workflowTitle,
  status,
  isSubmitting,
  onApprove,
  onReject,
}: WorkflowApprovalBannerProps) {
  const navigate = useNavigate();

  if (status === "approved") {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-5 py-4">
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" aria-hidden="true" />
        <p className="text-sm text-emerald-200">
          <strong className="text-emerald-100">Blueprint approved.</strong> Auto Execution workflows
          aren't available in this build yet — this blueprint is ready and waiting for when they
          are.
        </p>
      </div>
    );
  }

  if (status === "rejected") {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4 text-sm text-slate-400">
        This blueprint was rejected. Nothing further will run automatically — edit and start a new
        workflow if you'd like to try again.
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
          onClick={() => navigate(`/workflows/new?title=${encodeURIComponent(workflowTitle)}`)}
          disabled={isSubmitting}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
        >
          <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
          Edit Workflow
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
