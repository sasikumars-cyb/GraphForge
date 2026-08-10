import type { ComponentType } from "react";
import { ArrowRight, Ban, CheckCircle2, Compass, MinusCircle, XCircle } from "lucide-react";
import type { InvestigationStep, NextInvestigationDTO } from "../../types/agent";
import { Meter, SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// Node 2 of the investigation story: "what did GraphForge investigate?"
//
// Sourced entirely from `discovery_report.investigation` (InvestigationStep[])
// — the same raw action log `DebugPanel.InvestigationTrail` already renders
// as a flat list, buried three disclosure levels down. This view adds
// nothing new: it groups the identical data by `iteration` (already on
// every step) and gives each step's `outcome` a distinct, real visual
// treatment instead of one shared "success" icon.
//
// Bounded by construction, not by truncation: a real investigation runs a
// handful of reasoning cycles (single digits to ~14 in practice — see
// `MAX_CYCLES`/`MAX_CLARIFICATION_ROUNDS` on the backend engine), never one
// row per graph node. A 1,000+ component repository cannot make this list
// longer — `discovery_report.investigation` never scales with repository
// size, only with how many times the engine actually went and looked.
// ---------------------------------------------------------------------------

const OUTCOME_META: Record<
  InvestigationStep["outcome"],
  {
    label: string;
    icon: ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;
    fg: string;
  }
> = {
  // Four real, mutually exclusive outcomes on every step — see
  // `InvestigationStep.outcome` — each gets its own icon and color so
  // "we learned something" is never visually indistinguishable from "we
  // looked and found nothing," "the source wasn't reachable," or "the
  // attempt errored."
  success: { label: "Evidence gained", icon: CheckCircle2, fg: "text-success-fg" },
  not_found: { label: "No evidence found", icon: MinusCircle, fg: "text-fg-subtle" },
  unavailable: { label: "Unavailable", icon: Ban, fg: "text-warning-fg" },
  failed: { label: "Failed", icon: XCircle, fg: "text-danger-fg" },
};

interface IterationGroup {
  iteration: number;
  steps: InvestigationStep[];
}

function groupByIteration(steps: InvestigationStep[]): IterationGroup[] {
  const order: number[] = [];
  const byIteration = new Map<number, InvestigationStep[]>();
  for (const step of steps) {
    if (!byIteration.has(step.iteration)) {
      byIteration.set(step.iteration, []);
      order.push(step.iteration);
    }
    byIteration.get(step.iteration)?.push(step);
  }
  return order
    .sort((a, b) => a - b)
    .map((iteration) => ({ iteration, steps: byIteration.get(iteration) ?? [] }));
}

interface InvestigationTimelineProps {
  steps: InvestigationStep[];
  nextInvestigation: NextInvestigationDTO[];
}

export function InvestigationTimeline({ steps, nextInvestigation }: InvestigationTimelineProps) {
  const groups = groupByIteration(steps);
  const next = nextInvestigation[0] ?? null;

  if (groups.length === 0 && !next) return null;

  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Compass}>What GraphForge investigated</SectionHeading>
      <p className="text-[11px] text-fg-subtle">
        In order, grouped by reasoning cycle — bounded to what actually ran, not the repository size.
      </p>
      <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        <ol className="flex flex-col">
          {groups.map((group, groupIndex) => {
            const isLastGroup = groupIndex === groups.length - 1;
            return (
              <li key={group.iteration} className="relative pb-3 pl-6 last:pb-0">
                {(!isLastGroup || next) && (
                  <span
                    className="absolute top-5 bottom-0 left-[9px] w-px bg-line-muted"
                    aria-hidden="true"
                  />
                )}
                <span className="absolute top-0 left-0 flex h-5 w-5 items-center justify-center rounded-full border border-line-strong bg-canvas text-[9px] font-bold text-fg-muted">
                  {group.iteration}
                </span>
                <div className="flex flex-col gap-2">
                  {group.steps.map((step, stepIndex) => {
                    const meta = OUTCOME_META[step.outcome];
                    const Icon = meta.icon;
                    return (
                      <div key={`${step.evidence_id || step.action}-${stepIndex}`} className="flex flex-col gap-0.5">
                        <p className="font-mono text-[10.5px] text-fg-subtle">
                          <span className="text-info-fg">{step.provider}</span> · {step.action}
                        </p>
                        <p className="flex items-start gap-1.5 text-xs text-fg-secondary">
                          <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${meta.fg}`} aria-hidden="true" />
                          <span>
                            {step.summary}{" "}
                            <span className={`font-semibold tracking-wide uppercase ${meta.fg} text-[9px]`}>
                              {meta.label}
                            </span>
                          </span>
                        </p>
                      </div>
                    );
                  })}
                </div>
              </li>
            );
          })}

          {next && (
            <li className="relative pl-6">
              <span className="absolute top-0 left-0 flex h-5 w-5 items-center justify-center rounded-full border border-dashed border-accent-line bg-accent-bg text-accent-fg">
                <ArrowRight className="h-3 w-3" aria-hidden="true" />
              </span>
              <div className="flex flex-col gap-1">
                <p className="text-xs font-medium text-accent-fg">Investigating next: {next.label}</p>
                <div className="flex items-center gap-1.5">
                  <Meter value={next.priority} />
                  <span className="text-[10px] text-fg-subtle">expected value, at the last reasoning pass</span>
                </div>
              </div>
            </li>
          )}
        </ol>
      </div>
    </section>
  );
}
