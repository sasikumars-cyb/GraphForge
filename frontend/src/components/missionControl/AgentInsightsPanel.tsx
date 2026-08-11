import { RefreshCw } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { getInvestigationIntelligenceSummary } from "../../lib/api/investigationIntelligence";
import { formatRelativeTime } from "../../lib/formatDate";

/**
 * "What has GraphForge noticed that I didn't explicitly ask about?" — the
 * one Agent Insights type this pass has real, aggregate, cross-repository
 * data for: `repeated_failure_groups` from
 * GET /investigation-intelligence/summary.
 *
 * IMPORTANT — what this data is and isn't: each group is a knowledge
 * *provider* (Jira, Confluence, the architecture graph, …) repeatedly
 * failing or coming back unavailable for one capability during recent
 * investigations. It is a retrieval-reliability signal about GraphForge's
 * own information sources, not a claim about a bug in the user's code —
 * the copy below says exactly that, deliberately, rather than borrowing
 * "engineering failure" framing this data doesn't support.
 *
 * No invented confidence, severity, or causal language — every value
 * rendered here (`provider`, `capability`, `failure_count`,
 * `most_recent_at`, `scope_type`/`scope_id`) is read straight off the
 * API response, nothing derived or guessed.
 *
 * Other Agent Insight archetypes from the reference design — duplicate
 * implementation detection, documentation drift, standalone knowledge-gap
 * feeds — have no backend aggregate today (see the Mission Control design
 * plan) and are deliberately not represented here rather than faked.
 */
export function AgentInsightsPanel() {
  const { token } = useAuth();
  const query = useQuery({
    queryKey: ["investigation-intelligence-summary"],
    queryFn: ({ signal }) => getInvestigationIntelligenceSummary(token as string, signal),
    enabled: token !== null,
  });

  if (query.isPending) {
    return (
      <section aria-label="Agent insights" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">Agent insights</h2>
        <div className="h-24 animate-pulse rounded-xl border border-line-muted bg-surface-raised" />
      </section>
    );
  }

  // A failed/unavailable fetch degrades to the same empty treatment as
  // "genuinely nothing to report" — this section is informational, never
  // load-bearing for the rest of the page, matching how the former
  // WaitingOnYouPanel already treated its own non-critical GitHub check.
  const groups = [...(query.data?.repeated_failure_groups ?? [])].sort(
    (a, b) => new Date(b.most_recent_at).getTime() - new Date(a.most_recent_at).getTime(),
  );
  const windowDays = query.data?.window_days;

  return (
    <section aria-label="Agent insights" className="flex flex-col gap-2">
      <h2
        aria-label={
          groups.length > 0 ? `Agent insights, ${groups.length} new` : "Agent insights"
        }
        className="flex items-center gap-2 text-sm font-semibold text-fg"
      >
        Agent insights
        {groups.length > 0 && (
          <span
            aria-hidden="true"
            className="flex h-5 w-5 items-center justify-center rounded-full bg-accent-solid text-[11px] font-bold text-accent-on-solid"
          >
            {groups.length}
          </span>
        )}
      </h2>
      {groups.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-line px-6 py-8 text-center">
          <p className="text-sm font-medium text-fg-secondary">No new agent insights</p>
          <p className="text-xs text-fg-muted">
            GraphForge hasn't surfaced anything requiring attention.
          </p>
        </div>
      ) : (
        <div className="divide-y divide-line-muted rounded-xl border border-line-muted bg-surface">
          {groups.slice(0, 5).map((group) => (
            <div key={`${group.scope_type}:${group.scope_id}:${group.capability}:${group.provider}`} className="flex items-start gap-3 px-4 py-3">
              <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 text-accent-fg" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">
                  Repeated knowledge retrieval failures detected
                </p>
                <p className="text-xs text-fg-muted">
                  {group.provider} · {group.capability}
                </p>
                <p className="mt-0.5 text-xs text-fg-subtle">
                  {group.failure_count} retrieval failure{group.failure_count === 1 ? "" : "s"}
                  {windowDays ? ` in the last ${windowDays} days` : ""} · scope: {group.scope_type}
                </p>
              </div>
              <span className="shrink-0 text-xs text-fg-subtle">
                {formatRelativeTime(group.most_recent_at)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
