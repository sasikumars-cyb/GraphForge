import { Check, Loader2 } from "lucide-react";
import type { LiveProgress } from "../../types/agent";

// ---------------------------------------------------------------------------
// "Watch it happen" — the smallest real architecture change that gets a
// genuinely live signal out of a reasoning loop that otherwise runs
// synchronously start to finish (see app.orchestrator.live_progress's own
// docstring on why this is a checkpoint write through a separate session,
// not a change to how the run itself commits). What's rendered here is
// exactly the backend's own checklist, unmodified: real completed steps,
// at most one real active step, nothing pending or fabricated. A stage
// that hasn't reached its first checkpoint yet, or an agent that doesn't
// opt in, has no `live_progress` at all — this component simply isn't
// rendered in that case (see its one call site in WorkflowPage.tsx).
// ---------------------------------------------------------------------------

interface LiveProgressChecklistProps {
  progress: LiveProgress;
}

export function LiveProgressChecklist({ progress }: LiveProgressChecklistProps) {
  if (progress.steps.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-info-line/30 bg-info-bg/40 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-info-fg">Investigating</p>
        <span className="font-mono text-[10px] tabular-nums text-info-fg/70">
          step {progress.iteration} of {progress.max_iterations}
        </span>
      </div>
      <ul className="flex flex-col gap-1">
        {progress.steps.map((step, index) => (
          <li
            key={`${step.label}-${index}`}
            className="flex items-center gap-2 text-xs text-fg-secondary"
          >
            {step.status === "done" ? (
              <Check className="h-3.5 w-3.5 shrink-0 text-success-fg" aria-hidden="true" />
            ) : (
              <Loader2
                className="h-3.5 w-3.5 shrink-0 animate-spin text-info-fg"
                aria-hidden="true"
              />
            )}
            <span className={step.status === "active" ? "font-medium text-fg" : ""}>
              {step.label}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
