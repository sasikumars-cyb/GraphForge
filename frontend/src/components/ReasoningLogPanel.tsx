import { StatusBadge } from "./StatusBadge";
import type { ReasoningStep } from "../types/analysis";

/**
 * Renders the Change Investigation Agent's reasoning log - one row per
 * decision, including skips, so a judge/demo viewer can see the agent
 * adaptively choosing which evidence to gather rather than a fixed pipeline.
 */
export function ReasoningLogPanel({ steps }: { steps: ReasoningStep[] }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
        Agent reasoning log
      </p>
      <ol className="flex flex-col gap-2">
        {steps.map((step) => (
          <li key={step.step_number} className="rounded-md bg-surface-raised p-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-medium text-fg-secondary">Step {step.step_number}</span>
              <StatusBadge
                label={step.tool_selected ?? "Skipped"}
                tone={step.tool_selected ? "info" : "neutral"}
              />
            </div>
            <p className="mt-1 text-fg-muted">{step.goal}</p>
            <p className="text-fg-muted">{step.plan}</p>
            {step.observation && <p className="text-fg-secondary">{step.observation.summary}</p>}
            <p className="mt-1 text-fg-muted">{step.decision}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
