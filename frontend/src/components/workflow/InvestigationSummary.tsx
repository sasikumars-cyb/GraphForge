import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  FileCode,
  FileQuestion,
  Lightbulb,
  ShieldCheck,
  Target,
} from "lucide-react";
import type { ContextDiscoveryResult, EngineeringUnderstandingDTO } from "../../types/agent";
import { SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// The first screen of Context Explorer — what an engineer opening this
// ticket needs to understand within 20-30 seconds, without expanding
// Advanced Details or Debug. Every value here already exists on
// `ContextDiscoveryResult`/`EngineeringUnderstandingDTO` (both already
// fetched by `ContextExplorerPanel`); this component only curates and
// orders it, it introduces nothing new.
//
// Layout (in order):
//   1. Engineering Summary  — business goal / what we're trying to do
//   2. Root Cause           — what's broken and why (current_situation)
//   3. Files to Review      — concrete file paths from graph_components
//   4. Proposed Change      — recommendations (what should change)
//   5. Acceptance Criteria  — expected_outcome (what "done" means)
//   6. Why GraphForge believes this — confidence + evidence grounding
//
// Supporting information (repository, areas, constraints, missing info)
// lives in Advanced Details. Implementation internals stay in Debug.
// ---------------------------------------------------------------------------

const MAX_FILES = 8;

/** `graph_components` is `Record<string, unknown>[]` on the wire (the raw
 * Component facts Context Discovery recorded) — `file_path` is already on
 * every one of them (see app.indexer.graph.builder), just never read on
 * this screen before. Deduped and capped so a large architecture doesn't
 * turn "which files should I review" into another wall of text. */
function extractFilePaths(components: Record<string, unknown>[]): string[] {
  const seen: string[] = [];
  for (const component of components) {
    const path = component.file_path;
    if (typeof path === "string" && path.length > 0 && !seen.includes(path)) {
      seen.push(path);
    }
    if (seen.length >= MAX_FILES) break;
  }
  return seen;
}

interface InvestigationSummaryProps {
  result: ContextDiscoveryResult;
  understanding: EngineeringUnderstandingDTO;
}

/** Level 1, screen 1: the engineering investigation brief. Six sections,
 * each answering one question an engineer asks before touching code. */
export function InvestigationSummary({ result, understanding }: InvestigationSummaryProps) {
  const files = extractFilePaths(result.graph_components);
  const evidenceCount = result.discovery_report?.findings?.reduce(
    (total, group) => total + group.total,
    0,
  );

  return (
    <div className="flex flex-col gap-3">
      {/* 1 — Engineering Summary: orient the engineer to what this ticket
          is about — the business objective and high-level context. */}
      {understanding.business_goal && (
        <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <SectionHeading icon={Target}>Engineering Summary</SectionHeading>
          <p className="mt-1 text-xs leading-relaxed text-fg-secondary">
            {understanding.business_goal}
          </p>
        </div>
      )}

      {/* 2 — Root Cause: the evidence-grounded explanation of what's
          broken and why — the core analysis from Context Discovery. */}
      {understanding.current_situation && (
        <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <SectionHeading icon={AlertCircle}>Root Cause</SectionHeading>
          <p className="mt-1 text-xs leading-relaxed text-fg-secondary">
            {understanding.current_situation}
          </p>
        </div>
      )}

      {/* 3 — Files to Review: the concrete file paths an engineer should
          open first — extracted from the graph components Context Discovery
          recorded during its investigation. */}
      <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        <SectionHeading icon={FileCode}>Files to Review</SectionHeading>
        {files.length > 0 ? (
          <ul className="mt-1 flex flex-col gap-0.5">
            {files.map((path) => (
              <li key={path} className="truncate font-mono text-[11px] text-fg-secondary">
                {path}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-fg-subtle">
            No specific files identified — see Advanced Details for the full component list.
          </p>
        )}
      </div>

      {/* 4 — Proposed Change: what should change — the actionable
          recommendations from Context Discovery's analysis. */}
      <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        <SectionHeading icon={Lightbulb}>Proposed Change</SectionHeading>
        {understanding.recommendations.length > 0 ? (
          <ul className="mt-1 flex flex-col gap-0.5">
            {understanding.recommendations.map((item) => (
              <li key={item} className="text-xs text-fg-secondary">
                · {item}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-fg-subtle">No specific recommendation yet.</p>
        )}
      </div>

      {/* 5 — Acceptance Criteria: what "done" means — sourced from the
          same field the ticket's own AC section fills when one exists. */}
      {understanding.expected_outcome && (
        <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <SectionHeading icon={CheckCircle2}>Acceptance Criteria</SectionHeading>
          <p className="mt-1 text-xs leading-relaxed text-fg-secondary">
            {understanding.expected_outcome}
          </p>
        </div>
      )}

      {/* 6 — Why GraphForge believes this: the trust footer — confidence
          explanation plus how much evidence it's grounded in. Promoted here
          because "why should I trust this" is a first-20-seconds question. */}
      {(understanding.confidence_explanation || evidenceCount) && (
        <div className="rounded-lg border border-line-muted bg-canvas px-3 py-2.5">
          <SectionHeading icon={ShieldCheck}>Why GraphForge believes this</SectionHeading>
          {understanding.confidence_explanation && (
            <p className="mt-1 text-xs leading-relaxed text-fg-secondary">
              {understanding.confidence_explanation}
            </p>
          )}
          {typeof evidenceCount === "number" && evidenceCount > 0 && (
            <p className="mt-1 text-[11px] text-fg-subtle">
              Grounded in {evidenceCount} recorded fact{evidenceCount === 1 ? "" : "s"} — see
              Advanced Details for the full evidence trail.
            </p>
          )}
          {understanding.missing_information.length > 0 && (
            <p className="mt-1 flex items-center gap-1 text-[11px] text-warning-fg">
              <FileQuestion className="h-3 w-3 shrink-0" aria-hidden="true" />
              {understanding.missing_information.length} thing
              {understanding.missing_information.length === 1 ? "" : "s"} still unclear — see
              Advanced Details.
            </p>
          )}
        </div>
      )}

      {/* Next step — the one actionable CTA, the natural close of a
          20-second read. */}
      {understanding.next_step && (
        <div className="rounded-lg border border-accent-line/30 bg-accent-bg px-3 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-accent-fg">
            <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {understanding.next_step}
          </p>
        </div>
      )}
    </div>
  );
}
