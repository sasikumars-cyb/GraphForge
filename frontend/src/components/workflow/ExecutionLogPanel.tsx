import type { AgentStep, LLMTrace } from "../../types/agent";
import { buildExecutionLog } from "../../lib/workflowDerived";

interface ExecutionLogPanelProps {
  step: AgentStep;
  agentLabel: string;
  /** The real prompt sent to the LLM and its raw response, when the step
   * result carries one (currently: Planning). Absent for stages/results
   * that don't persist this yet, or for steps run before it existed. */
  llmTrace?: LLMTrace | null;
}

const KIND_STYLE: Record<string, string> = {
  lifecycle: "text-fg-muted",
  tool_call: "text-info-fg",
  graph_traversal: "text-cat-7-fg",
  graph_fact: "text-success-fg",
  llm_reasoning: "text-warning-fg",
  human_input: "text-accent-fg",
};

/** Feature 7 — a scrolling, timestamped log for a stage. Anchor
 * timestamps (stage start/end, in the step-level metadata) are real;
 * per-evidence-line clock times are interpolated between those two real
 * anchors, since the API doesn't expose per-evidence timestamps — see
 * buildExecutionLog()'s doc comment for why that's an honest choice
 * rather than a fabricated one. */
export function ExecutionLogPanel({ step, agentLabel, llmTrace }: ExecutionLogPanelProps) {
  const lines = buildExecutionLog(step, agentLabel);

  if (lines.length === 0 && !llmTrace) {
    return <p className="text-xs text-fg-muted">No log entries yet.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {lines.length > 0 && (
        <div className="rounded-lg bg-canvas p-3 font-mono text-[11.5px] leading-relaxed">
          {lines.map((line) => (
            <div key={line.key} className="grid grid-cols-[68px_1fr] gap-3 py-0.5">
              <span className="text-fg-subtle">{line.time}</span>
              <span>
                {line.reference && (
                  <span className="mr-1.5 rounded bg-surface-raised px-1 py-0.5 text-[10px] font-semibold text-fg-muted">
                    {line.reference}
                  </span>
                )}
                <span className={KIND_STYLE[line.kind] ?? "text-fg-secondary"}>{line.text}</span>
              </span>
            </div>
          ))}
        </div>
      )}

      {llmTrace && (
        <details className="group rounded-lg border border-line-muted bg-canvas">
          <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-xs font-medium text-fg-muted hover:text-fg-secondary">
            <span>
              Full LLM Prompt &amp; Response
              <span className="ml-2 font-normal text-fg-subtle">
                {llmTrace.provider ? `${llmTrace.provider} · ` : ""}
                {llmTrace.model}
                {llmTrace.latency_ms != null && ` · ${(llmTrace.latency_ms / 1000).toFixed(1)}s`}
                {llmTrace.total_tokens != null &&
                  ` · ${llmTrace.total_tokens.toLocaleString()} tokens`}
                {llmTrace.estimated_cost_usd != null &&
                  ` · ~$${llmTrace.estimated_cost_usd.toFixed(4)}`}
              </span>
            </span>
            <span className="text-fg-subtle group-open:hidden">Expand</span>
            <span className="hidden text-fg-subtle group-open:inline">Collapse</span>
          </summary>
          <div className="flex flex-col gap-3 border-t border-line-muted p-3">
            {(llmTrace.prompt_tokens != null || llmTrace.completion_tokens != null) && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-fg-muted">
                {llmTrace.prompt_tokens != null && (
                  <span>Prompt: {llmTrace.prompt_tokens.toLocaleString()} tokens</span>
                )}
                {llmTrace.completion_tokens != null && (
                  <span>Completion: {llmTrace.completion_tokens.toLocaleString()} tokens</span>
                )}
                {llmTrace.estimated_cost_usd != null && (
                  <span>
                    Estimated cost: ${llmTrace.estimated_cost_usd.toFixed(4)} (not a billing figure)
                  </span>
                )}
              </div>
            )}
            <div>
              <p className="mb-1 text-[10.5px] font-semibold tracking-wide text-fg-muted uppercase">
                Prompt sent to the model
              </p>
              <pre className="max-h-72 overflow-auto rounded-md bg-black/40 p-2 font-mono text-[11px] whitespace-pre-wrap text-fg-secondary">
                {llmTrace.prompt}
              </pre>
            </div>
            <div>
              <p className="mb-1 text-[10.5px] font-semibold tracking-wide text-fg-muted uppercase">
                Raw model response
              </p>
              <pre className="max-h-72 overflow-auto rounded-md bg-black/40 p-2 font-mono text-[11px] whitespace-pre-wrap text-fg-secondary">
                {llmTrace.raw_response}
              </pre>
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
