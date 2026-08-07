import { useQuery } from "@tanstack/react-query";
import { TrendingDown } from "lucide-react";
import { useAuth } from "../../app/auth-context";
import { getWorkflowLLMUsage } from "../../lib/api/metrics";
import { formatUsd } from "../../lib/formatMetrics";
import { formatDuration, stageLabel } from "../../lib/workflowDerived";
import type { AgentStep, WorkflowStageInfo } from "../../types/agent";

interface StageProgressionPanelProps {
  workflowId: string;
  stages: WorkflowStageInfo[];
  stepsByRunId: Map<string, AgentStep>;
}

interface CompletedStage {
  stage: string;
  step: AgentStep;
  durationMs: number | null;
}

const DROP_THRESHOLD = 0.05;

/**
 * Two things about a run's real execution that existed in the data but had
 * no view: how confidence moved stage to stage, and where the time and
 * money actually went.
 *
 * A workflow whose confidence went 0.9 → 0.6 → 0.4 is a materially
 * different artifact from one that went 0.5 → 0.7 → 0.85, and until now
 * they looked identical once finished — only the final stage's badge showed
 * anywhere. A drop between two stages is usually the most informative
 * single fact in the run (it means a later agent found something an
 * earlier one missed), so it's called out explicitly rather than left to
 * whoever squints at consecutive percentages.
 *
 * Duration comes from each step's own timestamps (always available, no
 * extra request). Cost is layered in from the workflow's LLM usage
 * endpoint — already fetched standalone by WorkflowLLMUsagePage, reused
 * here via the same TanStack Query cache key — and degrades to
 * duration-only if that request is still in flight or fails; nothing here
 * blocks on it.
 */
export function StageProgressionPanel({
  workflowId,
  stages,
  stepsByRunId,
}: StageProgressionPanelProps) {
  const { token } = useAuth();
  const usageQuery = useQuery({
    queryKey: ["workflow-llm-usage", workflowId],
    queryFn: ({ signal }) => getWorkflowLLMUsage(token as string, workflowId, signal),
    enabled: token !== null,
  });
  const costByStage = new Map(
    (usageQuery.data?.stages ?? []).map((s) => [s.stage, s.cost_usd]),
  );

  const completed: CompletedStage[] = stages
    .filter((s) => s.run_id && stepsByRunId.has(s.run_id))
    .map((s) => {
      const step = stepsByRunId.get(s.run_id as string) as AgentStep;
      const durationMs =
        step.created_at && step.completed_at
          ? new Date(step.completed_at).getTime() - new Date(step.created_at).getTime()
          : step.latency_ms;
      return { stage: s.stage, step, durationMs };
    });

  if (completed.length === 0) return null;

  const maxDuration = Math.max(...completed.map((c) => c.durationMs ?? 0), 1);
  const totalDurationMs = completed.reduce((sum, c) => sum + (c.durationMs ?? 0), 0);
  const totalCost = [...costByStage.values()].reduce((sum, c) => sum + c, 0);

  return (
    <div className="flex flex-col gap-5 rounded-xl border border-line-muted bg-surface p-5">
      {/* Confidence progression */}
      <div>
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Confidence by stage
        </p>
        <div className="flex items-stretch gap-1">
          {completed.map((c, i) => {
            const score = c.step.confidence.score;
            const prevScore = i > 0 ? completed[i - 1].step.confidence.score : null;
            const dropped =
              score !== null && prevScore !== null && prevScore - score >= DROP_THRESHOLD;
            return (
              <div key={c.stage} className="flex flex-1 items-center gap-1">
                {i > 0 && (
                  <div className="flex w-8 shrink-0 flex-col items-center justify-center">
                    <div
                      className={`h-px w-full ${dropped ? "bg-danger-line" : "bg-line-strong"}`}
                    />
                    {dropped && (
                      <span
                        className="mt-0.5 flex items-center gap-0.5 text-[10px] font-semibold text-danger-fg"
                        title={`Confidence dropped from ${Math.round((prevScore as number) * 100)}% to ${Math.round((score as number) * 100)}% here — the ${stageLabel(c.stage)} stage likely found something the previous stage didn't account for.`}
                      >
                        <TrendingDown className="h-3 w-3" aria-hidden="true" />
                      </span>
                    )}
                  </div>
                )}
                <div className="flex min-w-0 flex-1 flex-col items-center gap-1 rounded-lg bg-canvas px-2 py-2 text-center">
                  <span
                    className={`text-sm font-bold tabular-nums ${
                      score === null
                        ? "text-fg-muted"
                        : score >= 0.8
                          ? "text-success-fg"
                          : score >= 0.5
                            ? "text-warning-fg"
                            : "text-danger-fg"
                    }`}
                  >
                    {score === null ? "—" : `${Math.round(score * 100)}%`}
                  </span>
                  <span className="truncate text-[10px] text-fg-muted" title={stageLabel(c.stage)}>
                    {stageLabel(c.stage)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Duration / cost waterfall */}
      <div>
        <div className="mb-3 flex items-baseline justify-between">
          <p className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
            Time &amp; cost by stage
          </p>
          <p className="text-xs text-fg-muted">
            {formatDuration(totalDurationMs)}
            {usageQuery.data && totalCost > 0 && <> · {formatUsd(totalCost)}</>}
          </p>
        </div>
        <div className="flex flex-col gap-2">
          {completed.map((c) => {
            const cost = costByStage.get(c.stage);
            const widthPct = c.durationMs === null ? 0 : Math.max((c.durationMs / maxDuration) * 100, 4);
            return (
              <div key={c.stage} className="flex items-center gap-3">
                <span className="w-28 shrink-0 truncate text-xs text-fg-muted" title={stageLabel(c.stage)}>
                  {stageLabel(c.stage)}
                </span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-bg">
                  <div
                    className="h-full rounded-full bg-info-solid"
                    style={{ width: `${widthPct}%` }}
                  />
                </div>
                <span className="w-16 shrink-0 text-right text-xs tabular-nums text-fg-secondary">
                  {c.durationMs === null ? "—" : formatDuration(c.durationMs)}
                </span>
                <span className="w-16 shrink-0 text-right text-xs tabular-nums text-fg-muted">
                  {cost !== undefined ? formatUsd(cost) : ""}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
