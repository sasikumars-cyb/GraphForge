import { useEffect, useRef, useState } from "react";
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

/** How long the *active* step's own label has been on screen — client-side
 * only, reset whenever the active label changes. Not a claim about when the
 * backend actually started that step (this component has no such
 * timestamp, and fabricating one would violate "never fake progress") —
 * only "this is how long you, the viewer, have been looking at this exact
 * line," which is honest and is exactly what makes a long, silent
 * "Synthesizing findings…" stretch (curation + the final LLM synthesis
 * call — often the single longest part of a run) read as alive rather
 * than frozen. */
function useActiveStepElapsedSeconds(activeLabel: string | null): number {
  const startRef = useRef<number>(Date.now());
  const lastLabelRef = useRef<string | null>(activeLabel);
  const [elapsedMs, setElapsedMs] = useState(0);

  // React's own sanctioned "adjust state during render when a prop
  // changes" pattern — resets both the timer's start point *and* the
  // displayed value in the same render, so a fresh step never shows the
  // previous step's elapsed time even for the one frame before the next
  // interval tick would otherwise have corrected it.
  if (activeLabel !== lastLabelRef.current) {
    lastLabelRef.current = activeLabel;
    startRef.current = Date.now();
    setElapsedMs(0);
  }

  useEffect(() => {
    if (activeLabel === null) return;
    const id = window.setInterval(() => setElapsedMs(Date.now() - startRef.current), 1000);
    return () => window.clearInterval(id);
  }, [activeLabel]);

  return Math.floor(elapsedMs / 1000);
}

export function LiveProgressChecklist({ progress }: LiveProgressChecklistProps) {
  const activeStep = progress.steps.find((s) => s.status === "active") ?? null;
  const activeElapsedSeconds = useActiveStepElapsedSeconds(activeStep?.label ?? null);

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
        {progress.steps.map((step, index) => {
          const isActive = step.status === "active";
          return (
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
              <span className={isActive ? "font-medium text-fg" : ""}>{step.label}</span>
              {isActive && activeElapsedSeconds >= 5 && (
                <span className="font-mono text-[10px] tabular-nums text-info-fg/60">
                  {activeElapsedSeconds}s
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
