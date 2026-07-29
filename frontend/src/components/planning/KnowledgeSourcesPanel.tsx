import { GitBranch, LayoutGrid, FileText, ListTodo } from "lucide-react";
import { Card } from "../Card";
import type { PlanningResult } from "../../types/agent";
import type { Evidence } from "../../types/agent";

interface KnowledgeSourcesPanelProps {
  result: PlanningResult | undefined;
  evidence: Evidence[];
}

type SourceStatus = "success" | "not_found" | "unavailable" | "failed" | "unreferenced";

interface SourceRow {
  icon: typeof GitBranch;
  label: string;
  status: SourceStatus;
  detail: string;
}

const STATUS_DOT_TONE: Record<SourceStatus, string> = {
  success: "bg-success-solid",
  not_found: "bg-neutral-bg",
  unavailable: "bg-line-strong",
  failed: "bg-danger-solid",
  unreferenced: "bg-line-strong",
};

const STATUS_ICON_TONE: Record<SourceStatus, string> = {
  success: "text-success-fg",
  not_found: "text-fg-muted",
  unavailable: "text-fg-subtle",
  failed: "text-danger-fg",
  unreferenced: "text-fg-subtle",
};

function StatusDot({ status }: { status: SourceStatus }) {
  return (
    <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT_TONE[status]}`} aria-hidden="true" />
  );
}

// The Context Resolution Pipeline records one of these `reference` values
// per source it actually resolved (see app/context_pipeline/providers.py
// on the backend) — matched here by name rather than exposing the
// pipeline's internal tool/provider names to the user, per "do not expose
// unnecessary implementation details."
const JIRA_REFS = ["fetch_jira_issue"];
const CONFLUENCE_REFS = ["getTeamworkGraphContext", "getTeamworkGraphObject", "confluence_context"];
const GITHUB_REFS = ["fetch_github_reference"];

function findLatest(evidence: Evidence[], refs: string[]): Evidence | undefined {
  // Last match wins — a source can appear more than once (e.g. Confluence's
  // multi-turn discovery loop); the final entry is the one with the actual
  // outcome for this run.
  for (let i = evidence.length - 1; i >= 0; i--) {
    if (refs.includes(evidence[i].reference)) return evidence[i];
  }
  return undefined;
}

// `status` is the source of truth going forward (set by the backend — see
// Evidence.status). Runs persisted before that field existed have no
// `status` in their stored evidence JSON, so this falls back to the old
// text-based heuristic only for that legacy data, rather than showing
// "unavailable" for a run that actually succeeded.
function deriveStatus(ev: Evidence | undefined): SourceStatus {
  if (ev === undefined) return "unreferenced";
  if (ev.status) return ev.status;
  if (ev.summary.startsWith("FAILED:")) return "failed";
  if (ev.summary.toLowerCase().includes("no relevant")) return "not_found";
  return "success";
}

function cleanSummary(ev: Evidence): string {
  return ev.summary.startsWith("FAILED:") ? ev.summary.slice("FAILED:".length).trim() : ev.summary;
}

export function KnowledgeSourcesPanel({ result, evidence }: KnowledgeSourcesPanelProps) {
  const reposIndexed = (result?.repositories_consulted?.length ?? 0) > 0;
  const graphUsed = result?.graph_context_used ?? false;

  const jiraEvidence = findLatest(evidence, JIRA_REFS);
  const confluenceEvidence = findLatest(evidence, CONFLUENCE_REFS);
  const githubEvidence = findLatest(evidence, GITHUB_REFS);

  const jiraStatus = deriveStatus(jiraEvidence);
  const confluenceStatus = deriveStatus(confluenceEvidence);
  const githubStatus = deriveStatus(githubEvidence);

  const sources: SourceRow[] = [
    {
      icon: GitBranch,
      label: "Repository Graph",
      status: graphUsed ? "success" : reposIndexed ? "not_found" : "unreferenced",
      detail: graphUsed
        ? `${result?.repositories_consulted?.length ?? 0} repo${(result?.repositories_consulted?.length ?? 0) === 1 ? "" : "s"} indexed`
        : reposIndexed
          ? "Indexed but empty — no components found"
          : "No repositories indexed",
    },
    {
      icon: ListTodo,
      label: "Jira",
      status: jiraStatus,
      detail: jiraEvidence ? cleanSummary(jiraEvidence) : "No Jira reference in this request",
    },
    {
      icon: FileText,
      label: "Confluence",
      status: confluenceStatus,
      detail: confluenceEvidence
        ? cleanSummary(confluenceEvidence)
        : jiraStatus === "success"
          ? "No relevant Confluence content found"
          : "No Jira reference to anchor a Confluence lookup",
    },
    {
      icon: LayoutGrid,
      label: "GitHub",
      status: githubStatus,
      detail: githubEvidence ? cleanSummary(githubEvidence) : "No GitHub reference in this request",
    },
  ];

  const isGreenfield = !graphUsed && !reposIndexed;

  const planningMode = isGreenfield
    ? { label: "Greenfield", tone: "text-warning-fg bg-warning-bg ring-warning-line/30" }
    : { label: "Repository-Grounded", tone: "text-success-fg bg-success-bg ring-success-line/30" };

  // Count evidence items from graph tools
  const graphEvidence = evidence.filter(
    (e) => e.kind === "graph_traversal" || e.kind === "graph_fact"
  ).length;

  const anyExternalSource = [jiraStatus, confluenceStatus, githubStatus].some(
    (s) => s === "success"
  );

  return (
    <Card title="Context Sources">
      <div className="flex flex-col gap-3">
        {/* Planning mode badge */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-fg-muted">Planning mode</span>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${planningMode.tone}`}
          >
            {planningMode.label}
          </span>
        </div>

        <div className="h-px bg-surface-raised" />

        {/* Source rows */}
        <ul className="flex flex-col gap-2.5">
          {sources.map((src) => {
            const Icon = src.icon;
            return (
              <li key={src.label} className="flex items-start gap-2.5">
                <Icon
                  className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${STATUS_ICON_TONE[src.status]}`}
                  aria-hidden="true"
                />
                <StatusDot status={src.status} />
                <div className="min-w-0 flex-1">
                  <span className="text-xs font-medium text-fg-secondary">{src.label}</span>
                  <p className="mt-0.5 text-xs text-fg-muted" title={src.detail}>
                    {src.detail}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>

        {graphEvidence > 0 && (
          <>
            <div className="h-px bg-surface-raised" />
            <p className="text-xs text-fg-muted">
              {graphEvidence} graph traversal{graphEvidence === 1 ? "" : "s"} informed this plan.
            </p>
          </>
        )}

        {isGreenfield && !anyExternalSource && (
          <>
            <div className="h-px bg-surface-raised" />
            <p className="text-xs text-fg-muted">
              Connect GitHub, Jira, or Confluence in{" "}
              <span className="text-info-fg">Settings → Integrations</span> to ground future
              plans in real engineering data.
            </p>
          </>
        )}
      </div>
    </Card>
  );
}
