import type { AgentStep } from "../../types/agent";
import { buildExecutionLog } from "../../lib/workflowDerived";

interface ExecutionLogPanelProps {
  step: AgentStep;
  agentLabel: string;
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
export function ExecutionLogPanel({ step, agentLabel }: ExecutionLogPanelProps) {
  const lines = buildExecutionLog(step, agentLabel);

  if (lines.length === 0) {
    return <p className="text-xs text-slate-500">No log entries yet.</p>;
  }

  return (
    <div className="rounded-lg bg-slate-950 p-3 font-mono text-[11.5px] leading-relaxed">
      {lines.map((line) => (
        <div key={line.key} className="grid grid-cols-[68px_1fr] gap-3 py-0.5">
          <span className="text-slate-600">{line.time}</span>
          <span className={KIND_STYLE[line.kind] ?? "text-slate-300"}>{line.text}</span>
        </div>
      ))}
    </div>
  );
}
