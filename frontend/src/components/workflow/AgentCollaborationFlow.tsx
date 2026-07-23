import { ArrowDown } from "lucide-react";
import type { AgentStep, WorkflowStageInfo } from "../../types/agent";
import {
  deriveArtifactCounts,
  nextStageOf,
  stageLabel,
  STAGE_AGENT_LABEL,
} from "../../lib/workflowDerived";

interface AgentCollaborationFlowProps {
  stages: WorkflowStageInfo[];
  stepsByRunId: Map<string, AgentStep>;
}

/** Feature 9 — makes the hand-off between agents explicit instead of
 * showing four isolated results. "Produced" is each stage's real artifact
 * counts (same derivation as StageArtifactCard); "Consumed by" reflects
 * the real chaining `workflow_service.build_stage_context()` performs on
 * the backend (each completed stage's summary is folded into the next
 * stage's prompt) — this isn't a UI-only story, it's what actually
 * happens. */
export function AgentCollaborationFlow({ stages, stepsByRunId }: AgentCollaborationFlowProps) {
  const completedStages = stages.filter((s) => s.run_id && stepsByRunId.has(s.run_id));

  if (completedStages.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        No stage has completed yet — collaboration will appear here once one finishes.
      </p>
    );
  }

  return (
    <div className="flex flex-col items-stretch gap-0">
      {completedStages.map((stage, i) => {
        const step = stepsByRunId.get(stage.run_id as string) as AgentStep;
        const counts = deriveArtifactCounts(stage.stage, step.result);
        const next = nextStageOf(stage.stage);
        const isLast = i === completedStages.length - 1;

        return (
          <div key={stage.stage} className="flex flex-col items-center">
            <div className="w-full rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              <p className="text-sm font-bold text-slate-100">
                {STAGE_AGENT_LABEL[stage.stage] ?? stage.stage}
              </p>
              <p className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Produced
              </p>
              {counts.length > 0 ? (
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {counts.map((c) => (
                    <li
                      key={c.label}
                      className="rounded-full bg-brand-500/10 px-2.5 py-0.5 text-xs font-medium text-brand-200 ring-1 ring-inset ring-brand-500/25"
                    >
                      {c.count} {c.label}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs text-slate-500">
                  A synthesized result (no itemized artifacts).
                </p>
              )}
            </div>

            {!isLast && (
              <div className="flex flex-col items-center gap-1 py-2">
                <ArrowDown className="h-4 w-4 text-slate-600" aria-hidden="true" />
                <span className="text-[10.5px] font-medium uppercase tracking-wide text-slate-600">
                  consumed by {next ? stageLabel(next) : "next stage"}
                </span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
