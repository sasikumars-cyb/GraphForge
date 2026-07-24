import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { WorkflowStageInfo } from "../../types/agent";

interface WorkflowTimelineProps {
  stages: WorkflowStageInfo[];
  currentStage: string;
}

const STATUS_CONFIG: Record<string, { icon: typeof Circle; color: string; bgColor: string }> = {
  completed: {
    icon: CheckCircle2,
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/20",
  },
  running: {
    icon: Loader2,
    color: "text-sky-400",
    bgColor: "bg-sky-500/20",
  },
  failed: {
    icon: XCircle,
    color: "text-rose-400",
    bgColor: "bg-rose-500/20",
  },
  pending: {
    icon: Circle,
    color: "text-slate-500",
    bgColor: "bg-slate-800",
  },
};

/** Horizontal workflow timeline showing SDLC stage progression. Purely a
 * status readout — stage icons are not links. On the Dashboard this used to
 * nest a per-stage <Link to="/runs/:runId"> inside the card's own
 * <Link to="/workflows/:id">, so a click on a completed stage silently
 * skipped the workflow page (invalid nested anchors resolve to whichever
 * is innermost). A workflow card now has exactly one destination. */
export function WorkflowTimeline({ stages, currentStage }: WorkflowTimelineProps) {
  return (
    <nav aria-label="Workflow stages" className="w-full">
      <ol className="flex items-center gap-0">
        {stages.map((stage, idx) => {
          const config = STATUS_CONFIG[stage.status] ?? STATUS_CONFIG.pending;
          const Icon = config.icon;
          const isActive = stage.stage === currentStage;
          const isLast = idx === stages.length - 1;

          const content = (
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={`flex h-9 w-9 items-center justify-center rounded-full ring-2 ring-inset ${config.bgColor} ${
                  isActive
                    ? "ring-sky-400"
                    : stage.status === "completed"
                      ? "ring-emerald-500/40"
                      : "ring-slate-700"
                }`}
              >
                <Icon
                  className={`h-4.5 w-4.5 ${config.color} ${stage.status === "running" ? "animate-spin" : ""}`}
                  aria-hidden="true"
                />
              </div>
              <span
                className={`text-xs font-medium ${
                  isActive
                    ? "text-sky-300"
                    : stage.status === "completed"
                      ? "text-emerald-300"
                      : "text-slate-500"
                }`}
              >
                {stage.label}
              </span>
            </div>
          );

          return (
            <li key={stage.stage} className="flex items-center">
              <div role="img" aria-label={`${stage.label}: ${stage.status}`}>
                {content}
              </div>
              {!isLast && (
                <div
                  className={`mx-2 h-0.5 w-8 sm:w-12 ${
                    stage.status === "completed" ? "bg-emerald-500/40" : "bg-slate-700"
                  }`}
                  aria-hidden="true"
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
