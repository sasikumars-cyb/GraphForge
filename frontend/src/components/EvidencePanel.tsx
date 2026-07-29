import { useState } from "react";
import { FileSearch, Cpu, Database, Brain, AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
import type { Evidence } from "../types/agent";
import { Card } from "./Card";

interface EvidencePanelProps {
  evidence: Evidence[];
  defaultExpanded?: boolean;
}

const KIND_CONFIG: Record<string, { label: string; icon: typeof FileSearch; tone: string }> = {
  graph_traversal: {
    label: "Graph Traversal",
    icon: Database,
    tone: "text-info-fg bg-info-bg ring-info-line/30",
  },
  tool_call: {
    label: "Tool Call",
    icon: Cpu,
    tone: "text-warning-fg bg-warning-bg ring-warning-line/30",
  },
  graph_fact: {
    label: "Graph Fact",
    icon: FileSearch,
    tone: "text-success-fg bg-success-bg ring-success-line/30",
  },
  llm_reasoning: {
    label: "LLM Reasoning",
    icon: Brain,
    tone: "text-cat-7-fg bg-cat-7-bg ring-cat-7-line/30",
  },
};

const TRUNCATE_CHARS = 120;

function isFailed(ev: Evidence): boolean {
  return ev.summary.startsWith("FAILED:");
}

function EvidenceItem({ ev }: { ev: Evidence }) {
  const [expanded, setExpanded] = useState(false);
  const config = KIND_CONFIG[ev.kind] ?? KIND_CONFIG.tool_call;
  const Icon = isFailed(ev) ? AlertTriangle : config.icon;
  const failed = isFailed(ev);
  const needsTruncation = ev.summary.length > TRUNCATE_CHARS;
  const displaySummary =
    needsTruncation && !expanded ? ev.summary.slice(0, TRUNCATE_CHARS) + "…" : ev.summary;

  return (
    <li
      className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${
        failed ? "border-danger-line/30 bg-danger-bg" : "border-line-muted bg-surface"
      }`}
      role="listitem"
    >
      <span
        className={`mt-0.5 inline-flex shrink-0 items-center rounded-md p-1.5 ring-1 ring-inset ${
          failed ? "text-danger-fg bg-danger-bg ring-danger-line/30" : config.tone
        }`}
        aria-hidden="true"
      >
        <Icon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span
            className={`text-xs font-medium ${failed ? "text-danger-fg" : "text-fg-secondary"}`}
          >
            {failed ? "Failed" : config.label}
          </span>
          {ev.reference && (
            <span className="truncate text-xs text-fg-muted" title={ev.reference}>
              {ev.reference}
            </span>
          )}
        </div>
        <p className={`mt-0.5 text-sm ${failed ? "text-danger-fg" : "text-fg-secondary"}`}>
          {displaySummary}
        </p>
        {needsTruncation && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-1 flex items-center gap-1 text-xs text-info-fg hover:text-info-fg"
          >
            {expanded ? (
              <>
                <ChevronDown className="h-3 w-3" aria-hidden="true" />
                Collapse
              </>
            ) : (
              <>
                <ChevronRight className="h-3 w-3" aria-hidden="true" />
                Show full message
              </>
            )}
          </button>
        )}
      </div>
    </li>
  );
}

/** Displays a list of Evidence items grouped visually by kind, with expandable summaries. */
export function EvidencePanel({ evidence, defaultExpanded = false }: EvidencePanelProps) {
  const [panelExpanded, setPanelExpanded] = useState(defaultExpanded);

  if (evidence.length === 0) {
    return (
      <Card title="Evidence Trail">
        <p className="text-sm text-fg-muted">
          No evidence was collected for this run. Evidence items show the graph traversals, tool
          calls, and reasoning that produced the result.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Evidence Trail"
      description={`${evidence.length} verifiable item${evidence.length === 1 ? "" : "s"} — every claim is traceable`}
      action={
        <button
          type="button"
          onClick={() => setPanelExpanded((v) => !v)}
          className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg-secondary"
          aria-expanded={panelExpanded}
        >
          {panelExpanded ? (
            <>
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              Collapse
            </>
          ) : (
            <>
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              Expand
            </>
          )}
        </button>
      }
    >
      {panelExpanded ? (
        <ul className="space-y-3" role="list" aria-label="Evidence items">
          {evidence.map((ev, i) => (
            <EvidenceItem key={`${ev.kind}-${ev.reference}-${i}`} ev={ev} />
          ))}
        </ul>
      ) : (
        <div className="flex flex-wrap gap-2">
          {evidence.map((ev, i) => {
            const config = KIND_CONFIG[ev.kind] ?? KIND_CONFIG.tool_call;
            const failed = isFailed(ev);
            return (
              <span
                key={`${ev.kind}-${ev.reference}-${i}`}
                className={`rounded-full px-2.5 py-0.5 text-xs ring-1 ring-inset ${
                  failed ? "text-danger-fg bg-danger-bg ring-danger-line/30" : config.tone
                }`}
              >
                {failed ? "Failed" : config.label}
              </span>
            );
          })}
          <button
            type="button"
            onClick={() => setPanelExpanded(true)}
            className="text-xs text-info-fg hover:text-info-fg"
          >
            View details →
          </button>
        </div>
      )}
    </Card>
  );
}
