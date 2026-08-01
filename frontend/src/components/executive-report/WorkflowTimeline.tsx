import { Check, X, Minus, Circle } from "lucide-react";
import { Card } from "../Card";
import { CollapsibleSection } from "./CollapsibleSection";
import type { TimelineStage } from "../../lib/executiveReportMapper";

interface WorkflowTimelineProps {
  stages: TimelineStage[];
}

const STATUS_STYLES: Record<TimelineStage["status"], string> = {
  completed: "bg-success-solid text-success-on-solid",
  running: "bg-info-solid text-info-on-solid animate-pulse",
  failed: "bg-danger-solid text-danger-on-solid",
  skipped: "bg-neutral-solid text-fg-on-solid opacity-50",
  pending: "bg-surface-raised text-fg-muted border border-line",
};

const STATUS_ICONS: Record<TimelineStage["status"], React.ComponentType<{ className?: string }>> = {
  completed: Check,
  running: Circle,
  failed: X,
  skipped: Minus,
  pending: Circle,
};

/**
 * Visual workflow timeline showing stage progression with status indicators.
 */
export function WorkflowTimeline({ stages }: WorkflowTimelineProps) {
  return (
    <CollapsibleSection title="Workflow Timeline">
      <Card>
        <div className="flex items-center overflow-x-auto py-2" role="list" aria-label="Workflow stages">
          {stages.map((stage, i) => {
            const Icon = STATUS_ICONS[stage.status];
            return (
              <div key={stage.name} className="flex items-center" role="listitem">
                {/* Stage dot + label */}
                <div className="flex flex-col items-center gap-1.5 px-3">
                  <div
                    className={`flex h-8 w-8 items-center justify-center rounded-full ${STATUS_STYLES[stage.status]}`}
                    title={`${stage.label}: ${stage.status}`}
                  >
                    <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  </div>
                  <span className="whitespace-nowrap text-[0.65rem] text-fg-muted">
                    {stage.label}
                  </span>
                </div>
                {/* Connector line */}
                {i < stages.length - 1 && (
                  <div
                    className={`h-0.5 w-8 flex-shrink-0 sm:w-12 ${
                      stage.status === "completed" ? "bg-success-solid" : "bg-line"
                    }`}
                    aria-hidden="true"
                  />
                )}
              </div>
            );
          })}
        </div>
      </Card>
    </CollapsibleSection>
  );
}
