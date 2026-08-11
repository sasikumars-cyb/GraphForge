import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock,
  HelpCircle,
  Loader2,
  XCircle,
} from "lucide-react";
import type { WorkflowStageInfo } from "../../types/agent";

interface PipelineGraphProps {
  stages: WorkflowStageInfo[];
  selectedRunId: string | null;
  onSelectStage: (runId: string) => void;
  /**
   * Accessible name for the pipeline's `role="list"`. Defaults to the
   * generic "Workflow pipeline" the workflow detail page has always used,
   * where exactly one pipeline exists per page and no disambiguation is
   * needed. Mission Control renders one per active mission, so five
   * identically-named lists (and 30 identically-named stage buttons)
   * appeared in the accessibility tree with nothing tying each to its
   * mission — callers that render more than one should pass a name that
   * identifies which. Kept as a caller-supplied string rather than the
   * component building "… for {title}" itself, so no Mission
   * Control-specific copy lives in this shared component.
   */
  accessibleLabel?: string;
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
  {
    icon: typeof CheckCircle2;
    ring: string;
    iconColor: string;
    subLabel: string;
    subColor: string;
    /**
     * Icon used only by compact mode, where the `subLabel` text that
     * normally carries the status is not rendered. The default icons are
     * deliberately shared across statuses (`running`/`queued` are both
     * Loader2; `partial`/`failed` are both XCircle) because the text
     * beneath them always did the disambiguating. Without that text those
     * pairs collapse into a colour-only distinction — `partial` vs
     * `failed` differ by nothing but hue, which WCAG 2.1 SC 1.4.1 (Use of
     * Color) does not accept, and `running` vs `queued` differ by nothing
     * but border opacity. Set this only where the shared icon would be
     * ambiguous; statuses with an already-unique icon leave it unset and
     * fall back to `icon`.
     */
    compactIcon?: typeof CheckCircle2;
  }
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
    // Hollow dashed ring, and (in compact mode) not spinning — so "picked
    // up, agent work not started" reads differently at a glance from
    // `running`'s spinning solid arc, which shares both icon and colour.
    compactIcon: CircleDashed,
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
    // Triangle, not a circle — `failed` keeps XCircle, so the two differ
    // by silhouette rather than by amber-vs-red alone.
    compactIcon: AlertTriangle,
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
  accessibleLabel = "Workflow pipeline",
}: PipelineGraphProps) {
  return (
    <div
      className={compact ? "flex min-w-0 items-center" : "flex items-stretch"}
      role="list"
      aria-label={accessibleLabel}
    >
      {stages.map((stage, idx) => {
        const config = NODE_CONFIG[stage.status] ?? NODE_CONFIG.pending;
        // Compact mode drops the status text, so it needs the unambiguous
        // icon where one is defined; default mode keeps the icons it has
        // always used.
        const Icon = (compact && config.compactIcon) || config.icon;
        const isSelected = stage.run_id !== null && stage.run_id === selectedRunId;
        const prevDone = idx > 0 && stages[idx - 1].status === "completed";
        const isActiveStep = stage.status === "running" || stage.status === "queued";
        // Only `running` spins in compact mode. Default mode spins for
        // `queued` too, but there "Running…" vs "Starting…" is written out
        // beneath the icon; in compact both would be a spinning blue arc
        // with no text to tell them apart.
        const spins = compact ? stage.status === "running" : isActiveStep;

        // Shared across both modes — extracted so the two size variants
        // below differ only by their sizing/spacing, not by a second copy
        // of the selection and enabled-state logic.
        const stateClasses = `${config.ring} ${
          isSelected ? "bg-surface-raised ring-2 ring-accent-line" : ""
        } ${stage.run_id ? "cursor-pointer hover:bg-surface-raised" : "cursor-default opacity-70"}`;
        const sizeClasses = compact
          ? // overflow-hidden: below roughly 22px of allocated width the
            // fixed 16px icon would otherwise escape the button box and
            // overlap its neighbours. Clipping is the safe failure mode —
            // a min-width would push the row back past its container and
            // recreate the original overflow bug. No legitimate content is
            // clipped at any real width (verified 320px-1536px).
            "min-w-0 flex-1 gap-1 overflow-hidden rounded-lg px-1 py-2"
          : "flex-1 gap-2 rounded-xl px-3 py-4";

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
              className={`flex flex-col items-center border bg-surface text-center transition-all duration-300 ${sizeClasses} ${stateClasses}`}
              aria-current={isActiveStep ? "step" : undefined}
              aria-label={`${stage.label}: ${config.subLabel}`}
              // Native tooltip so the truncated label is recoverable by
              // pointer users; the accessible name above already carries
              // the full label and status for assistive tech.
              title={compact ? `${stage.label}: ${config.subLabel}` : undefined}
            >
              <Icon
                className={`${compact ? "h-4 w-4 shrink-0" : "h-5 w-5"} ${config.iconColor} ${
                  spins ? "animate-spin" : ""
                }`}
                aria-hidden="true"
              />
              {compact ? (
                <p className={`w-full truncate text-[10px] font-medium ${config.subColor}`}>
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
