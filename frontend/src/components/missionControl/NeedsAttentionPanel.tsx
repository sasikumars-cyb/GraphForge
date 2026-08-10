import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { listWorkflows } from "../../lib/api/workflows";
import { getSystemStatus } from "../../lib/api/system";
import { formatRelativeTime } from "../../lib/formatDate";
import { stageLabel } from "../../lib/workflowDerived";
import type { WorkflowListItem } from "../../types/agent";

/**
 * Mission Control's highest-priority section (evolved from the former
 * WaitingOnYouPanel) — everything currently blocked on a human decision,
 * ranked above anything merely informational.
 *
 * Three real sources, one merged list:
 * 1. `awaiting_clarification` workflows — Context Discovery paused on a
 *    blocking question.
 * 2. `awaiting_approval` workflows — a stage finished and needs a human
 *    decision to continue (this already covers "Engineering Review ready
 *    for approval": when `current_stage === "engineering_review"`, the
 *    row's own reason text says so via `stageLabel`).
 * 3. A genuinely unhealthy platform (`system-status`'s own
 *    `platform_status !== "healthy"`) — the one real, existing signal for
 *    "something is broken", elevated here instead of only living in the
 *    quieter System Health strip lower on the page, per the instruction
 *    that an unhealthy state belongs in Needs Attention, not buried in
 *    status chrome.
 *
 * No fabricated severities: workflow items are ordered by how long
 * they've been waiting (`updated_at`, oldest first — the longest-blocked
 * item is the most overdue for attention), with any platform-health issue
 * pinned first since it can be starving every other workflow at once.
 */
export function NeedsAttentionPanel() {
  const { token } = useAuth();

  const systemQuery = useQuery({
    queryKey: ["system-status"],
    queryFn: ({ signal }) => getSystemStatus(token as string, signal),
    enabled: token !== null,
  });
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

  const isLoading =
    systemQuery.isPending || approvalQuery.isPending || clarificationQuery.isPending;

  if (isLoading) {
    return (
      <section aria-label="Needs your attention" className="flex flex-col gap-2">
        <div className="h-5 w-40 animate-pulse rounded bg-surface-raised" />
        <div className="h-20 animate-pulse rounded-xl border border-line-muted bg-surface-raised" />
      </section>
    );
  }

  const platformUnhealthy =
    systemQuery.data !== undefined && systemQuery.data.platform_status !== "healthy";
  const awaitingApproval = [...(approvalQuery.data?.items ?? [])].sort(
    (a, b) => new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime(),
  );
  const awaitingClarification = [...(clarificationQuery.data?.items ?? [])].sort(
    (a, b) => new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime(),
  );
  const total = (platformUnhealthy ? 1 : 0) + awaitingApproval.length + awaitingClarification.length;

  if (total === 0) {
    return (
      <section aria-label="Needs your attention" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">Needs your attention</h2>
        <div className="flex flex-col items-center justify-center gap-1 rounded-xl border border-success-line/30 bg-success-bg/40 px-6 py-8 text-center">
          <CheckCircle2 className="h-6 w-6 text-success-fg" aria-hidden="true" />
          <p className="mt-1 text-sm font-semibold text-fg">You're all clear</p>
          <p className="text-xs text-fg-muted">GraphForge has nothing waiting for you.</p>
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Needs your attention" className="flex flex-col gap-2">
      {/* The badge's number and the heading text are separate DOM text
          nodes with no literal space between them in source — JSX
          collapses the whitespace-only line between them, so their
          concatenated accessible name would otherwise read as "16Needs
          your attention". An explicit aria-label on the heading (with the
          visual badge marked decorative) keeps the announced name
          grammatical without depending on JSX whitespace behavior. */}
      <h2
        aria-label={`${total} item${total === 1 ? "" : "s"} need your attention`}
        className="flex items-center gap-2 text-sm font-semibold text-fg"
      >
        <span
          aria-hidden="true"
          className="flex h-5 w-5 items-center justify-center rounded-full bg-warning-solid text-[11px] font-bold text-warning-on-solid"
        >
          {total}
        </span>
        Needs your attention
      </h2>
      {/* Capped height, not a capped item count — real usage regularly
          exceeds a dozen items, and truncating the list outright would
          hide genuine work waiting on the user with no way to see the
          rest (there's no dedicated "everything pending my review" route
          to link a "view all" to). Scrolling inside this one section
          keeps it from pushing Active Missions off-screen, satisfying
          the "Needs Attention + at least part of Active Missions above
          the fold" requirement regardless of how many items exist. */}
      <div className="max-h-80 divide-y divide-line-muted overflow-y-auto rounded-xl border border-warning-line/30 bg-warning-bg/40">
        {platformUnhealthy && systemQuery.data && (
          <Link
            to="/settings"
            className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-hover"
          >
            <AlertTriangle className="h-4 w-4 shrink-0 text-danger-fg" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-fg">Platform issue</p>
              <p className="text-xs text-fg-muted">
                {systemQuery.data.platform_status === "degraded"
                  ? "Degraded — check AI provider and connection configuration"
                  : "Error — platform needs attention"}
              </p>
            </div>
            <span className="shrink-0 rounded-md bg-danger-solid px-2.5 py-1 text-xs font-semibold text-danger-on-solid">
              Resolve
            </span>
          </Link>
        )}
        {awaitingClarification.map((w) => (
          <AttentionRow
            key={w.workflow_id}
            workflow={w}
            icon={HelpCircle}
            reason="Context Discovery needs an answer"
            actionLabel="Answer"
          />
        ))}
        {awaitingApproval.map((w) => (
          <AttentionRow
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

function AttentionRow({
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
