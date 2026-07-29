import type { ReactNode } from "react";
import { ArrowRight, FileOutput } from "lucide-react";
import type { AgentStep } from "../../types/agent";
import { ConfidenceBadge } from "../agents/ConfidenceBadge";
import {
  deriveArtifactCounts,
  nextStageOf,
  resultSummary,
  stageLabel,
} from "../../lib/workflowDerived";

interface StageArtifactCardProps {
  stage: string;
  step: AgentStep;
  stages?: { stage: string }[];
}

/** Feature 4 — everything a completed stage produced, at a glance: the
 * summary, its evidence count, confidence, execution time, a count of
 * real artifacts by type, and exactly which stage consumes this output
 * next (mirrors the real backend chaining in workflow_service.py). */
export function StageArtifactCard({ stage, step, stages }: StageArtifactCardProps) {
  const summary = resultSummary(step.result);
  const counts = deriveArtifactCounts(stage, step.result);
  const next = nextStageOf(stage, stages);

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-line-muted bg-surface p-4">
      {summary && <p className="text-sm leading-relaxed text-fg-secondary">{summary}</p>}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Confidence">
          <ConfidenceBadge confidence={step.confidence} />
        </Stat>
        <Stat
          label="Evidence"
          value={`${step.evidence.length} item${step.evidence.length === 1 ? "" : "s"}`}
        />
        <Stat
          label="Execution time"
          value={step.latency_ms != null ? `${(step.latency_ms / 1000).toFixed(1)}s` : "—"}
        />
        <Stat label="Artifacts produced" value={String(counts.reduce((a, c) => a + c.count, 0))} />
      </div>

      {counts.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {counts.map((c) => (
            <span
              key={c.label}
              className="inline-flex items-center gap-1.5 rounded-full bg-surface-raised px-2.5 py-1 text-xs font-medium text-fg-secondary"
            >
              <FileOutput className="h-3 w-3 text-fg-muted" aria-hidden="true" />
              {c.count} {c.label}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 border-t border-line-muted pt-3 text-xs text-fg-muted">
        <span>Hands off to</span>
        <ArrowRight className="h-3 w-3" aria-hidden="true" />
        <span className="font-semibold text-fg-secondary">
          {next ? stageLabel(next) : "Workflow output"}
        </span>
      </div>
    </div>
  );
}

function Stat({ label, value, children }: { label: string; value?: string; children?: ReactNode }) {
  return (
    <div>
      <p className="text-[10.5px] font-medium uppercase tracking-wide text-fg-muted">{label}</p>
      <div className="mt-1 text-sm font-semibold text-fg">{children ?? value}</div>
    </div>
  );
}
