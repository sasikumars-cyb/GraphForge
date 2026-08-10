import type { ReportViewModel } from "../../lib/api/reports";
import { ReportHeader } from "./ReportHeader";
import { ConfidenceJourneyCard } from "./ConfidenceJourneyCard";
import { InvestigationTimeline } from "./InvestigationTimeline";
import { KnowledgeSplitPanel } from "./KnowledgeSplitPanel";
import { HypothesesSection } from "./HypothesesSection";
import { ContradictionsSection } from "./ContradictionsSection";
import { EvidenceSummaryCard } from "./EvidenceSummaryCard";
import { NextActionsCard } from "./NextActionsCard";

/**
 * Report V2 Phase 2 (ADR 0024) — the deterministic, visualization-first
 * report. Every section below is a real component rendering an already-
 * decided `ReportViewModel` field; nothing here interprets, invents, or
 * reshapes data — see each child component's own docstring for its exact
 * source.
 *
 * Section order matches ADR 0024 §3's visual hierarchy exactly:
 * Question → Confidence/Readiness → Timeline → Known/Unknown →
 * Hypotheses → Contradictions → Evidence → What's next. This is the one
 * place that order is decided — never re-ordered per call site.
 */
export function ReportView({ model }: { model: ReportViewModel }) {
  return (
    <div className="flex flex-col gap-4 p-1">
      <ReportHeader header={model.header} />
      {model.executive_summary && (
        <p className="rounded-lg border border-accent-line/30 bg-accent-bg px-4 py-3 text-sm leading-relaxed text-accent-fg">
          {model.executive_summary}
        </p>
      )}
      <ConfidenceJourneyCard confidence={model.confidence} />
      <InvestigationTimeline timeline={model.timeline} />
      <KnowledgeSplitPanel knowledge={model.knowledge} />
      <HypothesesSection hypotheses={model.hypotheses} />
      <ContradictionsSection contradictions={model.contradictions} />
      <EvidenceSummaryCard evidence={model.evidence} />
      <NextActionsCard nextActions={model.next_actions} />
    </div>
  );
}
