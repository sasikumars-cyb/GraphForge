import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { WorkflowStageInfo } from "../../types/agent";

interface WorkflowTimelineProps {
  stages: WorkflowStageInfo[];
  currentStage: string;
}

const STATUS_CONFIG: Record<string, { icon: typeof Circle; color: string; bgColor: string }> = {
  completed: {
    icon: CheckCircle2,
    color: "text-success-fg",
    bgColor: "bg-success-bg",
  },
  running: {
    icon: Loader2,
    color: "text-info-fg",
    bgColor: "bg-info-bg",
  },
  failed: {
    icon: XCircle,
    color: "text-danger-fg",
    bgColor: "bg-danger-bg",
  },
  pending: {
    icon: Circle,
    color: "text-fg-muted",
    bgColor: "bg-surface-raised",
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
                    ? "ring-info-line"
                    : stage.status === "completed"
                      ? "ring-success-line/40"
                      : "ring-line"
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
                    ? "text-info-fg"
                    : stage.status === "completed"
                      ? "text-success-fg"
                      : "text-fg-muted"
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
                    stage.status === "completed" ? "bg-success-bg" : "bg-surface-active"
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
