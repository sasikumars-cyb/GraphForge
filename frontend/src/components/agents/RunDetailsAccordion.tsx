import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { RunStatusBadge } from "./RunStatusBadge";
import type { AgentStep, RunDetail } from "../../types/agent";

/** Technical execution detail (goal, subject, model, timestamps, latency,
 * prompt version) for a single agent run — collapsed by default,
 * everywhere. Extracted from what used to be three near-identical copies
 * (Planning had this pattern right, as a collapsed accordion at the
 * bottom; Development and Testing each had their own always-open "Run
 * Details" card sitting *above* the actual implementation/test-plan
 * story, which is backwards — "how would we implement this" should be
 * the first thing on screen, not "what model ran and when"). One
 * component now, used the same collapsed-at-the-bottom way in all three,
 * so the same kind of information gets the same visual weight in the
 * same place regardless of which capability page it's on. */
export function RunDetailsAccordion({
  run,
  step,
  /** Overrides `run.goal` when the raw goal string ("plan_freeform",
   * "develop_change_plan", ...) isn't the label the page wants shown —
   * defaults to the real value rather than requiring every caller to
   * pass one. */
  goalLabel,
}: {
  run: RunDetail;
  step: AgentStep | undefined;
  goalLabel?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-line-muted bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="focus-ring flex w-full items-center justify-between px-5 py-4 text-left"
        aria-expanded={open}
      >
        <span className="text-sm font-semibold text-fg-secondary">Run details</span>
        {open ? (
          <ChevronDown className="h-4 w-4 text-fg-muted" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-4 w-4 text-fg-muted" aria-hidden="true" />
        )}
      </button>

      {open && (
        <div className="border-t border-line-muted px-5 py-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
            <div>
              <dt className="text-xs text-fg-muted">Goal</dt>
              <dd className="text-fg-secondary">{goalLabel ?? run.goal}</dd>
            </div>
            {run.subject.display_name && (
              <div>
                <dt className="text-xs text-fg-muted">Subject</dt>
                <dd className="truncate text-fg-secondary" title={run.subject.display_name}>
                  {run.subject.display_name}
                </dd>
              </div>
            )}
            <div>
              <dt className="text-xs text-fg-muted">Status</dt>
              <dd>
                <RunStatusBadge status={run.status} />
              </dd>
            </div>
            {run.model && (
              <div>
                <dt className="text-xs text-fg-muted">Model</dt>
                <dd className="text-fg-secondary">{run.model}</dd>
              </div>
            )}
            {run.started_at && (
              <div>
                <dt className="text-xs text-fg-muted">Started</dt>
                <dd className="text-fg-secondary">{new Date(run.started_at).toLocaleString()}</dd>
              </div>
            )}
            {run.completed_at && (
              <div>
                <dt className="text-xs text-fg-muted">Completed</dt>
                <dd className="text-fg-secondary">{new Date(run.completed_at).toLocaleString()}</dd>
              </div>
            )}
            {step?.latency_ms != null && (
              <div>
                <dt className="text-xs text-fg-muted">Duration</dt>
                <dd className="text-fg-secondary">{(step.latency_ms / 1000).toFixed(1)}s</dd>
              </div>
            )}
            {step?.confidence && (
              <div>
                <dt className="text-xs text-fg-muted">Confidence</dt>
                <dd className="text-fg-secondary">
                  {step.confidence.score !== null ? `${Math.round(step.confidence.score * 100)}%` : "—"}
                </dd>
              </div>
            )}
            {step?.prompt_version && (
              <div>
                <dt className="text-xs text-fg-muted">Prompt version</dt>
                <dd className="text-fg-secondary">{step.prompt_version}</dd>
              </div>
            )}
          </dl>
        </div>
      )}
    </div>
  );
}
