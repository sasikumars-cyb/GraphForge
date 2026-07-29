import { CheckCircle2, Loader2, XCircle, Clock } from "lucide-react";
import type { AgentStep, WorkflowStageInfo } from "../../types/agent";
import { evidenceToActivityLines, STAGE_AGENT_LABEL } from "../../lib/workflowDerived";

interface AgentActivityFeedProps {
  stages: WorkflowStageInfo[];
  stepsByRunId: Map<string, AgentStep>;
}

/** Feature 3 — a live-feeling activity feed, one section per stage, built
 * entirely from each stage's real Evidence entries (paraphrased to short
 * present-tense lines — never inventing a claim the evidence doesn't
 * contain). The running stage's most recent evidence anchors an animated
 * "in progress" line; queued stages show "Waiting…" honestly, since they
 * have produced no evidence yet. */
export function AgentActivityFeed({ stages, stepsByRunId }: AgentActivityFeedProps) {
  return (
    <div className="flex flex-col divide-y divide-line-muted">
      {stages.map((stage) => {
        const step = stage.run_id ? stepsByRunId.get(stage.run_id) : undefined;
        const label = STAGE_AGENT_LABEL[stage.stage] ?? stage.stage;
        const lines = step ? evidenceToActivityLines(step.evidence) : [];

        return (
          <div key={stage.stage} className="flex gap-3 py-3.5 first:pt-0 last:pb-0">
            <div className="mt-0.5 shrink-0">
              <StageGlyph status={stage.status} />
            </div>
            <div className="min-w-0 flex-1">
              <p
                className={`text-sm font-semibold ${
                  stage.status === "pending" ? "text-fg-muted" : "text-fg"
                }`}
              >
                {label}
              </p>

              {stage.status === "pending" && (
                <p className="mt-0.5 text-xs italic text-fg-subtle">Waiting…</p>
              )}

              {lines.length > 0 && (
                <ul className="mt-1.5 flex flex-col gap-1">
                  {lines.map((line, i) => (
                    <li
                      key={line.key}
                      className="flex items-start gap-2 text-xs animate-[activity-in_300ms_ease-out_backwards]"
                      style={{ animationDelay: `${i * 60}ms` }}
                    >
                      <span className={line.failed ? "text-danger-fg" : "text-success-fg"}>
                        {line.failed ? "✕" : "✓"}
                      </span>
                      <span className={line.failed ? "text-danger-fg" : "text-fg-secondary"}>
                        {line.text}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              {(stage.status === "running" || stage.status === "queued") && (
                <p className="mt-1.5 flex items-center gap-1.5 text-xs text-info-fg">
                  <span className="inline-flex gap-0.5">
                    <Dot delay={0} />
                    <Dot delay={150} />
                    <Dot delay={300} />
                  </span>
                  {stage.status === "queued" ? "Starting up…" : lines.length > 0 ? "Working…" : "Starting up…"}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function StageGlyph({ status }: { status: WorkflowStageInfo["status"] }) {
  if (status === "completed")
    return <CheckCircle2 className="h-4 w-4 text-success-fg" aria-hidden="true" />;
  if (status === "running" || status === "queued")
    return <Loader2 className="h-4 w-4 animate-spin text-info-fg" aria-hidden="true" />;
  if (status === "failed") return <XCircle className="h-4 w-4 text-danger-fg" aria-hidden="true" />;
  return <Clock className="h-4 w-4 text-fg-subtle" aria-hidden="true" />;
}

function Dot({ delay }: { delay: number }) {
  return (
    <span
      className="h-1 w-1 animate-bounce rounded-full bg-info-solid"
      style={{ animationDelay: `${delay}ms` }}
      aria-hidden="true"
    />
  );
}
