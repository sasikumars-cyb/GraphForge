import { FileSearch, Cpu, Database, Brain, AlertTriangle } from "lucide-react";
import type { Evidence } from "../types/agent";
import { Card } from "./Card";

interface EvidencePanelProps {
  evidence: Evidence[];
}

const KIND_CONFIG: Record<string, { label: string; icon: typeof FileSearch; tone: string }> = {
  graph_traversal: {
    label: "Graph Traversal",
    icon: Database,
    tone: "text-sky-300 bg-sky-500/10 ring-sky-500/30",
  },
  tool_call: {
    label: "Tool Call",
    icon: Cpu,
    tone: "text-amber-300 bg-amber-500/10 ring-amber-500/30",
  },
  graph_fact: {
    label: "Graph Fact",
    icon: FileSearch,
    tone: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30",
  },
  llm_reasoning: {
    label: "LLM Reasoning",
    icon: Brain,
    tone: "text-violet-300 bg-violet-500/10 ring-violet-500/30",
  },
};

function isFailed(ev: Evidence): boolean {
  return ev.summary.startsWith("FAILED:");
}

/** Displays a list of Evidence items grouped visually by kind. */
export function EvidencePanel({ evidence }: EvidencePanelProps) {
  if (evidence.length === 0) {
    return (
      <Card title="Evidence Trail">
        <p className="text-sm text-slate-500">No evidence was collected for this run. Evidence items show the graph traversals, tool calls, and reasoning that produced the result.</p>
      </Card>
    );
  }

  return (
    <Card title="Evidence Trail" description={`${evidence.length} verifiable item${evidence.length === 1 ? "" : "s"} — every claim is traceable`}>
      <ul className="space-y-3" role="list" aria-label="Evidence items">
        {evidence.map((ev, i) => {
          const config = KIND_CONFIG[ev.kind] ?? KIND_CONFIG.tool_call;
          const Icon = isFailed(ev) ? AlertTriangle : config.icon;
          const failed = isFailed(ev);

          return (
            <li
              key={`${ev.kind}-${ev.reference}-${i}`}
              className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${
                failed
                  ? "border-rose-500/30 bg-rose-500/5"
                  : "border-slate-800 bg-slate-900/40"
              }`}
              role="listitem"
            >
              <span
                className={`mt-0.5 inline-flex shrink-0 items-center rounded-md p-1.5 ring-1 ring-inset ${
                  failed ? "text-rose-300 bg-rose-500/10 ring-rose-500/30" : config.tone
                }`}
                aria-hidden="true"
              >
                <Icon className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-xs font-medium ${failed ? "text-rose-300" : "text-slate-300"}`}
                  >
                    {failed ? "Failed" : config.label}
                  </span>
                  {ev.reference && (
                    <span className="truncate text-xs text-slate-500" title={ev.reference}>
                      {ev.reference}
                    </span>
                  )}
                </div>
                <p className={`mt-0.5 text-sm ${failed ? "text-rose-200" : "text-slate-200"}`}>
                  {ev.summary}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
