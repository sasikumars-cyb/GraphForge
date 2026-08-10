import { Link } from "react-router-dom";
import {
  CheckCircle2,
  Database,
  FileBarChart,
  HelpCircle,
  ThumbsDown,
  ThumbsUp,
  type LucideIcon,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { listWorkflows } from "../../lib/api/workflows";
import { listReports } from "../../lib/api/reports";
import { getArchitectureSummary } from "../../lib/api/architecture";
import { formatRelativeTime } from "../../lib/formatDate";
import { stageLabel } from "../../lib/workflowDerived";

interface ActivityItem {
  key: string;
  icon: LucideIcon;
  event: string;
  subject: string;
  timestamp: string;
  to: string | null;
}

/**
 * "What changed in GraphForge recently?" — not Runs (that's every
 * execution); this is a curated, chronological merge of three real,
 * already-listable sources: workflow status changes, report generations,
 * and repository indexing completions. There is no unified activity-log
 * table backing this — each source is read with its own existing list
 * endpoint (already sorted server-side by recency) and merged
 * client-side by timestamp, so this is a snapshot composed from current
 * state, not a true append-only event log. `in_progress` workflows are
 * skipped here since ActiveMissionsPanel already covers "what's
 * currently running" — this feed is about things that changed.
 */
export function RecentActivityFeed() {
  const { token } = useAuth();
  const workflowsQuery = useQuery({
    queryKey: ["workflows-recent"],
    queryFn: ({ signal }) => listWorkflows(token as string, { page_size: 8 }, signal),
    enabled: token !== null,
  });
  const reportsQuery = useQuery({
    queryKey: ["reports-recent"],
    queryFn: ({ signal }) => listReports(token as string, signal),
    enabled: token !== null,
  });
  // Same query key KnowledgeCoveragePanel uses — TanStack Query dedupes
  // this to a single network request when both are mounted together.
  const architectureQuery = useQuery({
    queryKey: ["architecture-summary"],
    queryFn: ({ signal }) => getArchitectureSummary(token as string, signal),
    enabled: token !== null,
  });

  const isLoading =
    workflowsQuery.isPending || reportsQuery.isPending || architectureQuery.isPending;

  if (isLoading) {
    return (
      <section aria-label="Recent activity" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">Recent activity</h2>
        <div className="h-40 animate-pulse rounded-xl border border-line-muted bg-surface-raised" />
      </section>
    );
  }

  const items: ActivityItem[] = [];

  for (const w of workflowsQuery.data?.items ?? []) {
    const { event, icon } = workflowEvent(w.status, w.current_stage);
    if (!event) continue; // in_progress — see docstring
    items.push({
      key: `workflow:${w.workflow_id}:${w.status}`,
      icon,
      event,
      subject: w.title,
      timestamp: w.updated_at,
      to: `/workflows/${w.workflow_id}`,
    });
  }

  for (const r of reportsQuery.data ?? []) {
    if (r.status !== "completed" || !r.completed_at) continue;
    items.push({
      key: `report:${r.id}`,
      icon: FileBarChart,
      event: "Report generated",
      subject: r.workflow_title,
      timestamp: r.completed_at,
      to: "/reports",
    });
  }

  for (const repo of architectureQuery.data?.repositories ?? []) {
    if (!repo.last_indexed_at) continue;
    items.push({
      key: `repo:${repo.repository_id}`,
      icon: Database,
      event: "Repository indexed",
      subject: repo.full_name,
      timestamp: repo.last_indexed_at,
      to: `/repositories/${repo.repository_id}`,
    });
  }

  items.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  const recent = items.slice(0, 8);

  return (
    <section aria-label="Recent activity" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold text-fg">Recent activity</h2>
      {recent.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-xs text-fg-muted">
          Nothing has happened yet — activity will appear here as GraphForge works.
        </p>
      ) : (
        <div className="divide-y divide-line-muted rounded-xl border border-line-muted bg-surface">
          {recent.map((item) => {
            const Icon = item.icon;
            const row = (
              <div className="flex items-center gap-3 px-4 py-2.5">
                <Icon className="h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-fg">{item.event}</p>
                  <p className="truncate text-xs text-fg-muted">{item.subject}</p>
                </div>
                <span className="shrink-0 text-xs text-fg-subtle">
                  {formatRelativeTime(item.timestamp)}
                </span>
              </div>
            );
            return item.to ? (
              <Link key={item.key} to={item.to} className="block transition-colors hover:bg-surface-hover">
                {row}
              </Link>
            ) : (
              <div key={item.key}>{row}</div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function workflowEvent(
  status: string,
  currentStage: string,
): { event: string | null; icon: LucideIcon } {
  switch (status) {
    case "completed":
      return { event: "Workflow completed", icon: CheckCircle2 };
    case "awaiting_clarification":
      return { event: "Investigation needs clarification", icon: HelpCircle };
    case "awaiting_approval":
      return { event: `${stageLabel(currentStage)} ready for review`, icon: HelpCircle };
    case "approved":
      return { event: "Blueprint approved", icon: ThumbsUp };
    case "rejected":
      return { event: "Blueprint rejected", icon: ThumbsDown };
    default:
      // in_progress and anything else not yet named — deliberately not
      // shown, ActiveMissionsPanel already covers "what's running now".
      return { event: null, icon: CheckCircle2 };
  }
}
