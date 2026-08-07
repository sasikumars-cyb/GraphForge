import { Link } from "react-router-dom";
import { CheckCircle2, HelpCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { listWorkflows } from "../../lib/api/workflows";
import { formatRelativeTime } from "../../lib/formatDate";
import { stageLabel } from "../../lib/workflowDerived";
import type { WorkflowListItem } from "../../types/agent";

/**
 * GraphForge's core loop is "AI proposes, human approves" — every workflow
 * eventually stalls on a person, not the system, and until now the home
 * page had no way to show that. It was a platform-status page (providers,
 * connections, version): a user with three workflows blocked on their own
 * approval saw zero of them without visiting Runs and reading statuses one
 * row at a time.
 *
 * Two backend workflow statuses are directly the "it's on you" states —
 * `awaiting_approval` (a stage finished, needs a human decision to
 * continue) and `awaiting_clarification` (Context Discovery paused
 * mid-reasoning on a blocking question) — fetched as two small, separate
 * queries since `listWorkflows` takes one status value per call.
 */
export function WaitingOnYouPanel() {
  const { token } = useAuth();
  const approvalQuery = useQuery({
    queryKey: ["workflows-waiting", "awaiting_approval"],
    queryFn: ({ signal }) =>
      listWorkflows(token as string, { status: "awaiting_approval", page_size: 10 }, signal),
    enabled: token !== null,
  });
  const clarificationQuery = useQuery({
    queryKey: ["workflows-waiting", "awaiting_clarification"],
    queryFn: ({ signal }) =>
      listWorkflows(token as string, { status: "awaiting_clarification", page_size: 10 }, signal),
    enabled: token !== null,
  });

  const isLoading = approvalQuery.isPending || clarificationQuery.isPending;
  if (isLoading) return null; // Home's other widgets already carry a loading story; no skeleton race.

  const awaitingApproval = approvalQuery.data?.items ?? [];
  const awaitingClarification = clarificationQuery.data?.items ?? [];
  const total = awaitingApproval.length + awaitingClarification.length;

  if (total === 0) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-line-muted bg-surface px-4 py-3 text-sm text-fg-muted">
        <CheckCircle2 className="h-4 w-4 shrink-0 text-success-fg" aria-hidden="true" />
        Nothing waiting on you — every workflow is either running or done.
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-warning-solid text-[11px] font-bold text-warning-on-solid">
          {total}
        </span>
        Waiting on you
      </h2>
      <div className="divide-y divide-line-muted rounded-xl border border-warning-line/30 bg-warning-bg/40">
        {awaitingClarification.map((w) => (
          <WaitingRow
            key={w.workflow_id}
            workflow={w}
            icon={HelpCircle}
            reason="Context Discovery needs an answer"
            actionLabel="Answer"
          />
        ))}
        {awaitingApproval.map((w) => (
          <WaitingRow
            key={w.workflow_id}
            workflow={w}
            icon={CheckCircle2}
            reason={`${stageLabel(w.current_stage)} is ready for your review`}
            actionLabel="Review"
          />
        ))}
      </div>
    </section>
  );
}

function WaitingRow({
  workflow,
  icon: Icon,
  reason,
  actionLabel,
}: {
  workflow: WorkflowListItem;
  icon: typeof HelpCircle;
  reason: string;
  actionLabel: string;
}) {
  return (
    <Link
      to={`/workflows/${workflow.workflow_id}`}
      className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-hover"
    >
      <Icon className="h-4 w-4 shrink-0 text-warning-fg" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-fg">{workflow.title}</p>
        <p className="text-xs text-fg-muted">{reason}</p>
      </div>
      <span className="shrink-0 text-xs text-fg-subtle">
        {formatRelativeTime(workflow.updated_at)}
      </span>
      <span className="shrink-0 rounded-md bg-warning-solid px-2.5 py-1 text-xs font-semibold text-warning-on-solid">
        {actionLabel}
      </span>
    </Link>
  );
}
