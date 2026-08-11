import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  PenLine,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { stageLabel } from "../../lib/workflowDerived";

interface FailureInfo {
  /** The stage id (e.g. "testing") whose run failed. */
  stage: string;
  /** Run.error_message — the raw exception text. Only ever shown behind an
   * expandable "View error details" disclosure, never inline. */
  errorMessage: string | null;
}

interface ApprovalGateBannerProps {
  completedStage: string;
  nextStage: string;
  /** The workflow's FULL original request (`original_prompt`), not its
   * short AI-generated `title` — this seeds the objective box of the
   * refinement NewWorkflowPage creates, and whatever lands there becomes
   * the new workflow's own `original_prompt`. Passing the summarized title
   * silently strips the detail Context Discovery matches repository names
   * against, so a refined workflow re-scoped itself onto the wrong
   * repository. */
  refineObjective: string;
  /** This workflow's own id — threaded into "Refine"'s parentId (see
   * WorkflowApprovalBanner and NewWorkflowPage's parentId handling). */
  workflowId: string;
  isSubmitting: boolean;
  onApprove: () => void;
  onReject: () => void;
  /** When set, this stage has already failed — render the failure variant
   * (Retry Stage / Refine / View error details) instead of the
   * approval variant. `onApprove` doubles as "Retry Stage": the backend's
   * /continue endpoint already allows re-running the same current_stage
   * after a failed attempt (its "already has a run" guard only blocks
   * queued/running/completed, not failed), so retrying is the exact same
   * call — no new endpoint needed. */
  failure?: FailureInfo;
}

/** Feature 6 — every stage transition today already requires an explicit
 * human action (there is no backend auto-advance), so this banner doesn't
 * add a new capability — it makes that existing gate visible and
 * deliberate instead of a plain "Continue" button. "Approve" calls the
 * same continueWorkflow() the old button did; "Reject" calls
 * POST /workflows/{id}/reject (status → "rejected", terminal) via the
 * caller's onReject — same real backend mutation as the initial blueprint
 * rejection, not a UI-only fake. "Refine" navigates to NewWorkflowPage
 * with parentId=this workflow's id — the resulting POST /workflows carries
 * this workflow's completed stage(s) forward as context (see
 * workflows.py's create_workflow) rather than starting the agent cold. */
export function ApprovalGateBanner({
  completedStage,
  nextStage,
  refineObjective,
  workflowId,
  isSubmitting,
  onApprove,
  onReject,
  failure,
}: ApprovalGateBannerProps) {
  const navigate = useNavigate();
  const refineHref = `/workflows/new?title=${encodeURIComponent(refineObjective)}&parentId=${encodeURIComponent(workflowId)}`;

  if (failure) {
    return (
      <div className="flex flex-col gap-3 rounded-xl border border-danger-line/40 bg-danger-bg px-5 py-4">
        <div className="flex items-start gap-3">
          <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-danger-fg" aria-hidden="true" />
          <div className="flex-1">
            <p className="text-sm text-danger-fg">
              <strong className="font-semibold text-danger-fg">{stageLabel(failure.stage)}</strong>{" "}
              failed. The agent hit an error while running this stage — nothing was skipped or
              corrupted, and your workflow is safe to retry.
            </p>
            {failure.errorMessage && (
              <details className="group mt-2">
                <summary className="focus-ring inline-flex cursor-pointer items-center gap-1.5 rounded-sm text-xs font-medium text-danger-fg underline-offset-2 hover:underline">
                  <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                  View error details
                </summary>
                <pre className="mt-2 max-h-48 overflow-auto rounded-lg border border-danger-line/30 bg-code-bg p-3 font-mono text-xs whitespace-pre-wrap text-danger-fg">
                  {failure.errorMessage}
                </pre>
              </details>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 self-end">
          <button
            type="button"
            onClick={() => navigate(refineHref)}
            disabled={isSubmitting}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line transition-colors hover:bg-surface-hover hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
          >
            <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
            Refine
          </button>
          <button
            type="button"
            onClick={onApprove}
            disabled={isSubmitting}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-danger-solid px-4 py-2 text-xs font-semibold text-danger-on-solid shadow-xs transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {isSubmitting ? "Retrying…" : "Retry Stage"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-warning-line/40 bg-warning-bg px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-warning-fg" aria-hidden="true" />
        <p className="text-sm text-warning-fg">
          <strong className="font-semibold text-warning-fg">{stageLabel(completedStage)}</strong> is
          complete. Review its output above, then approve to start{" "}
          <strong className="font-semibold text-warning-fg">{stageLabel(nextStage)}</strong>.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={onReject}
          disabled={isSubmitting}
          className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line transition-colors hover:bg-surface-hover hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
        >
          <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Reject
        </button>
        <button
          type="button"
          onClick={() => navigate(refineHref)}
          disabled={isSubmitting}
          className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line transition-colors hover:bg-surface-hover hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
        >
          <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
          Refine
        </button>
        <button
          type="button"
          onClick={onApprove}
          disabled={isSubmitting}
          className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-accent-solid px-4 py-2 text-xs font-semibold text-accent-on-solid shadow-xs transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
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
