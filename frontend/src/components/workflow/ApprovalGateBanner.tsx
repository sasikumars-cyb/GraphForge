import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, PenLine, ShieldCheck, XCircle } from "lucide-react";
import { stageLabel } from "../../lib/workflowDerived";

interface ApprovalGateBannerProps {
  completedStage: string;
  nextStage: string;
  workflowTitle: string;
  isSubmitting: boolean;
  onApprove: () => void;
}

/** Feature 6 — every stage transition today already requires an explicit
 * human action (there is no backend auto-advance), so this banner doesn't
 * add a new capability — it makes that existing gate visible and
 * deliberate instead of a plain "Continue" button. "Approve" calls the
 * same continueWorkflow() the old button did; "Reject" and "Edit Workflow"
 * are UI-only (no backend endpoint exists for either, so neither claims to
 * mutate workflow state — Reject simply leaves this workflow as-is and
 * returns to the hub; Edit starts a fresh workflow with the same
 * objective). */
export function ApprovalGateBanner({
  completedStage,
  nextStage,
  workflowTitle,
  isSubmitting,
  onApprove,
}: ApprovalGateBannerProps) {
  const navigate = useNavigate();
  const [rejected, setRejected] = useState(false);

  if (rejected) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4 text-sm text-slate-400">
        This workflow was left at{" "}
        <strong className="text-slate-200">{stageLabel(completedStage)}</strong>. Nothing further
        will run automatically — pick it back up any time from Run History.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" aria-hidden="true" />
        <p className="text-sm text-amber-200">
          <strong className="text-amber-100">{stageLabel(completedStage)}</strong> is complete.
          Review its output above, then approve to start{" "}
          <strong className="text-amber-100">{stageLabel(nextStage)}</strong>.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={() => setRejected(true)}
          disabled={isSubmitting}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
        >
          <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Reject
        </button>
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
          onClick={onApprove}
          disabled={isSubmitting}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-brand-400 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {isSubmitting ? "Starting…" : "Approve & Continue"}
        </button>
      </div>
    </div>
  );
}
