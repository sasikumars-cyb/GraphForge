import { GitBranch, ListTodo, FileText, Sprout } from "lucide-react";
import { Card } from "../Card";
import type { PlanningResult } from "../../types/agent";

interface GreenfieldRecommendationsProps {
  result: PlanningResult;
}

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

function deriveRepoSuggestions(result: PlanningResult): string[] {
  // Use affected_components first — they're already service names
  const fromComponents = result.affected_components
    .map(slugify)
    .filter((s) => s.length > 2);

  if (fromComponents.length >= 3) return fromComponents.slice(0, 6);

  // Fall back to step component references
  const fromSteps = result.implementation_steps
    .map((s) => s.affected_component ?? "")
    .filter(Boolean)
    .map(slugify)
    .filter((s) => s.length > 2);

  const combined = [...new Set([...fromComponents, ...fromSteps])];
  if (combined.length >= 2) return combined.slice(0, 6);

  // Generic fallback using project type
  return [
    "core-service",
    "shared-library",
    "api-gateway",
    "data-store",
    "worker-service",
    "infrastructure",
  ];
}

function deriveEpics(result: PlanningResult): string[] {
  // Use implementation_phases if the v2 agent produced them
  if (result.implementation_phases && result.implementation_phases.length > 0) {
    return result.implementation_phases.map((p) => p.name).slice(0, 8);
  }

  // Derive from implementation steps — group by order buckets
  if (result.implementation_steps.length > 0) {
    const steps = result.implementation_steps.slice(0, 8);
    return steps.map((s) => {
      const desc = s.description;
      // Take first noun phrase (up to first verb or comma)
      const match = desc.match(/^(?:implement|create|build|add|set up|configure|design|define|migrate|refactor|integrate|establish)\s+(.+?)(?:[,.]|$)/i);
      return match ? match[1].replace(/\b\w/g, (c) => c.toUpperCase()).slice(0, 40) : desc.slice(0, 40);
    });
  }

  return ["Foundation", "Core Services", "API Layer", "Data Layer", "Observability", "Deployment"];
}

const CONFLUENCE_DOCS = [
  "Architecture Overview",
  "ADR-001 · Technology Choices",
  "Data Contracts & Schemas",
  "API Specification",
  "Deployment Guide",
  "Runbook & Playbook",
  "Monitoring & Alerting Guide",
  "Onboarding Guide",
];

export function GreenfieldBanner({ result }: { result: PlanningResult }) {
  const label = result.project_type_label || "New Project";
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3">
      <Sprout className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" aria-hidden="true" />
      <div>
        <p className="text-sm font-medium text-amber-200">Greenfield Project — {label}</p>
        <p className="mt-0.5 text-xs text-amber-300/70">
          No engineering knowledge sources (repositories, Jira, Confluence) were available. This
          plan was generated from first principles. Connect your engineering systems in{" "}
          <span className="font-medium text-amber-200">Settings → Tool Registry</span> to ground
          future plans in real architecture data.
        </p>
      </div>
    </div>
  );
}

export function GreenfieldRecommendations({ result }: GreenfieldRecommendationsProps) {
  const repos = deriveRepoSuggestions(result);
  const epics = deriveEpics(result);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      {/* Repository recommendations */}
      <Card
        title="Suggested Repositories"
        description="Recommended initial repository structure for this project"
      >
        <ul className="flex flex-col gap-2">
          {repos.map((repo, i) => (
            <li
              key={repo}
              className="flex items-center gap-2.5 rounded-md border border-slate-800 bg-slate-900/40 px-3 py-2"
            >
              <GitBranch className="h-3.5 w-3.5 shrink-0 text-sky-400" aria-hidden="true" />
              <span className="font-mono text-xs text-slate-300">{repo}</span>
              {i === 0 && (
                <span className="ml-auto rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-400">
                  primary
                </span>
              )}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-slate-600">
          Create these in GitHub and index them via{" "}
          <span className="text-slate-500">Settings → Repositories</span>.
        </p>
      </Card>

      {/* Jira epics */}
      <Card
        title="Suggested Jira Epics"
        description="Recommended epic structure aligned with the implementation plan"
      >
        <ul className="flex flex-col gap-2">
          {epics.map((epic, i) => (
            <li
              key={i}
              className="flex items-center gap-2.5 rounded-md border border-slate-800 bg-slate-900/40 px-3 py-2"
            >
              <ListTodo className="h-3.5 w-3.5 shrink-0 text-violet-400" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <span className="text-xs font-mono text-slate-500">
                  EPIC-{String(i + 1).padStart(2, "0")}
                </span>
                <span className="ml-2 text-xs text-slate-300">{epic}</span>
              </div>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-slate-600">
          Connect Jira in <span className="text-slate-500">Settings → Tool Registry</span> to
          automatically populate backlog.
        </p>
      </Card>

      {/* Confluence docs */}
      <Card
        title="Suggested Documentation"
        description="Recommended Confluence pages to create alongside this project"
      >
        <ul className="flex flex-col gap-2">
          {CONFLUENCE_DOCS.map((doc) => (
            <li
              key={doc}
              className="flex items-center gap-2.5 rounded-md border border-slate-800 bg-slate-900/40 px-3 py-2"
            >
              <FileText className="h-3.5 w-3.5 shrink-0 text-teal-400" aria-hidden="true" />
              <span className="text-xs text-slate-300">{doc}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-slate-600">
          Connect Confluence in{" "}
          <span className="text-slate-500">Settings → Tool Registry</span> to link docs to runs.
        </p>
      </Card>
    </div>
  );
}
