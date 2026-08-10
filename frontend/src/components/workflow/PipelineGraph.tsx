import { CheckCircle2, Loader2, XCircle, Clock, HelpCircle } from "lucide-react";
import type { WorkflowStageInfo } from "../../types/agent";

interface PipelineGraphProps {
  stages: WorkflowStageInfo[];
  selectedRunId: string | null;
  onSelectStage: (runId: string) => void;
  /**
   * Embedded rendering for space-constrained containers — e.g. Mission
   * Control's Active Missions card, which is roughly half the width this
   * component was originally built for on the full workflow detail page.
   *
   * The default layout's stage nodes are `flex-1` but have no `min-w-0`,
   * so flexbox refuses to shrink them below their label text's intrinsic
   * width ("Documentation Planning" etc.) — with 6 stages that intrinsic
   * total regularly exceeds a half-width card by 250-450px, spilling
   * stages outside the card at every breakpoint (only reachable via the
   * page's own invisible `overflow-x: auto`, not a discoverable
   * interaction). Compact mode fixes this at the source: every node gets
   * `min-w-0` so the row's total width is always exactly the container's
   * width, and a shorter icon-first node with a truncated label absorbs
   * the shrinkage instead of silently overflowing. Ordering, status
   * semantics (completed/current/pending/blocked), and accessible names
   * are unchanged from the default mode — only the visual footprint
   * shrinks. Default (`compact` unset) renders byte-for-byte the same
   * markup/classes as before this prop existed.
   */
  compact?: boolean;
}

const NODE_CONFIG: Record<
  WorkflowStageInfo["status"],
  { icon: typeof CheckCircle2; ring: string; iconColor: string; subLabel: string; subColor: string }
> = {
  completed: {
    icon: CheckCircle2,
    ring: "border-success-line/50",
    iconColor: "text-success-fg",
    subLabel: "Complete",
    subColor: "text-success-fg",
  },
  running: {
    icon: Loader2,
    ring: "border-info-line shadow-md",
    iconColor: "text-info-fg",
    subLabel: "Running…",
    subColor: "text-info-fg",
  },
  // The Run row exists and has been picked up for this stage but hasn't
  // started doing agent work yet — visually distinct from "running" (no
  // shimmer bar, since there's no progress to show) but still spinning and
  // labeled as active, not the same static clock as a stage that hasn't
  // been reached at all (see `pending` below).
  queued: {
    icon: Loader2,
    ring: "border-info-line/60",
    iconColor: "text-info-fg",
    subLabel: "Starting…",
    subColor: "text-info-fg",
  },
  // Agent finished but returned a degraded/incomplete result — closer to
  // "failed" than "completed" in that it needs the reviewer's attention,
  // but it did produce output (unlike a hard failure).
  partial: {
    icon: XCircle,
    ring: "border-warning-line/60",
    iconColor: "text-warning-fg",
    subLabel: "Partial",
    subColor: "text-warning-fg",
  },
  failed: {
    icon: XCircle,
    ring: "border-danger-line/60",
    iconColor: "text-danger-fg",
    subLabel: "Failed",
    subColor: "text-danger-fg",
  },
  pending: {
    icon: Clock,
    ring: "border-line-muted",
    iconColor: "text-fg-subtle",
    subLabel: "Queued",
    subColor: "text-fg-muted",
  },
  // Context Discovery's reasoning loop paused on a blocking question — see
  // ContextClarificationBanner. Distinct from "partial"/"failed": nothing
  // went wrong, it's waiting on a human answer to keep reasoning.
  awaiting_input: {
    icon: HelpCircle,
    ring: "border-warning-line/60",
    iconColor: "text-warning-fg",
    subLabel: "Needs input",
    subColor: "text-warning-fg",
  },
};

/** Features 2 + 5 — one CI/CD-style pipeline graph that both shows live
 * execution (running stage animates, connectors fill in as stages
 * complete) and doubles as the workflow's structural graph (completed
 * stages stay clickable to inspect their run). Deliberately a single
 * component rather than two near-identical visualizations. */
export function PipelineGraph({
  stages,
  selectedRunId,
  onSelectStage,
  compact = false,
}: PipelineGraphProps) {
  return (
    <div
      className={compact ? "flex min-w-0 items-center" : "flex items-stretch"}
      role="list"
      aria-label="Workflow pipeline"
    >
      {stages.map((stage, idx) => {
        const config = NODE_CONFIG[stage.status] ?? NODE_CONFIG.pending;
        const Icon = config.icon;
        const isSelected = stage.run_id !== null && stage.run_id === selectedRunId;
        const prevDone = idx > 0 && stages[idx - 1].status === "completed";
        const isActiveStep = stage.status === "running" || stage.status === "queued";

        return (
          <div
            key={stage.stage}
            className={compact ? "flex min-w-0 flex-1 items-center" : "flex flex-1 items-stretch"}
            role="listitem"
          >
            {idx > 0 && (
              <div
                className={
                  compact
                    ? "flex w-2 shrink-0 items-center"
                    : "flex w-6 shrink-0 items-center sm:w-10"
                }
              >
                <div
                  className={`h-0.5 w-full transition-colors duration-500 ${
                    prevDone ? "bg-success-bg" : "bg-surface-raised"
                  }`}
                  aria-hidden="true"
                />
              </div>
            )}
            <button
              type="button"
              disabled={!stage.run_id}
              onClick={() => stage.run_id && onSelectStage(stage.run_id)}
              className={
                compact
                  ? `flex min-w-0 flex-1 flex-col items-center gap-1 rounded-lg border bg-surface px-1 py-2 text-center transition-all duration-300 ${config.ring} ${
                      isSelected ? "bg-surface-raised ring-2 ring-accent-line" : ""
                    } ${stage.run_id ? "cursor-pointer hover:bg-surface-raised" : "cursor-default opacity-70"}`
                  : `flex flex-1 flex-col items-center gap-2 rounded-xl border bg-surface px-3 py-4 text-center transition-all duration-300 ${config.ring} ${
                      isSelected ? "bg-surface-raised ring-2 ring-accent-line" : ""
                    } ${stage.run_id ? "cursor-pointer hover:bg-surface-raised" : "cursor-default opacity-70"}`
              }
              aria-current={isActiveStep ? "step" : undefined}
              aria-label={`${stage.label}: ${config.subLabel}`}
            >
              <Icon
                className={`${compact ? "h-4 w-4" : "h-5 w-5"} ${config.iconColor} ${
                  isActiveStep ? "animate-spin" : ""
                }`}
                aria-hidden="true"
              />
              {compact ? (
                <p className={`w-full truncate text-[9px] font-medium ${config.subColor}`}>
                  {stage.label}
                </p>
              ) : (
                <div>
                  <p className="text-sm font-semibold text-fg">{stage.label}</p>
                  <p className={`text-[11px] font-medium ${config.subColor}`}>{config.subLabel}</p>
                </div>
              )}
              {!compact && stage.status === "running" && (
                <div className="h-1 w-full overflow-hidden rounded-full bg-surface-raised">
                  <div className="h-full w-1/3 animate-[pipeline-shimmer_1.4s_ease-in-out_infinite] rounded-full bg-info-solid" />
                </div>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
