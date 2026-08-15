import { ArrowRight } from "lucide-react";
import type { TimelineSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { EmptyState } from "../EmptyState";

/** [ Investigation Timeline ] — "what did it investigate", as a bounded
 * vertical stepper (ADR 0024 §10), never the raw evidence/graph dump. The
 * backend already caps this at 8 steps server-side (ADR §12's scale rule)
 * — a long investigation never ships more DOM nodes than that; the
 * remainder is reported as a count only, not fetched or rendered.
 *
 * Rendered collapsed, last, as audit/provenance detail. The execution
 * trail is what makes a conclusion traceable, but it is not the decision
 * — a post-review document that leads with 25 workflow steps buries the
 * one thing a reader came for. `defaultOpen` lets a surface that really is
 * about execution (rather than about the decision) opt back in. */
export function InvestigationTimeline({
  timeline,
  defaultOpen = false,
}: {
  timeline: TimelineSectionVM;
  defaultOpen?: boolean;
}) {
  if (timeline.availability.status === "unavailable") {
    return (
      <Card title="Investigation timeline">
        <EmptyState
          title="No investigation trail recorded"
          description={
            timeline.availability.reason ?? "Context Discovery did not complete for this workflow."
          }
        />
      </Card>
    );
  }

  const total = timeline.steps.length + timeline.truncated_count;

  return (
    <Card
      title="Investigation timeline"
      description={`${total} step${total === 1 ? "" : "s"} — audit detail, not part of the decision`}
    >
      <details open={defaultOpen} className="group">
        <summary className="cursor-pointer list-none text-xs font-medium text-fg-muted hover:text-fg-secondary">
          <span className="group-open:hidden">Show how the investigation ran ({total} steps)</span>
          <span className="hidden group-open:inline">Hide execution detail</span>
        </summary>
        <div className="mt-3">
          <ol className="flex flex-col">
        {timeline.steps.map((step, i) => (
          <li key={i} className="relative flex gap-3 pb-4 last:pb-0">
            {i < timeline.steps.length - 1 && (
              <span
                className="absolute left-[11px] top-6 bottom-0 w-px bg-line"
                aria-hidden="true"
              />
            )}
            <span className="z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line bg-surface-raised text-[10px] font-bold text-fg-muted">
              {step.cycle}
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold uppercase tracking-wide text-info-fg">
                  {step.provider}
                </span>
                {step.outcome && (
                  <span
                    className={`text-[10px] font-medium ${
                      step.outcome === "success"
                        ? "text-success-fg"
                        : step.outcome === "unavailable"
                          ? "text-warning-fg"
                          : "text-fg-subtle"
                    }`}
                  >
                    {step.outcome.replace(/_/g, " ")}
                  </span>
                )}
              </div>
              <p className="text-sm text-fg-secondary">{step.summary}</p>
              {step.intent && (
                <p className="mt-0.5 flex items-start gap-1 text-xs text-fg-subtle">
                  <ArrowRight className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                  {step.intent}
                </p>
              )}
            </div>
          </li>
        ))}
          </ol>
          {timeline.truncated_count > 0 && (
            <p className="ml-9 mt-1 text-xs text-fg-subtle">
              + {timeline.truncated_count} more step{timeline.truncated_count === 1 ? "" : "s"}{" "}
              (lower-signal retrievals, kept out of this view for scale)
            </p>
          )}
        </div>
      </details>
    </Card>
  );
}
