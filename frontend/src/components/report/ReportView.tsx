import type { ReportViewModel } from "../../lib/api/reports";
import { ReportHeader } from "./ReportHeader";
import { ReviewOutcomeCard } from "./ReviewOutcomeCard";
import { ConfidenceJourneyCard } from "./ConfidenceJourneyCard";
import { InvestigationTimeline } from "./InvestigationTimeline";
import { KnowledgeSplitPanel } from "./KnowledgeSplitPanel";
import { ConfirmedFindingsCard } from "./ConfirmedFindingsCard";
import { HypothesesSection } from "./HypothesesSection";
import { ContradictionsSection } from "./ContradictionsSection";
import { EvidenceSummaryCard } from "./EvidenceSummaryCard";
import { NextActionsCard } from "./NextActionsCard";

/**
 * The post–Engineering Review document. Every section below is a real
 * component rendering an already-decided `ReportViewModel` field; nothing
 * here interprets, invents, reshapes, or recounts data — see each child
 * component's own docstring for its exact source.
 *
 * Section order is the engineering-decision hierarchy, and this is the one
 * place it is decided:
 *
 *   1. Problem statement          ReportHeader
 *   2. Investigation summary      executive_summary
 *   3. Confirmed findings         ConfirmedFindingsCard   (what's proven)
 *   4. Potential root cause       HypothesesSection       (what's not)
 *   5. Evidence                   EvidenceSummaryCard
 *   6. Contradictions / gaps      ContradictionsSection + KnowledgeSplitPanel
 *   7. Engineering Review outcome ReviewOutcomeCard
 *   8. Recommended next steps     NextActionsCard
 *   9. Confidence & readiness     ConfidenceJourneyCard
 *  10. Provenance                 InvestigationTimeline   (collapsed)
 *
 * The ordering rule behind it: proof before speculation, and the decision
 * before the execution trail. The timeline is real, useful provenance and
 * is kept in full — but it renders last and collapsed, because a document
 * whose job is to communicate an engineering decision cannot be dominated
 * by the workflow steps that produced it.
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
      <ConfirmedFindingsCard findings={model.findings} />
      <HypothesesSection hypotheses={model.hypotheses} />
      <EvidenceSummaryCard evidence={model.evidence} />
      <ContradictionsSection contradictions={model.contradictions} />
      <KnowledgeSplitPanel knowledge={model.knowledge} />
      <ReviewOutcomeCard outcome={model.review_outcome} />
      <NextActionsCard nextActions={model.next_actions} />
      <ConfidenceJourneyCard confidence={model.confidence} />
      <InvestigationTimeline timeline={model.timeline} />
    </div>
  );
}
