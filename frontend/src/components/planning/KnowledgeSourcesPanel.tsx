import { GitBranch, LayoutGrid, FileText, ListTodo } from "lucide-react";
import { Card } from "../Card";
import type { PlanningResult } from "../../types/agent";
import type { Evidence } from "../../types/agent";

interface KnowledgeSourcesPanelProps {
  result: PlanningResult | undefined;
  evidence: Evidence[];
}

interface SourceRow {
  icon: typeof GitBranch;
  label: string;
  connected: boolean;
  detail: string;
}

function StatusDot({ connected }: { connected: boolean }) {
  return (
    <span
      className={`h-2 w-2 shrink-0 rounded-full ${connected ? "bg-emerald-400" : "bg-slate-600"}`}
      aria-hidden="true"
    />
  );
}

export function KnowledgeSourcesPanel({ result, evidence }: KnowledgeSourcesPanelProps) {
  const reposIndexed = (result?.repositories_consulted?.length ?? 0) > 0;
  const graphUsed = result?.graph_context_used ?? false;
  const graphEmpty = reposIndexed && !graphUsed;

  const sources: SourceRow[] = [
    {
      icon: GitBranch,
      label: "Repository Graph",
      connected: graphUsed,
      detail: graphUsed
        ? `${result?.repositories_consulted?.length ?? 0} repo${(result?.repositories_consulted?.length ?? 0) === 1 ? "" : "s"} indexed`
        : reposIndexed
          ? "Indexed but empty — no components found"
          : "No repositories indexed",
    },
    {
      icon: ListTodo,
      label: "Jira",
      connected: false,
      detail: "Not connected",
    },
    {
      icon: FileText,
      label: "Confluence",
      connected: false,
      detail: "Not connected",
    },
    {
      icon: LayoutGrid,
      label: "Architecture Graph",
      connected: graphUsed,
      detail: graphUsed ? "Architecture data available" : "Empty — no components traversed",
    },
  ];

  const isGreenfield =
    !graphUsed && !reposIndexed;

  const planningMode = isGreenfield
    ? { label: "Greenfield", tone: "text-amber-300 bg-amber-500/10 ring-amber-500/30" }
    : { label: "Repository-Grounded", tone: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30" };

  // Count evidence items from graph tools
  const graphEvidence = evidence.filter(
    (e) => e.kind === "graph_traversal" || e.kind === "graph_fact"
  ).length;

  return (
    <Card title="Knowledge Sources">
      <div className="flex flex-col gap-3">
        {/* Planning mode badge */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-500">Planning mode</span>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${planningMode.tone}`}
          >
            {planningMode.label}
          </span>
        </div>

        <div className="h-px bg-slate-800" />

        {/* Source rows */}
        <ul className="flex flex-col gap-2.5">
          {sources.map((src) => {
            const Icon = src.icon;
            return (
              <li key={src.label} className="flex items-center gap-2.5">
                <Icon
                  className={`h-3.5 w-3.5 shrink-0 ${src.connected ? "text-emerald-400" : "text-slate-600"}`}
                  aria-hidden="true"
                />
                <StatusDot connected={src.connected} />
                <div className="min-w-0 flex-1">
                  <span className="text-xs font-medium text-slate-300">{src.label}</span>
                  <span className="ml-1.5 text-xs text-slate-500">{src.detail}</span>
                </div>
              </li>
            );
          })}
        </ul>

        {graphEvidence > 0 && (
          <>
            <div className="h-px bg-slate-800" />
            <p className="text-xs text-slate-500">
              {graphEvidence} graph traversal{graphEvidence === 1 ? "" : "s"} informed this plan.
            </p>
          </>
        )}

        {isGreenfield && (
          <>
            <div className="h-px bg-slate-800" />
            <p className="text-xs text-slate-500">
              Connect GitHub, Jira, or Confluence in{" "}
              <span className="text-sky-400">Settings → Tool Registry</span> to ground future
              plans in real engineering data.
            </p>
          </>
        )}
      </div>
    </Card>
  );
}
