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
  lifecycle: "text-slate-500",
  tool_call: "text-sky-400",
  graph_traversal: "text-violet-400",
  graph_fact: "text-emerald-400",
  llm_reasoning: "text-amber-400",
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
    return <p className="text-xs text-slate-500">No log entries yet.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {lines.length > 0 && (
        <div className="rounded-lg bg-slate-950 p-3 font-mono text-[11.5px] leading-relaxed">
          {lines.map((line) => (
            <div key={line.key} className="grid grid-cols-[68px_1fr] gap-3 py-0.5">
              <span className="text-slate-600">{line.time}</span>
              <span>
                {line.reference && (
                  <span className="mr-1.5 rounded bg-slate-800 px-1 py-0.5 text-[10px] font-semibold text-slate-400">
                    {line.reference}
                  </span>
                )}
                <span className={KIND_STYLE[line.kind] ?? "text-slate-300"}>{line.text}</span>
              </span>
            </div>
          ))}
        </div>
      )}

      {llmTrace && (
        <details className="group rounded-lg border border-slate-800 bg-slate-950">
          <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-xs font-medium text-slate-400 hover:text-slate-200">
            <span>
              Full LLM Prompt &amp; Response
              <span className="ml-2 font-normal text-slate-600">
                {llmTrace.model}
                {llmTrace.latency_ms != null && ` · ${(llmTrace.latency_ms / 1000).toFixed(1)}s`}
              </span>
            </span>
            <span className="text-slate-600 group-open:hidden">Expand</span>
            <span className="hidden text-slate-600 group-open:inline">Collapse</span>
          </summary>
          <div className="flex flex-col gap-3 border-t border-slate-800 p-3">
            <div>
              <p className="mb-1 text-[10.5px] font-semibold tracking-wide text-slate-500 uppercase">
                Prompt sent to the model
              </p>
              <pre className="max-h-72 overflow-auto rounded-md bg-black/40 p-2 font-mono text-[11px] whitespace-pre-wrap text-slate-300">
                {llmTrace.prompt}
              </pre>
            </div>
            <div>
              <p className="mb-1 text-[10.5px] font-semibold tracking-wide text-slate-500 uppercase">
                Raw model response
              </p>
              <pre className="max-h-72 overflow-auto rounded-md bg-black/40 p-2 font-mono text-[11px] whitespace-pre-wrap text-slate-300">
                {llmTrace.raw_response}
              </pre>
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
