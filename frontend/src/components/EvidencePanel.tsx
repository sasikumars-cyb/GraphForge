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

// `reference` is free text, but every producer of it (context discovery,
// planning, etc.) writes it as "source:action" or "source:action:target" —
// see e.g. "jira:fetch_work_item:PROT-5750". This reads only the source
// prefix, which is what a reader actually wants to know ("where did this
// come from?"), not the raw internal action name. A handful of sources get
// a friendlier label; anything else falls back to a generic humanization
// (underscores to spaces, title case) so a source this dictionary doesn't
// know about still reads as words, not a raw identifier.
const SOURCE_LABELS: Record<string, string> = {
  jira: "Jira",
  github: "GitHub",
  confluence: "Confluence",
  graph: "Architecture graph",
  request_parser: "Request analysis",
  test_coverage: "Test coverage",
  testrail: "TestRail",
  human: "Your input",
  human_input: "Your input",
  llm: "Reasoning",
};

// Generic, per-source "why this matters" boilerplate for the highlight
// cards below — answers "why should I trust this?" without inventing a
// ticket-specific claim the evidence itself doesn't make. Deliberately
// keyed on source, not on any particular finding's content.
const SOURCE_WHY_IT_MATTERS: Record<string, string> = {
  jira: "Defines what this investigation is trying to resolve.",
  github: "Shows where this behavior is implemented in code.",
  confluence: "Documents design context for this area.",
  graph: "Shows how this code connects to the rest of the system.",
  request_parser: "Confirms what was referenced in the original request.",
  test_coverage: "Shows existing test coverage for this area.",
  testrail: "Shows existing test coverage for this area.",
  human: "A correction or answer provided during the investigation.",
  human_input: "A correction or answer provided during the investigation.",
};

function humanize(key: string): string {
  return key
    .split("_")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

// Only treated as a "source:action" reference when it actually has that
// shape — a reference with no colon carries no source signal. `null` in
// that case, distinct from an empty string, so callers can each fall back
// to whatever's right for them (a kind label for the display name, nothing
// at all for "why it matters").
function sourcePrefix(ev: Evidence): string | null {
  const colonIndex = ev.reference.indexOf(":");
  if (colonIndex <= 0) return null;
  const prefix = ev.reference.slice(0, colonIndex).trim();
  return prefix || null;
}

function sourceLabel(ev: Evidence): string {
  // Falls back to the evidence's own `kind` label instead of humanizing
  // the whole reference into something that reads like a source but isn't
  // (e.g. a bare "traverse_architecture_graph" becoming "Traverse
  // Architecture Graph" rather than the real "Graph Traversal").
  const prefix = sourcePrefix(ev);
  if (prefix) return SOURCE_LABELS[prefix] ?? humanize(prefix);
  return KIND_CONFIG[ev.kind]?.label ?? "Evidence";
}

function whyItMatters(ev: Evidence): string | null {
  const prefix = sourcePrefix(ev);
  return prefix ? (SOURCE_WHY_IT_MATTERS[prefix] ?? null) : null;
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
            {failed ? "Failed" : sourceLabel(ev)}
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

/** Up to 3 evidence items worth reading before diving into the full list —
 * one per distinct source where possible, preferring items that actually
 * found something (a real summary, not "nothing found"/a graph traversal
 * count) so the highlight is substantive rather than mechanical. Falls
 * back to whatever's available when fewer than 3 qualify — never invents
 * content, only orders what's real. */
function pickHighlights(evidence: Evidence[]): Evidence[] {
  const substantive = evidence.filter(
    (ev) => !isFailed(ev) && ev.status !== "not_found" && ev.status !== "unavailable" && ev.kind !== "graph_traversal",
  );
  const pool = substantive.length > 0 ? substantive : evidence.filter((ev) => !isFailed(ev));
  const seenSources = new Set<string>();
  const picked: Evidence[] = [];
  for (const ev of pool) {
    const source = sourceLabel(ev);
    if (seenSources.has(source)) continue;
    seenSources.add(source);
    picked.push(ev);
    if (picked.length >= 3) break;
  }
  // Fewer than 3 distinct sources qualified — fill the rest from the same
  // pool (allowing repeat sources) rather than showing fewer than 3 when
  // more evidence exists to show.
  if (picked.length < 3) {
    for (const ev of pool) {
      if (picked.length >= 3) break;
      if (!picked.includes(ev)) picked.push(ev);
    }
  }
  return picked;
}

interface SourceCount {
  label: string;
  count: number;
  failed: boolean;
}

function countBySource(evidence: Evidence[]): SourceCount[] {
  const counts = new Map<string, SourceCount>();
  for (const ev of evidence) {
    const failed = isFailed(ev);
    const label = failed ? "Failed" : sourceLabel(ev);
    const key = `${label}:${failed}`;
    const existing = counts.get(key);
    if (existing) existing.count += 1;
    else counts.set(key, { label, count: 1, failed });
  }
  return [...counts.values()];
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

  const highlights = pickHighlights(evidence);
  const sourceCounts = countBySource(evidence);

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
        <div className="flex flex-col gap-3">
          {/* A few actual findings, not a wall of identical unlabeled pills
              (the previous default view was one "Tool Call" chip repeated
              once per item — 25 of them for a real investigation, which
              said nothing). Each card names where it came from and what
              was actually found. */}
          {highlights.length > 0 && (
            <ul className="flex flex-col gap-2" aria-label="Evidence highlights">
              {highlights.map((ev, i) => {
                const why = whyItMatters(ev);
                return (
                  <li
                    key={`highlight-${ev.kind}-${ev.reference}-${i}`}
                    className="rounded-lg border border-line-muted bg-surface px-3 py-2"
                  >
                    <p className="text-xs font-medium text-fg-secondary">{sourceLabel(ev)}</p>
                    <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">{ev.summary}</p>
                    {why && (
                      <p className="mt-1 text-[11px] text-fg-subtle">
                        <span className="font-medium text-fg-muted">Why it matters:</span> {why}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {/* Every remaining item, deduped and counted by source — replaces
              what used to be one identically-labeled pill per item. */}
          <div className="flex flex-wrap items-center gap-2" aria-label="Evidence source counts">
            {sourceCounts.map(({ label, count, failed }) => (
              <span
                key={label}
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs ring-1 ring-inset ${
                  failed
                    ? "text-danger-fg bg-danger-bg ring-danger-line/30"
                    : "text-fg-secondary bg-surface-raised ring-line"
                }`}
              >
                {label}
                {count > 1 && <span className="text-fg-subtle">· {count}</span>}
              </span>
            ))}
            <button
              type="button"
              onClick={() => setPanelExpanded(true)}
              className="text-xs text-info-fg hover:text-info-fg"
            >
              View all {evidence.length} evidence item{evidence.length === 1 ? "" : "s"} →
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}
