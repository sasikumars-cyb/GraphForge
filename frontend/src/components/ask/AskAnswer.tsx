import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  FileText,
  GitBranch,
  Radar,
  Layers,
  Compass,
  ListTree,
  BookOpen,
  CheckSquare,
  FlaskConical,
  Network,
  type LucideIcon,
} from "lucide-react";
import { ProvenanceTag, type ProvenanceKind } from "../intelligence/ProvenanceTag";
import type { ReadinessLevel, WorkItemType } from "../../types/conversation";

/**
 * The single rendering path for one GraphForge turn in the conversational
 * investigation — every source that produces an answer (a fresh
 * deterministic grounding, a follow-up reasoning turn) maps into this one
 * shape before reaching here. Deliberately compact: a chat turn, not a
 * report page — see this component's own layout for the progressive-
 * disclosure shape the product brief asks for (direct answer first,
 * evidence/impact only as supporting detail, never a full-page card per
 * turn).
 */
export interface DisplayEvidence {
  source: string;
  label: string;
  provenance: ProvenanceKind;
}

export interface DisplayAction {
  label: string;
  href: string;
}

export interface DisplayEntity {
  ref: string;
  name: string;
  impact_level: "low" | "medium" | "high" | null;
}

export interface DisplayImpact {
  severity: "low" | "medium" | "high";
  summary: string;
  affected: string[];
}

export interface DisplayWorkItem {
  id: string;
  title: string;
  type: WorkItemType;
  status: "existing" | "proposed";
}

export interface DisplayReadiness {
  level: ReadinessLevel;
  score: number;
}

export interface DisplayAnswer {
  answer: string;
  why: string;
  evidence: DisplayEvidence[];
  impact?: DisplayImpact;
  entities?: DisplayEntity[];
  workItems?: DisplayWorkItem[];
  readiness?: DisplayReadiness;
  actions: DisplayAction[];
  /** True when synthesis itself failed and this turn fell back to plain
   * deterministic facts (or a bare apology) — suppresses the "AI Insight"
   * framing a turn that never actually reasoned shouldn't claim. */
  degraded?: boolean;
}

const SEVERITY_STYLE: Record<"low" | "medium" | "high", string> = {
  low: "bg-success-bg text-success-fg ring-success-line/40",
  medium: "bg-warning-bg text-warning-fg ring-warning-line/40",
  high: "bg-danger-bg text-danger-fg ring-danger-line/40",
};

const ACTION_ICON: Record<string, LucideIcon> = {
  "Explore impact": Radar,
  "View repository": GitBranch,
  "View dependency graph": Layers,
  "View full investigation": Compass,
  "Create migration plan": FileText,
  "Validate migration": ClipboardCheck,
  "Show dependencies": Network,
  "Create planning workflow": FileText,
  "Generate testing strategy": ClipboardCheck,
};

const WORK_ITEM_TYPE_ICON: Record<WorkItemType, LucideIcon> = {
  epic: ListTree,
  story: BookOpen,
  task: CheckSquare,
  spike: FlaskConical,
};

const READINESS_STYLE: Record<ReadinessLevel, string> = {
  ready: "bg-success-bg text-success-fg ring-success-line/40",
  mostly_ready: "bg-info-bg text-info-fg ring-info-line/40",
  needs_clarification: "bg-warning-bg text-warning-fg ring-warning-line/40",
  not_ready: "bg-danger-bg text-danger-fg ring-danger-line/40",
};

const READINESS_LABEL: Record<ReadinessLevel, string> = {
  ready: "Ready",
  mostly_ready: "Mostly ready",
  needs_clarification: "Needs clarification",
  not_ready: "Not ready",
};

function WorkItemRow({ item }: { item: DisplayWorkItem }) {
  const Icon = WORK_ITEM_TYPE_ICON[item.type];
  return (
    <div className="flex items-center gap-2.5 py-1 text-sm">
      <Icon className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-fg-secondary">{item.title}</span>
      <span className="shrink-0 text-[11px] text-fg-muted">{item.id}</span>
      <span
        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset ${
          item.status === "existing"
            ? "bg-neutral-bg text-fg-secondary ring-line"
            : "border border-dashed border-line bg-transparent text-fg-muted ring-0"
        }`}
      >
        {item.status === "existing" ? "Existing" : "Proposed"}
      </span>
    </div>
  );
}

function EntityRow({ entity }: { entity: DisplayEntity }) {
  return (
    <div className="flex items-center gap-3 py-0.5 text-sm">
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-neutral-bg text-[11px] font-semibold text-fg-secondary ring-1 ring-inset ring-line">
        {entity.ref}
      </span>
      <span className="min-w-0 flex-1 truncate text-fg-secondary">{entity.name}</span>
      {entity.impact_level && (
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${SEVERITY_STYLE[entity.impact_level]}`}
        >
          {entity.impact_level.toUpperCase()}
        </span>
      )}
    </div>
  );
}

/** One GraphForge turn — the answer, entities (if this turn named any),
 * a one-line evidence strip, and action buttons. `why` and the full
 * impact breakdown stay behind a "Details" disclosure so the default
 * read is the short conversational answer, not a report. */
export function AskAnswer({ data }: { data: DisplayAnswer }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const hasDetails = Boolean(data.why) || (data.impact && data.impact.affected.length > 0);

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-[15px] leading-relaxed text-fg">{data.answer}</p>

      {data.readiness && (
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${READINESS_STYLE[data.readiness.level]}`}
          >
            {READINESS_LABEL[data.readiness.level]} — {data.readiness.score}%
          </span>
          <ProvenanceTag kind="derived" label="Readiness derived from completeness criteria" />
        </div>
      )}

      {data.workItems && data.workItems.length > 0 && (
        <div className="flex flex-col rounded-lg border border-line-muted bg-surface px-3 py-1.5">
          {data.workItems.map((item) => (
            <WorkItemRow key={item.id} item={item} />
          ))}
        </div>
      )}

      {data.entities && data.entities.length > 0 && (
        <div className="flex flex-col rounded-lg border border-line-muted bg-surface px-3 py-1.5">
          {data.entities.map((e) => (
            <EntityRow key={e.ref} entity={e} />
          ))}
        </div>
      )}

      {hasDetails && (
        <button
          type="button"
          onClick={() => setDetailsOpen((v) => !v)}
          className="flex w-fit items-center gap-1 text-xs font-medium text-fg-muted transition-colors hover:text-fg-secondary"
        >
          {detailsOpen ? (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Why
        </button>
      )}
      {detailsOpen && (
        <div className="flex flex-col gap-2 rounded-lg bg-neutral-bg px-3 py-2.5">
          {data.why && <p className="text-sm leading-relaxed text-fg-secondary">{data.why}</p>}
          {data.impact && data.impact.affected.length > 0 && (
            <ul className="flex flex-wrap gap-1.5" role="list">
              {data.impact.affected.map((name) => (
                <li
                  key={name}
                  className="rounded-md bg-surface px-2 py-0.5 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line"
                >
                  {name}
                </li>
              ))}
            </ul>
          )}
          {data.impact && (
            <ProvenanceTag
              kind="derived"
              label="Blast radius calculated from dependency relationships"
            />
          )}
          {!data.degraded && data.entities && data.entities.length > 0 && (
            <ProvenanceTag kind="ai_insight" label="Impact ranking is GraphForge's reasoning, not a direct measurement" />
          )}
        </div>
      )}

      {data.evidence.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-medium text-fg-muted">Evidence</span>
          {data.evidence.slice(0, 5).map((item, i) => (
            <span key={i} className="inline-flex items-center gap-1">
              <span className="text-xs text-fg-muted">{item.source}</span>
              <ProvenanceTag kind={item.provenance} />
            </span>
          ))}
        </div>
      )}

      {data.actions.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {data.actions.map((action) => {
            const Icon = ACTION_ICON[action.label] ?? ArrowRight;
            return (
              <Link
                key={action.label}
                to={action.href}
                className="flex items-center gap-1.5 rounded-lg bg-accent-solid px-3 py-1.5 text-xs font-medium text-accent-on-solid shadow-sm transition-colors hover:brightness-110"
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {action.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
