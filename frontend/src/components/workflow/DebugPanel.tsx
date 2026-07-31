import { useState } from "react";
import {
  AlertTriangle,
  Bug,
  Check,
  CircleSlash,
  Database,
  GitBranch,
  ListChecks,
  Loader2,
  Search,
  Workflow,
  X,
  XCircle,
} from "lucide-react";
import type {
  CapabilityBreakdown,
  DebugBundleDTO,
  DiscoveryGap,
  FindingGroup,
  InvestigationStep,
  TranscriptEntry,
} from "../../types/agent";
import { SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// Level 3 — Debug. "How did it reach that conclusion?" This is the only
// level that shows raw execution internals, and it costs a real fetch
// (`?debug=true`) — nothing here renders until an engineer explicitly asks
// for it. Every field is read straight from the existing `debug_bundle` on
// EngineeringUnderstandingDTO; no new backend contract is introduced.
// ---------------------------------------------------------------------------

const TRANSCRIPT_STYLE: Record<TranscriptEntry["kind"], string> = {
  intent: "text-fg-muted italic",
  observation: "text-fg-secondary",
  question: "text-warning-fg font-medium",
  answer: "text-accent-fg font-medium",
  conclusion: "text-fg font-medium",
};

const OUTCOME_MARK: Record<InvestigationStep["outcome"], { icon: typeof Check; tone: string }> = {
  success: { icon: Check, tone: "text-success-fg" },
  not_found: { icon: CircleSlash, tone: "text-fg-muted" },
  unavailable: { icon: CircleSlash, tone: "text-fg-muted" },
  failed: { icon: X, tone: "text-danger-fg" },
};

/** The investigation, in order, with what the engine was trying to learn at
 * each step — "what did you actually search?" */
function InvestigationTrail({ steps }: { steps: InvestigationStep[] }) {
  if (steps.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Search}>Investigation Trail</SectionHeading>
      <ol className="flex flex-col gap-2 rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        {steps.map((step) => {
          const mark = OUTCOME_MARK[step.outcome] ?? OUTCOME_MARK.success;
          const Icon = mark.icon;
          return (
            <li key={step.evidence_id} className="flex items-start gap-2 text-xs">
              <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${mark.tone}`} aria-hidden="true" />
              <div className="flex flex-col">
                <span className="text-fg-secondary">{step.summary}</span>
                {step.intent && <span className="text-fg-subtle italic">{step.intent}</span>}
                <span className="text-fg-subtle">
                  {step.provider} · {step.action}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** Per-capability confidence with the signals that produced it — a score is
 * never shown without the decomposition it was computed from. */
function CapabilitySignals({ items }: { items: CapabilityBreakdown[] }) {
  const applicable = items.filter((item) => item.necessity !== "not_applicable");
  if (applicable.length === 0) return null;

  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={ListChecks}>Capability Signals</SectionHeading>
      <div className="flex flex-col gap-2.5">
        {applicable.map((item) => (
          <div key={item.capability} className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-xs font-medium text-fg-secondary">
                {item.label}
                {item.necessity === "recommended" && (
                  <span className="ml-1.5 text-fg-subtle">(optional)</span>
                )}
              </p>
              <span
                className={`text-sm font-semibold ${
                  item.satisfied ? "text-success-fg" : "text-warning-fg"
                }`}
              >
                {Math.round(item.score * 100)}%
              </span>
            </div>
            <ul className="mt-1.5 flex flex-col gap-1">
              {item.signals.map((signal) => (
                <li key={signal.label} className="flex items-start gap-1.5 text-xs">
                  {signal.satisfied ? (
                    <Check
                      className="mt-0.5 h-3 w-3 shrink-0 text-success-fg"
                      aria-hidden="true"
                    />
                  ) : (
                    <X className="mt-0.5 h-3 w-3 shrink-0 text-fg-muted" aria-hidden="true" />
                  )}
                  <span className={signal.satisfied ? "text-fg-secondary" : "text-fg-muted"}>
                    {signal.label}
                    {!signal.satisfied && signal.detail && (
                      <span className="text-fg-subtle"> — {signal.detail}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Facts grouped by kind, each with the evidence that established it. An
 * unverified item is a human claim investigation could not corroborate, and
 * is labelled as such rather than displayed as knowledge. */
function Findings({ groups }: { groups: FindingGroup[] }) {
  const visible = groups.filter((g) => g.items.length > 0);
  if (visible.length === 0) return null;

  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Database}>Findings &amp; Evidence</SectionHeading>
      <div className="flex flex-col gap-2">
        {visible.map((group) => (
          <div key={group.kind} className="rounded-lg bg-surface-raised px-3 py-2">
            <p className="text-xs font-medium text-fg-secondary capitalize">
              {group.kind.replace(/_/g, " ")}
              {group.total > group.items.length && (
                <span className="ml-1.5 font-normal text-fg-subtle">
                  showing {group.items.length} of {group.total}
                </span>
              )}
            </p>
            <ul className="mt-1 flex flex-col gap-1">
              {group.items.map((item) => (
                <li key={item.fact_id} className="text-xs">
                  <span className="text-fg-secondary">{item.subject}</span>
                  {!item.verified && (
                    <span className="ml-1.5 rounded bg-warning-bg px-1 py-0.5 text-warning-fg">
                      unverified claim
                    </span>
                  )}
                  {item.evidence && (
                    <span className="block text-fg-subtle">↳ {item.evidence.summary}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

/** What's missing, why it matters, and what to do about it. */
function Gaps({ gaps }: { gaps: DiscoveryGap[] }) {
  const open = gaps.filter((g) => g.status !== "verified");
  if (open.length === 0) return null;

  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={AlertTriangle}>Gaps (raw)</SectionHeading>
      <div className="flex flex-col gap-2">
        {open.map((gap) => {
          const blocking = gap.severity === "blocking";
          const Icon = blocking ? XCircle : AlertTriangle;
          return (
            <div
              key={gap.gap_id}
              className={`rounded-lg border px-3 py-2 ${
                blocking
                  ? "border-danger-line/30 bg-danger-bg"
                  : "border-warning-line/30 bg-warning-bg"
              }`}
            >
              <p
                className={`flex items-start gap-1.5 text-xs font-medium ${
                  blocking ? "text-danger-fg" : "text-warning-fg"
                }`}
              >
                <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {gap.summary}
              </p>
              <p className="mt-1 pl-5 text-xs text-fg-muted">{gap.why}</p>
              {gap.recommended_action.length > 0 && (
                <p className="mt-1 pl-5 text-xs font-medium text-fg-secondary">
                  Try: {gap.recommended_action.join(" · ")}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** How the engine narrates itself, in order — the raw reasoning trace. */
function RawReasoning({ entries }: { entries: TranscriptEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Workflow}>Raw Reasoning</SectionHeading>
      <ol className="flex flex-col gap-1 border-l border-line-muted pl-3">
        {entries.map((entry, i) => (
          <li key={i} className={`text-xs ${TRANSCRIPT_STYLE[entry.kind]}`}>
            {entry.text}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** What Context Discovery actually traversed in the knowledge graph. */
function GraphTraversal({
  components,
  topics,
  repositoryRanking,
}: {
  components: Record<string, unknown>[];
  topics: Record<string, unknown>[];
  repositoryRanking: string[];
}) {
  if (components.length === 0 && topics.length === 0 && repositoryRanking.length === 0) {
    return null;
  }
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={GitBranch}>Graph Traversal</SectionHeading>
      <div className="flex flex-col gap-1.5 rounded-lg bg-surface-raised px-3 py-2 text-xs text-fg-secondary">
        {repositoryRanking.length > 0 && (
          <p>
            <span className="font-medium">Repository ranking:</span> {repositoryRanking.join(", ")}
          </p>
        )}
        {components.length > 0 && (
          <p>
            <span className="font-medium">Components traversed:</span> {components.length}
          </p>
        )}
        {topics.length > 0 && (
          <p>
            <span className="font-medium">Topics:</span> {topics.length}
          </p>
        )}
      </div>
    </section>
  );
}

/** Retrieval-time internals: capability confidence, planning metadata,
 * working memory, and the assumptions the engine proceeded on. */
function RetrievalInformation({
  capabilityConfidence,
  assumptions,
}: {
  capabilityConfidence: Record<string, number>;
  assumptions: string[];
}) {
  const entries = Object.entries(capabilityConfidence);
  if (entries.length === 0 && assumptions.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Database}>Retrieval Information</SectionHeading>
      <div className="flex flex-col gap-1.5 rounded-lg bg-surface-raised px-3 py-2 text-xs">
        {entries.length > 0 && (
          <ul className="flex flex-col gap-0.5">
            {entries.map(([capability, score]) => (
              <li key={capability} className="text-fg-secondary">
                {capability}: {Math.round(score * 100)}%
              </li>
            ))}
          </ul>
        )}
        {assumptions.length > 0 && (
          <div>
            <p className="font-medium text-fg-secondary">Assumptions proceeding on:</p>
            <ul className="flex flex-col gap-0.5">
              {assumptions.map((a) => (
                <li key={a} className="text-fg-muted">
                  · {a}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

interface DebugPanelProps {
  bundle: DebugBundleDTO | null;
  isLoading: boolean;
  error: string | null;
  /** Called once, the first time an engineer expands this section — the
   * parent owns the `?debug=true` fetch since it already owns the
   * `fetchUnderstanding` call and its token/workflowId. */
  onExpand: () => void;
}

/** Level 3 of the progressive-disclosure hierarchy: collapsed by default,
 * and — unlike Advanced Details — costs a real network request the first
 * time it's opened. Answers "how did it reach that conclusion?": the full
 * investigation trail, capability signals, findings and evidence, graph
 * traversal, raw reasoning, and retrieval internals. This is the only level
 * that shows implementation details. */
export function DebugPanel({ bundle, isLoading, error, onExpand }: DebugPanelProps) {
  const [hasExpanded, setHasExpanded] = useState(false);

  return (
    <details
      className="group rounded-lg border border-line-muted bg-canvas px-3 py-2.5"
      onToggle={(e) => {
        if (e.currentTarget.open && !hasExpanded) {
          setHasExpanded(true);
          onExpand();
        }
      }}
    >
      <summary className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-fg-muted hover:text-fg-secondary">
        <Bug className="h-3.5 w-3.5" aria-hidden="true" />
        Debug
      </summary>
      <div className="mt-3 flex flex-col gap-4">
        {isLoading && (
          <div className="flex items-center gap-2 text-xs text-fg-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            Loading debug information…
          </div>
        )}
        {error && <p className="text-xs text-danger-fg">{error}</p>}
        {bundle && (
          <>
            <InvestigationTrail steps={bundle.investigation_trail as unknown as InvestigationStep[]} />
            <CapabilitySignals
              items={bundle.confidence_breakdown as unknown as CapabilityBreakdown[]}
            />
            <Findings groups={bundle.findings as unknown as FindingGroup[]} />
            <Gaps gaps={bundle.gaps as unknown as DiscoveryGap[]} />
            <RawReasoning entries={bundle.transcript as unknown as TranscriptEntry[]} />
            <GraphTraversal
              components={bundle.graph_components}
              topics={bundle.graph_topics}
              repositoryRanking={bundle.repository_ranking}
            />
            <RetrievalInformation
              capabilityConfidence={bundle.capability_confidence}
              assumptions={bundle.assumptions}
            />
          </>
        )}
      </div>
    </details>
  );
}
