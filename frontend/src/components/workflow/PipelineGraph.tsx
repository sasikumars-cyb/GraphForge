import { CheckCircle2, Loader2, XCircle, Clock } from "lucide-react";
import type { WorkflowStageInfo } from "../../types/agent";

interface PipelineGraphProps {
  stages: WorkflowStageInfo[];
  selectedRunId: string | null;
  onSelectStage: (runId: string) => void;
}

const NODE_CONFIG: Record<
  WorkflowStageInfo["status"],
  { icon: typeof CheckCircle2; ring: string; iconColor: string; subLabel: string; subColor: string }
> = {
  completed: {
    icon: CheckCircle2,
    ring: "border-emerald-500/50",
    iconColor: "text-emerald-400",
    subLabel: "Complete",
    subColor: "text-emerald-400",
  },
  running: {
    icon: Loader2,
    ring: "border-sky-400 shadow-[0_0_0_3px_rgba(56,189,248,0.15)]",
    iconColor: "text-sky-400",
    subLabel: "Running…",
    subColor: "text-sky-400",
  },
  failed: {
    icon: XCircle,
    ring: "border-rose-500/60",
    iconColor: "text-rose-400",
    subLabel: "Failed",
    subColor: "text-rose-400",
  },
  pending: {
    icon: Clock,
    ring: "border-slate-800",
    iconColor: "text-slate-600",
    subLabel: "Queued",
    subColor: "text-slate-500",
  },
};

/** Features 2 + 5 — one CI/CD-style pipeline graph that both shows live
 * execution (running stage animates, connectors fill in as stages
 * complete) and doubles as the workflow's structural graph (completed
 * stages stay clickable to inspect their run). Deliberately a single
 * component rather than two near-identical visualizations. */
export function PipelineGraph({ stages, selectedRunId, onSelectStage }: PipelineGraphProps) {
  return (
    <div className="flex items-stretch" role="list" aria-label="Workflow pipeline">
      {stages.map((stage, idx) => {
        const config = NODE_CONFIG[stage.status] ?? NODE_CONFIG.pending;
        const Icon = config.icon;
        const isSelected = stage.run_id !== null && stage.run_id === selectedRunId;
        const prevDone = idx > 0 && stages[idx - 1].status === "completed";

        return (
          <div key={stage.stage} className="flex flex-1 items-stretch" role="listitem">
            {idx > 0 && (
              <div className="flex w-6 shrink-0 items-center sm:w-10">
                <div
                  className={`h-0.5 w-full transition-colors duration-500 ${
                    prevDone ? "bg-emerald-500/50" : "bg-slate-800"
                  }`}
                  aria-hidden="true"
                />
              </div>
            )}
            <button
              type="button"
              disabled={!stage.run_id}
              onClick={() => stage.run_id && onSelectStage(stage.run_id)}
              className={`flex flex-1 flex-col items-center gap-2 rounded-xl border bg-slate-900/60 px-3 py-4 text-center transition-all duration-300 ${config.ring} ${
                isSelected ? "bg-slate-800/80 ring-2 ring-brand-400" : ""
              } ${stage.run_id ? "cursor-pointer hover:bg-slate-800/60" : "cursor-default opacity-70"}`}
              aria-current={stage.status === "running" ? "step" : undefined}
              aria-label={`${stage.label}: ${config.subLabel}`}
            >
              <Icon
                className={`h-5 w-5 ${config.iconColor} ${stage.status === "running" ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              <div>
                <p className="text-sm font-semibold text-slate-100">{stage.label}</p>
                <p className={`text-[11px] font-medium ${config.subColor}`}>{config.subLabel}</p>
              </div>
              {stage.status === "running" && (
                <div className="h-1 w-full overflow-hidden rounded-full bg-slate-800">
                  <div className="h-full w-1/3 animate-[pipeline-shimmer_1.4s_ease-in-out_infinite] rounded-full bg-sky-400" />
                </div>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
